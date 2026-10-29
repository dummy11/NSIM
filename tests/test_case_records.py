"""Consolidated case records: durability, replay, compatibility and I/O bounds."""

from collections import Counter
import copy
import errno
import json
from pathlib import Path
from unittest.mock import patch

from test_regression import Fixture
from nsim.case_records import checksum
from nsim.layout import simulation_directory
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import replay, run
from nsim.storage import IntegrityError, atomic_write, digest, preflight, write_json


class CaseRecordTests(Fixture):
    def one(self, phase='run'):
        return create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42], phase=phase)

    def test_single_record_replaces_metadata_and_has_bounded_writes(self):
        writes = Counter()
        def write(path, data):
            writes[Path(path)] += 1
            return atomic_write(path, data)
        with patch('nsim.storage.atomic_write', side_effect=write):
            record, state = run(self.one(), [self.out])
        self.assertTrue(state['passed'])
        for name in ('plan.json', 'state.json', 'final.json', 'analysis.json', 'attempt.json', 'records'):
            self.assertFalse((record / name).exists(), name)
        value = json.loads((record / 'run.json').read_text())
        self.assertEqual(value['analysis']['verdict'], 'PASS')
        self.assertTrue(value['terminal'])
        self.assertEqual(len(value['task']['attempts']), 1)
        self.assertEqual(writes[record / 'run.json'], 3)  # start, attempt, final publication
        self.assertEqual(load_final(record)[0]['id'], state['id'])
        self.assertTrue(load_final(record)[1]['passed'])

    def test_single_failure_replay_and_sim_reuse(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        build_plan = create_plan(self.project, submit_paths=[self.submit], phase='com')
        build, _ = run(build_plan, [self.out])
        before = digest(build / 'build.json')
        record, old = run(self.one('sim'), [self.out])
        target, new = replay(record, seed=43)
        self.assertEqual(new['phase'], 'sim')
        self.assertEqual(target.name, 'smoke_43')
        self.assertEqual(new['tasks'][0]['parent']['run'], old['id'])
        self.assertEqual(digest(build / 'build.json'), before)
        self.assertEqual(load_final(target)[1]['tasks'][0]['effective_verdict'], 'FAIL')

    def test_analysis_damage_is_incomplete_and_recoverable(self):
        record, _ = run(self.one(), [self.out])
        value = json.loads((record / 'run.json').read_text())
        value['analysis']['verdict'] = 'FAIL'
        write_json(record / 'run.json', value)
        self.assertEqual(load_final(record)[1]['tasks'][0]['effective_verdict'], 'INCOMPLETE')
        _, recovered = replay(record, recovery=True)
        self.assertTrue(recovered['passed'])
        self.assertTrue(load_final(record)[1]['passed'])

    def test_control_damage_and_interruption_refuse_replay(self):
        record, _ = run(self.one(), [self.out])
        saved = json.loads((record / 'run.json').read_text())
        changed = copy.deepcopy(saved)
        changed['plan']['tasks'][0]['seed'] = 99
        write_json(record / 'run.json', changed)
        with self.assertRaisesRegex(IntegrityError, 'control checksum'):
            replay(record)
        changed = copy.deepcopy(saved)
        changed['terminal'] = False
        changed['state']['terminal'] = False
        changed['checksums']['control'] = checksum({k: v for k, v in changed.items() if k not in ('analysis', 'checksums')})
        write_json(record / 'run.json', changed)
        with self.assertRaisesRegex(IntegrityError, 'terminal completion'):
            replay(record)

    def test_regression_writes_each_case_twice_and_checkpoints_full_state(self):
        self.cases.write_text('\n'.join('case_%d' % i for i in range(16)))
        self.data['fake'] = {'outcomes': {'case_3': 'fail'}}
        self.save_submit()
        writes = Counter()
        def write(path, data):
            writes[Path(path)] += 1
            return atomic_write(path, data)
        plan = self.plan()
        with patch('nsim.storage.atomic_write', side_effect=write):
            journal, state = run(plan, [self.out], jobs=4)
        self.assertEqual(writes[journal / 'plan.json'], 1)
        self.assertGreaterEqual(writes[journal / 'state.json'], len(plan['tasks']) + 2)
        self.assertLessEqual(writes[journal / 'state.json'], 2 * len(plan['tasks']) + 2)
        self.assertEqual(writes[journal / 'final.json'], 1)
        self.assertFalse((journal / 'records').exists())
        for task in plan['tasks']:
            folder = simulation_directory(self.out, plan, task)
            self.assertEqual(writes[folder / 'run.json'], 2)
            value = json.loads((folder / 'run.json').read_text())
            self.assertNotIn('plan', value)
            self.assertNotIn('state', value)
            self.assertFalse((folder / 'analysis.json').exists())
            self.assertFalse((folder / 'attempt.json').exists())
        failed = next(t for t in plan['tasks'] if t['case'] == 'case_3')
        source = simulation_directory(self.out, plan, failed)
        old_hash = digest(journal / 'final.json')
        new, retried = replay(source)
        self.assertNotEqual(new, journal)
        self.assertEqual([t['case'] for t in retried['tasks']], ['case_3'])
        self.assertEqual(digest(journal / 'final.json'), old_hash)
        self.assertIn('run.json', (new / 'report.html').read_text())

    def test_missing_case_record_recovers_from_shared_plan(self):
        plan = self.plan()
        journal, _ = run(plan, [self.out])
        source = simulation_directory(self.out, plan, plan['tasks'][0])
        (source / 'run.json').unlink()
        self.assertFalse(load_final(journal)[1]['passed'])
        _, state = replay(journal, recovery=True)
        self.assertEqual(len(state['tasks']), 1)
        self.assertTrue(state['passed'])

    def test_damaged_analysis_recovers_through_regression_case_directory(self):
        plan = self.plan()
        journal, _ = run(plan, [self.out])
        source = simulation_directory(self.out, plan, plan['tasks'][0])
        value = json.loads((source / 'run.json').read_text())
        value['analysis']['verdict'] = 'FAIL'
        write_json(source / 'run.json', value)
        selected, state = load_final(source)
        self.assertEqual(len(selected['tasks']), 1)
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'INCOMPLETE')
        _, recovered = replay(source, recovery=True)
        self.assertEqual(len(recovered['tasks']), 1)
        self.assertTrue(recovered['passed'])
        self.assertFalse(load_final(journal)[1]['passed'])

    def test_missing_analysis_recovers_but_unbound_reference_is_rejected(self):
        record, _ = run(self.one(), [self.out])
        value = json.loads((record / 'run.json').read_text())
        del value['analysis']
        write_json(record / 'run.json', value)
        self.assertEqual(load_final(record)[1]['tasks'][0]['effective_verdict'], 'INCOMPLETE')
        self.assertTrue(replay(record, recovery=True)[1]['passed'])
        plan = self.plan()
        run(plan, [self.out])
        source = simulation_directory(self.out, plan, plan['tasks'][0])
        value = json.loads((source / 'run.json').read_text())
        value['seed'] = 99
        value['checksums']['control'] = checksum({k: v for k, v in value.items() if k not in ('analysis', 'checksums')})
        write_json(source / 'run.json', value)
        with self.assertRaisesRegex(IntegrityError, 'reference does not match'):
            replay(source, recovery=True)

    def test_single_failure_requires_explicit_recovery_root(self):
        with patch('nsim.runner.preflight', side_effect=OSError(errno.EIO, 'execution failed')):
            record, state = run(self.one(), [self.out])
        self.assertFalse(state['passed'])
        self.assertEqual(len(state['tasks'][0]['attempts']), 1)
        backup, recovered = replay(record, roots=[self.backup], recovery=True)
        self.assertTrue(recovered['passed'])
        self.assertTrue(load_final(backup)[1]['passed'])
        self.assertEqual(recovered['tasks'][0]['parent']['attempt'], state['tasks'][0]['attempts'][0]['id'])

    def test_legacy_single_record_can_be_read_and_rerun(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        record, _ = run(self.one(), [self.out])
        plan, state = load_final(record)
        value = json.loads((record / 'run.json').read_text())
        legacy = {k: value[k] for k in ('record_dir', 'run_id', 'task_id', 'case', 'seed', 'submit', 'build_dir', 'build_id', 'build_instance', 'mode', 'case_config')}
        legacy['schema_version'] = 1
        saved = write_json(record / 'run.json', legacy)
        state['tasks'][0]['attempts'][-1]['evidence'][str(record / 'run.json')] = saved
        write_json(record / 'plan.json', plan)
        write_json(record / 'state.json', state)
        write_json(record / 'final.json', dict(schema_version=2, terminal=True,
                   files={n: digest(record / n) for n in ('plan.json', 'state.json')}))
        self.assertEqual(load_final(record)[0]['id'], plan['id'])
        _, retried = replay(record)
        self.assertEqual(retried['tasks'][0]['parent']['run'], plan['id'])
        self.assertFalse((record / 'plan.json').exists())
        self.assertEqual(json.loads((record / 'run.json').read_text())['schema_version'], 2)
