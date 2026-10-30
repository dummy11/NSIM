"""Readable regression IDs keep list provenance, isolation and replay links."""

from datetime import datetime
import json
from pathlib import Path
import shlex
from unittest.mock import patch
from uuid import UUID

from test_regression import Fixture
from nsim.batch import prepare_batch, run_batch
from nsim.cli import build_parser
from nsim.identifiers import new_regression_id
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import replay, run
from nsim.storage import digest


class RegressionIdentityTests(Fixture):
    def setUp(self):
        super().setUp()
        clock = patch('nsim.identifiers.datetime')
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.clock.now.return_value = datetime(2026, 10, 29, 15, 30, 0)

    def test_same_second_regressions_have_unique_ids_and_independent_records(self):
        plans = [self.plan(phase='regr') for _ in range(2)]
        self.assertNotEqual(plans[0]['id'], plans[1]['id'])
        for plan in plans:
            self.assertTrue(plan['id'].startswith('smoke_20261029T153000_'))
            suffix = plan['id'].rsplit('_', 1)[1]
            self.assertEqual(len(suffix), 32)
            self.assertEqual(UUID(hex=suffix).version, 4)
            record, state = run(plan, [self.out])
            self.assertTrue(state['passed'])
            self.assertEqual(record, self.out / 'regr_dir' / plan['id'] / 'report')
            for filename, field in (('plan.json', 'id'), ('state.json', 'id'), ('live.json', 'run_id')):
                self.assertEqual(json.loads((record / filename).read_text())[field], plan['id'])
            for filename in ('report.md', 'report.html', 'dashboard.html'):
                self.assertIn(plan['id'], (record / filename).read_text())
            self.assertEqual(load_final(record)[0]['id'], plan['id'])
        self.assertEqual(len(list((self.out / 'regr_dir').iterdir())), 2)

    def test_multiple_caselists_use_input_order_and_deduplicate_resolved_paths(self):
        other = self.project / 'basic.list'
        other.write_text('fast\n')
        plan = create_plan(self.project, caselist=[self.cases, other, self.cases],
                           submit_paths=[self.submit], seeds=[42], phase='regr')
        self.assertTrue(plan['id'].startswith('smoke-basic_20261029T153000_'))
        self.assertEqual(plan['caselists'], [str(self.cases), str(other)])
        record, _ = run(plan, [self.out], dry_run=True)
        saved = json.loads((record / 'plan.json').read_text())
        self.assertEqual(saved['caselists'], plan['caselists'])

    def test_long_combined_list_names_are_bounded_without_shortening_uuid(self):
        paths = [self.project / (letter * 35 + '.list') for letter in ('a', 'b')]
        for path in paths:
            path.write_text('smoke\n')
        plan = create_plan(self.project, caselist=paths, submit_paths=[self.submit],
                           seeds=[42], phase='regr')
        label, stamp, suffix = plan['id'].rsplit('_', 2)
        self.assertEqual(label, 'a' * 35 + '-' + 'b' * 12)
        self.assertEqual(stamp, '20261029T153000')
        self.assertEqual(len(suffix), 32)
        self.assertEqual(len(plan['id']), 97)
        record, _ = run(plan, [self.out], dry_run=True)
        self.assertEqual(json.loads((record / 'plan.json').read_text())['caselists'],
                         [str(path) for path in paths])
        for names in ([], ['夜间回归.list'], ['---.list']):
            self.assertTrue(new_regression_id(names).startswith('regr_20261029T153000_'))

    def test_batch_rows_use_their_own_caselist_names(self):
        other = self.project / 'basic.list'
        other.write_text('fast\n')
        source = self.project / 'nightly.list'
        source.write_text('\n'.join(shlex.join(['-sub', str(self.submit), '-l', str(path),
                                                '-seed', '42']) for path in (self.cases, other)))
        args = build_parser().parse_args(['regr', '-rl', str(source), '--project',
                                         str(self.project), '-o', str(self.out), '--dry-run'])
        batch, entries = prepare_batch(args)
        self.assertTrue(batch['id'].startswith('nightly_20261029T153000_'))
        journal, state = run_batch(batch, entries)
        for label, entry, row in zip(('smoke', 'basic'), entries, state['regressions']):
            self.assertTrue(row['id'].startswith(label + '_20261029T153000_'))
            self.assertEqual(row['id'], entry['plan']['id'])
            child = json.loads((Path(row['record_dir']) / 'plan.json').read_text())
            self.assertEqual(child['batch']['id'], batch['id'])
        self.assertEqual(load_final(journal)[0]['id'], batch['id'])

    def test_legacy_uuid_rerun_uses_saved_list_names_and_preserves_source(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        plan = self.plan(phase='regr')
        plan['id'] = '8c4654ad324e4cc689f967769fc054e6'
        record, _ = run(plan, [self.out])
        original_digest = digest(record / 'final.json')
        retried, state = replay(record)
        saved = json.loads((retried / 'plan.json').read_text())
        self.assertTrue(saved['id'].startswith('rerun_20261029T153000_'))
        self.assertNotEqual(retried, record)
        self.assertEqual(saved['source']['id'], plan['id'])
        self.assertEqual(saved['source']['mode'], 'rerun')
        self.assertEqual(saved['caselists'], [str(self.cases)])
        self.assertEqual(len(state['tasks']), 1)
        self.assertEqual(state['tasks'][0]['seed'], 42)
        self.assertEqual(state['tasks'][0]['parent']['run'], plan['id'])
        self.assertEqual(digest(record / 'final.json'), original_digest)
        self.assertEqual(load_final(record)[0]['id'], plan['id'])

    def test_recovery_keeps_label_but_gets_a_fresh_id_and_timestamp(self):
        record, state = run(self.plan(phase='regr'), [self.out])
        Path(state['tasks'][0]['attempts'][0]['result']).write_text('broken')
        self.clock.now.return_value = datetime(2026, 10, 30, 10, 0, 0)
        recovered, result = replay(record, roots=[self.backup], recovery=True)
        saved = json.loads((recovered / 'plan.json').read_text())
        self.assertTrue(result['passed'])
        self.assertTrue(saved['id'].startswith('recover_20261030T100000_'))
        self.assertEqual(saved['source']['id'], state['id'])
        self.assertEqual(saved['source']['mode'], 'recover')

    def test_regression_without_caselist_has_fallback_and_single_case_keeps_uuid(self):
        combined = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit],
                               seeds=[42, 43])
        self.assertTrue(combined['id'].startswith('regr_20261029T153000_'))
        for options in ({'cases': ['smoke'], 'seeds': [42]}, {'phase': 'com'}):
            plan = create_plan(self.project, submit_paths=[self.submit], **options)
            self.assertRegex(plan['id'], r'^[a-f0-9]{32}$')
