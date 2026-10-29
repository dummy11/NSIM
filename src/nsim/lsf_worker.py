"""Execute a hashed request on an LSF execution host; publish bounded evidence."""

import copy
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time

from nsim.config import read_json
from nsim.layout import validate_layout
from nsim.planning import check_identity
from nsim.hooks import execute_stage
from nsim.storage import digest, preflight, verify_files, write_json


def execute_request(path, expected):
    path = Path(path)
    if digest(path) != expected:
        raise ValueError('worker request checksum changed')
    request = read_json(path)
    record = {'schema_version': 1, 'token': request['token'], 'request_sha256': expected,
              'host': socket.gethostname(), 'pid': os.getpid(), 'started_at': time.time(),
              'exit_code': None, 'timed_out': False, 'cancelled': False, 'error': None}
    cancel = threading.Event()
    def stop(signum, frame):
        cancel.set()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    try:
        plan = copy.deepcopy(request['plan'])
        for submit in plan['submits']:
            for source in submit.get('controller_inputs', []):
                plan['input_hashes'].pop(source, None)
        if request.get('worker_python'):
            plan['runtime']['python'] = request['worker_python']
            plan['runtime']['executable_hash'] = request['worker_python_sha256']
        check_identity(plan)
        verify_files(request['build_evidence'])
        validate_layout(plan, [Path(request['root'])])
        work = Path(request['cwd'])
        if not work.is_dir():
            raise ValueError('execution working directory is unavailable')
        for root in set([request['root'], str(work)]):
            preflight(Path(root), request['min_free_bytes'], request['min_free_inodes'])
        for guard in request['guards']:
            if read_json(Path(guard)).get('token') != request['token']:
                raise ValueError('worker reservation is missing or changed')
        stage = execute_stage(request['stage'], cancel)
        record['tool_pid'] = stage.pop('pid', None)
        record.update(stage)
        check_identity(plan)
        verify_files(request['build_evidence'])
        preflight(work, request['min_free_bytes'], request['min_free_inodes'])
    except (OSError, ValueError) as exc:
        record['error'] = str(exc)
    finally:
        record['ended_at'] = time.time()
        write_json(path.parent / 'worker.json', record)
    return 0 if record['error'] is None else 1


def main():
    try:
        if len(sys.argv) != 3:
            raise ValueError('usage: python -m nsim.lsf_worker REQUEST SHA256')
        return execute_request(sys.argv[1], sys.argv[2])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'nsim worker: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
