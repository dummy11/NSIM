"""Compile products, provenance and shared-reader/exclusive-writer ownership."""

from contextlib import ExitStack
import copy
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from nsim.config import ConfigError, read_json
from nsim.layout import (build_directory, check_environment_link, make_directory,
                         prepare_environment_link, isolated_regression, record_directory)
from nsim.planning import check_identity
from nsim.ownership import OWNER_FILE, claim_product
from nsim.lsf import LsfError
from nsim.storage import digest, preflight, verify_files, write_json


def compile_command(plan, task, build):
    submit = plan['submits'][task['submit']]
    config = submit.get('compile')
    if config:
        values = {'project': plan['project']['root'], 'build_dir': str(build)}
        return [arg.format(**values) for arg in config['argv']]
    return None


def artifact_files(build, names):
    files = {}
    for name in names:
        path = build / name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != build):
            raise ConfigError(f'compile artifact must not be a symlink: {path}')
        if path.is_file():
            members = [path]
        elif path.is_dir():
            members = sorted(path.rglob('*'))
            if not any(member.is_file() for member in members):
                raise ConfigError(f'compile artifact directory is empty: {path}')
        else:
            raise ConfigError(f'compile artifact missing: {path}')
        for member in members:
            if member.is_symlink():
                raise ConfigError(f'compile artifact must not be a symlink: {member}')
            if member.is_file():
                files[str(member.relative_to(build))] = digest(member)
    return files


class Builds:
    """Hold product locks through publication and share one build per controller."""

    def __init__(self, plan):
        self.plan = plan
        self.stack = ExitStack()
        self.mutex = threading.Lock()
        self.path_locks = {}
        self.entries = {}
        self.reserved = {}
        self.on_event = None
        self.lsf_plan_sha256 = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def descriptor(self, root, task):
        return self.entries[build_directory(root, self.plan, task)][0].fileno()

    def snapshot_reused_build(self, root, task, build, policy):
        """Keep historical sim regressions and case replay off mutable builds.

        Validate under a shared lock, copy bytes (never hardlinks), and bind all
        build evidence to the private snapshot. No compiler or compile hook runs.
        """
        source_plan = copy.deepcopy(self.plan)
        source_plan['execution']['layout'] = 'single-v1'
        source_plan.pop('build_refs', None)
        reference = self.plan.get('execution', {}).get('reuse_builds', {}).get(str(task['submit']))
        if reference:
            source_plan['execution']['com_dir'] = reference['path']
            source_plan['build_refs'] = {task['id']: reference}
        with Builds(source_plan) as source_builds:
            source, manifest = source_builds.ensure(root, task, threading.Event(), policy)
            original_hash = digest(source / 'build.json')
            ignored = {'.build.lock', OWNER_FILE, 'env', '.lsf-readers', '.lsf-active.json'}
            for path in source.rglob('*'):
                if path.is_symlink() and path != source / 'env':
                    raise ConfigError(f'cannot snapshot a symlink in compile products: {path}')
            make_directory(build, root)
            claim_product(build, self.plan, 'compile')
            shutil.copytree(source, build, dirs_exist_ok=True,
                            ignore=lambda directory, names: ignored if Path(directory) == source else [])
            prepare_environment_link(build, self.plan['project']['root'])
            manifest = copy.deepcopy(manifest)
            for field in ('stage_evidence', 'scheduler_evidence'):
                manifest[field] = {str(build / Path(path).relative_to(source)): value
                                   for path, value in manifest.get(field, {}).items()}
            if manifest.get('log_analysis'):
                manifest['log_analysis']['path'] = str(build / Path(manifest['log_analysis']['path']).relative_to(source))
            manifest['snapshot_source'] = dict(path=str(source), sha256=original_hash,
                                                scheduler=manifest.pop('scheduler', None))
            manifest['record_dir'] = str(record_directory(root, self.plan))
            # Revalidate before releasing the source reader lock.
            source_builds.validate(source, task)
            if digest(source / 'build.json') != original_hash:
                raise ConfigError('compilation changed while copying the regression snapshot')
            write_json(build / 'build.json', manifest)
            (build / '.build.lock').touch(exist_ok=False)

    def reserve(self, root, task, policy):
        """Acquire all ownership before a controller clears simulation results."""
        build = build_directory(root, self.plan, task)
        with self.mutex:
            if build in self.reserved:
                return self.reserved[build]
            reuse = self.plan.get('execution', {}).get('phase') == 'sim'
            lock = build / '.build.lock'
            if reuse and isolated_regression(self.plan):
                self.snapshot_reused_build(root, task, build, policy)
            if reuse:
                if not build.is_dir() or not lock.is_file():
                    raise ConfigError(f'no successful compilation at {build}; run nsim com first')
            else:
                if root in build.parents:
                    make_directory(build, root)
                else:
                    # An external compile parent must already exist (e.g. mounted storage).
                    preflight(build if build.is_dir() else build.parent,
                              policy['min_free_bytes'], policy['min_free_inodes'])
                    build.mkdir(exist_ok=True)
            claim_product(build, self.plan, 'compile', reuse=reuse)
            if lock.is_symlink() or (build / 'build.json').is_symlink():
                raise ConfigError(f'refusing symlink in compile metadata: {build}')
            stream = self.stack.enter_context(lock.open('r' if reuse else 'a+'))
            try:
                fcntl.flock(stream, (fcntl.LOCK_SH if reuse else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConfigError(f'compile directory is in use: {build}') from exc
            from nsim.lsf import check_reservations
            check_reservations(build, writing=not reuse)
            check_environment_link(build, self.plan['project']['root'])
            self.reserved[build] = stream
            return stream

    def ensure(self, root, task, cancel, policy):
        build = build_directory(root, self.plan, task)
        with self.mutex:
            ownership = self.path_locks.setdefault(build, threading.Lock())
        with ownership:
            if cancel.is_set():
                from nsim.lsf import LsfError
                raise LsfError('controller cancelled before compilation', cancelled=True)
            if build in self.entries:
                return build, self.entries[build][1]
            stream = self.reserve(root, task, policy)
            reuse = self.plan.get('execution', {}).get('phase') == 'sim'
            if reuse:
                manifest = self.validate(build, task)
            else:
                manifest = self.compile(build, task, stream, cancel, root, policy)
                if self.plan.get('execution', {}).get('phase', 'run') in ('run', 'regr'):
                    fcntl.flock(stream, fcntl.LOCK_SH)
            self.entries[build] = (stream, manifest)
            return build, manifest

    def validate(self, build, task):
        try:
            check_environment_link(build, self.plan['project']['root'], required=True)
            manifest = read_json(build / 'build.json')
            submit = self.plan['submits'][task['submit']]
            if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1
                    or manifest.get('ready') is not True or not isinstance(manifest.get('id'), str)
                    or manifest.get('build_id') != task['build_id'] or manifest.get('mode') != submit['mode']
                    or manifest.get('input_hashes') != submit['build_inputs']
                    or manifest.get('config') != submit['build_config']):
                raise ConfigError(f'compilation is unsuccessful or incompatible: {build}')
            names = submit['compile']['artifacts'] if submit.get('compile') else (
                ['fake-model.json'] if submit['adapter'] == 'fake' else [])
            if artifact_files(build, names) != manifest['artifacts']:
                raise ConfigError(f'compile artifacts changed: {build}')
            verify_files(manifest['input_hashes'])
            reference = self.plan.get('build_refs', {}).get(task['id'])
            if reference and (reference['path'] != str(build) or reference['sha256'] != digest(build / 'build.json')):
                raise ConfigError(f'recorded compilation changed; exact replay unavailable: {build}')
            return manifest
        except (OSError, KeyError, TypeError) as exc:
            raise ConfigError(f'cannot reuse compilation at {build}: {exc}') from exc

    def compile(self, build, task, stream, cancel, root, policy):
        check_identity(self.plan)
        check_environment_link(build, self.plan['project']['root'])
        # Invalidate completion first, keeping the locked inode stable.
        (build / 'build.json').unlink(missing_ok=True)
        for child in build.iterdir():
            if child.name in ('.build.lock', OWNER_FILE):
                continue
            if child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                shutil.rmtree(child)
        prepare_environment_link(build, self.plan['project']['root'])
        submit = self.plan['submits'][task['submit']]
        argv = compile_command(self.plan, task, build)
        manifest = {'schema_version': 1, 'id': uuid.uuid4().hex, 'build_id': task['build_id'],
                    'record_dir': str(record_directory(root, self.plan)),
                    'submit': submit['name'], 'mode': submit['mode'], 'adapter': submit['adapter'],
                    'kind': 'command' if argv else 'fake' if submit['adapter'] == 'fake' else 'prebuilt',
                    'ready': False, 'compiled': False, 'artifacts': {}, 'command': argv,
                    'input_hashes': submit['build_inputs'], 'config': submit['build_config'],
                    'started_at': time.time(), 'exit_code': None}
        write_json(build / 'build.json', manifest)
        from nsim.hooks import execute_stage, spec
        request = spec(self.plan, task, 'compile', build, build, argv, build / 'compile.log',
                       build / 'compile.log', submit['compile']['timeout_seconds'] if argv else 60)
        try:
            if argv and submit['scheduler']['kind'] == 'lsf':
                from nsim.lsf import run_process
                stage = run_process(self.plan, task, root, policy, argv, build,
                                    build / 'compile.log', build / 'compile.log',
                                    submit['compile']['timeout_seconds'], cancel, 'compile', on_event=self.on_event)
                manifest['scheduler'] = stage['job']
                manifest['scheduler_evidence'] = stage['evidence']
            else:
                stage = execute_stage(request, cancel, (stream.fileno(),))
            manifest.update(exit_code=stage['exit_code'], log_analysis=stage.get('log_analysis'),
                            read_only_environment=stage.get('read_only_environment'),
                            stage_evidence=stage.get('evidence', {}), hooks=stage.get('hooks', []))
            if stage.get('error'):
                raise OSError(stage.get('error_errno'), stage['error'])
            if stage.get('hook_failed'):
                raise ConfigError(stage['hook_reason'])
            if stage['timed_out'] or stage['cancelled'] or stage['exit_code'] != 0:
                raise ConfigError('compilation timed out, was cancelled or failed')
            analysis = stage.get('log_analysis')
            blocking = analysis.get('blocking_counts', analysis['counts']) if analysis else None
            if not analysis or not analysis['complete'] or blocking['error'] + blocking['fatal']:
                raise ConfigError('compile log analysis: ' + (analysis['failure_reason'] if analysis else 'unavailable'))
            if argv:
                manifest['artifacts'] = artifact_files(build, submit['compile']['artifacts'])
                manifest['compiled'] = True
            elif submit['adapter'] == 'fake':
                manifest['artifacts'] = artifact_files(build, ['fake-model.json'])
            check_identity(self.plan)
            manifest['ready'] = True
        except LsfError as exc:
            manifest['reason'] = str(exc)
            manifest['scheduler_evidence'] = exc.evidence
            raise
        except ValueError as exc:
            manifest['reason'] = str(exc)
        finally:
            manifest['ended_at'] = time.time()
            write_json(build / 'build.json', manifest)
        return manifest
