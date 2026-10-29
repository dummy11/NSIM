"""Single-controller rolling execution, durable journals and explicit replay."""

from concurrent.futures import ThreadPoolExecutor
import copy
import fcntl
import os
from pathlib import Path
import queue
import threading
import time
import uuid

from nsim.checks import ACCEPTED
from nsim.case_records import make_record
from nsim.config import ConfigError, integer, number
from nsim.layout import (SimulationDirectories, is_regression, make_directory, build_directory,
                         record_directory, simulation_directory, validate_layout, isolated_regression,
                         workspace_directory)
from nsim.compilation import Builds
from nsim.dashboard import Dashboard
from nsim.planning import check_identity
from nsim.reports import assessed, html_report, load_final, markdown
from nsim.runner import execute
from nsim.storage import atomic_write, digest, error_record, preflight, write_json


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


def validate_roots(plan, roots):
    roots = list(dict.fromkeys(Path(p).resolve() for p in roots))
    if not roots:
        raise ConfigError("at least one output root is required")
    sources = [Path(plan['project']['root']), *[Path(s['root']) for s in plan['project'].get('sources', {}).values()]]
    tool = Path(__file__).resolve().parent
    def nested(a, b):
        return a == b or a in b.parents or b in a.parents
    for root in roots:
        if any(nested(root, source) for source in sources) or nested(root, tool):
            raise ConfigError("output, project and tool roots must be separate non-nested locations")
    for index, root in enumerate(roots):
        if any(nested(root, other) for other in roots[index + 1:]):
            raise ConfigError("output roots must not be nested")
    return roots


def run(plan, roots, jobs=1, retries=1, retry_seconds=120,
        min_free_bytes=0, min_free_inodes=0, dry_run=False, on_progress=None):
    with SimulationDirectories(plan, min_free_bytes, min_free_inodes) as directories, Builds(plan) as builds:
        return _run(plan, roots, directories, builds, jobs, retries, retry_seconds,
                    min_free_bytes, min_free_inodes, dry_run, on_progress)


def _run(plan, roots, directories, builds, jobs, retries, retry_seconds,
         min_free_bytes, min_free_inodes, dry_run, on_progress=None):
    integer(jobs, "jobs", 1)
    integer(retries, "retries")
    number(retry_seconds, "retry_seconds")
    integer(min_free_bytes, "min_free_bytes")
    integer(min_free_inodes, "min_free_inodes")
    roots = validate_roots(plan, roots)
    validate_layout(plan, roots)
    check_identity(plan)
    phase = plan.get('execution', {}).get('phase', 'run')
    destinations = [str(simulation_directory(roots[0], plan, task)).casefold() for task in plan['tasks']]
    if len(set(destinations)) != len(destinations):
        raise ConfigError('duplicate simulation destinations; case names must not differ only in case')
    if not dry_run:
        for submit in plan['submits']:
            if submit.get('unresolved_tools'):
                raise ConfigError('cannot execute plan with unresolved tools: ' + ', '.join(submit['unresolved_tools']))
            if submit['adapter'] == 'vcs':
                raise ConfigError('native VCS execution is not yet enabled; use --dry-run')
    pool = StoragePool(roots)
    record_root = None
    for root in roots:
        try:
            preflight(root, min_free_bytes, min_free_inodes)
            record_root = root
            break
        except OSError as exc:
            pool.isolate(root, error_record(exc, root))
    if record_root is None:
        raise OSError('no healthy output root; cannot persist run records: ' + str(pool.quarantined))
    plan = copy.deepcopy(plan)
    journal = None
    if dry_run or is_regression(plan):
        execution_root = record_root / 'regr_dir' / plan['id']
        # Claim the execution namespace before creating any compile/simulation
        # directories. Never silently adopt an old or another controller's run.
        make_directory(execution_root.parent, record_root)
        execution_root.mkdir(exist_ok=False)
        journal = execution_root / 'report'
        journal.mkdir()
        if isolated_regression(plan) and not dry_run:
            for root in roots:
                if root == record_root or str(root) in pool.quarantined:
                    continue
                try:
                    preflight(root, min_free_bytes, min_free_inodes)
                except OSError as exc:
                    pool.isolate(root, error_record(exc, root))
                    continue
                make_directory(root / 'regr_dir', root)
                (root / 'regr_dir' / plan['id']).mkdir(exist_ok=False)
    cancel = threading.Event()
    compile_policy = dict(min_free_bytes=min_free_bytes, min_free_inodes=min_free_inodes)
    if not dry_run:
        for task in plan['tasks']:
            builds.reserve(record_root, task, compile_policy)
    if not dry_run and (phase == 'sim' or (phase == 'com' and not is_regression(plan))):
        # Validate all reused builds before replacing any simulation output.
        for task in plan['tasks']:
            build, _ = builds.ensure(record_root, task, cancel, compile_policy)
            if phase == 'sim':
                plan.setdefault('build_refs', {})[task['id']] = {
                    'path': str(build), 'sha256': digest(build / 'build.json')}
    if not dry_run and phase != 'com':
        # Reserve every primary destination before replacing any existing result.
        for task in plan['tasks']:
            directories.reserve(record_root, task)
    if journal is not None:
        pass
    elif phase == 'com':
        journal = record_directory(record_root, plan)
    else:
        journal = directories.prepare(record_root, plan['tasks'][0])
    policy = dict(jobs=jobs, retries=retries, retry_seconds=retry_seconds,
                  min_free_bytes=min_free_bytes, min_free_inodes=min_free_inodes,
                  output_roots=[str(p) for p in roots], record_dir=str(journal))
    plan["policy"] = policy
    single = not dry_run and not is_regression(plan) and phase != 'com'
    if phase == 'com':
        (journal / "records").mkdir()
    if not single:
        write_json(journal / "plan.json", plan)
    if dry_run:
        from nsim.eda import commands
        preview = {'schema_version': 1, 'executed': False,
                   'tasks': [commands(plan, task, record_root) for task in plan['tasks']]}
        write_json(journal / 'commands.json', preview)
        write_json(journal / 'preflight.json', {'selected_root': str(record_root), 'failures': pool.quarantined})
        return journal, {"id": plan["id"], "dry_run": True, "tasks": plan["tasks"], "passed": False}
    state = {"schema_version": 1, "id": plan["id"], 'phase': phase, "terminal": False, "started_at": time.time(),
             "ended_at": None, "report_errors": [], 'scheduler_jobs': {}, "quarantined": {}, "tasks": [
                 dict(task, submit_name=plan["submits"][task["submit"]]["name"], status="planned", attempts=[])
                 for task in plan["tasks"]]}
    def save_single(lifecycle):
        path = journal / 'run.json'
        task = state['tasks'][0]
        value = make_record(plan, task, path, state, lifecycle)
        saved = write_json(path, value)
        if task['attempts']:
            task['attempts'][-1]['evidence'][str(path)] = saved

    if single:
        save_single('running')
    notifications = queue.SimpleQueue()
    ready = threading.Event()
    dashboard = Dashboard(journal) if is_regression(plan) else None
    def job_changed(event):
        notifications.put(event)
        ready.set()
    builds.on_event = job_changed
    for _, manifest in builds.entries.values():
        job = manifest.get('scheduler')
        if job:
            job_changed({key: job[key] for key in ('token', 'task_id', 'phase', 'job_id', 'status', 'terminal', 'record')})

    def completed(future):
        # Completion hook wakes the dispatcher; it refills every vacant slot.
        ready.set()

    def collect_events():
        changed = False
        while not notifications.empty():
            event = notifications.get_nowait()
            state['scheduler_jobs'][event['token']] = event
            if dashboard:
                dashboard.update(state, jobs=[event])
            changed = True
        return changed

    def task_work(task):
        attempts = []
        deadline = time.monotonic() + retry_seconds
        for retry in range(retries + 1):
            if cancel.is_set():
                break
            root = pool.choose()
            if root is None:
                break
            attempt = execute(plan, task, root, policy, cancel, directories, builds, attempts)
            attempt["retry_index"] = retry
            attempt["parent"] = attempts[-1]["id"] if attempts else task["parent"]
            attempts.append(attempt)
            # Records now share output storage. If this write fails, stop dispatching;
            # never claim that a failed attempt was durably recorded elsewhere.
            try:
                if phase == 'com':
                    write_json(journal / "records" / (attempt["id"] + ".json"), attempt)
                elif not attempt.get('record_path'):
                    # Failures before simulation (or a failed product write) still
                    # need a durable case record before emitting completion.
                    path = directories.prepare(record_root, task) / 'run.json'
                    attempt['record_path'] = str(path)
                    value = make_record(plan, dict(task, status='completed', attempts=attempts),
                                        path, lifecycle='attempt_complete')
                    attempt['evidence'][str(path)] = write_json(path, value)
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

    def progress(lifecycle='running', message=''):
        if dashboard:
            if dashboard.publish(state, lifecycle, message) and on_progress:
                on_progress(journal, dashboard.snapshot)

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
                    if dashboard:
                        dashboard.update(state, tasks=[task])
                    future = executor.submit(task_work, task)
                    active[future] = task
                    future.add_done_callback(completed)
                    launched = True
                if launched:
                    progress()
                if not active:
                    break
                ready.clear()
                changed = collect_events()
                done = [future for future in active if future.done()]
                if not done:
                    if changed:
                        progress()
                    ready.wait(.2)
                    continue
                for future in done:
                    task = active.pop(future)
                    task["attempts"] = future.result()
                    task["status"] = "completed"
                    if dashboard:
                        dashboard.update(state, tasks=[task])
                if done:
                    progress()
            collect_events()
            for task in state['tasks']:
                if task['status'] == 'planned':
                    task['status'] = 'cancelled'
                    task['attempts'] = [{'id': uuid.uuid4().hex, 'verdict': 'CANCELLED', 'terminal': True,
                                         'reason': 'not submitted after controller stopped dispatching',
                                         'evidence': {}, 'checks': {}, 'exit_code': None, 'parent': task['parent']}]
            unresolved = [attempt for task in state['tasks'] for attempt in task['attempts']
                          if attempt.get('terminal') is False]
            if unresolved:
                state.update(message='unresolved LSF jobs; no terminal completion published',
                             ended_at=time.time(), scheduler_errors=[a['reason'] for a in unresolved])
                state = assessed(state)
                if single:
                    save_single('unresolved')
                else:
                    write_json(journal / 'state.json', state)
                progress('unresolved', state['message'])
                return journal, state
            state["quarantined"] = pool.quarantined
            state["terminal"] = True
            state["ended_at"] = time.time()
            state = assessed(state)
            files = ['plan.json', 'state.json']
            if is_regression(plan):
                # Reports are derived views. Retain any publication error in the
                # machine-readable result so recovery can run on another root.
                try:
                    write_json(journal / 'failed.json', [t['id'] for t in state['tasks'] if t['effective_verdict'] not in ACCEPTED])
                    atomic_write(journal / 'report.md', markdown(state, verify=False).encode())
                    atomic_write(journal / 'report.html', html_report(state, verify=False))
                    files.extend(['failed.json', 'report.md', 'report.html'])
                except OSError as exc:
                    state['report_errors'].append(error_record(exc, journal))
                    # Remove partial human reports: an earlier PASS must not survive
                    # a later report publication failure.
                    for filename in ('failed.json', 'report.md', 'report.html'):
                        (journal / filename).unlink(missing_ok=True)
            state["quarantined"] = pool.quarantined
            state = assessed(state, verify=False)
            if single:
                save_single('completed')
                return journal, state
            write_json(journal / "state.json", state)
            progress('finalizing')
            write_json(journal / "final.json", {"schema_version": 2, "terminal": True,
                                                "files": {n: digest(journal / n) for n in files}})
            progress('complete', '最终报告发布失败，请检查报告错误。' if state['report_errors'] else '')
            return journal, state
        except BaseException as exc:
            # Stop dispatching and wait for cancellation before publishing a stopped UI.
            # No completed/terminal run is invented for an interrupted controller.
            cancel.set()
            executor.shutdown(wait=True, cancel_futures=True)
            collect_events()
            for future, task in active.items():
                if future.done() and not future.cancelled():
                    try:
                        task['attempts'] = future.result()
                        task['status'] = 'completed'
                    except BaseException:
                        task['status'] = 'interrupted'
            try:
                state['message'] = str(exc) or 'controller interrupted'
                state['terminal'] = False
                if single:
                    save_single('interrupted' if isinstance(exc, KeyboardInterrupt) else 'error')
                else:
                    write_json(journal / 'state.json', assessed(state, verify=False))
                progress('interrupted' if isinstance(exc, KeyboardInterrupt) else 'error',
                         '控制器已停止；未发布新的最终完成记录。' if isinstance(exc, KeyboardInterrupt) else str(exc))
            except (OSError, ValueError):
                # If output storage is unavailable, keep the primary error.
                # No new terminal verdict is displayed without a saved summary.
                pass
            raise
        finally:
            cancel.set()
            executor.shutdown(wait=True, cancel_futures=True)
            if dashboard:
                dashboard.close()


def replay(source, roots=None, recovery=False, seed=None, **overrides):
    source = Path(source).resolve()
    original, state = load_final(source)
    if original.get('kind') == 'batch':
        raise ConfigError('choose an individual regression from the batch report for rerun/recover')
    check_identity(original)
    selected = state["tasks"] if state["report_errors"] else [
        task for task in state["tasks"] if task["effective_verdict"] not in ACCEPTED]
    if recovery and not state["report_errors"]:
        selected = [t for t in selected if t["effective_verdict"] in {"INFRA_ERROR", "INCOMPLETE"}]
    if not selected:
        return None, {"passed": state["passed"], "message": "no matching failed tasks", "tasks": []}
    plan = copy.deepcopy(original)
    plan["id"] = uuid.uuid4().hex
    if (is_regression(original) or isolated_regression(original)) and original['execution']['phase'] != 'com':
        plan['kind'] = 'regression'
        plan['execution']['layout'] = 'isolated-regression-v1'
        if original['execution']['phase'] == 'sim':
            plan['execution']['reuse_builds'] = {
                str(task['submit']): original['build_refs'][task['id']]
                for task in original['tasks']}
            plan.pop('build_refs', None)
        else:
            plan['execution']['phase'] = 'regr'
        for field in ('com_dir', 'sim_dir'):
            value = Path(plan['execution'].get(field, field))
            if value.is_absolute():
                workspace = workspace_directory(Path(original['policy']['output_roots'][0]),
                                                original, original['tasks'][0])
                plan['execution'][field] = os.path.relpath(value, workspace)
    if 'batch' in plan:
        plan['source_batch'] = plan.pop('batch')
    plan["source"] = {"journal": str(source), "id": original["id"], "mode": "recover" if recovery else "rerun"}
    plan["tasks"] = []
    destinations = {}
    for task in selected:
        copy_task = next(copy.deepcopy(t) for t in original["tasks"] if t["id"] == task["id"])
        copy_task["parent"] = {"run": original["id"], "task": task["id"], "attempt": task["attempts"][-1]["id"]}
        if seed is not None:
            integer(seed, "seed", 1, 2147483647)
            copy_task["seed"] = seed
        destination = (copy_task['submit'], copy_task['case'], copy_task['seed'])
        if destination in destinations:
            existing = destinations[destination]
            existing.setdefault('parents', [existing['parent']]).append(copy_task['parent'])
            continue
        destinations[destination] = copy_task
        plan["tasks"].append(copy_task)
    plan['seed_selection'] = {'source': 'replay_override' if seed is not None else 'replay', 'repeat': None,
                              'values': list(dict.fromkeys(task['seed'] for task in plan['tasks']))}
    policy = original["policy"]
    settings = {key: policy[key] for key in ("jobs", "retries", "retry_seconds", "min_free_bytes", "min_free_inodes")}
    settings.update({k: v for k, v in overrides.items() if v is not None})
    return run(plan, roots or policy["output_roots"], **settings)
