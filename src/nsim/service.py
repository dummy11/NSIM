"""Single-controller rolling execution, durable journals and explicit replay."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import copy
import fcntl
import html
import os
from pathlib import Path
import threading
import time
import uuid

from nsim.checks import ACCEPTED
from nsim.config import ConfigError, integer, number
from nsim.planning import check_identity
from nsim.reports import assessed, html_report, load_final, markdown
from nsim.runner import execute
from nsim.storage import atomic_write, digest, encode, error_record, preflight, write_json


class StoragePool:
    def __init__(self, roots):
        self.roots = roots
        self.quarantined = {}
        self.lock = threading.Lock()

    def choose(self):
        with self.lock:
            return next((p for p in self.roots if str(p) not in self.quarantined), None)

    def isolate(self, root, error):
        with self.lock:
            self.quarantined[str(root)] = error


def validate_roots(plan, roots, events):
    roots = list(dict.fromkeys(Path(p).resolve() for p in roots))
    events = Path(events).resolve()
    if not roots:
        raise ConfigError("at least one output root is required")
    source = Path(plan["project"]["root"])
    tool = Path(__file__).resolve().parent
    def nested(a, b):
        return a == b or a in b.parents or b in a.parents
    for root in roots:
        if nested(root, events) or nested(root, source) or nested(root, tool):
            raise ConfigError("output, event, project and tool roots must be separate non-nested locations")
    if nested(events, source) or nested(events, tool):
        raise ConfigError("event root must be outside project and tool trees")
    for index, root in enumerate(roots):
        if any(nested(root, other) for other in roots[index + 1:]):
            raise ConfigError("output roots must not be nested")
    return roots, events


def run(plan, roots, events, jobs=1, retries=1, retry_seconds=120,
        min_free_bytes=0, min_free_inodes=0, dry_run=False):
    integer(jobs, "jobs", 1)
    integer(retries, "retries")
    number(retry_seconds, "retry_seconds")
    integer(min_free_bytes, "min_free_bytes")
    integer(min_free_inodes, "min_free_inodes")
    roots, events = validate_roots(plan, roots, events)
    check_identity(plan)
    if not dry_run:
        for submit in plan['submits']:
            if submit.get('unresolved_tools'):
                raise ConfigError('cannot execute plan with unresolved tools: ' + ', '.join(submit['unresolved_tools']))
            if submit['adapter'] == 'vcs' or submit['scheduler']['kind'] == 'lsf':
                raise ConfigError('native VCS/LSF execution is not yet enabled; use --dry-run')
    preflight(events)
    plan = copy.deepcopy(plan)
    policy = dict(jobs=jobs, retries=retries, retry_seconds=retry_seconds,
                  min_free_bytes=min_free_bytes, min_free_inodes=min_free_inodes,
                  output_roots=[str(p) for p in roots], event_root=str(events))
    plan["policy"] = policy
    journal = events / plan["id"]
    journal.mkdir(exist_ok=False)
    (journal / "attempts").mkdir()
    write_json(journal / "plan.json", plan)
    if dry_run:
        failures = []
        for root in roots:
            try:
                preflight(root, min_free_bytes, min_free_inodes)
                base = root / "regressions" / plan["id"]
                for directory in ("config", "build", "tasks", "logs", "waves", "reports"):
                    (base / directory).mkdir(parents=True, exist_ok=True)
                write_json(base / "config" / "plan.json", plan)
                write_json(base / "metadata.json", {"id": plan["id"], "state": "planned", "journal": str(journal)})
                from nsim.eda import commands
                preview = {'schema_version': 1, 'executed': False, 'tasks': [commands(plan, task, root) for task in plan['tasks']]}
                write_json(journal / 'commands.json', preview)
                write_json(base / 'config' / 'commands.json', preview)
                write_json(journal / "preflight.json", {"selected_root": str(root), "failures": failures})
                return journal, {"id": plan["id"], "dry_run": True, "tasks": plan["tasks"], "passed": False}
            except OSError as exc:
                failures.append(error_record(exc, root))
        write_json(journal / "preflight.json", {"selected_root": None, "failures": failures})
        raise OSError("no healthy output root for dry-run plan")
    cancel = threading.Event()
    pool = StoragePool(roots)
    state = {"schema_version": 1, "id": plan["id"], "terminal": False, "started_at": time.time(),
             "ended_at": None, "report_errors": [], "quarantined": {}, "tasks": [
                 dict(task, submit_name=plan["submits"][task["submit"]]["name"], status="planned", attempts=[])
                 for task in plan["tasks"]]}

    def task_work(task):
        attempts = []
        deadline = time.monotonic() + retry_seconds
        for retry in range(retries + 1):
            if cancel.is_set():
                break
            root = pool.choose()
            if root is None:
                break
            attempt = execute(plan, task, root, policy, cancel)
            attempt["retry_index"] = retry
            attempt["parent"] = attempts[-1]["id"] if attempts else task["parent"]
            attempts.append(attempt)
            # If the primary disk fails, this independent journal still owns the failure.
            try:
                write_json(journal / "attempts" / (attempt["id"] + ".json"), attempt)
            except OSError as exc:
                raise OSError(exc.errno, f"attempt evidence NOT persisted: task={task['id']} "
                              f"attempt={attempt['id']} host={attempt.get('host')} "
                              f"output={root} journal={journal}: {exc}") from exc
            if attempt["verdict"] != "INFRA_ERROR":
                break
            pool.isolate(root, attempt.get("storage_error", {"reason": attempt.get("reason")}))
            if time.monotonic() >= deadline:
                break
        if not attempts:
            attempts.append({"id": uuid.uuid4().hex, "verdict": "INFRA_ERROR" if not cancel.is_set() else "CANCELLED",
                             "reason": "no healthy storage available" if not cancel.is_set() else "controller cancelled",
                             "evidence": {}, "checks": {}, "parent": task["parent"], "exit_code": None})
        return attempts

    def progress():
        write_json(journal / "live.json", assessed(state))
        atomic_write(journal / "live.html", html_report(state))

    executor = ThreadPoolExecutor(max_workers=jobs)
    with (journal / "controller.lock").open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.write(str(os.getpid()))
        lock.flush()
        pending = iter(state["tasks"])
        active = {}
        try:
            progress()
            while True:
                launched = False
                while len(active) < jobs and not cancel.is_set():
                    task = next(pending, None)
                    if task is None:
                        break
                    task["status"] = "running"
                    task["submitted_at"] = time.time()
                    active[executor.submit(task_work, task)] = task
                    launched = True
                if launched:
                    progress()
                if not active:
                    break
                done, _ = wait(active, timeout=.2, return_when=FIRST_COMPLETED)
                for future in done:
                    task = active.pop(future)
                    task["attempts"] = future.result()
                    task["status"] = "completed"
                if done:
                    progress()
            state["quarantined"] = pool.quarantined
            state["terminal"] = True
            state["ended_at"] = time.time()
            state = assessed(state)
            # Publish each healthy output root, then commit the controller snapshot LAST.
            for root in roots:
                if str(root) in pool.quarantined:
                    continue
                try:
                    preflight(root, min_free_bytes, min_free_inodes)
                    base = root / "regressions" / plan["id"]
                    for directory in ("config", "build", "tasks", "logs", "waves", "reports"):
                        (base / directory).mkdir(parents=True, exist_ok=True)
                    # Output roots contain a pointer, not competing aggregate verdicts.
                    # A later failure publishing another root must not leave an earlier false PASS.
                    report_url = (journal / "report.html").as_uri()
                    payloads = {"config/plan.json": encode(plan),
                                "metadata.json": encode({"id": plan["id"], "journal": str(journal),
                                                         "authority": "journal/final.json"}),
                                "reports/report.md": f"Final report: [{plan['id']}]({report_url})\n".encode(),
                                "reports/report.html": (f'<!doctype html><meta charset="utf-8">'
                                                        f'<a href="{html.escape(report_url, quote=True)}">'
                                                        'Open the authoritative journal report</a>').encode()}
                    for filename, content in payloads.items():
                        atomic_write(base / filename, content)
                    write_json(base / "complete.json", {"schema_version": 1, "kind": "artifact_index", "journal": str(journal),
                                                        "files": {n: digest(base / n) for n in payloads}})
                except OSError as exc:
                    state["report_errors"].append(error_record(exc, root))
                    pool.isolate(root, error_record(exc, root))
            state["quarantined"] = pool.quarantined
            state = assessed(state)
            write_json(journal / "state.json", state)
            atomic_write(journal / "report.md", markdown(state).encode())
            atomic_write(journal / "report.html", html_report(state))
            write_json(journal / "failed.json", [t["id"] for t in state["tasks"] if t["effective_verdict"] not in ACCEPTED])
            progress()
            write_json(journal / "final.json", {"schema_version": 1, "terminal": True,
                                                "files": {n: digest(journal / n) for n in
                                                          ("plan.json", "state.json", "report.md", "report.html", "failed.json")}})
            return journal, state
        finally:
            cancel.set()
            executor.shutdown(wait=True, cancel_futures=True)


def replay(source, roots=None, events=None, recovery=False, seed=None, **overrides):
    source = Path(source).resolve()
    original, state = load_final(source)
    check_identity(original)
    selected = state["tasks"] if state["report_errors"] else [
        task for task in state["tasks"] if task["effective_verdict"] not in ACCEPTED]
    if recovery and not state["report_errors"]:
        selected = [t for t in selected if t["effective_verdict"] in {"INFRA_ERROR", "INCOMPLETE"}]
    if not selected:
        return None, {"passed": state["passed"], "message": "no matching failed tasks", "tasks": []}
    plan = copy.deepcopy(original)
    plan["id"] = uuid.uuid4().hex
    plan["source"] = {"journal": str(source), "id": original["id"], "mode": "recover" if recovery else "rerun"}
    plan["tasks"] = []
    for task in selected:
        copy_task = next(copy.deepcopy(t) for t in original["tasks"] if t["id"] == task["id"])
        copy_task["parent"] = {"run": original["id"], "task": task["id"], "attempt": task["attempts"][-1]["id"]}
        if seed is not None:
            integer(seed, "seed", 1, 2147483647)
            copy_task["seed"] = seed
        plan["tasks"].append(copy_task)
    policy = original["policy"]
    settings = {key: policy[key] for key in ("jobs", "retries", "retry_seconds", "min_free_bytes", "min_free_inodes")}
    settings.update({k: v for k, v in overrides.items() if v is not None})
    return run(plan, roots or policy["output_roots"], events or policy["event_root"], **settings)
