"""Resolve a compile directory to trusted execution records and rebuild it."""

import copy
from pathlib import Path

from nsim.config import ConfigError, read_json
from nsim.layout import build_directory
from nsim.planning import create_plan
from nsim.replay_paths import anchor, directory_name
from nsim.reports import load_final
from nsim.storage import digest, IntegrityError


def load_compile_source(source):
    if (source / 'plan.json').is_file():
        plan, state = load_final(source)
        if plan['execution']['phase'] != 'com':
            raise ConfigError('compile directory contains a non-compile control plan')
        return plan, state
    manifest = read_json(source / 'build.json')
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
        raise IntegrityError('invalid compile manifest')
    recorded = manifest.get('record_dir')
    if recorded is not None and (not isinstance(recorded, str) or not recorded or not Path(recorded).is_absolute()):
        raise IntegrityError('invalid compile record directory')
    if recorded:
        candidates = [Path(recorded)]
    else:
        # Legacy builds have no link. Limit discovery to their enclosing
        # regression or conventional sibling simulation directory.
        enclosing = next((parent / 'report' for parent in source.parents
                          if (parent / 'report' / 'plan.json').is_file()), None)
        candidates = ([enclosing] if enclosing else
                      [path.parent for path in sorted((source.parent / 'sim_dir').glob('*/run.json'))])
    matches = []
    expected = digest(source / 'build.json')
    for candidate in candidates:
        try:
            plan, state = load_final(candidate)
        except (OSError, ValueError):
            if recorded or len(candidates) == 1:
                raise
            continue
        if plan.get('kind') == 'batch':
            raise IntegrityError('compile directory must refer to an individual execution record')
        tasks = [task for task in state['tasks'] if task['attempts'] and
                 task['attempts'][-1].get('build_dir') == str(source) and
                 task['attempts'][-1].get('evidence', {}).get(str(source / 'build.json')) == expected]
        indices = {task['submit'] for task in tasks}
        if len(indices) == 1:
            plan = copy.deepcopy(plan)
            plan['tasks'] = [task for task in plan['tasks'] if task['submit'] in indices]
            matches.append((plan, dict(state, tasks=tasks)))
    if len(matches) != 1:
        raise IntegrityError('cannot uniquely bind compile directory to a completed run; '
                             'use its recorded case directory with rerun --rebuild')
    plan, state = matches[0]
    if not any(build_directory(Path(root), plan, plan['tasks'][0]) == source
               for root in plan['policy']['output_roots']):
        raise IntegrityError('compile directory does not match its recorded plan')
    return plan, state


def prepare_compile_rerun(source, original, state, output_dir=None, execution_options=None):
    from nsim.profiles import changed, merged_options, recorded
    if changed({key: value for key, value in (execution_options or {}).items()
                if key in ('waves', 'sim_args', 'cm_dir')}):
        raise ConfigError('compile directory replay does not accept waveform or simulation options')
    indices = list(dict.fromkeys(task['submit'] for task in original['tasks']))
    submits = [original['submits'][index] for index in indices]
    root, parent = anchor(original)
    retained = {key: value for key, value in original['execution'].get('options', {}).items()
                if key in ('coverage', 'compile_args', 'defines', 'cm_metrics', 'cm_hier')}
    selected = {key: value for key, value in (execution_options or {}).items()
                if key in ('coverage', 'compile_args', 'defines', 'cm_metrics', 'cm_hier')}
    plan = create_plan(original['project']['root'], phase='com',
                       submit_paths=[submit['source'] for submit in submits],
                       directory=original['execution']['workspace'],
                       execution_options=merged_options(retained, selected),
                       profile_defaults=recorded(original))
    if [submit['name'] for submit in plan['submits']] != [submit['name'] for submit in submits]:
        raise ConfigError('rerun submit names no longer match the original compilation')
    for submit in plan['submits']:
        if submit['adapter'] == 'command' and not submit.get('compile'):
            raise ConfigError(f'rerun requires a compile step for submit {submit["name"]}')
    name = directory_name(output_dir, 'rerun')
    if output_dir is None:
        plan['id'] = name
    plan['kind'] = 'regression'
    plan['execution'].update(layout='isolated-regression-v1', replay_parent=parent, replay_dir=name)
    plan['source'] = {'journal': str(source), 'id': original['id'], 'mode': 'rerun', 'scope': 'compile',
                      'root_id': (original.get('source') or {}).get('root_id', original['id'])}
    batch = original.get('batch', original.get('source_batch'))
    if batch:
        plan['source_batch'] = batch
    for task, index in zip(plan['tasks'], indices):
        previous = next(task for task in state['tasks'] if task['submit'] == index)
        task['parent'] = {'run': original['id'], 'task': previous['id'],
                          'attempt': previous['attempts'][-1]['id'] if previous['attempts'] else None}
    return plan, [root]
