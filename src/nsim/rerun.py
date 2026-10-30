"""Plan fixed case/seed failures against current inputs in a private rebuild."""

from pathlib import Path

from nsim.checks import ACCEPTED
from nsim.config import ConfigError
from nsim.identifiers import new_regression_id
from nsim.layout import isolated_regression, is_regression, workspace_directory
from nsim.planning import create_plan


def prepare_rerun(source, original, state):
    if original['execution']['phase'] == 'com':
        raise ConfigError('rerun requires completed case results; use com to retry compilation')
    selected = [task for task in state['tasks'] if task['effective_verdict'] not in ACCEPTED]
    if not selected:
        return None
    root_candidates = [Path(path).resolve() for path in original['policy']['output_roots']]
    journal = Path(original['policy']['record_dir'])
    roots = [root for root in root_candidates if root in journal.parents]
    if not roots and len(root_candidates) == 1 and not (is_regression(original) or isolated_regression(original)):
        # A standalone run may have an absolute -sim-dir outside its output root.
        roots = root_candidates
    if len(roots) != 1:
        raise ConfigError('cannot locate the original record inside one recorded output root')
    root = roots[0]
    previous = {task['id']: task for task in original['tasks']}
    tasks = [previous[task['id']] for task in selected]
    submit_indices = list(dict.fromkeys(task['submit'] for task in tasks))
    submits = [original['submits'][index] for index in submit_indices]
    selection = {(original['submits'][t['submit']]['name'], t['case'], t['seed']) for t in tasks}
    caselists = list(dict.fromkeys(path for task in tasks for path in task.get('caselists', original.get('caselists', []))))
    plan = create_plan(
        original['project']['root'], caselist=caselists or None,
        cases=None if caselists else list(dict.fromkeys(t['case'] for t in tasks)),
        submit_paths=[submit['source'] for submit in submits],
        seeds=list(dict.fromkeys(task['seed'] for task in tasks)),
        phase='run', directory=original['execution'].get('workspace'),
        plusargs=original.get('plusargs', ()), task_selection=selection)
    for submit in plan['submits']:
        if submit['adapter'] == 'command' and not submit.get('compile'):
            raise ConfigError(f'rerun requires a compile step for submit {submit["name"]}; prebuilt-only inputs cannot be rebuilt')
    # A case reference inside a regression still belongs to that regression.
    # A standalone case uses its workspace, outside the owned case directory.
    anchor = original['execution'].get('rerun_parent')
    if anchor is None:
        if is_regression(original) or isolated_regression(original):
            parent = journal.parent if original['execution'].get('layout') else journal
        else:
            parent = workspace_directory(root, original, tasks[0])
        anchor = str(parent.relative_to(root))
    plan['id'] = new_regression_id(caselists)
    plan['kind'] = 'regression'
    plan['execution'].update(phase='regr', layout='isolated-regression-v1', rerun_parent=anchor)
    plan['seed_selection'].update(source='rerun', repeat=None)
    plan['source'] = {'journal': str(source), 'id': original['id'], 'mode': 'rerun'}
    batch = original.get('batch', original.get('source_batch'))
    if batch:
        plan['source_batch'] = batch
    parents = {(original['submits'][t['submit']]['name'], t['case'], t['seed']): t for t in selected}
    for task in plan['tasks']:
        prior = parents[(plan['submits'][task['submit']]['name'], task['case'], task['seed'])]
        task['parent'] = {'run': original['id'], 'task': prior['id'], 'attempt': prior['attempts'][-1]['id']}
    return plan, [root]
