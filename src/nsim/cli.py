"""Command line surface delegating planning, execution and reporting to services."""

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Optional, Sequence

from nsim import get_version
from nsim.cli_aliases import add_argument, add_command
from nsim.config import MODES, ConfigError, discover, load_project
from nsim.planning import create_plan
from nsim.service import replay, run
from nsim.storage import IntegrityError, preflight
from nsim.lsf import LsfError
from nsim.project_paths import ProjectError, resolve_root
from nsim import projects as project_admin


def build_parser(parser_class=argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser = parser_class(
        prog="nsim", description="Traceable ASIC simulation regression orchestration."
    )
    add_argument(parser, "--version", action="version", version="%(prog)s " + get_version())
    add_argument(parser, '-o', '--output-root', dest='global_output_root', action='append', type=Path,
                        help='Select one output root for com/sim/run/regr/recover')
    commands = parser.add_subparsers(dest="command")
    project_admin.add_commands(commands)
    from nsim.submit_commands import add_commands
    add_commands(commands)
    from nsim.lsf_commands import add_commands as add_lsf_commands
    add_lsf_commands(commands)
    capabilities = add_command(commands, 'capabilities', help='supported project schemas')
    add_argument(capabilities, '--json', action='store_true')
    info = add_command(commands, "version", help="Show the tool version for provenance records")
    add_argument(info, "--json", action="store_true", help="Emit machine-readable tool identity")
    project = add_command(commands, "project", help="Discover or inspect project descriptors")
    project_commands = project.add_subparsers(dest="action", required=True)
    listing = add_command(project_commands, "list")
    add_argument(listing, "roots", nargs="+", type=Path)
    inspect = add_command(project_commands, "inspect")
    add_argument(inspect, "path", type=Path)
    for stage, description in (('com', 'Compile only'), ('sim', 'Simulate using existing compile products'),
                               ('run', 'Compile, simulate and check results'),
                               ('regr', 'Compile and run one or more regression case lists')):
        execute = add_command(commands, stage, help=description)
        add_argument(execute, "--project", type=Path, help='Project name/path; default: discover from current directory')
        if stage != 'com':
            selection = execute.add_mutually_exclusive_group(required=True)
            if stage != 'regr':
                add_argument(selection, '--case')
            add_argument(selection, '-l', '--caselist', action='extend', nargs='+', type=Path)
            if stage == 'regr':
                add_argument(selection, '-rl', '--regr-list', type=Path, help='Batch list: one regr argument list per line')
            add_argument(execute, '--plusargs', action='extend', nargs='+', default=[],
                                 help='Final runtime +KEY overrides, shared with regression-list rows')
            seeds = execute.add_mutually_exclusive_group()
            add_argument(seeds, '-seed', '--seed', action='extend', nargs='+', type=int)
            if stage == 'regr':
                add_argument(seeds, '-repeat', type=int, help='Generate N distinct random seeds; exclusive with -seed')
        add_argument(execute, '-sub', '--submit', action='extend', nargs='+', type=Path, required=stage != 'regr',
                             help='Submit names under project/submit, or explicit JSON paths')
        add_argument(execute, '-mode', choices=MODES, help='Default: infer from submits; an explicit mode must match')
        add_argument(execute, '-dir', help='Workspace name (default: selected mode)')
        add_argument(execute, '-com-dir', default='com_dir', help='Compile path relative to workspace, or absolute path')
        add_argument(execute, '-sim-dir', default='sim_dir', help='Simulation parent relative to workspace, or absolute path')
        add_argument(execute, '-o', "--output-root", action="append", type=Path,
                             help="Select one output root for this run")
        add_argument(execute, '-j', '-jobs', "--jobs", type=int, default=1,
                             help='Maximum unfinished jobs across this invocation (default: 1)')
        add_argument(execute, "--retries", type=int, default=0,
                             help="Deprecated; automatic infrastructure retries are disabled (must be 0)")
        add_argument(execute, "--retry-seconds", type=float, default=0,
                             help="Deprecated; automatic infrastructure retries are disabled (must be 0)")
        add_argument(execute, "--min-free-bytes", type=int, default=0)
        add_argument(execute, "--min-free-inodes", type=int, default=0)
        add_argument(execute, "--dry-run", action="store_true")
        add_argument(execute, '-scheduler', "--scheduler", choices=["local", "lsf"],
                             help='Override submit scheduler (default: lsf)')
        add_argument(execute, '-q', '-queue', "--queue")
        add_argument(execute, "--resource", action="append")
        add_argument(execute, "--license", action="append", help="LSF resource=count; CLI replaces submit licenses")
    for action in ("rerun", "recover"):
        again = add_command(commands, action, help="Rebuild a compile directory, rerun a case, or rebuild regression failures" if action == "rerun" else "Rerun a single case/compile directory, or recover regression evidence failures")
        add_argument(again, "source", type=Path, help="Completed case, compile, regression, or batch directory")
        add_argument(again, '--rebuild', '-rebuild', action='store_true',
                     help='Recompile current case inputs; a case source otherwise reuses its recorded build')
        if action == 'recover':
            add_argument(again, '-o', "--output-root", action="append", type=Path)
            add_argument(again, '-seed', "--seed", type=int)
        add_argument(again, "--jobs", type=int)
        add_argument(again, "--retries", type=int)
        add_argument(again, "--retry-seconds", type=float)
        add_argument(again, '-dir', '--dir', dest='output_dir', help='Name the new replay directory')
    report = add_command(commands, "report", help="Inspect saved results or rebuild an HTML report")
    add_argument(report, "source", type=Path, help="Completed case, regression or batch directory")
    add_argument(report, "--rebuild", action="store_true", help="Write a derived HTML view without changing execution records")
    add_argument(report, "--json", action="store_true")
    add_argument(report, '-dir', '--dir', dest='output_dir', help='Name the new rebuilt report directory')
    add_argument(report, '--include', action='append', type=Path, default=[], help='Select a rerun or recover directory')
    storage = add_command(commands, "storage", help="Probe an existing storage root")
    probes = storage.add_subparsers(dest="action", required=True)
    probe = add_command(probes, "check")
    add_argument(probe, "root", type=Path)
    add_argument(probe, "--min-free-bytes", type=int, default=0)
    add_argument(probe, "--min-free-inodes", type=int, default=0)
    return parser


def resolve_submits(root, values):
    paths = []
    for value in values:
        if value.is_absolute() or len(value.parts) > 1 or value.is_file():
            path = value.resolve()
        else:
            filename = value.name if value.suffix == '.json' else value.name + '.json'
            path = (root / 'submit' / filename).resolve()
        if not path.is_file():
            raise ConfigError(f'submit not found: {value} (resolved to {path})')
        if path not in paths:
            paths.append(path)
    return paths


def prepare_execution(args):
    if not args.submit:
        raise ConfigError('one or more -sub/--submit values are required for a single regression')
    overrides = {k: v for k, v in {'kind': args.scheduler, 'queue': args.queue, 'resources': args.resource}.items() if v is not None}
    if args.license is not None:
        try:
            overrides['licenses'] = {k: int(v) for k, v in (item.split('=', 1) for item in args.license)}
        except ValueError as exc:
            raise ConfigError('--license requires resource=positive-integer') from exc
    root = resolve_root(args.project)
    plan = create_plan(root, caselist=getattr(args, "caselist", None),
                       cases=[args.case] if getattr(args, 'case', None) else None,
                       submit_paths=resolve_submits(root, args.submit), seeds=getattr(args, 'seed', None),
                       allow_missing_tools=args.dry_run, scheduler_overrides=overrides,
                       phase=args.command, mode=args.mode, directory=args.dir,
                       com_dir=args.com_dir, sim_dir=args.sim_dir, repeat=getattr(args, 'repeat', None),
                       plusargs=getattr(args, 'plusargs', []))
    defaults = plan['project'].get('storage_defaults', {})
    roots = args.output_root or args.global_output_root or defaults.get('output_roots') or (
        [os.environ['NSIM_OUTPUT_ROOT']] if os.environ.get('NSIM_OUTPUT_ROOT') else None)
    if not roots:
        raise ConfigError('provide --output-root, or save project defaults with '
                          'nsim update --project NAME --output-root PATH')
    from nsim.config import integer, number
    integer(args.jobs, 'jobs', 1)
    integer(args.retries, 'retries')
    number(args.retry_seconds, 'retry_seconds')
    integer(args.min_free_bytes, 'min_free_bytes')
    integer(args.min_free_inodes, 'min_free_inodes')
    from nsim.service import validate_roots
    from nsim.layout import validate_layout
    roots = validate_roots(plan, roots)
    validate_layout(plan, roots)
    if not args.dry_run and any(s['adapter'] == 'vcs' for s in plan['submits']):
        raise ConfigError('native VCS execution is not yet enabled; use --dry-run')
    settings = dict(jobs=args.jobs, retries=args.retries, retry_seconds=args.retry_seconds,
                    min_free_bytes=args.min_free_bytes, min_free_inodes=args.min_free_inodes, dry_run=args.dry_run)
    return plan, roots, settings


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    execution_commands = ('com', 'sim', 'run', 'regr', 'recover')
    if args.global_output_root and args.command not in execution_commands:
        parser.error('-o before the command applies only to execution or replay commands')
    if args.command == 'capabilities':
        print(json.dumps({'version': get_version(), 'project_schemas': [1, 2]}))
        return 0
    if args.command == "version":
        identity = {"tool": "nsim", "version": get_version()}
        print(json.dumps(identity, sort_keys=True) if args.json else "nsim " + identity["version"])
        return 0
    try:
        if args.command == 'submit':
            from nsim.submit_commands import dispatch
            return dispatch(args)
        if args.command == 'lsf':
            from nsim.lsf_commands import dispatch
            return dispatch(args)
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
            from nsim.report_command import report
            output, code = report(args.source, rebuild=args.rebuild, as_json=args.json,
                                  output_dir=args.output_dir, includes=args.include)
            print(output)
            return code
        if args.command in ('com', 'sim', 'run', 'regr'):
            if getattr(args, 'regr_list', None):
                from nsim.batch import run_batch, prepare_batch, validate_outer
                validate_outer(list(argv) if argv is not None else sys.argv[1:])
                batch, entries = prepare_batch(args)
                journal, state = run_batch(batch, entries)
            else:
                plan, roots, settings = prepare_execution(args)
                journal, state = run(plan, roots, **settings)
        elif args.command in ("rerun", "recover"):
            journal, state = replay(args.source, roots=getattr(args, 'output_root', None) or args.global_output_root,
                                    recovery=args.command == "recover", seed=getattr(args, 'seed', None), jobs=args.jobs,
                                    retries=args.retries, retry_seconds=args.retry_seconds,
                                    output_dir=args.output_dir, rebuild=getattr(args, 'rebuild', False))
        else:
            parser.print_help()
            return 0
        print(json.dumps({"record_dir": str(journal) if journal else None,
                          'phase': state.get('phase', args.command),
                          "journal": str(journal) if journal else None,
                          "passed": state["passed"], "tasks": state.get("task_count", len(state.get("tasks", []))),
                          "dry_run": state.get("dry_run", False), "message": state.get("message")}, indent=2))
        if state.get("dry_run"):
            return 0
        return 0 if state["passed"] else 2 if state.get("report_errors") or state.get('scheduler_errors') else 1
    except (OSError, ConfigError, ProjectError, IntegrityError, UnicodeError, LsfError) as exc:
        print(f"nsim: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("nsim: interrupted; no terminal journal is published until all jobs are known finished", file=sys.stderr)
        return 130
