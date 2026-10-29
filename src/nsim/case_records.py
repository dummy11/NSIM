"""One atomically published record per case; legacy journals remain readable."""

import copy
import hashlib
import json
from pathlib import Path

from nsim.layout import is_regression
from nsim.log_analysis import case_summary
from nsim.storage import IntegrityError, encode


def checksum(value):
    return hashlib.sha256(encode(value)).hexdigest()


def make_record(plan, task, path, state=None, lifecycle='running'):
    """Build from controller memory. Never reread a growing journal to update it.

    Self hashes live in the enclosing regression state, not inside the hashed
    case. A single-run record embeds its control state and checksums sections;
    damage to the derived analysis remains recoverable with the intact plan.
    """
    task = copy.deepcopy(task)
    for attempt in task['attempts']:
        attempt['evidence'] = {p: h for p, h in attempt.get('evidence', {}).items()
                               if Path(p) != Path(path)}
    latest = task['attempts'][-1] if task['attempts'] else {}
    submit = plan['submits'][task['submit']]
    value = dict(schema_version=2, kind='case', run_id=plan['id'], task_id=task['id'],
                 record_dir=plan['policy']['record_dir'], case=task['case'], seed=task['seed'],
                 submit=submit['name'], mode=submit['mode'], case_config=task.get('case_config'),
                 lifecycle=lifecycle, terminal=lifecycle == 'completed', task=task,
                 analysis=case_summary(task, latest) if latest and lifecycle != 'running' else None)
    for key in ('build_dir', 'build_id', 'build_instance'):
        value[key] = latest.get(key)
    if not is_regression(plan) and Path(path).parent == Path(plan['policy']['record_dir']):
        value['plan'] = plan
        value['state'] = ({k: v for k, v in state.items() if k != 'tasks'} if state is not None else
                          dict(schema_version=1, id=plan['id'], phase=plan['execution']['phase'],
                               terminal=False, started_at=None, ended_at=None, report_errors=[],
                               scheduler_jobs={}, quarantined={}))
        value['terminal'] = value['state']['terminal']
    value['checksums'] = dict(control=checksum({k: v for k, v in value.items() if k != 'analysis'}),
                              analysis=checksum(value['analysis']))
    return value


def read_record(path):
    try:
        data = Path(path).read_bytes()
        value = json.loads(data)
        if not isinstance(value, dict):
            raise IntegrityError('malformed case record')
        if value.get('schema_version') == 2:
            control = {k: v for k, v in value.items() if k not in ('analysis', 'checksums')}
            if checksum(control) != value['checksums']['control']:
                raise IntegrityError('case control checksum mismatch')
        return value, hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IntegrityError(f'cannot read case record {path}: {exc}') from exc


def reference_matches(value, actual_hash, expected_hash, task, attempt):
    if actual_hash == expected_hash:
        return True
    if value.get('schema_version') != 2:
        return False
    # A damaged derived summary must not prevent recovery through a case path.
    # Reconstruct ONLY analysis from the trusted parent state. Matching the
    # original whole-file hash proves every other field is still the original.
    restored = dict(value, analysis=case_summary(task, attempt))
    return checksum(restored) == expected_hash


def unpack_single(value, path, file_hash):
    try:
        plan = value['plan']
        task = value['task']
        state = dict(value['state'], tasks=[task])
        if (value['terminal'] is not True or state['terminal'] is not True or
                value['lifecycle'] != 'completed' or
                plan['id'] != state['id'] or plan['id'] != value['run_id'] or
                len(plan['tasks']) != 1 or plan['tasks'][0]['id'] != task['id'] or
                task['id'] != value['task_id']):
            raise IntegrityError('source has no terminal completion record')
        if checksum(value.get('analysis')) != value['checksums']['analysis']:
            task['record_error'] = 'case analysis checksum mismatch'
        if task['attempts']:
            task['attempts'][-1]['evidence'][str(path)] = file_hash
        return plan, state
    except (KeyError, TypeError) as exc:
        raise IntegrityError(f'malformed single-run record: {path}') from exc
