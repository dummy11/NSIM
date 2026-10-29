"""Process-level LSF test service. It launches the actual NSIM worker locally."""
import fcntl
import json
import os
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
            job = state['jobs'].get(job_id)
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
            status = 'UNKWN' if mode == 'unknown' else job['status']
            print(json.dumps({'RECORDS': [{'JOBID': job_id, 'STAT': status,
                                          'EXIT_CODE': job.get('exit_code', '-'), 'EXEC_HOST': 'test-node'}]}))
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
        with state_file(folder) as state:
            if state.get('malformed_worker'):
                request_path = Path(shlex.split(job['script'].splitlines()[1])[-2])
                worker_path = request_path.parent / 'worker.json'
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
