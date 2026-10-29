"""Collect scheduler output after stage analysis, preserving diagnostic line numbers."""

import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import time

from nsim.config import ConfigError
from nsim.storage import digest


class Transcript:
    def __init__(self, main, directory, phase, argv):
        self.main = Path(main)
        self.directory = Path(directory)
        self.control = self.directory / 'controller.log'
        if self.main.is_symlink():
            raise ConfigError('stage log must not be a symlink')
        self.main.write_text(f'[nsim LSF] {phase}: submission starting; scheduler output follows stage output.\n')
        self.event('stage', {'phase': phase, 'cwd': str(self.main.parent), 'argv': argv})

    def event(self, label, value):
        with self.control.open('ab') as stream:
            timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            stream.write((f'\n[nsim LSF {timestamp}] {label}\n' +
                          (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())

    def output(self, result):
        self.event('command result', {'returncode': result.returncode})
        for name in ('stdout', 'stderr'):
            value = getattr(result, name, '')
            if value:
                self.event(name, value)

    def exception(self, exc):
        self.event('command exception', str(exc))
        for name in ('stdout', 'stderr'):
            value = getattr(exc, name, None)
            if value:
                self.event(name, value.decode(errors='replace') if isinstance(value, bytes) else value)

    def snapshot(self):
        result = {}
        for name in ('farm.out', 'farm.err'):
            path = self.directory / name
            if path.is_symlink():
                raise ConfigError(f'refusing symlink in LSF output: {path}')
            try:
                info = path.stat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ConfigError(f'LSF output is not a regular file: {path}')
            result[name] = (info.st_ino, info.st_size, info.st_mtime_ns)
        return result

    def finish(self, job, worker, budget):
        """Append bounded streams only after the worker is done; never re-run analysis."""
        issues = []
        terminal = job['terminal']
        worker_evidence = (worker or {}).get('evidence', {})
        expected = worker_evidence.get(str(self.main)) if isinstance(worker_evidence, dict) else None
        if terminal and expected and digest(self.main) != expected:
            issues.append('stage log changed after worker analysis')
        self.event('final scheduler state', {key: job.get(key) for key in
                   ('job_id', 'phase', 'status', 'terminal', 'host', 'exit_code', 'cancel_reason', 'error')})
        if worker:
            self.event('worker result', {key: worker.get(key) for key in
                       ('host', 'pid', 'exit_code', 'timed_out', 'cancelled', 'error')})
        if not terminal:
            issues.append('job is not confirmed terminal; output may continue and this transcript is incomplete')
        # LSF may copy spooled output only at completion. A successful worker
        # needs both output files to become visible and stable before acceptance.
        ran = any(item['status'] in ('RUN', 'USUSP', 'SSUSP') for item in job['history'])
        cancelled_pending = job.get('cancel_reason') and worker is None and not ran
        required = terminal and job.get('job_id') and not cancelled_pending
        deadline = time.monotonic() + budget
        previous = None
        while True:
            current = self.snapshot()
            if not terminal or (current == previous and (not required or len(current) == 2)):
                break
            if time.monotonic() >= deadline:
                issues.append('LSF output is missing or did not become stable before result_wait_seconds')
                break
            previous = current
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
        if issues:
            self.event('log collection incomplete', '; '.join(issues))
        evidence = {}
        with self.main.open('ab') as output:
            for name in ('controller.log', 'farm.out', 'farm.err'):
                source = self.directory / name
                output.write(f'\n[nsim LSF] BEGIN {name}\n'.encode())
                if name != 'controller.log' and name not in current:
                    output.write(b'[nsim LSF] no output file available\n')
                else:
                    info = source.stat()
                    size = info.st_size if name == 'controller.log' else current[name][1]
                    hashed = hashlib.sha256()
                    with source.open('rb') as stream:
                        remaining = size
                        while remaining:
                            chunk = stream.read(min(1024 * 1024, remaining))
                            if not chunk:
                                issues.append(f'LSF output truncated while collecting {name}')
                                break
                            output.write(chunk)
                            hashed.update(chunk)
                            remaining -= len(chunk)
                    evidence[str(source)] = hashed.hexdigest()
                output.write(f'\n[nsim LSF] END {name}\n'.encode())
            if terminal and current != self.snapshot():
                issues.append('LSF output changed during collection')
            if issues:
                output.write(('[nsim LSF] transcript incomplete: ' + '; '.join(issues) + '\n').encode())
            output.flush()
            os.fsync(output.fileno())
        # An unresolved job may still append. Do not seal mutable log evidence.
        if terminal:
            evidence[str(self.main)] = digest(self.main)
        else:
            evidence = {}
        return evidence, '; '.join(issues) if issues else None
