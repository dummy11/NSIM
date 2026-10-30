"""Read old/new LSF reservations without discarding ambiguous evidence."""

import os
from pathlib import Path
import re

from nsim.case_records import read_record, checksum
from nsim.config import ConfigError, read_json
from nsim.storage import digest, sync_directory

TOKEN = re.compile(r'^[0-9a-f]{32}$')
POST_TERMINAL = {'PDONE', 'PERR', 'POST_DONE', 'POST_ERR'}


def safe_path(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ConfigError(f'refusing symlink in LSF metadata: {path}')
    return path


def read_object(path):
    value = read_json(safe_path(path))
    if not isinstance(value, dict):
        raise ConfigError(f'malformed LSF metadata object: {path}')
    return value


def load_job(path):
    path = safe_path(path)
    if path.name == 'run.json':
        value, saved = read_record(path)
        if value.get('schema_version') != 2:
            raise ConfigError(f'unsupported case occupancy record: {path}')
        job = value.get('lsf')
    else:
        job = read_json(path)
        saved = digest(path)
    if (not isinstance(job, dict) or job.get('schema_version') != 1
            or not TOKEN.fullmatch(str(job.get('token', '')))
            or job.get('name') != 'nsim_' + job['token']
            or type(job.get('terminal')) is not bool
            or job.get('phase') not in ('compile', 'simulate')
            or not isinstance(job.get('status'), str)
            or 'job_id' not in job
            or job['job_id'] is not None and (not str(job['job_id']).isdigit() or int(job['job_id']) <= 0)
            or not isinstance(job.get('submit_argv'), list)
            or not all(isinstance(arg, str) for arg in job['submit_argv'])
            or job.get('record') != str(path)):
        raise ConfigError(f'malformed LSF job identity: {path}')
    return job, saved


def context(path, job):
    """Resolve the immutable request, including historical sidecar requests."""
    path = safe_path(path)
    request = job.get('request')
    if request is None:
        request_path = safe_path(path.parent / 'request.json')
        if digest(request_path) != job.get('request_sha256'):
            raise ConfigError(f'LSF request checksum changed: {request_path}')
        request = read_object(request_path)
    elif checksum(request) != job.get('request_sha256'):
        raise ConfigError(f'LSF request checksum changed: {path}')
    if not isinstance(request, dict) or request.get('token') != job['token']:
        raise ConfigError(f'LSF request token mismatch: {path}')
    cwd = safe_path(request['cwd'])
    expected = cwd / 'run.json' if path.name == 'run.json' else cwd / '.lsf-jobs' / job['token'] / 'job.json'
    if path != expected:
        raise ConfigError(f'LSF record does not belong to working directory: {path}')
    guards = [safe_path(p) for p in request['guards']]
    build = request.get('build_dir')
    if build is None and request.get('build_evidence'):
        manifests = [Path(p).parent for p in request['build_evidence'] if Path(p).name == 'build.json']
        if len(manifests) != 1:
            raise ConfigError('cannot determine protected compile directory')
        build = manifests[0]
    build = safe_path(build) if build else None
    allowed = {cwd / '.lsf-active.json'} if path.name != 'run.json' else set()
    if build and build != cwd:
        allowed.add(build / '.lsf-readers' / (job['token'] + '.json'))
    if set(guards) != allowed:
        raise ConfigError(f'LSF protected paths conflict with request: {path}')
    if request.get('plan_ref'):
        # A durable copy of only the scheduler settings is sufficient for recovery;
        # inspecting/releasing never loads code or submits another worker.
        config = job['config']
    else:
        plan = request['plan']
        tasks = [t for t in plan['tasks'] if t['id'] == job['task_id']]
        if len(tasks) != 1:
            raise ConfigError('LSF request task identity is missing or ambiguous')
        task = tasks[0]
        config = plan['submits'][task['submit']]['scheduler']
    if not isinstance(config, dict) or config.get('kind') != 'lsf':
        raise ConfigError('record has no LSF scheduler configuration')
    return dict(cwd=cwd, build=build, guards=guards, config=config, request=request)


def resolution_path(cwd, token):
    if not TOKEN.fullmatch(str(token)):
        raise ConfigError('invalid LSF token')
    return Path(cwd) / ('.lsf-release-' + token + '.json')


def released(path, job, saved=None):
    cwd = Path(path).parent if Path(path).name == 'run.json' else Path(path).parents[2]
    receipt = resolution_path(cwd, job['token'])
    if not os.path.lexists(receipt):
        return False
    value = read_object(receipt)
    body = {k: v for k, v in value.items() if k != 'checksum'}
    if (value.get('schema_version') != 1 or value.get('checksum') != checksum(body)
            or value.get('token') != job['token'] or value.get('record') != str(path)
            or value.get('record_sha256') != (saved or digest(Path(path)))):
        raise ConfigError(f'invalid LSF release receipt: {receipt}')
    return value.get('released') is True


def reservation_records(directory):
    """Bounded metadata scan, not a recursive scan of tool outputs or env links."""
    records = safe_path(Path(directory) / '.lsf-jobs')
    if not records.exists():
        return []
    result = []
    for child in sorted(records.iterdir()):
        safe_path(child)
        if not child.is_dir() or not TOKEN.fullmatch(child.name):
            raise ConfigError(f'unrecognized LSF job record: {child}')
        record = child / 'job.json'
        # A regression can contain a byte-for-byte snapshot of an older build.
        # These terminal sidecars remain evidence, not live jobs in the snapshot.
        manifest_path = Path(directory) / 'build.json'
        if manifest_path.is_file():
            manifest = read_object(manifest_path)
            snapshot = manifest.get('snapshot_source', {})
            if not isinstance(snapshot, dict):
                raise ConfigError(f'malformed compile snapshot: {manifest_path}')
            source = snapshot.get('path')
            if source and record.is_file():
                job = read_object(record)
                original = str(Path(source) / record.relative_to(directory))
                expected = manifest.get('scheduler_evidence', {}).get(str(record))
                if job.get('terminal') is True and job.get('record') == original and expected == digest(record):
                    continue
        result.append(record)
    return result


def check_case_record(directory, required=False):
    path = safe_path(Path(directory) / 'run.json')
    if not path.exists():
        if required:
            raise ConfigError(f'existing simulation record missing; occupancy unknown: {path}')
        return
    value, saved = read_record(path)
    if 'lsf' in value:
        job, saved = load_job(path)
        if not job['terminal'] and not released(path, job, saved):
            raise ConfigError(f'LSF job reservation still active or unresolved: {path}; token={job["token"]}')
    elif value.get('lifecycle') in ('running', 'unresolved', 'interrupted', 'error'):
        # Legacy LSF has its own guards; a local record has no remote writer.
        attempts = value.get('task', {}).get('attempts', [])
        for attempt in attempts:
            if attempt.get('terminal') is False or attempt.get('scheduler_record'):
                record = attempt.get('scheduler_record') or attempt.get('scheduler', {}).get('record')
                if record:
                    job, job_hash = load_job(record)
                    if job['terminal'] or released(record, job, job_hash):
                        continue
                raise ConfigError(f'LSF occupancy has no confirmed terminal identity: {path}')


def durable_directories(path, root):
    """Persist new directory entries as well as the pre-submit record rename."""
    path, root = Path(path), Path(root)
    for current in (path, *path.parents):
        sync_directory(current)
        if current == root:
            break
