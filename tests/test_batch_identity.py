"""Readable batch names preserve identity, output isolation and old records."""

from datetime import datetime
import json
from pathlib import Path
import shlex
from unittest.mock import patch
from uuid import UUID

from test_regression import Fixture
from nsim.batch import new_batch_id, prepare_batch, run_batch
from nsim.cli import build_parser
from nsim.reports import load_final


class BatchIdentityTests(Fixture):
    def setUp(self):
        super().setUp()
        self.list = self.project / 'nightly.list'
        self.list.write_text(shlex.join(['-sub', str(self.submit), '-l', str(self.cases),
                                        '-seed', '42']) + '\n')
        self.args = build_parser().parse_args(['regr', '-rl', str(self.list),
            '--project', str(self.project), '-o', str(self.out), '--dry-run'])
        clock = patch('nsim.batch.datetime')
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.clock.now.return_value = datetime(2026, 10, 29, 15, 30, 0)

    def test_readable_id_propagates_to_directories_records_and_reports(self):
        batch, entries = prepare_batch(self.args)
        self.assertTrue(batch['id'].startswith('nightly_20261029T153000_'))
        journal, state = run_batch(batch, entries)
        self.assertEqual(journal, self.out / 'regr_batch_dir' / batch['id'] / 'report')
        for filename in ('plan.json', 'state.json', 'live.json'):
            self.assertEqual(json.loads((journal / filename).read_text())['id'], batch['id'])
        for filename in ('report.md', 'report.html', 'dashboard.html'):
            self.assertIn(batch['id'], (journal / filename).read_text())
        for entry, row in zip(entries, state['regressions']):
            self.assertEqual(entry['plan']['batch']['id'], batch['id'])
            child = json.loads((Path(row['record_dir']) / 'plan.json').read_text())
            self.assertEqual(child['batch']['id'], batch['id'])
        self.assertEqual(load_final(journal)[0]['id'], batch['id'])

    def test_same_list_in_same_second_keeps_full_unique_uuids(self):
        batches = [prepare_batch(self.args) for _ in range(2)]
        first, second = [batch['id'] for batch, _ in batches]
        self.assertEqual(first.rsplit('_', 1)[0], second.rsplit('_', 1)[0])
        self.assertNotEqual(first, second)
        for batch, entries in batches:
            suffix = batch['id'].rsplit('_', 1)[1]
            self.assertEqual(len(suffix), 32)
            self.assertEqual(UUID(hex=suffix).version, 4)
            journal, _ = run_batch(batch, entries)
            self.assertEqual(load_final(journal)[0]['id'], batch['id'])
        self.assertEqual(len(list((self.out / 'regr_batch_dir').iterdir())), 2)

    def test_list_labels_are_bounded_safe_filename_components(self):
        for filename, expected in (
            ('nightly smoke;$(echo x).list', 'nightly-smoke-echo-x'),
            ('nightly.regr.list', 'nightly.regr'),
            ('nightly_smoke.list', 'nightly_smoke'),
            ('.hidden.list', 'hidden'),
            ('夜间回归.list', 'batch'),
            ('---.list', 'batch'),
            ('n' * 200 + '.list', 'n' * 48),
        ):
            with self.subTest(filename=filename):
                batch_id = new_batch_id(Path(filename))
                self.assertEqual(batch_id.rsplit('_', 2)[0], expected)
                self.assertRegex(batch_id, r'^[A-Za-z0-9_.\-]+$')
                self.assertLess(len(batch_id.encode()), 128)

    def test_existing_batch_directory_is_never_reused(self):
        batch, entries = prepare_batch(self.args)
        journal, _ = run_batch(batch, entries)
        original = (journal / 'final.json').read_bytes()
        with patch('nsim.service.run') as runner, self.assertRaises(FileExistsError):
            run_batch(batch, entries)
        runner.assert_not_called()
        self.assertEqual((journal / 'final.json').read_bytes(), original)

    def test_legacy_uuid_batch_records_still_load(self):
        batch, entries = prepare_batch(self.args)
        batch['id'] = '8c4654ad324e4cc689f967769fc054e6'
        for entry in entries:
            entry['plan']['batch']['id'] = batch['id']
        journal, _ = run_batch(batch, entries)
        plan, state = load_final(journal.parent)
        self.assertEqual(plan['id'], batch['id'])
        self.assertEqual(state['id'], batch['id'])
