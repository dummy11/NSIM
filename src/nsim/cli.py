"""Command line surface delegating planning, execution and reporting to services."""

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Optional, Sequence

from nsim import get_version
from nsim.config import ConfigError, discover, load_project
from nsim.planning import create_plan
from nsim.reports import load_final, markdown
from nsim.service import replay, run
from nsim.storage import IntegrityError, preflight
from nsim.project_paths import ProjectError, resolve_root
from nsim import projects as project_admin


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nsim", description="Traceable ASIC simulation regression orchestration."
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + get_version())
    commands = parser.add_subparsers(dest="command")
    project_admin.add_commands(commands)
    capabilities = commands.add_parser('capabilities', help='supported project schemas')
    capabilities.add_argument('--json', action='store_true')
    info = commands.add_parser("version", help="Show the tool version for provenance records")
    info.add_argument("--json", action="store_true", help="Emit machine-readable tool identity")
    project = commands.add_parser("project", help="Discover or inspect project descriptors")
    project_commands = project.add_subparsers(dest="action", required=True)
    listing = project_commands.add_parser("list")
    listing.add_argument("roots", nargs="+", type=Path)
    inspect = project_commands.add_parser("inspect")
    inspect.add_argument("path", type=Path)
    launch = commands.add_parser("run", help="Plan and execute a local regression")
    launch_commands = launch.add_subparsers(dest="action", required=True)
    for action in ("case", "regression"):
        execute = launch_commands.add_parser(action)
        execute.add_argument("--project", type=Path, required=True)
        execute.add_argument("--case" if action == "case" else "--caselist", required=True)
        execute.add_argument("--submit", action="append", type=Path, required=True)
        execute.add_argument("--seed", action="append", type=int)
        execute.add_argument("--output-root", action="append", type=Path,
                             help="Existing output root; repeat to configure fallback storage")
        execute.add_argument("--event-root", type=Path, help="Existing independent control journal directory")
        execute.add_argument("--jobs", type=int, default=1)
        execute.add_argument("--retries", type=int, default=1)
        execute.add_argument("--retry-seconds", type=float, default=120)
        execute.add_argument("--min-free-bytes", type=int, default=0)
        execute.add_argument("--min-free-inodes", type=int, default=0)
        execute.add_argument("--dry-run", action="store_true")
        execute.add_argument("--scheduler", choices=["local", "lsf"])
        execute.add_argument("--queue")
        execute.add_argument("--resource", action="append")
        execute.add_argument("--license", action="append", help="LSF resource=count; CLI replaces submit licenses")
    for action in ("rerun", "recover"):
        again = commands.add_parser(action, help="Replay terminal failures" if action == "rerun" else "Recover terminal storage/evidence failures")
        again.add_argument("source", type=Path, help="Completed control journal directory")
        again.add_argument("--output-root", action="append", type=Path)
        again.add_argument("--event-root", type=Path)
        again.add_argument("--seed", type=int)
        again.add_argument("--jobs", type=int)
        again.add_argument("--retries", type=int)
        again.add_argument("--retry-seconds", type=float)
    report = commands.add_parser("report", help="Validate and show terminal evidence")
    reports = report.add_subparsers(dest="action", required=True)
    show = reports.add_parser("show")
    show.add_argument("source", type=Path)
    show.add_argument("--json", action="store_true")
    storage = commands.add_parser("storage", help="Probe an existing storage root")
    probes = storage.add_subparsers(dest="action", required=True)
    probe = probes.add_parser("check")
    probe.add_argument("root", type=Path)
    probe.add_argument("--min-free-bytes", type=int, default=0)
    probe.add_argument("--min-free-inodes", type=int, default=0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == 'capabilities':
        print(json.dumps({'version': get_version(), 'project_schemas': [1, 2]}))
        return 0
    if args.command == "version":
        identity = {"tool": "nsim", "version": get_version()}
        print(json.dumps(identity, sort_keys=True) if args.json else "nsim " + identity["version"])
        return 0
    try:
        if args.command in ('bootstrap', 'list', 'status', 'update', 'switch'):
            return project_admin.dispatch(args)
        if args.command == "project":
            projects = discover(args.roots) if args.action == "list" else [load_project(args.path)]
            print(json.dumps([{"name": p.name, "root": str(p.root), "inputs": [str(i) for i in p.inputs]}
                              for p in projects], indent=2))
            return 0
        if args.command == "storage":
            print(json.dumps(preflight(args.root.resolve(), args.min_free_bytes, args.min_free_inodes), indent=2))
            return 0
        if args.command == "report":
            _, state = load_final(args.source)
            print(json.dumps(state, indent=2) if args.json else markdown(state))
            return 0 if state["passed"] else 1
        if args.command == "run":
            roots = args.output_root or ([Path(os.environ["NSIM_OUTPUT_ROOT"])] if "NSIM_OUTPUT_ROOT" in os.environ else None)
            events = args.event_root or os.environ.get("NSIM_EVENT_ROOT")
            if not roots or not events:
                raise ConfigError("provide --output-root and --event-root (or NSIM_OUTPUT_ROOT / NSIM_EVENT_ROOT)")
            overrides = {k: v for k, v in {'kind': args.scheduler, 'queue': args.queue, 'resources': args.resource}.items() if v is not None}
            if args.license is not None:
                try:
                    overrides['licenses'] = {k: int(v) for k, v in (item.split('=', 1) for item in args.license)}
                except ValueError as exc:
                    raise ConfigError('--license requires resource=positive-integer') from exc
            plan = create_plan(resolve_root(args.project), caselist=getattr(args, "caselist", None),
                               cases=[args.case] if args.action == "case" else None,
                               submit_paths=args.submit, seeds=args.seed, allow_missing_tools=args.dry_run, scheduler_overrides=overrides)
            journal, state = run(plan, roots, events, jobs=args.jobs, retries=args.retries,
                                 retry_seconds=args.retry_seconds, min_free_bytes=args.min_free_bytes,
                                 min_free_inodes=args.min_free_inodes, dry_run=args.dry_run)
        elif args.command in ("rerun", "recover"):
            journal, state = replay(args.source, roots=args.output_root, events=args.event_root,
                                    recovery=args.command == "recover", seed=args.seed, jobs=args.jobs,
                                    retries=args.retries, retry_seconds=args.retry_seconds)
        else:
            parser.print_help()
            return 0
        print(json.dumps({"journal": str(journal) if journal else None,
                          "passed": state["passed"], "tasks": len(state["tasks"]),
                          "dry_run": state.get("dry_run", False), "message": state.get("message")}, indent=2))
        if state.get("dry_run"):
            return 0
        return 0 if state["passed"] else 2 if state.get("report_errors") else 1
    except (OSError, ConfigError, ProjectError, IntegrityError, UnicodeError) as exc:
        print(f"nsim: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("nsim: interrupted; no terminal journal is published until all jobs are known finished", file=sys.stderr)
        return 130
