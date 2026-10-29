"""Process-local source write protection; never chmod the user's checkout."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

from nsim.config import ConfigError


def source_paths(roots, work):
    """Include external symlink targets so env cannot escape to writable inputs."""
    work = Path(work).resolve()
    pending = list(roots)
    protected = set()

    def failed(error):
        raise ConfigError(f'cannot inspect read-only environment: {error}') from error

    while pending:
        try:
            path = Path(pending.pop()).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ConfigError(f'cannot resolve read-only environment: {exc}') from exc
        if any(path == root or root in path.parents for root in protected):
            continue
        if path == work or path in work.parents or work in path.parents:
            raise ConfigError(f'read-only environment overlaps execution directory: {path}')
        protected.add(path)
        if path.is_dir():
            for directory, dirs, files in os.walk(path, followlinks=False, onerror=failed):
                for name in dirs + files:
                    member = Path(directory) / name
                    if member.is_symlink():
                        pending.append(member)
        elif not path.is_file():
            raise ConfigError(f'read-only environment target must be a file or directory: {path}')
    return sorted(str(path) for path in protected if not any(parent in protected for parent in path.parents))


class ReadOnlyEnvironment:
    def __init__(self, roots, work, environment):
        self.roots = source_paths(roots, work)
        self.work = str(Path(work).resolve())
        if sys.platform == 'darwin':
            self.backend = 'seatbelt'
            executable = '/usr/bin/sandbox-exec'
            rules, parameters = [], []
            for index, path in enumerate(self.roots):
                key = f'NSIM_READ_ONLY_{index}'
                kind = 'subpath' if Path(path).is_dir() else 'literal'
                rules.append(f'({kind} (param "{key}"))')
                parameters += ['-D', key + '=' + path]
            rules.append('(literal (param "NSIM_ENV_LINK"))')
            parameters += ['-D', 'NSIM_ENV_LINK=' + str(Path(work) / 'env')]
            profile = '(version 1)(allow default)(deny file-write* ' + ' '.join(rules) + ')'
            self.prefix = [executable, *parameters, '-p', profile]
        elif sys.platform.startswith('linux'):
            self.backend = 'bubblewrap'
            executable = shutil.which('bwrap', path=environment.get('PATH', os.defpath))
            if not executable:
                raise ConfigError('read-only env requires bubblewrap (bwrap) on the execution node; '
                                  'refusing to run without source write protection')
            self.prefix = [str(Path(executable).resolve()), '--die-with-parent', '--unshare-pid',
                           '--bind', '/', '/', '--dev-bind', '/dev', '/dev', '--proc', '/proc',
                           '--cap-drop', 'ALL', '--chdir', self.work]
            for path in self.roots:
                self.prefix += ['--ro-bind', path, path]
        else:
            raise ConfigError(f'read-only env is unsupported on {sys.platform}; refusing unprotected execution')

    def command(self, argv, pass_fds=()):
        if self.backend == 'bubblewrap':
            # Keep the controller's product locks alive for the sandbox lifetime.
            sync = [part for fd in pass_fds for part in ('--sync-fd', str(fd))]
            return [*self.prefix, *sync, '--', *argv]
        return [*self.prefix, *argv]

    def describe(self):
        return {'backend': self.backend, 'protected_paths': self.roots}

    def verify(self, environment):
        """Fail before any user command if the OS cannot establish protection."""
        try:
            result = subprocess.run(self.command([sys.executable, '-I', '-c', 'pass']),
                                    cwd=self.work, env=environment, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConfigError(f'cannot establish read-only env protection: {exc}') from exc
        if result.returncode:
            message = (result.stderr or result.stdout).strip()[:2000]
            raise ConfigError(f'cannot establish read-only env protection ({self.backend}): {message}; '
                              'no unprotected fallback is allowed')
