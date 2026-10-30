"""Single-controller rolling execution, durable journals and explicit replay."""

from concurrent.futures import ThreadPoolExecutor
import copy
import fcntl
import os
from pathlib import Path
import queue
import sys
import threading
import time
import uuid

from nsim.checks import ACCEPTED
from nsim.case_records import make_record
from nsim.config import ConfigError, integer, number
from nsim.identifiers import new_regression_id
from nsim.layout import (SimulationDirectories, is_regression, make_directory, build_directory,
                         record_directory, simulation_directory, validate_layout, isolated_regression,
                         workspace_directory, regression_directory)
from nsim.compilation import Builds
from nsim.dashboard import Dashboard
from nsim.planning import check_identity
from nsim.reports import assessed, html_report, load_final, markdown
from nsim.runner import execute
from nsim.storage import atomic_write, digest, error_record, preflight, write_json


class JournalWriteError(OSError):
    """The controller cannot durably account for the regression's progress."""


class ExecutionDirectoryExists(FileExistsError):
    """The selected new execution directory was claimed by another invocation."""


def validate_roots(plan, roots):
    roots = list(dict.fromkeys(Path(p).resolve() for p in roots))
    if not roots:
        raise ConfigError("at least one output root is required")
    if len(roots) != 1:
        raise ConfigError("select exactly one output root for this run; use -o to choose from project defaults")
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


def run(plan, roots, jobs=1, retries=0, retry_seconds=0,
        min_free_bytes=0, min_free_inodes=0, dry_run=False, on_progress=None):
    with SimulationDirectories(plan, min_free_bytes, min_free_inodes) as directories, Builds(plan) as builds:
        return _run(plan, roots, directories, builds, jobs, retries, retry_seconds,
                    min_free_bytes, min_free_inodes, dry_run, on_progress)


def run_singles(plan, roots, jobs=1, retries=0, retry_seconds=0,
                min_free_bytes=0, min_free_inodes=0, dry_run=False):
    """Run explicit case selections as independent case records, not a regression."""
    if plan['execution']['phase'] not in ('run', 'sim') or plan['caselists']:
        raise ConfigError('independent case selection requires run/sim --case')
    integer(jobs, 'jobs', 1)
    roots = validate_roots(plan, roots)
    validate_layout(plan, roots)

    def one(task):
        selected = copy.deepcopy(plan)
        selected['id'] = uuid.uuid4().hex
        selected['kind'] = 'case'
        selected['execution']['layout'] = 'single-v1'
        selected['tasks'] = [copy.deepcopy(task)]
        selected['seed_selection']['values'] = [task['seed']]
        selected['case_definitions'] = {task['case']: selected['case_definitions'][task['case']]}
        return selected

    def execute_submit(tasks):
        first = one(tasks[0])
        results = []
        with SimulationDirectories(first, min_free_bytes, min_free_inodes) as directories, Builds(first) as builds:
            for task in tasks:
                selected = one(task)
                record, state = _run(selected, roots, directories, builds, 1, retries, retry_seconds,
                                     min_free_bytes, min_free_inodes, dry_run)
                results.append((task['id'], record, state))
        return results

    groups = [[task for task in plan['tasks'] if task['submit'] == index]
              for index in dict.fromkeys(task['submit'] for task in plan['tasks'])]
    with ThreadPoolExecutor(max_workers=min(jobs, len(groups))) as executor:
        grouped = [executor.submit(execute_submit, tasks) for tasks in groups]
        completed = {task_id: (record, state) for future in grouped
                     for task_id, record, state in future.result()}
    ordered = [(task['id'], *completed[task['id']]) for task in plan['tasks']]
    return None, dict(phase=plan['execution']['phase'], dry_run=dry_run,
                      passed=all(state['passed'] for _, _, state in ordered),
                      task_count=len(ordered),
                      report_errors=[error for _, _, state in ordered for error in state.get('report_errors', [])],
                      scheduler_errors=[error for _, _, state in ordered for error in state.get('scheduler_errors', [])],
                      runs=[{'case': task['case'], 'seed': task['seed'],
                             'submit': plan['submits'][task['submit']]['name'],
                             'record_dir': str(completed[task['id']][0]),
                             'passed': completed[task['id']][1]['passed']}
                            for task in plan['tasks']],
                      tasks=[state['tasks'][0] for _, _, state in ordered])


def _run(plan, roots, directories, builds, jobs, retries, retry_seconds,
         min_free_bytes, min_free_inodes, dry_run, on_progress=None):
    integer(jobs, "jobs", 1)
    integer(retries, "retries")
    number(retry_seconds, "retry_seconds")
    if retries or retry_seconds:
        raise ConfigError("automatic infrastructure retries and output-root failover are disabled; use rerun/recover explicitly")
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
    record_root = roots[0]
    preflight(record_root, min_free_bytes, min_free_inodes)
    plan = copy.deepcopy(plan)
    journal = None
    if dry_run or is_regression(plan):
        execution_root = (record_root / 'preview_dir' / plan['id']
                          if dry_run and not is_regression(plan) else regression_directory(record_root, plan))
        # Claim the execution namespace before creating any compile/simulation
        # directories. Never silently adopt an old or another controller's run.
        make_directory(execution_root.parent, record_root)
        try:
            execution_root.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise ExecutionDirectoryExists(f'execution directory already exists: {execution_root}') from exc
        journal = execution_root / 'report'
        journal.mkdir()
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
    if not dry_run and phase != 'com' and not isolated_regression(plan):
        # Shared/historical layouts still reserve all destinations up front.
        # Isolated regressions prepare each case only inside its dispatch slot.
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
    if any(submit['scheduler']['kind'] == 'lsf' for submit in plan['submits']):
        from nsim.case_records import checksum
        builds.lsf_plan_sha256 = checksum(plan)
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
        write_json(journal / 'preflight.json', {'selected_root': str(record_root), 'failures': {}})
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
    else:
        # A case record may be unwritable while the regression journal remains
        # writable. Keep an independent, durable account of every case result.
        write_json(journal / 'state.json', assessed(state, verify=False))
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
        attempt = execute(plan, task, record_root, policy, cancel, directories, builds)
        attempt['retry_index'] = 0
        attempt['parent'] = task['parent']
        # A missing per-case record is this case's error. The dispatcher saves
        # the failure to the independent regression state before refilling.
        if not attempt.get('record_path') and not attempt.get('case_record_error'):
            target = None
            try:
                if phase == 'com':
                    target = journal / 'records' / (attempt['id'] + '.json')
                    write_json(target, attempt)
                else:
                    target = directories.prepare(record_root, task) / 'run.json'
                    value = make_record(plan, dict(task, status='completed', attempts=[attempt]),
                                        target, lifecycle='attempt_complete')
                    attempt['evidence'][str(target)] = write_json(target, value)
                    attempt['record_path'] = str(target)
            except (OSError, ValueError) as exc:
                attempt['case_record_error'] = str(exc)
                attempt['case_record_failure'] = {'errno': getattr(exc, 'errno', None),
                                                  'path': getattr(exc, 'filename', None) or (str(target) if target else None),
                                                  'message': str(exc)}
                attempt['record_path'] = None
                if attempt.get('terminal') is not False:
                    attempt['verdict'] = 'INFRA_ERROR'
                attempt['reason'] = (attempt.get('reason', '') + ' | case record write failed: ' + str(exc)).strip(' |')
        return [attempt]

    def checkpoint():
        if single:
            return
        try:
            write_json(journal / 'state.json', assessed(state, verify=False))
        except OSError as exc:
            raise JournalWriteError(exc.errno, f'regression state NOT persisted at {journal / "state.json"}: {exc}') from exc

    display_failed = False
    def progress(lifecycle='running', message=''):
        nonlocal display_failed
        if dashboard and not display_failed:
            try:
                published = dashboard.publish(state, lifecycle, message)
            except OSError as exc:
                # live.json and dashboard.html are derived display files. The
                # full state.json checkpoint, not the display, controls dispatch.
                state.setdefault('display_errors', []).append(str(exc))
                print(f'nsim: dashboard publication failed: {exc}', file=sys.stderr)
                display_failed = True
                published = False
            if published and on_progress:
                on_progress(journal, dashboard.snapshot)

    executor = ThreadPoolExecutor(max_workers=jobs)
    with (journal / "controller.lock").open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.write(str(os.getpid()))
        lock.flush()
        pending = iter(state["tasks"])
        active = {}
        retained = set()
        try:
            progress()
            while True:
                launched = False
                while len(active) + len(retained) < jobs and not cancel.is_set():
                    task = next(pending, None)
                    if task is None:
                        break
                    task["status"] = "running"
                    task["submitted_at"] = time.time()
                    try:
                        checkpoint()
                    except JournalWriteError:
                        task['status'] = 'planned'
                        task.pop('submitted_at', None)
                        raise
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
                    if any(attempt.get('terminal') is False for attempt in task['attempts']):
                        retained.add(task['id'])
                    if dashboard:
                        dashboard.update(state, tasks=[task])
                if done:
                    checkpoint()
                    progress()
            collect_events()
            unresolved = [attempt for task in state['tasks'] for attempt in task['attempts']
                          if attempt.get('terminal') is False]
            if unresolved:
                for task in state['tasks']:
                    if task['status'] == 'planned':
                        task['pending_reason'] = 'not executed: LSF capacity retained by unresolved job'
                state.update(message='unresolved LSF jobs; no terminal completion published',
                             ended_at=time.time(), scheduler_errors=[a['reason'] for a in unresolved])
                state = assessed(state)
                if single:
                    save_single('unresolved')
                else:
                    checkpoint()
                progress('unresolved', state['message'])
                return journal, state
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
            state = assessed(state, verify=False)
            if single:
                save_single('completed')
                return journal, state
            checkpoint()
            progress('finalizing')
            write_json(journal / "final.json", {"schema_version": 2, "terminal": True,
                                                "files": {n: digest(journal / n) for n in files}})
            progress('complete', '最终报告发布失败，请检查报告错误。' if state['report_errors'] else '')
            return journal, state
        except BaseException as exc:
            # A failed central checkpoint stops new dispatch, but already
            # submitted jobs must not be cancelled as a side effect.
            if not isinstance(exc, JournalWriteError):
                cancel.set()
            executor.shutdown(wait=True, cancel_futures=not isinstance(exc, JournalWriteError))
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


def replay(source, roots=None, recovery=False, seed=None, output_dir=None, rebuild=False,
           execution_options=None, plusargs=(), **overrides):
    source = Path(source).resolve()
    from nsim.compile_replay import load_compile_source
    compile_source = (source / 'build.json').is_file()
    original, state = load_compile_source(source) if compile_source else load_final(source)
    if original.get('kind') == 'batch':
        from nsim.batch_replay import replay_batch
        return replay_batch(source, original, state, roots=roots, recovery=recovery, seed=seed,
                            output_dir=output_dir, rebuild=rebuild, execution_options=execution_options,
                            plusargs=plusargs, **overrides)
    prepared = prepare_replay(source, original, state, roots, recovery, seed, output_dir, rebuild,
                              execution_options=execution_options, plusargs=plusargs, **overrides)
    if prepared is None:
        return None, {'passed': state['passed'], 'message': 'no matching failed tasks', 'tasks': []}
    plan, roots, settings = prepared
    return _run_replay(plan, roots, settings, output_dir)


def prepare_replay(source, original, state, roots=None, recovery=False, seed=None,
                   output_dir=None, rebuild=False, execution_options=None, plusargs=(), **overrides):
    """Prepare without executing so a batch can validate every child first."""
    from nsim.compile_replay import prepare_compile_rerun
    from nsim.single_replay import case_source
    compile_source = (Path(source) / 'build.json').is_file()
    from nsim.profiles import changed
    if recovery and not compile_source and not case_source(original) and changed(execution_options, plusargs):
        raise ConfigError('whole-regression recover preserves historical execution options')
    if (compile_source or original['execution']['phase'] == 'com') and plusargs:
        raise ConfigError('compile directory replay does not accept simulation plusargs')
    if not recovery or compile_source or case_source(original) or original['execution']['phase'] == 'com':
        from nsim.rerun import prepare_rerun
        if seed is not None or roots is not None:
            raise ConfigError('rerun preserves case/seed and the original output root; overrides are not supported')
        prepared = (prepare_compile_rerun(source, original, state, output_dir, execution_options)
                    if compile_source or original['execution']['phase'] == 'com' else
                    prepare_rerun(source, original, state, output_dir=output_dir, rebuild=rebuild,
                                  execution_options=execution_options, extra_plusargs=plusargs))
        if prepared is None:
            return None
        plan, roots = prepared
        policy = original['policy']
        settings = {key: policy[key] for key in ('jobs', 'min_free_bytes', 'min_free_inodes')}
        settings.update(retries=0, retry_seconds=0)
        settings.update({key: value for key, value in overrides.items() if value is not None})
        return plan, roots, settings
    check_identity(original)
    selected = [task for task in state['tasks'] if task['effective_verdict'] not in
                ACCEPTED | {'FAIL', 'NO_COMPARE', 'INPUT_CHANGED'}]
    if not selected:
        return None
    for index in {task['submit'] for task in selected}:
        submit = original['submits'][index]
        if submit['adapter'] == 'command' and not submit.get('compile'):
            raise ConfigError(f'recover requires a compile step for submit {submit["name"]}; '
                              'prebuilt-only inputs cannot be rebuilt')
    from nsim.replay_paths import anchor, directory_name
    _, anchor_path = anchor(original)
    name = directory_name(output_dir, 'recover')
    plan = copy.deepcopy(original)
    plan['execution'].pop('rerun_parent', None)
    plan['execution'].pop('reuse_builds', None)
    plan.pop('build_refs', None)
    plan['id'] = name if output_dir is None else new_regression_id(plan.get('caselists', []))
    plan['kind'] = 'regression'
    plan['execution'].update(phase='regr', layout='isolated-regression-v1',
                             replay_parent=anchor_path, replay_dir=name,
                             com_dir='com_dir', sim_dir='sim_dir')
    if 'batch' in plan:
        plan['source_batch'] = plan.pop('batch')
    plan['source'] = {'journal': str(source), 'id': original['id'], 'mode': 'recover',
                      'root_id': (original.get('source') or {}).get('root_id', original['id'])}
    plan["tasks"] = []
    destinations = {}
    for task in selected:
        copy_task = next(copy.deepcopy(t) for t in original["tasks"] if t["id"] == task["id"])
        copy_task['parent'] = {'run': original['id'], 'task': task['id'],
                               'attempt': task['attempts'][-1]['id'] if task['attempts'] else None}
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
    settings = {key: policy[key] for key in ("jobs", "min_free_bytes", "min_free_inodes")}
    # Historical policies may describe automatic failover. Replaying one is an
    # explicit new run and must use the current no-failover contract.
    settings.update(retries=0, retry_seconds=0)
    settings.update({k: v for k, v in overrides.items() if v is not None})
    return plan, roots or policy['output_roots'], settings


def _run_replay(plan, roots, settings, output_dir):
    """Retry only an automatically chosen name that collided at atomic mkdir."""
    from nsim.identifiers import new_replay_name
    from nsim.layout import regression_directory
    for _ in range(10):
        try:
            return run(plan, roots, **settings)
        except ExecutionDirectoryExists:
            if output_dir is not None or not regression_directory(Path(roots[0]).resolve(), plan).exists():
                raise
            mode = plan['source']['mode']
            name = new_replay_name(mode)
            plan['id'] = name
            plan['execution']['replay_dir'] = name
    raise ConfigError('cannot allocate a unique replay directory')
