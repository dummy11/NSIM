"""Read-only occupancy inspection and identity-checked release of one LSF job."""

from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import pwd
import subprocess
import time

from nsim.case_records import checksum, read_record
from nsim.cli_aliases import add_argument, add_command
from nsim.config import ConfigError, read_json
from nsim.layout import build_directory, simulation_directory
from nsim.lsf_records import (POST_TERMINAL, TOKEN, context, load_job, released,
                              read_object, reservation_records, resolution_path, safe_path)
from nsim.ownership import OWNER_FILE
from nsim.storage import digest, sync_directory, write_json


def add_commands(commands):
    parser = add_command(commands, 'lsf', help='Inspect and safely release unresolved LSF occupancy')
    actions = parser.add_subparsers(dest='action', required=True)
    inspect = add_command(actions, 'inspect', help='Read saved job/occupancy records')
    add_argument(inspect, 'source', type=Path, help='Case, compile or regression directory')
    add_argument(inspect, '--check-lsf', action='store_true', help='Also query current LSF identity and status')
    add_argument(inspect, '--json', action='store_true')
    release = add_command(actions, 'release', help='Verify one job has stopped, then release its occupancy')
    add_argument(release, 'source', type=Path)
    add_argument(release, '--token', required=True, help='Exact submission token printed by inspect')
    add_argument(release, '--json', action='store_true')


def _scope(source, issues, pending):
    source = safe_path(source)
    if not source.is_dir():
        raise ConfigError(f'LSF inspection directory not found: {source}')
    journal = source / 'report' if (source / 'report' / 'plan.json').exists() else source
    if (journal / 'plan.json').exists():
        plan = read_object(journal / 'plan.json')
        if plan.get('kind') == 'batch':
            raise ConfigError('choose an individual regression directory from the batch')
        if not isinstance(plan.get('tasks'), list) or not plan['tasks']:
            raise ConfigError(f'inspection plan has no task scope: {journal}')
        roots = [safe_path(p) for p in plan['policy']['output_roots']]
        statuses = {}
        try:
            state = read_object(journal / 'state.json')
            if state['id'] != plan['id']:
                raise ValueError('plan/state identity mismatch')
            statuses = {t['id']: t for t in state['tasks']}
            if set(statuses) != {t['id'] for t in plan['tasks']}:
                raise ValueError('plan/state task scope mismatch')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(dict(path=str(journal / 'state.json'), error=str(exc)))
        directories = set()
        for root in roots:
            for task in plan['tasks']:
                build = safe_path(build_directory(root, plan, task))
                directories.add(build)
                if plan['execution']['phase'] == 'com':
                    continue
                case = safe_path(simulation_directory(root, plan, task))
                saved = statuses.get(task['id'], {})
                if not case.exists():
                    if saved.get('status') == 'planned':
                        pending.append(str(case))
                    elif saved.get('status') == 'running' and not saved.get('attempts'):
                        # Compilation/preparation may still be in progress.
                        issues.append(dict(path=str(case), error='dispatched case has no record yet; preparation or interruption'))
                    else:
                        issues.append(dict(path=str(case), error='case directory missing; occupancy cannot be established'))
                else:
                    directories.add(case)
        return directories
    if any((source / name).exists() for name in (OWNER_FILE, 'run.json', 'build.json', '.lsf-active.json', '.lsf-readers', '.lsf-jobs')):
        return {source}
    raise ConfigError(f'not a recorded case, compile or regression directory: {source}')


def inspect(source, check_lsf=False, include_resolved=False):
    issues, pending = [], []
    candidates = {}
    try:
        directories = _scope(source, issues, pending)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return dict(source=str(source), jobs=[], issues=[dict(path=str(source), error=str(exc))],
                    uncreated_cases=[], complete=False)

    def candidate(path, guard=None):
        key = str(safe_path(path))
        candidates.setdefault(key, [])
        if guard:
            candidates[key].append(str(guard))

    def guard_record(path):
        try:
            value = read_object(path)
            if not isinstance(value, dict) or not TOKEN.fullmatch(str(value.get('token', ''))):
                raise ConfigError('missing or invalid token in occupancy marker')
            candidate(value['record'], path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(dict(path=str(path), error=str(exc)))

    for directory in sorted(directories):
        try:
            safe_path(directory)
            if not directory.exists():
                issues.append(dict(path=str(directory), error='compile directory missing'))
                continue
            case_path = directory / 'run.json'
            if os.path.lexists(case_path):
                value, _ = read_record(safe_path(case_path))
                if 'lsf' in value:
                    candidate(case_path)
                elif any(a.get('terminal') is False for a in value.get('task', {}).get('attempts', [])):
                    # Old case records point at the sidecar job; do not lose them
                    # merely because an active marker has gone missing.
                    for attempt in value['task']['attempts']:
                        path = attempt.get('scheduler_record') or attempt.get('scheduler', {}).get('record')
                        if path:
                            candidate(path)
                        elif attempt.get('terminal') is False:
                            issues.append(dict(path=str(case_path), error='unresolved attempt has no job record'))
            elif (directory / '.run.lock').exists():
                issues.append(dict(path=str(case_path), error='simulation record missing; occupancy unknown'))
            for path in reservation_records(directory):
                candidate(path)
            active = directory / '.lsf-active.json'
            if os.path.lexists(active):
                guard_record(active)
            readers = safe_path(directory / '.lsf-readers')
            if readers.exists():
                for path in sorted(readers.iterdir()):
                    guard_record(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(dict(path=str(directory), error=str(exc)))
    jobs = []
    for record, markers in sorted(candidates.items()):
        entry = dict(record=record, markers=markers, token=None, job_id=None,
                     saved_status=None, protected_paths=[], released=False, issues=[], missing_markers=[])
        try:
            if markers:
                hint = read_object(markers[0])
                entry.update(token=hint.get('token'), name=hint.get('job_name'))
            job, saved = load_job(record)
            entry.update(token=job['token'], job_id=job['job_id'], name=job['name'], phase=job['phase'],
                         saved_status=job['status'], terminal=job['terminal'])
            info = context(record, job)
            entry['protected_paths'] = list(dict.fromkeys(str(p) for p in (info['cwd'], info['build']) if p))
            entry['released'] = released(record, job, saved)
            all_markers = set(markers) | {str(p) for p in info['guards'] if os.path.lexists(p)}
            entry['markers'] = sorted(all_markers)
            for marker in all_markers:
                value = read_object(marker)
                if (Path(marker) not in info['guards'] or value.get('token') != job['token']
                        or value.get('record') != record or value.get('job_name') != job['name']):
                    raise ConfigError(f'conflicting occupancy marker: {marker}')
            entry['needs_release'] = not entry['released'] and (not job['terminal'] or bool(all_markers))
            if not job['terminal'] and not entry['released']:
                entry['missing_markers'] = [str(p) for p in info['guards'] if not os.path.lexists(p)]
            if check_lsf and entry['needs_release']:
                entry['checked'] = check_job(job, info)
            if include_resolved or entry['needs_release']:
                jobs.append(entry)
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            entry['issues'].append(str(exc))
            jobs.append(entry)
    return dict(source=str(safe_path(source)), jobs=jobs, issues=issues, uncreated_cases=pending,
                complete=not issues and not any(j['issues'] or j['missing_markers'] for j in jobs))


def check_job(job, info):
    from nsim.lsf import query, TERMINAL
    expected = dict(job)
    expected.setdefault('owner', pwd.getpwuid(Path(job['record']).stat().st_uid).pw_name)
    args = job['submit_argv']
    for key, option in (('output_file', '-o'), ('error_file', '-e')):
        expected.setdefault(key, args[args.index(option) + 1])
    state = query(info['config'], job['job_id'], identity=expected)
    post_confirmed = job.get('include_postproc') is True or state['status'] in POST_TERMINAL
    return dict(**state, checked_at=time.time(), identity_confirmed=True,
                releasable=state['status'] in TERMINAL and post_confirmed,
                reason=('job/postprocessing not confirmed stopped' if not post_confirmed or state['status'] not in TERMINAL else None))


def release(source, token):
    if not TOKEN.fullmatch(token):
        raise ConfigError('invalid LSF token; copy the complete token from inspect')
    snapshot = inspect(source, include_resolved=True)
    matches = [j for j in snapshot['jobs'] if j['token'] == token]
    if len(matches) != 1 or matches[0]['issues']:
        raise ConfigError('cannot release: token is missing, ambiguous or has damaged/conflicting records; run inspect')
    entry = matches[0]
    path = safe_path(entry['record'])
    job, before = load_job(path)
    info = context(path, job)
    with ExitStack() as stack:
        locks = {info['cwd']: '.run.lock' if job['phase'] == 'simulate' else '.build.lock'}
        if info['build']:
            locks[info['build']] = '.build.lock'
        for directory, name in sorted(locks.items()):
            owner = read_object(directory / OWNER_FILE)
            kind = 'compile' if name == '.build.lock' else 'simulation'
            if (owner.get('uid') != os.getuid() or directory.stat().st_uid != os.getuid()
                    or owner.get('kind') != kind):
                raise ConfigError(f'cannot release another user\'s output: {directory}')
            lock = safe_path(directory / name)
            if not lock.is_file():
                raise ConfigError(f'product lock missing: {lock}')
            stream = stack.enter_context(lock.open('r'))
            shared = name == '.build.lock' and job['phase'] == 'simulate'
            try:
                fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConfigError(f'controller/product still in use: {directory}') from exc
        job, current = load_job(path)
        if current != before:
            raise ConfigError('LSF record changed while acquiring locks; inspect again')
        if released(path, job, current):
            return dict(token=token, released=True, already_released=True, record=str(path))
        info = context(path, job)
        for guard in info['guards']:
            if os.path.lexists(guard):
                value = read_object(guard)
                if (value.get('token') != token or value.get('record') != str(path)
                        or value.get('job_name') != job['name']):
                    raise ConfigError(f'occupancy changed or belongs to another job: {guard}')
        try:
            checked = check_job(job, info)
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            raise ConfigError(f'cannot release: job identity/status is unconfirmed: {exc}') from exc
        if not checked['releasable']:
            raise ConfigError(f'cannot release token {token}: LSF {checked["status"]}; {checked["reason"]}')
        receipt_path = safe_path(resolution_path(info['cwd'], token))
        receipt = dict(schema_version=1, token=token, record=str(path), record_sha256=current,
                       job_id=checked['job_id'], checked=checked, released=False,
                       uid=os.getuid(), protected_paths=entry['protected_paths'],
                       removed_markers=[str(p) for p in info['guards']], created_at=time.time())
        def save():
            write_json(receipt_path, dict(receipt, checksum=checksum(receipt)))
            sync_directory(receipt_path.parent)
        # Audit first, then remove only this token's guards, then commit release.
        # An interruption leaves either safe protection or a retryable receipt.
        save()
        for guard in info['guards']:
            guard.unlink(missing_ok=True)
            if guard.parent.exists():
                sync_directory(guard.parent)
        receipt['released'] = True
        save()
        return dict(token=token, job_id=checked['job_id'], released=True,
                    record=str(path), receipt=str(receipt_path), protected_paths=entry['protected_paths'])


def dispatch(args):
    if args.action == 'release':
        result = release(args.source, args.token)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else
              f'已释放 token={result["token"]} 的占用；原 case 结论不变，未重新提交任务。')
        return 0
    result = inspect(args.source, check_lsf=args.check_lsf)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f'未解决/异常记录 {len(result["jobs"])}；未创建的待执行 case {len(result["uncreated_cases"])}；'
              f'检查范围 {"完整" if result["complete"] else "不完整"}')
        for job in result['jobs']:
            live = job.get('checked', {})
            print(f'token={job["token"] or "未知"} job_id={job["job_id"] or "未知"} '
                  f'saved={job["saved_status"] or "未知"} checked={live.get("status", "未查询")}')
            print('  record=' + job['record'])
            for path in job['protected_paths']:
                print('  占用: ' + path)
            for issue in job['issues']:
                print('  问题: ' + issue)
            for path in job['missing_markers']:
                print('  占用标记缺失: ' + path)
            if live.get('reason'):
                print('  ' + live['reason'])
        for issue in result['issues']:
            print(f'问题: {issue["path"]}: {issue["error"]}')
    return 0 if result['complete'] else 2
