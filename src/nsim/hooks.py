"""Ordered stage hooks and in-job log analysis; never submit another LSF job."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from nsim.config import ConfigError, fields, integer, name, number, read_json, strings
from nsim.layout import check_environment_link
from nsim.log_analysis import brief, empty_report, scan, snapshot, validate_custom, wait_stable
from nsim.readonly import ReadOnlyEnvironment
from nsim.storage import digest, write_json

STAGES = ('pre_compile', 'post_compile', 'pre_sim', 'post_sim')


def config(value):
    fields(value, STAGES, set(), 'hooks')
    result = {}
    for stage in STAGES:
        items = value.get(stage, [])
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise ConfigError(f'hooks.{stage} must be a hook object or an ordered array')
        result[stage] = []
        names = set()
        for index, item in enumerate(items):
            fields(item, {'name', 'argv', 'timeout_seconds'}, {'name', 'argv'}, 'hook')
            ident = name(item['name'], 'hook name')
            if ident in names:
                raise ConfigError(f'duplicate hook name in {stage}: {ident}')
            names.add(ident)
            if ident == 'log_analysis' and (stage != 'post_sim' or index != len(items) - 1):
                raise ConfigError('custom log_analysis must be the last post_sim hook')
            result[stage].append(dict(name=ident, argv=strings(item['argv'], 'hook.argv', True),
                                      timeout_seconds=number(item.get('timeout_seconds', 60), 'hook timeout', .01)))
    return result


def kill_group(process):
    if getattr(process, '_nsim_group_closed', False):
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    process._nsim_group_closed = True


def spec(plan, task, phase, build, work, argv, stdout, stderr, timeout):
    submit = plan['submits'][task['submit']]
    return dict(phase=phase, task={'case': task['case'], 'seed': task['seed']}, cwd=str(work), argv=argv,
                stdout=str(stdout), stderr=str(stderr), timeout_seconds=timeout,
                environment=dict(plan['environment'], **submit['env']), hooks=submit['hooks'],
                read_only_roots=list(dict.fromkeys([plan['project']['root'],
                    *[source['root'] for source in plan['project'].get('sources', {}).values()]])),
                log_analysis=submit['log_analysis'], preparation=submit['adapter'], build_id=task['build_id'],
                values=dict(project=plan['project']['root'], build_dir=str(build),
                            case=task['case'], seed=task['seed'], result=str(Path(work) / 'result.json'),
                            wave_dir=str(Path(work) / 'waves'), log_dir=str(work),
                            analysis=str(Path(work) / ('log_analysis.json' if phase == 'compile'
                                                       else '.nsim-log-analysis.json'))))


def process(argv, work, env, log, timeout, cancel, pass_fds, protection):
    record = dict(argv=argv, started_at=time.time(), exit_code=None, timed_out=False, cancelled=False,
                  read_only_environment=protection.describe())
    child = None
    try:
        with log.open('ab') as out:
            record['log_start_byte'] = out.tell()
            if cancel.is_set():
                record['cancelled'] = True
                record['log_end_byte'] = record['log_start_byte']
                return record
            child = subprocess.Popen(protection.command(argv, pass_fds), cwd=work, env=env, stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=subprocess.STDOUT, start_new_session=True, pass_fds=pass_fds)
            record['pid'] = child.pid
            deadline = time.monotonic() + timeout
            while child.poll() is None:
                if cancel.is_set() or time.monotonic() >= deadline:
                    record['cancelled'] = cancel.is_set()
                    record['timed_out'] = not cancel.is_set()
                    kill_group(child)
                    break
                cancel.wait(.02)
            record['exit_code'] = child.returncode
            kill_group(child)
            if record['timed_out'] or record['cancelled']:
                out.write(b'\n[nsim] command timed out\n' if record['timed_out'] else b'\n[nsim] command cancelled\n')
            out.flush()
            os.fsync(out.fileno())
            record['log_end_byte'] = out.tell()
    except OSError as exc:
        record['error'] = str(exc)
        record['error_errno'] = exc.errno
        try:
            with log.open('ab') as out:
                out.write(('\n[nsim] command failed: ' + str(exc) + '\n').encode())
                out.flush()
                os.fsync(out.fileno())
                record['log_end_byte'] = out.tell()
        except OSError:
            pass  # The structured error remains authoritative if the log is unwritable.
    finally:
        if child is not None:
            kill_group(child)
        record['ended_at'] = time.time()
    return record


def succeeded(record):
    return record['exit_code'] == 0 and not record['timed_out'] and not record['cancelled'] and not record.get('error')


def execute_stage(request, cancel, pass_fds=()):
    work = Path(request['cwd'])
    check_environment_link(work, request['values']['project'], required=True)
    protection = ReadOnlyEnvironment(request['read_only_roots'], work, request['environment'])
    protection.verify(request['environment'])
    phase = request['phase']
    pre, post = ('pre_compile', 'post_compile') if phase == 'compile' else ('pre_sim', 'post_sim')
    stage_log = Path(request['stdout'])
    if stage_log.is_symlink():
        raise ConfigError('stage log must not be a symlink')
    if request.get('append_log'):
        stage_log.touch(exist_ok=True)
    else:
        stage_log.write_bytes(b'')
    settings = request['log_analysis']
    # Old submit files may explicitly scan the former stderr sidecar. Keep an
    # empty compatibility file, never a duplicate of the merged output.
    legacy = 'compile.err' if phase == 'compile' else 'launch.log'
    if legacy in settings['compile_files' if phase == 'compile' else 'files']:
        legacy_log = work / legacy
        if legacy_log.is_symlink():
            raise ConfigError('legacy log must not be a symlink')
        legacy_log.write_bytes(b'')
    analyzer_log = work / '.nsim-analysis-output.log'
    analyzer_item = None
    hooks = []
    evidence = {}
    outcome = dict(exit_code=None, timed_out=False, cancelled=False, started_at=time.time(),
                   read_only_environment=protection.describe(),
                   hook_failed=False, hook_reason=None, main_started=False)
    hook_record = work / 'hooks.json'

    def invoke(stage, index, hook):
        nonlocal analyzer_item
        # An analyzer must not change its own input just by printing. Defer its
        # console output until after input stability has been checked.
        log = analyzer_log if hook['name'] == 'log_analysis' else stage_log
        if log == analyzer_log:
            if log.is_symlink():
                raise ConfigError('analyzer output must not be a symlink')
            log.write_bytes(b'')
        values = dict(request['values'], exit_code='' if outcome['exit_code'] is None else str(outcome['exit_code']),
                      timed_out='1' if outcome['timed_out'] else '0')
        argv = [arg.format(**values) for arg in hook['argv']]
        item = process(argv, work, request['environment'], log, hook['timeout_seconds'], cancel, pass_fds, protection)
        item.update(stage=stage, name=hook['name'], stdout=str(stage_log), stderr=str(stage_log), kind='command')
        if log == analyzer_log:
            analyzer_item = item
        hooks.append(item)
        if not succeeded(item):
            outcome['hook_failed'] = True
            outcome['hook_reason'] = outcome['hook_reason'] or f'{stage} hook {hook["name"]} failed (exit={item["exit_code"]}, timeout={item["timed_out"]})'
        write_json(hook_record, {'schema_version': 1, 'phase': phase, 'hooks': hooks})
        return item

    try:
        for index, hook in enumerate(request['hooks'][pre]):
            if not succeeded(invoke(pre, index, hook)):
                break
        if not outcome['hook_failed'] and not cancel.is_set():
            if request['argv']:
                outcome.update(process(request['argv'], work, request['environment'], stage_log,
                                       request['timeout_seconds'], cancel, pass_fds, protection), main_started=True)
            else:
                if request['preparation'] == 'fake':
                    write_json(work / 'fake-model.json', {'build_id': request['build_id'], 'fixture': True})
                with stage_log.open('ab') as output:
                    output.write(b'Prepared inputs; no EDA compiler executed.\n')
                outcome['exit_code'] = 0
        # Failed or timed-out tools still run post hooks. Explicit cancellation skips commands.
        custom = None
        for index, hook in enumerate(request['hooks'][post]):
            if hook['name'] == 'log_analysis':
                custom = (index, hook)
                continue
            invoke(post, index, hook)
        # A simulation analyzer writes only its temporary protocol. The controller
        # publishes only the summary together with the final case verdict.
        check_environment_link(work, request['values']['project'], required=True)
        analysis_path = Path(request['values']['analysis'])
        analysis_path.unlink(missing_ok=True)
        settle_deadline = time.monotonic() + settings['settle_seconds']
        stable, stable_error = wait_stable(work, settings, phase, cancel)
        custom_value = None
        if custom and not stable_error:
            item = invoke(post, *custom)
            try:
                if not succeeded(item):
                    raise ConfigError('custom log_analysis hook did not succeed')
                if analysis_path.is_symlink() or not analysis_path.is_file() or analysis_path.stat().st_size > 16 * 1024 ** 2:
                    raise ConfigError('custom analysis output missing, unsafe or larger than 16 MiB')
                custom_value = validate_custom(read_json(analysis_path), settings['max_diagnostics'])
            except (OSError, ValueError) as exc:
                outcome.update(hook_failed=True, hook_reason=outcome['hook_reason'] or str(exc))
                custom_value = {'complete': False, 'counts': dict(warning=0, error=0, fatal=0), 'diagnostics': []}
        elif custom:
            hooks.append(dict(stage=post, name='log_analysis', kind='command', skipped=stable_error))
        else:
            hooks.append(dict(stage=post, name='log_analysis', kind='builtin', started_at=time.time()))
        budget = settings['max_scan_bytes']
        scans = 0
        while True:
            if stable_error:
                analysis, logs = empty_report(phase, request['task'], bool(custom)), {}
                analysis.update(complete=False, issues=[stable_error], failure_reason=stable_error)
                break
            # Simulation needs counts and the first failure, not a diagnostic
            # array. Keep the shared compilation report's existing contract.
            scan_settings = dict(settings, max_scan_bytes=budget,
                                 max_diagnostics=settings['max_diagnostics'] if phase == 'compile' else 0)
            analysis, logs = scan(work, scan_settings, phase, request['task'], cancel, custom_value)
            scans += 1
            budget -= sum(item['bytes'] for item in analysis['files'])
            try:
                changed = stable != snapshot(work, settings, phase)
            except (OSError, ValueError):
                changed = True
            if not changed:
                break
            analysis.update(complete=False, failure_reason='logs changed during log_analysis hook')
            analysis['issues'].append(analysis['failure_reason'])
            # Retry only the built-in read-only scanner, never arbitrary user hooks.
            remaining = settle_deadline - time.monotonic()
            if custom or remaining <= 0 or budget <= 0 or cancel.is_set():
                break
            stable, stable_error = wait_stable(work, dict(settings, settle_seconds=remaining), phase, cancel)
        analysis['scan_attempts'] = scans
        published_path = analysis_path if phase == 'compile' else work / 'run.json'
        if phase == 'compile':
            write_json(published_path, analysis)
            evidence[str(published_path)] = digest(published_path)
        if not custom:
            hooks[-1].update(ended_at=time.time(), complete=analysis['complete'], result=str(published_path))
        outcome['log_analysis'] = brief(analysis, published_path)
        evidence.update(logs)
    finally:
        if analyzer_item is not None:
            with stage_log.open('ab') as output, analyzer_log.open('rb') as source:
                analyzer_item['log_start_byte'] = output.tell()
                shutil.copyfileobj(source, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
                analyzer_item['log_end_byte'] = output.tell()
            analyzer_log.unlink()
        if phase == 'simulate':
            (work / '.nsim-log-analysis.json').unlink(missing_ok=True)
        outcome['cancelled'] = cancel.is_set()
        outcome['ended_at'] = time.time()
        write_json(hook_record, {'schema_version': 1, 'phase': phase, 'hooks': hooks})
    evidence[str(hook_record)] = digest(hook_record)
    # Refresh after the analyzer's deferred output; native tool logs are
    # evidence, but are not scanned again by default.
    for log in (stage_log, work / ('vcs.log' if phase == 'compile' else 'sim.log')):
        if log.is_symlink():
            raise ConfigError('tool log must not be a symlink')
        if log.is_file() and (str(log) not in evidence or (log == stage_log and analyzer_item is not None)):
            evidence[str(log)] = digest(log)
    outcome.update(hooks=hooks, evidence=evidence)
    return outcome
