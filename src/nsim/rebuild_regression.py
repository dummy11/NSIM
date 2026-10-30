"""Build a separate summary from an original regression and selected replays."""

import copy
import json
import math
from pathlib import Path

from nsim.config import ConfigError
from nsim.layout import is_regression
from nsim.replay_paths import directory_name
from nsim.reports import assessed, html_report, load_final, markdown
from nsim.storage import IntegrityError, atomic_write, digest


def _load_candidate(path, root_id):
    path = Path(path).absolute()
    if path.name == 'report' and (path / 'plan.json').is_file():
        path = path.parent
    plan, state = load_final(path, allow_broken_views=True)
    source = plan.get('source') or {}
    mode = source.get('mode')
    if mode not in ('rerun', 'recover') or source.get('root_id', source.get('id')) != root_id:
        raise ConfigError(f'not a rerun/recover of {root_id}: {path}')
    ended = state.get('ended_at')
    if not state.get('terminal') or type(ended) not in (int, float) or not math.isfinite(ended):
        raise ConfigError(f'cannot determine replay completion time: {path}; use --include')
    return mode, path, plan, state, ended


def _choose(source_root, root_id, includes):
    explicit = {}
    for path in includes:
        item = _load_candidate(path, root_id)
        if item[0] in explicit and explicit[item[0]][1] != item[1]:
            raise ConfigError(f'--include accepts one {item[0]} directory for this summary')
        explicit[item[0]] = item
    found = {'rerun': [], 'recover': []}
    for child in source_root.iterdir():
        if not child.is_dir() or child.is_symlink() or child.name in ('report', 'rerun'):
            continue
        if not child.name.startswith(('rerun_', 'recover_')) and not (child / 'report' / 'plan.json').is_file():
            continue
        if not (child / 'report' / 'plan.json').is_file():
            if child.name.startswith(('rerun_', 'recover_')) and child.name.split('_', 1)[0] not in explicit:
                raise ConfigError(f'cannot inspect replay directory: {child}; use --include')
            continue
        try:
            item = _load_candidate(child, root_id)
        except (ConfigError, OSError, IntegrityError) as exc:
            if child.name.split('_', 1)[0] not in explicit:
                raise ConfigError(f'cannot identify replay directory {child}: {exc}; use --include') from exc
            continue
        found[item[0]].append(item)
    chosen = {}
    for mode in ('rerun', 'recover'):
        if mode in explicit:
            chosen[mode] = explicit[mode]
            continue
        rows = sorted(found[mode], key=lambda item: item[4], reverse=True)
        if len(rows) > 1 and rows[0][4] == rows[1][4]:
            raise ConfigError(f'cannot identify latest {mode}; specify its directory with --include')
        if rows:
            chosen[mode] = rows[0]
    return chosen


def rebuild(source, original_plan, original_state, output_dir=None, includes=()):
    if not is_regression(original_plan) or original_plan.get('source'):
        raise ConfigError('rebuild summary requires the original regression directory')
    source_root = Path(original_plan['policy']['record_dir']).parent
    chosen = _choose(source_root, original_plan['id'], includes)
    merged = copy.deepcopy(original_state)
    merged['report_errors'] = []
    merged['phase'] = 'summary'
    task_index = {task['id']: index for index, task in enumerate(merged['tasks'])}
    selection = {}
    explicit_paths = {Path(path).absolute().parent if Path(path).name == 'report'
                      else Path(path).absolute() for path in includes}
    for mode, (_, path, plan, state, ended) in chosen.items():
        for task in state['tasks']:
            parent = task.get('parent') or {}
            key = parent.get('task', task['id'])
            if key not in task_index:
                continue
            original = merged['tasks'][task_index[key]]
            if (task['case'], task['seed'], task['submit_name']) != (
                    original['case'], original['seed'], original['submit_name']):
                continue  # A changed seed is supplementary, not a replacement.
            if key in selection and ended == selection[key][0] and path != selection[key][2]:
                previous_explicit = selection[key][2] in explicit_paths
                current_explicit = path in explicit_paths
                if previous_explicit == current_explicit:
                    raise ConfigError(f'cannot determine latest result for task {key}; specify one replay with --include')
                if not current_explicit:
                    continue
            if key not in selection or ended >= selection[key][0]:
                selection[key] = (ended, mode, path, task)
    for key, (_, mode, path, task) in selection.items():
        merged['tasks'][task_index[key]] = copy.deepcopy(task)
        merged['tasks'][task_index[key]]['summary_source'] = {'mode': mode, 'record_dir': str(path)}
    merged = assessed(merged)
    name = directory_name(output_dir, 'report')
    target = source_root / name
    if output_dir is None:
        from nsim.replay_paths import directory_name as fresh_name
        for _ in range(10):
            try:
                target.mkdir(exist_ok=False)
                break
            except FileExistsError:
                target = source_root / fresh_name(None, 'report')
        else:
            raise ConfigError('cannot allocate a unique rebuilt report directory')
    else:
        target.mkdir(exist_ok=False)
    sources = {'original': str(source_root), **{mode: str(row[1]) for mode, row in chosen.items()}}
    manifest = {'schema_version': 1, 'original_id': original_plan['id'], 'sources': sources,
                'source_hashes': {label: digest(Path(path) / 'report' / 'final.json')
                                  for label, path in sources.items()},
                'task_sources': {key: {'mode': row[1], 'record_dir': str(row[2])}
                                 for key, row in selection.items()}}
    notice = '汇总原回归与最新的 rerun/recover；来源：' + ', '.join(f'{key}={value}' for key, value in sources.items())
    atomic_write(target / 'summary.json', json.dumps(manifest, indent=2, ensure_ascii=False).encode())
    atomic_write(target / 'state.json', json.dumps(merged, indent=2, ensure_ascii=False).encode())
    atomic_write(target / 'report.md', markdown(merged).encode())
    atomic_write(target / 'report.html', html_report(merged, notice=notice))
    issues = [str(task['evidence_error']) for task in merged['tasks'] if task.get('evidence_error')]
    return target / 'report.html', merged, issues
