"""Process-level LSF test service. It launches the actual NSIM worker locally."""
import fcntl
import json
import os
import pwd
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager


@contextmanager
def state_file(folder):
    folder = Path(folder)
    with (folder / 'lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = folder / 'state.json'
        state = json.loads(path.read_text())
        yield state
        path.write_text(json.dumps(state))


def event(state, kind, job_id, **extra):
    state['events'].append(dict(kind=kind, job_id=job_id, time=time.time(), **extra))


def main():
    operation, folder, *args = sys.argv[1:]
    if operation == 'bsub':
        script = sys.stdin.read()
        with state_file(folder) as state:
            event(state, 'ATTEMPT', None)
            mode = state.get('submit_mode')
            attempt_number = sum(item['kind'] == 'ATTEMPT' for item in state['events'])
            if mode == 'reject' or attempt_number in state.get('reject_attempts', []):
                print('Submission refused', file=sys.stderr)
                return 1
            job_id = str(len(state['jobs']) + 1)
            job = {'args': args, 'script': script, 'status': 'PEND', 'job_id': job_id,
                   'cwd': args[args.index('-cwd') + 1], 'stdout': args[args.index('-o') + 1],
                   'stderr': args[args.index('-e') + 1], 'worker_pid': None, 'queries': 0}
            job.update(name=args[args.index('-J') + 1], owner=pwd.getpwuid(os.getuid()).pw_name,
                       submit_time=str(time.time()), include_postproc=os.environ.get('LSB_JOB_INCLUDE_POSTPROC'))
            state['jobs'][job_id] = job
            event(state, 'SUBMIT', job_id)
            active = sum(j['status'] not in ('DONE', 'EXIT') for j in state['jobs'].values())
            state['peak'] = max(state.get('peak', 0), active)
            worker = subprocess.Popen([sys.executable, __file__, 'worker', folder, job_id],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)
            job['daemon_pid'] = worker.pid
        print('Lost submission reply' if mode == 'no_id' else f'Job <{job_id}> is submitted to queue <test>.')
        if state.get('submit_stderr'):
            print(state['submit_stderr'], file=sys.stderr)
        return 0
    if operation == 'bjobs':
        job_id = args[-1]
        with state_file(folder) as state:
            if '-J' in args:
                name = args[args.index('-J') + 1]
                matches = [j for j in state['jobs'].values() if j['name'] == name]
            else:
                matches = [state['jobs'][job_id]] if job_id in state['jobs'] else []
            job = matches[0] if matches else None
            if job is None:
                print(json.dumps({'RECORDS': []}))
                return 0
            job['queries'] += 1
            if state.get('query_stderr'):
                print(state['query_stderr'], file=sys.stderr)
            mode = ('unknown' if job_id in state.get('unknown_job_ids', [])
                    or any(name in job['cwd'] for name in state.get('unknown_cases', []))
                    else state.get('query_mode'))
            if mode == 'malformed' or mode == 'transient' and job['queries'] <= 2:
                print('temporary malformed response')
                return 1
            if mode == 'missing':
                print(json.dumps({'RECORDS': []}))
                return 0
            rows = []
            for match in matches:
                rows.append({'JOBID': match['job_id'], 'STAT': 'UNKWN' if mode == 'unknown' else match['status'],
                             'EXIT_CODE': match.get('exit_code', '-'), 'EXEC_HOST': 'test-node',
                             'JOB_NAME': match['name'], 'USER': match['owner'], 'SUBMIT_TIME': match['submit_time'],
                             'OUTPUT_FILE': match['stdout'], 'ERROR_FILE': match['stderr']})
            for row in rows:
                # Hold a completed worker as RUN for a deterministic number of
                # polls when testing checkpoint frequency, independent of load.
                if row['STAT'] in ('DONE', 'EXIT') and job['queries'] <= state.get('terminal_after_queries', 0):
                    row['STAT'] = 'RUN'
                row.update(state.get('query_overrides', {}))
            if state.get('duplicate_query') and rows:
                rows.append(dict(rows[0]))
            print(json.dumps({'RECORDS': rows}))
        return 0
    if operation == 'bkill':
        job_id = args[-1]
        with state_file(folder) as state:
            event(state, 'KILL', job_id)
            if state.get('kill_stdout'):
                print(state['kill_stdout'])
            if state.get('cancel_refused'):
                return 1
            job = state['jobs'][job_id]
            if job['worker_pid']:
                try:
                    os.killpg(job['worker_pid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass
            elif job['status'] == 'PEND':
                job['status'] = 'EXIT'
                job['exit_code'] = 143
                event(state, 'TERMINAL', job_id)
        return 0
    if operation == 'worker':
        job_id = args[0]
        with state_file(folder) as state:
            delay = state.get('pending_seconds', .08)
        time.sleep(delay)
        with state_file(folder) as state:
            job = state['jobs'][job_id]
            if job['status'] == 'EXIT':
                return 0
            if state.get('launch_error'):
                Path(job['stdout']).write_text('LSF launch attempted\n')
                Path(job['stderr']).write_text(state['launch_error'] + '\n')
                job.update(status='EXIT', exit_code=127)
                event(state, 'TERMINAL', job_id)
                return 0
            if state.get('no_worker'):
                job['status'] = 'DONE'
                job['exit_code'] = 0
                event(state, 'TERMINAL', job_id)
                return 0
            job['status'] = 'RUN'
            event(state, 'RUN', job_id)
            with open(job['stdout'], 'wb') as stdout, open(job['stderr'], 'wb') as stderr:
                stdout.write(state.get('farm_stdout', '').encode())
                stderr.write(state.get('farm_stderr', '').encode())
                stdout.flush()
                stderr.flush()
                process = subprocess.Popen(['/bin/sh'], stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                           cwd=job['cwd'], start_new_session=True)
            process.stdin.write(job['script'].encode())
            process.stdin.close()
            job['worker_pid'] = process.pid
        code = process.wait()
        # A test can hold this job active until the controller refills its
        # window, without depending on relative worker startup/duration times.
        deadline = time.monotonic() + 10
        while True:
            with state_file(folder) as state:
                case_dir = Path(state['jobs'][job_id]['cwd']).name
                required = state.get('terminal_requires_cases', {}).get(case_dir, 0)
                ready = len(state['jobs']) >= required
            if ready or time.monotonic() >= deadline:
                break
            time.sleep(.02)
        with state_file(folder) as state:
            if state.get('malformed_worker'):
                request_path = Path(shlex.split(job['script'].splitlines()[1])[-2])
                worker_path = (Path(json.loads(request_path.read_text())['lsf']['request']['result_path'])
                               if request_path.name == 'run.json' else request_path.parent / 'worker.json')
                worker = json.loads(worker_path.read_text())
                worker['timed_out'] = 'false'
                worker_path.write_text(json.dumps(worker))
            job = state['jobs'][job_id]
            with open(job['stdout'], 'ab') as output:
                output.write(state.get('farm_trailer', '').encode())
            delayed = state.get('farm_delay', 0)
            withheld = state.get('withhold_farm', False)
            if delayed or withheld:
                for output in (job['stdout'], job['stderr']):
                    Path(output).rename(output + '.pending')
            job['exit_code'] = code
            job['status'] = 'DONE' if code == 0 else 'EXIT'
            event(state, 'TERMINAL', job_id)
        if delayed and not withheld:
            time.sleep(delayed)
            for output in (job['stdout'], job['stderr']):
                Path(output + '.pending').rename(output)
        return 0
    raise ValueError(operation)


if __name__ == '__main__':
    sys.exit(main())
