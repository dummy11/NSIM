"""Plan batch replay from recorded child membership, then execute sequentially."""

import copy
from pathlib import Path

from nsim.batch import BatchDirectoryExists, run_batch
from nsim.config import ConfigError, integer, number
from nsim.layout import validate_layout
from nsim.replay_paths import directory_name
from nsim.reports import load_final


def replay_batch(source, original, state, roots=None, recovery=False, seed=None,
                 output_dir=None, rebuild=False, execution_options=None, plusargs=(), **overrides):
    from nsim.service import prepare_replay, validate_roots
    if not recovery and (roots is not None or seed is not None):
        raise ConfigError('rerun preserves case/seed and the original output root; overrides are not supported')
    mode = 'recover' if recovery else 'rerun'
    name = directory_name(output_dir, mode)
    # Explicit names are directory labels; use an independent unique batch ID.
    batch_id = directory_name(None, mode) if output_dir is not None else name
    entries, skipped = [], []
    for entry, row in zip(original['entries'], state['regressions']):
        if row['status'] == 'preview':
            raise ConfigError('cannot rerun/recover a batch containing dry-run regressions')
        record = Path(row['record_dir'])
        child_plan, child_state = load_final(record)
        prepared = prepare_replay(record, child_plan, child_state, roots=roots, recovery=recovery,
                                  seed=seed, rebuild=rebuild, execution_options=execution_options,
                                  plusargs=plusargs, **overrides)
        if prepared is None:
            skipped.append(dict(id=row['id'], line=row['line'], record_dir=str(record),
                                reason='no matching failed tasks'))
            continue
        plan, child_roots, settings = prepared
        child_roots = validate_roots(plan, child_roots)
        validate_layout(plan, child_roots)
        integer(settings['jobs'], 'jobs', 1)
        integer(settings['min_free_bytes'], 'min_free_bytes')
        integer(settings['min_free_inodes'], 'min_free_inodes')
        integer(settings['retries'], 'retries')
        number(settings['retry_seconds'], 'retry_seconds')
        if settings['retries'] or settings['retry_seconds']:
            raise ConfigError('automatic infrastructure retries are disabled; retries must be 0')
        for submit in plan['submits']:
            if submit.get('unresolved_tools'):
                raise ConfigError('cannot execute plan with unresolved tools')
            if submit['adapter'] == 'vcs':
                raise ConfigError('native VCS execution is not yet enabled; use --dry-run')
        plan['batch'] = dict(id=batch_id, list=original['list'], line=entry['line'],
                             index=len(entries) + 1, argv=entry['argv'], cwd=original['cwd'])
        settings['dry_run'] = False
        entries.append(dict(index=len(entries) + 1, line=entry['line'], argv=entry['argv'], plan=plan,
                            roots=[str(root) for root in child_roots], settings=settings,
                            source=dict(id=row['id'], record_dir=str(record), index=entry['index'])))
    if not entries:
        return None, dict(passed=state['passed'], tasks=[], message='no matching failed tasks')
    selected_roots = validate_roots(entries[0]['plan'], roots or original['output_roots'])
    for entry in entries[1:]:
        validate_roots(entry['plan'], selected_roots)
    parent = original.get('execution', {}).get('replay_parent', str(Path('regr_batch_dir') / original['id']))
    batch = {key: copy.deepcopy(original[key]) for key in
             ('schema_version', 'kind', 'cwd', 'list', 'list_sha256', 'snapshot')}
    batch.update(id=batch_id, output_roots=[str(root) for root in selected_roots], entries=entries,
                 execution=dict(replay_parent=parent, replay_dir=name), skipped=skipped,
                 source=dict(journal=str(source), id=original['id'], mode=mode,
                             root_id=(original.get('source') or {}).get('root_id', original['id'])))
    for _ in range(10):
        try:
            return run_batch(batch, entries)
        except BatchDirectoryExists:
            if output_dir is not None:
                raise
            name = directory_name(None, mode)
            batch['id'] = name
            batch['execution']['replay_dir'] = name
            for entry in entries:
                entry['plan']['batch']['id'] = name
    raise ConfigError('cannot allocate a unique batch replay directory')
