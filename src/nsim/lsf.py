"""LSF process execution with durable reservations and confirmed terminal states."""

import json
import os
import pwd
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

from nsim.config import ConfigError, read_json
from nsim.lsf_logs import Transcript
from nsim.storage import digest, sync_directory, write_json
from nsim.case_records import checksum
from nsim.lsf_records import check_case_record, durable_directories, load_job, released, reservation_records

TERMINAL = {'DONE', 'EXIT', 'PDONE', 'PERR', 'POST_DONE', 'POST_ERR'}
SUCCESS = {'DONE', 'PDONE', 'POST_DONE'}
ACTIVE = {'PEND', 'RUN', 'PSUSP', 'USUSP', 'SSUSP', 'WAIT', 'PROV'}
QUERY_FIELDS = 'jobid stat exit_code exec_host job_name user output_file error_file submit_time'
REJECTED_SUBMISSION = re.compile(r'(?im)^\s*(?:submission refused|job not submitted)\b')


class LsfError(RuntimeError):
    def __init__(self, message, terminal=True, record=None, cancelled=False, evidence=None, job=None):
        super().__init__(message)
        self.terminal = terminal
        self.record = str(record) if record else None
        self.cancelled = cancelled
        self.evidence = evidence or {}
        self.job = job


def check_reservations(directory, writing=False, require_case_record=False):
    directory = Path(directory)
    if (directory / '.lsf-active.json').exists() or (directory / '.lsf-active.json').is_symlink():
        raise ConfigError(f'LSF job reservation still active or unresolved: {directory}/.lsf-active.json')
    check_case_record(directory, required=require_case_record)
    for path in reservation_records(directory):
        job, saved = load_job(path)
        if not job['terminal'] and not released(path, job, saved):
            raise ConfigError(f'LSF job reservation still active or unresolved: {path}')
    readers = directory / '.lsf-readers'
    if writing and (readers.is_symlink() or readers.exists() and any(readers.iterdir())):
        raise ConfigError(f'LSF job reservations still consume compile products: {readers}')


def control(argv, timeout, script=None, transcript=None, env=None):
    if transcript:
        transcript.event('command', argv)
    try:
        result = subprocess.run(argv, input=script, capture_output=True, text=True, timeout=timeout,
                                env=env if env is not None else dict(os.environ, LC_ALL='C', LANG='C'))
    except (OSError, subprocess.TimeoutExpired) as exc:
        if transcript:
            transcript.exception(exc)
        raise
    if transcript:
        transcript.output(result)
    return result


def query(config, job_id, timeout=None, transcript=None, identity=None):
    fields = 'jobid stat exit_code exec_host'
    if identity:
        fields = QUERY_FIELDS
    argv = [config['bjobs'], '-a', '-json', '-o', fields]
    if job_id:
        argv.append(str(job_id))
    elif identity:
        argv += ['-u', identity['owner'], '-J', identity['name']]
    else:
        raise ValueError('query needs a job ID or recorded submission identity')
    result = control(argv, timeout or config['command_timeout_seconds'], transcript=transcript)
    try:
        if result.returncode != 0:
            raise ValueError(f'bjobs exited {result.returncode}')
        records = read_json(Path(config['bjobs']), content=result.stdout)['RECORDS']
        if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
            raise ValueError('RECORDS is not an array of objects')
        rows = [{str(k).upper(): v for k, v in row.items()} for row in records]
        if job_id:
            rows = [row for row in rows if str(row.get('JOBID', row.get('JOB_ID'))) == str(job_id)]
        else:
            rows = [row for row in rows if row.get('JOB_NAME') == identity['name']]
        if len(rows) != 1 or not isinstance(rows[0].get('STAT'), str):
            raise ValueError('expected exactly one matching job record')
        row = rows[0]
        actual_id = str(row.get('JOBID', row.get('JOB_ID')))
        if not actual_id.isdigit() or int(actual_id) <= 0:
            raise ValueError('invalid job ID')
        observed = {}
        if identity:
            for key, field in (('name', 'JOB_NAME'), ('owner', 'USER'),
                               ('output_file', 'OUTPUT_FILE'), ('error_file', 'ERROR_FILE')):
                if not identity.get(key) or row.get(field) != identity[key]:
                    raise ValueError(f'job identity mismatch or missing: {field}')
                observed[key] = row[field]
            if not row.get('SUBMIT_TIME'):
                raise ValueError('job submission time is missing')
            observed['submit_time'] = row['SUBMIT_TIME']
            previous = identity.get('scheduler_identity', {}).get('submit_time')
            if previous is not None and previous != row['SUBMIT_TIME']:
                raise ValueError('job submission time changed')
        return {'job_id': actual_id, 'status': row['STAT'].upper(), 'exit_code': row.get('EXIT_CODE'),
                'host': row.get('EXEC_HOST'), 'scheduler_identity': observed}
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f'bjobs did not establish identity/status for job {job_id}: {exc}; {result.stderr.strip()}') from exc


def cancel_and_confirm(config, job, save, transcript=None):
    """Never use bkill -r: its success does not prove the process has exited."""
    deadline = time.monotonic() + config['cancel_seconds']
    job['cancel_attempted'] = True
    try:
        state = query(config, job['job_id'], transcript=transcript, identity=job)
        job.update(state)
        if state['status'] in TERMINAL:
            job['terminal'] = True
            save()
            return True
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        job['query_error'] = str(exc)
        return False
    try:
        response = control([config['bkill'], str(job['job_id'])],
                           min(config['command_timeout_seconds'], config['cancel_seconds']), transcript=transcript)
        job['cancel_response'] = {'returncode': response.returncode, 'stdout': response.stdout, 'stderr': response.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        job['cancel_error'] = str(exc)
    while time.monotonic() < deadline:
        try:
            state = query(config, job['job_id'], min(config['command_timeout_seconds'], max(.01, deadline - time.monotonic())),
                          transcript=transcript, identity=job)
            job.update(state)
            if state['status'] in TERMINAL:
                job['terminal'] = True
                save()
                return True
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            job['query_error'] = str(exc)
        time.sleep(min(config['poll_seconds'], max(0, deadline - time.monotonic())))
    return False


def run_process(plan, task, root, policy, argv, cwd, stdout, stderr, timeout, cancel,
                phase, build=None, on_event=None, on_record=None, plan_sha256=None):
    """One synchronous job lifetime; the controller's task slots bound all submissions."""
    if cancel.is_set():
        raise LsfError('controller cancelled before submission', cancelled=True)
    submit = plan['submits'][task['submit']]
    config = submit['scheduler']
    cwd = Path(cwd)
    check_reservations(cwd)
    token = uuid.uuid4().hex
    compact = phase == 'simulate' and on_record is not None
    if compact:
        from nsim.layout import is_regression, make_directory
        journal = Path(policy['record_dir'])
        records = journal / '.lsf' if is_regression(plan) else cwd.parent / '.lsf' / plan['id']
        make_directory(records, journal if is_regression(plan) else cwd.parent)
        record_path = cwd / 'run.json'
        guards = []
        shared_control = records.parent / 'lsf.log' if is_regression(plan) else records / 'lsf.log'
    else:
        records = cwd / '.lsf-jobs'
        if records.is_symlink():
            raise ConfigError(f'refusing symlink in LSF records: {records}')
        records.mkdir(exist_ok=True)
        shared_control = None
        guards = [cwd / '.lsf-active.json']
    directory = records / token
    directory.mkdir()
    if not compact:
        record_path = directory / 'job.json'
    if build is not None and Path(build) != cwd:
        readers = Path(build) / '.lsf-readers'
        if readers.is_symlink():
            raise ConfigError(f'refusing symlink in compile readers: {readers}')
        readers.mkdir(exist_ok=True)
        guards.append(readers / (token + '.json'))
    worker_python = config.get('worker_python', plan['runtime']['python'])
    # A worker needs identity and its own layout, not every task and source snapshot.
    worker_plan = {key: plan[key] for key in ('id', 'kind', 'project', 'runtime', 'input_hashes', 'submits', 'execution')}
    worker_plan['tasks'] = [task]
    build_evidence = {}
    if build is not None:
        build = Path(build)
        manifest = read_json(build / 'build.json')
        build_evidence = {str(build / 'build.json'): digest(build / 'build.json'),
                          **{str(build / name): value for name, value in manifest['artifacts'].items()}}
    request = {'schema_version': 1, 'token': token, 'plan': worker_plan, 'root': str(root), 'cwd': str(cwd),
               'argv': argv, 'stdout': str(stdout), 'stderr': str(stderr), 'timeout_seconds': timeout,
               'environment': dict(plan['environment'], **submit['env']), 'guards': list(map(str, guards)),
               'min_free_bytes': policy['min_free_bytes'], 'min_free_inodes': policy['min_free_inodes'],
               'worker_python': worker_python, 'worker_python_sha256': digest(Path(worker_python)),
               'build_evidence': build_evidence}
    from nsim.hooks import spec
    request['stage'] = spec(plan, task, phase, build or cwd, cwd, argv, stdout, stderr, timeout)
    request['stage']['append_log'] = True
    if compact:
        for key in ('plan', 'argv', 'stdout', 'stderr', 'timeout_seconds', 'environment'):
            request.pop(key)
        request.update(schema_version=2, task_id=task['id'], build_dir=str(build) if build else None,
                       result_path=str(directory / 'worker.json'), occupancy=str(record_path),
                       plan_ref={'path': str(journal / 'plan.json' if is_regression(plan) else record_path),
                                 'sha256': plan_sha256 or checksum(plan)})
        request_path = record_path
        request_hash = checksum(request)
    else:
        request_path = directory / 'request.json'
        write_json(request_path, request)
        request_hash = digest(request_path)
    worker_env = dict(plan['environment'])
    if plan['runtime'].get('wheel_sha256') is not None:
        worker_env['NSIM_WHEEL_SHA256'] = plan['runtime']['wheel_sha256']
    from nsim.runtime import module_command
    worker_argv = ['/usr/bin/env', '-i', *[f'{k}={v}' for k, v in worker_env.items()],
                   *module_command(worker_python, 'nsim.lsf_worker', str(request_path), request_hash)]
    script = '#!/bin/sh\nexec ' + shlex.join(worker_argv) + '\n'
    job_name = 'nsim_' + token
    submit_argv = [config['bsub'], '-J', job_name, '-cwd', str(cwd),
                   '-o', str(directory / 'farm.out'), '-e', str(directory / 'farm.err')]
    if config.get('queue'):
        submit_argv += ['-q', config['queue']]
    for value in config['resources']:
        submit_argv += ['-R', value]
    if config['licenses']:
        submit_argv += ['-R', 'rusage[' + ':'.join(f'{k}={v}' for k, v in sorted(config['licenses'].items())) + ']']
    job = {'schema_version': 1, 'token': token, 'task_id': task['id'], 'phase': phase,
           'job_id': None, 'name': job_name, 'status': 'SUBMITTING', 'terminal': False,
           'record': str(record_path), 'request_sha256': request_hash, 'submit_argv': submit_argv,
           'created_at': time.time(), 'history': [], 'owner': pwd.getpwuid(os.getuid()).pw_name,
           'uid': os.getuid(), 'cwd': str(cwd), 'record_dir': str(policy.get('record_dir', cwd)),
           'output_file': str(directory / 'farm.out'), 'error_file': str(directory / 'farm.err'),
           'include_postproc': True}
    if compact:
        job.update(request=request, config=config, spool=str(directory))
    transcript = Transcript(stdout, directory, phase, argv, token=token, shared_control=shared_control)
    def publish():
        if on_event:
            on_event({k: job[k] for k in ('token', 'task_id', 'phase', 'job_id', 'status', 'terminal', 'record')})

    def changed():
        if not job['history'] or job['history'][-1]['status'] != job['status']:
            job['history'].append({'status': job['status'], 'time': time.time()})
            transcript.event('state', {key: job.get(key) for key in ('job_id', 'status', 'host', 'exit_code')})
            publish()

    def save():
        changed()
        transcript.checkpoint()
        if compact:
            on_record(job)
        else:
            write_json(record_path, job)
        sync_directory(record_path.parent)
        publish()
    attempted = False
    worker = None
    result = None
    failure = None
    sealed = False
    try:
        save()
        for guard in guards:
            write_json(guard, {'token': token, 'record': str(record_path), 'job_name': job_name})
        durable_directories(cwd, root if Path(root) in cwd.parents else cwd.parent)
        durable_directories(directory, root if Path(root) in directory.parents else records.parent)
        for guard in guards:
            durable_directories(guard.parent, root if Path(root) in guard.parents else guard.parent.parent)
        if cancel.is_set():
            raise LsfError('controller cancelled before submission', cancelled=True)
        attempted = True
        # LSF client configuration is needed on the submission host only.
        response = control(submit_argv, config['command_timeout_seconds'], script=script, transcript=transcript,
                           env=dict(os.environ, LC_ALL='C', LANG='C', LSB_EXIT_IF_CWD_NOTEXIST='Y', LSB_JOB_INCLUDE_POSTPROC='Y'))
        job['submission'] = {'returncode': response.returncode, 'stdout': response.stdout, 'stderr': response.stderr}
        ids = set(re.findall(r'Job\s*<(\d+)>', response.stdout + '\n' + response.stderr))
        if len(ids) != 1 or int(next(iter(ids))) <= 0:
            # A missing ID alone does not prove rejection: bsub may have
            # accepted a job before its reply was lost. Only an explicit
            # negative response with a failed command is terminal here.
            if response.returncode != 0 and REJECTED_SUBMISSION.search(response.stderr):
                job['terminal'] = True
                job['status'] = 'REJECTED'
                raise LsfError('submission rejected: ' + response.stderr.strip(), record=record_path)
            raise LsfError('submission did not return one job ID; acceptance is unknown', terminal=False, record=record_path)
        job['job_id'] = ids.pop()
        job['status'] = 'SUBMITTED'
        save()
        deadline = time.monotonic() + config['deadline_seconds']
        unknown_since = None
        timed_out = False
        while True:
            if cancel.is_set() or time.monotonic() >= deadline:
                timed_out = not cancel.is_set()
                job['cancel_reason'] = 'scheduler deadline' if timed_out else 'controller cancelled'
                confirmed = cancel_and_confirm(config, job, save, transcript)
                if not confirmed:
                    raise LsfError('LSF cancellation has no confirmed terminal state', terminal=False, record=record_path)
                break
            try:
                state = query(config, job['job_id'], transcript=transcript, identity=job)
                previous = job['status']
                job.update(state)
                job['terminal'] = state['status'] in TERMINAL
                if job['status'] != previous:
                    changed()
                if job['terminal']:
                    break
                if state['status'] in ACTIVE:
                    unknown_since = None
                else:
                    unknown_since = unknown_since or time.monotonic()
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                job['query_error'] = str(exc)
                unknown_since = unknown_since or time.monotonic()
                if job['status'] != 'UNKNOWN':
                    job['status'] = 'UNKNOWN'
                    save()
            if unknown_since and time.monotonic() - unknown_since >= config['unknown_seconds']:
                raise LsfError('LSF job status remains unknown; reservation retained',
                               terminal=False, record=record_path)
            cancel.wait(config['poll_seconds'])
        job['ended_at'] = time.time()
        save()
        worker_path = directory / 'worker.json'
        settle = time.monotonic() + config['result_wait_seconds']
        worker = None
        while time.monotonic() < settle:
            try:
                worker = read_json(worker_path)
                break
            except (OSError, ValueError):
                if cancel.is_set() or timed_out:
                    break
                time.sleep(min(.05, config['result_wait_seconds']))
        if cancel.is_set() or timed_out:
            result = {'exit_code': None, 'timed_out': timed_out, 'cancelled': cancel.is_set(),
                      'job': job, 'worker': worker, 'evidence': {}}
            return result
        if (not isinstance(worker, dict) or worker.get('schema_version') != 1
                or worker.get('token') != token or worker.get('request_sha256') != request_hash
                or type(worker.get('timed_out')) is not bool or type(worker.get('cancelled')) is not bool
                or not isinstance(worker.get('host'), str)
                or worker.get('error') is not None and not isinstance(worker['error'], str)
                or type(worker.get('exit_code')) is not int and not worker.get('error') and not worker.get('hook_failed') and not worker.get('cancelled')):
            raise LsfError('terminal LSF job has no valid worker result', record=record_path)
        if worker.get('error'):
            raise LsfError('execution-node validation failed: ' + str(worker['error']), record=record_path)
        if job['status'] not in SUCCESS and worker.get('exit_code') == 0:
            raise LsfError('LSF reports failure despite worker success', record=record_path)
        result = {**worker, 'job': job, 'worker': worker, 'evidence': dict(worker.get('evidence', {}))}
        if not compact:
            result['evidence'].update({str(record_path): digest(record_path), str(worker_path): digest(worker_path),
                                      str(request_path): request_hash})
        return result
    except BaseException as exc:
        if cancel.is_set() and not job['terminal'] and job['job_id'] and not job.get('cancel_attempted'):
            try:
                cancel_and_confirm(config, job, save, transcript)
            except (OSError, ValueError):
                pass
        if not attempted:
            job['terminal'] = True
        job['status'] = job['status'] if job['terminal'] else 'UNKNOWN'
        job['error'] = str(exc)
        try:
            save()
        except OSError:
            pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        failure = LsfError(f"{exc}; job={job['job_id']} record={record_path}",
                           terminal=job['terminal'], record=record_path,
                           cancelled=isinstance(exc, LsfError) and exc.cancelled, job=job)
        raise failure from exc
    finally:
        try:
            # Worker evidence describes the analyzed prefix. Only the controller
            # appends scheduler output, then seals the complete transcript.
            worker_record = worker if isinstance(worker, dict) else None
            try:
                evidence, issue = transcript.finish(job, worker_record, config['result_wait_seconds'])
            except (OSError, ValueError) as log_error:
                issue = 'LSF log collection failed: ' + str(log_error)
                try:
                    with Path(stdout).open('ab') as stream:
                        stream.write(('\n[nsim LSF] transcript incomplete: ' + issue + '\n').encode())
                        stream.flush()
                        os.fsync(stream.fileno())
                    evidence = {str(stdout): digest(Path(stdout))} if job['terminal'] else {}
                except OSError as fallback_error:
                    issue += '; fallback log failed: ' + str(fallback_error)
                    evidence = {}
            job['log_collection'] = {'path': str(stdout), 'complete': issue is None, 'issue': issue,
                                     'sha256': evidence.get(str(stdout))}
            if compact:
                job['worker'] = worker_record
                job['log_collection']['sources'] = transcript.sources
            try:
                save()
                sealed = True
                if not compact:
                    evidence[str(record_path)] = digest(record_path)
            except OSError as record_error:
                if failure is None:
                    raise LsfError('LSF job record write failed: ' + str(record_error),
                                   terminal=job['terminal'], record=record_path, job=job) from record_error
            if not compact:
                evidence[str(request_path)] = request_hash
            if result is not None:
                result['evidence'].update(evidence)
            if failure is not None:
                failure.evidence.update(evidence)
            if issue and failure is None and result is not None:
                raise LsfError(issue, terminal=job['terminal'], record=record_path, evidence=evidence, job=job)
        finally:
            # Unresolved jobs retain guards even if the controller exits. No blind replay.
            if job['terminal'] or not attempted:
                for guard in guards:
                    if guard.exists() and read_json(guard).get('token') == token:
                        guard.unlink()
                        sync_directory(guard.parent)
            if (compact and sealed and failure is None and result is not None
                    and job['terminal'] and job.get('log_collection', {}).get('complete')):
                # Only transient files already embedded in run.json/run.log can go.
                # Unknown/incomplete jobs retain their spool for investigation.
                for name in ('worker.json', 'farm.out', 'farm.err'):
                    (directory / name).unlink(missing_ok=True)
                try:
                    directory.rmdir()
                except OSError:
                    pass
