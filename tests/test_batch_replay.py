"""Whole-batch replay preserves row membership, selection, and original results."""

import json
from pathlib import Path
import shlex
from unittest.mock import patch

from test_regression import Fixture
from nsim.batch import prepare_batch, run_batch
from nsim.cli import build_parser
from nsim.config import ConfigError
from nsim.reports import load_final
from nsim.service import replay
from nsim.storage import digest, IntegrityError


class BatchReplayTests(Fixture):
    def launch_batch(self, rows=None, dry_run=False):
        self.cases.write_text('smoke\nfast\n')
        listing = self.project / 'nightly.list'
        common = ['-sub', str(self.submit), '-l', str(self.cases)]
        rows = rows or [common + ['-seed', '42'], common + ['-seed', '43']]
        listing.write_text('\n'.join(shlex.join(row) for row in rows) + '\n')
        args = build_parser().parse_args(['regr', '-rl', str(listing), '--project', str(self.project),
                                         '-o', str(self.out), *(['--dry-run'] if dry_run else [])])
        batch, entries = prepare_batch(args)
        return run_batch(batch, entries)

    def test_batch_rerun_uses_current_inputs_and_exact_failed_pairs_in_each_row(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        source, old = self.launch_batch()
        before = {Path(row['record_dir']) / 'final.json': digest(Path(row['record_dir']) / 'final.json')
                  for row in old['regressions']}
        before[source / 'final.json'] = digest(source / 'final.json')
        self.data['fake'] = {}
        self.save_submit()
        self.cases.write_text('smoke\nfast\nnew_case\n')
        target, state = replay(source.parent, jobs=2)
        self.assertTrue(state['passed'], state)
        self.assertEqual(state['task_count'], 2)
        self.assertEqual(target.parent.parent, source.parent)
        self.assertEqual(len(state['regressions']), 2)
        plan, checked = load_final(target.parent)
        self.assertTrue(checked['passed'])
        self.assertEqual(plan['source']['id'], old['id'])
        for entry, prior in zip(plan['entries'], old['regressions']):
            child = entry['plan']
            self.assertEqual([(t['case'], t['seed']) for t in child['tasks']], [('smoke', 41 + entry['index'])])
            self.assertEqual(child['source']['id'], prior['id'])
            self.assertEqual(child['batch']['id'], plan['id'])
            self.assertEqual(entry['settings']['jobs'], 2)
        self.assertEqual(before, {path: digest(path) for path in before})
        self.assertFalse(load_final(source)[1]['passed'])

    def test_recover_batch_uses_historical_inputs_and_can_select_another_root(self):
        source, old = self.launch_batch()
        for row in old['regressions']:
            _, child = load_final(Path(row['record_dir']))
            Path(child['tasks'][0]['attempts'][0]['result']).write_text('damaged')
        target, state = replay(source, recovery=True, roots=[self.backup])
        self.assertTrue(state['passed'], state)
        self.assertEqual(state['task_count'], 2)
        self.assertEqual(target.parent.parent, self.backup / 'regr_batch_dir' / source.parent.name)
        self.assertTrue(load_final(target)[1]['passed'])
        self.assertTrue(all(self.backup in Path(row['record_dir']).parents for row in state['regressions']))

    def test_repeated_batch_rerun_keeps_original_anchor_and_row_duplicates(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        row = ['-sub', str(self.submit), '-l', str(self.cases), '-seed', '42']
        source, _ = self.launch_batch(rows=[row, row])
        first, _ = replay(source)
        second, state = replay(first)
        self.assertFalse(state['passed'])
        self.assertEqual(state['task_count'], 2)
        self.assertEqual(first.parent.parent, source.parent)
        self.assertEqual(second.parent.parent, source.parent)
        self.assertEqual(len({row['id'] for row in state['regressions']}), 2)
        self.assertEqual(load_final(second)[0]['source']['id'], load_final(first)[0]['id'])

    def test_skipped_rows_are_recorded_and_all_no_match_does_not_create_directories(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        passing = self.project / 'passing.list'
        passing.write_text('fast\n')
        rows = [['-sub', str(self.submit), '-l', str(path), '-seed', '42'] for path in (passing, self.cases)]
        source, _ = self.launch_batch(rows=rows)
        target, state = replay(source)
        self.assertEqual(len(state['regressions']), 1)
        self.assertEqual(state['regressions'][0]['line'], 2)
        self.assertEqual(len(state['skipped']), 1)
        self.assertIn('1 轮无匹配任务', (target / 'report.html').read_text())
        before = set(source.parent.iterdir())
        result, _ = replay(source, recovery=True)
        self.assertIsNone(result)
        self.assertEqual(set(source.parent.iterdir()), before)

    def test_all_children_are_planned_before_first_execution(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        other = self.project / 'other.json'
        other.write_text(json.dumps(dict(self.data, name='other')))
        rows = [['-sub', str(submit), '-l', str(self.cases), '-seed', '42'] for submit in (self.submit, other)]
        source, _ = self.launch_batch(rows=rows)
        other.unlink()
        with patch('nsim.service.run') as execution, self.assertRaises((ConfigError, OSError)):
            replay(source)
        execution.assert_not_called()
        self.assertFalse(list(source.parent.glob('rerun_*')))

    def test_recovery_batch_rejects_changed_inputs_and_named_collision_preserves_results(self):
        source, old = self.launch_batch()
        for row in old['regressions']:
            _, child = load_final(Path(row['record_dir']))
            Path(child['tasks'][0]['attempts'][0]['result']).write_text('damaged')
        first, _ = replay(source, recovery=True, output_dir='recovered')
        before = digest(first / 'final.json')
        with self.assertRaises(FileExistsError):
            replay(source, recovery=True, output_dir='recovered')
        self.assertEqual(digest(first / 'final.json'), before)
        (self.project / 'input.txt').write_text('changed')
        with self.assertRaises(IntegrityError):
            replay(source, recovery=True)

    def test_preview_and_damaged_control_batches_are_rejected(self):
        source, _ = self.launch_batch(dry_run=True)
        with self.assertRaisesRegex(ConfigError, 'dry-run'):
            replay(source)
        source, _ = self.launch_batch()
        (source / 'state.json').write_text('{}')
        with self.assertRaises(IntegrityError):
            replay(source)
