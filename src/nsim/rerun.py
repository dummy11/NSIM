"""Replay a selected case, or failed regression pairs, against current inputs."""

from nsim.config import ConfigError
from nsim.replay_paths import anchor, directory_name
from nsim.planning import create_plan


def prepare_rerun(source, original, state, output_dir=None, rebuild=False,
                  execution_options=None, extra_plusargs=()):
    if original['execution']['phase'] == 'com':
        raise ConfigError('rerun requires completed case results; use com to retry compilation')
    from nsim.single_replay import case_source, reuse_compilation
    single = case_source(original)
    selected = [task for task in state['tasks'] if single or task['effective_verdict'] in ('FAIL', 'NO_COMPARE')]
    if not selected:
        return None
    root, anchor_path = anchor(original)
    previous = {task['id']: task for task in original['tasks']}
    tasks = [previous[task['id']] for task in selected]
    submit_indices = list(dict.fromkeys(task['submit'] for task in tasks))
    submits = [original['submits'][index] for index in submit_indices]
    selection = {(original['submits'][t['submit']]['name'], t['case'], t['seed']) for t in tasks}
    caselists = list(dict.fromkeys(path for task in tasks for path in task.get('caselists', original.get('caselists', []))))
    from nsim.profiles import merged_options, recorded
    defaults = recorded(original)
    options = merged_options(original['execution'].get('options'), execution_options)
    if rebuild or not single:
        options.pop('compile_coverage_by_submit', None)
    if single and not rebuild:
        options['compile_coverage_by_submit'] = {
            submit['name']: submit.get('effective_profile', {}).get('compile_coverage',
                '-cm' in submit.get('vcs', {}).get('compile_args', [])) for submit in submits}
    from nsim.profiles import override_groups
    plan = create_plan(
        original['project']['root'], caselist=caselists or None,
        cases=None if caselists else list(dict.fromkeys(t['case'] for t in tasks)),
        submit_paths=[submit['source'] for submit in submits],
        seeds=list(dict.fromkeys(task['seed'] for task in tasks)),
        phase='run', directory=original['execution'].get('workspace'),
        plusargs=override_groups(original.get('plusargs', ()), extra_plusargs), task_selection=selection,
        execution_options=options, profile_defaults=defaults)
    if single and not rebuild:
        for submit in plan['submits']:
            profile = submit['effective_profile']
            requires_instrumentation = (submit['adapter'] == 'vcs' or bool(submit.get('coverage', {}).get('compile_args')))
            if requires_instrumentation and profile['coverage'] and not profile['compile_coverage']:
                raise ConfigError('coverage collection requires coverage-instrumented compilation; rerun with --rebuild')
    for submit in plan['submits']:
        if (rebuild or not single) and submit['adapter'] == 'command' and not submit.get('compile'):
            raise ConfigError(f'rerun requires a compile step for submit {submit["name"]}; prebuilt-only inputs cannot be rebuilt')
    # A case reference inside a regression still belongs to that regression.
    # A standalone case uses its workspace, outside the owned case directory.
    name = directory_name(output_dir, 'rerun')
    plan['id'] = name if output_dir is None else plan['id']
    plan['kind'] = 'regression'
    plan['execution'].update(phase='regr', layout='isolated-regression-v1',
                             replay_parent=anchor_path, replay_dir=name)
    plan['seed_selection'].update(source='rerun', repeat=None)
    plan['source'] = {'journal': str(source), 'id': original['id'], 'mode': 'rerun',
                      'root_id': (original.get('source') or {}).get('root_id', original['id'])}
    if single:
        plan['source']['scope'] = 'case'
        if not rebuild:
            reuse_compilation(plan, selected[0], root)
    batch = original.get('batch', original.get('source_batch'))
    if batch:
        plan['source_batch'] = batch
    parents = {(original['submits'][t['submit']]['name'], t['case'], t['seed']): t for t in selected}
    for task in plan['tasks']:
        prior = parents[(plan['submits'][task['submit']]['name'], task['case'], task['seed'])]
        task['parent'] = {'run': original['id'], 'task': prior['id'],
                          'attempt': prior['attempts'][-1]['id'] if prior['attempts'] else None}
    return plan, [root]
