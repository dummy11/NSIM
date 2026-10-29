"""Regression-list interoperability, independent evidence and batch lifecycle."""

import copy
import json
from pathlib import Path
import shlex
import subprocess
import sys
from unittest.mock import patch

from test_regression import Fixture
from nsim.batch import prepare_batch, run_batch
from nsim.cli import build_parser, prepare_execution
from nsim.config import ConfigError
from nsim.layout import build_directory, simulation_directory
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import replay, run
from nsim.storage import IntegrityError, digest, write_json


class BatchTests(Fixture):
    def setUp(self):
        super().setUp()
        self.cases.write_text('smoke\nfast\n')
        self.list = self.project / 'nightly.list'
        self.parser = build_parser()

    def row(self, *options):
        return ['-sub', str(self.submit), '-l', str(self.cases), '-seed', '42', *map(str, options)]

    def prepare(self, rows, *options):
        self.list.write_text('\n'.join(shlex.join(row) for row in rows) + '\n')
        args = self.parser.parse_args(['regr', '-rl', str(self.list), '--project', str(self.project),
                                      '-o', str(self.out), *map(str, options)])
        return prepare_batch(args)

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, '-m', 'nsim', *map(str, args)], cwd=self.project,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else result

    def test_direct_and_list_share_expansion_and_effective_arguments(self):
        self.cases.write_text('smoke +TRACE=1\nfast\n')
        other = self.project / 'other.list'
        other.write_text('fast\nslow\n')
        second = self.project / 'second.json'
        write_json(second, dict(self.data, name='second'))
        row = ['-sub', str(self.submit), str(second), '-l', str(self.cases), str(other),
               '-seed', '42', '43', '-jobs', '3', '--plusargs', '+TRACE=2', '+TEXT=hello # world']
        direct_args = self.parser.parse_args(['regr', '--project', str(self.project), '-o', str(self.out), *row])
        direct, roots, settings = prepare_execution(direct_args)
        batch, entries = self.prepare([row])
        child = entries[0]['plan']
        self.assertEqual(direct['tasks'], child['tasks'])
        self.assertEqual(settings, entries[0]['settings'])
        self.assertEqual(len(child['tasks']), 12)
        self.assertEqual(child['seed_selection']['values'], [42, 43])
        for task in child['tasks']:
            self.assertIn('+TRACE=2', task['case_config']['argv'])
            self.assertEqual(task['case_config']['origins']['+TRACE'], 'cli')
        self.assertEqual(child['batch']['argv'], row)
        self.assertEqual(child['snapshots'][str(self.list)], self.list.read_text())

    def test_repeated_rows_have_independent_builds_reports_and_seed_directories(self):
        batch, entries = self.prepare([self.row('--plusargs', '+TRACE=0'), self.row('--plusargs', '+TRACE=1')])
        (self.out / 'regr_dir' / 'old' / 'report').mkdir(parents=True)
        write_json(self.out / 'regr_dir' / 'old' / 'report' / 'state.json', {'passed': True, 'tasks': [1] * 999})
        journal, state = run_batch(batch, entries)
        self.assertTrue(state['passed'])
        self.assertEqual(journal.parent.parent, self.out / 'regr_batch_dir')
        self.assertEqual(state['task_count'], 4)
        self.assertEqual(state['totals']['passed'], 4)
        self.assertEqual(state['regression_counts']['passed'], 2)
        self.assertFalse((self.out / 'base').exists())
        build_ids = []
        build_instances = []
        for entry, row in zip(entries, state['regressions']):
            record = Path(row['record_dir'])
            self.assertEqual(record.name, 'report')
            self.assertEqual(record.parent.parent, self.out / 'regr_dir')
            self.assertTrue((record / 'live.json').is_file())
            self.assertTrue((record / 'dashboard.html').is_file())
            self.assertTrue((record / 'report.html').is_file())
            self.assertTrue(load_final(record.parent)[1]['passed'])
            build = build_directory(self.out, entry['plan'], entry['plan']['tasks'][0])
            manifest = json.loads((build / 'build.json').read_text())
            build_ids.append(manifest['build_id'])
            build_instances.append(manifest['id'])
            for task in entry['plan']['tasks']:
                sim = simulation_directory(self.out, entry['plan'], task)
                log = (sim / 'run.log').read_text()
                self.assertIn(entry['plan']['plusargs'][0], log)
                self.assertEqual(json.loads((sim / 'run.json').read_text())['record_dir'], str(record))
        self.assertEqual(build_ids[0], build_ids[1])
        self.assertNotEqual(build_instances[0], build_instances[1])
        self.assertTrue(load_final(journal.parent)[1]['passed'])
        output = self.cli('report', 'show', journal.parent, '--json')
        self.assertEqual(output['totals']['passed'], 4)
        self.assertIn('回归', (journal / 'dashboard.html').read_text())

    def test_new_single_regression_preserves_previous_evidence(self):
        plan = self.plan()
        first, _ = run(plan, [self.out])
        saved = digest(first / 'final.json')
        second, _ = run(self.plan(), [self.out])
        self.assertNotEqual(first, second)
        self.assertEqual(digest(first / 'final.json'), saved)
        self.assertTrue(load_final(first)[1]['passed'])
        with self.assertRaises(FileExistsError):
            run(plan, [self.out])
        self.assertTrue(load_final(first)[1]['passed'])

    def test_failure_continues_and_rerun_preserves_original_batch(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        passed = self.project / 'passing.list'
        passed.write_text('fast\n')
        batch, entries = self.prepare([self.row(), ['-sub', str(self.submit), '-l', str(passed), '-seed', '42']])
        journal, state = run_batch(batch, entries)
        self.assertEqual([r['status'] for r in state['regressions']], ['failed', 'passed'])
        old = Path(state['regressions'][0]['record_dir'])
        old_hash = digest(old / 'final.json')
        new, retried = replay(old.parent)
        self.assertNotEqual(new, old)
        self.assertEqual([t['seed'] for t in retried['tasks']], [42])
        self.assertEqual(digest(old / 'final.json'), old_hash)
        self.assertFalse(load_final(journal)[1]['passed'])
        self.assertEqual(json.loads((new / 'plan.json').read_text())['source_batch']['id'], batch['id'])
        task = entries[0]['plan']['tasks'][0]
        selected, _ = replay(simulation_directory(self.out, entries[0]['plan'], task))
        self.assertEqual(selected.name, 'report')
        self.assertNotEqual(selected, old)

    def test_dry_run_all_rows_and_outer_option_rejection(self):
        self.prepare([self.row('-jobs', '2'), self.row('-jobs', '3')])
        result = self.cli('regr', '-rl', self.list, '-o', self.out, '--dry-run')
        self.assertTrue(result['dry_run'])
        journal = Path(result['journal'])
        state = json.loads((journal / 'state.json').read_text())
        self.assertFalse(state['passed'])
        self.assertEqual(state['regression_counts']['preview'], 2)
        for row in state['regressions']:
            report = Path(row['record_dir'])
            self.assertEqual({p.name for p in report.parent.iterdir()}, {'report'})
            preview = json.loads((report / 'commands.json').read_text())
            self.assertTrue(all(str(report.parent) in t['cwd'] for t in preview['tasks']))
        for options in (['-jobs', '1'], ['-queue', 'normal'], ['-repeat', '1']):
            error = self.cli('regr', '-rl', self.list, '-o', self.out, *options, code=2)
            self.assertIn('inside the list', error.stderr)

    def test_all_rows_prevalidated_before_any_outputs(self):
        invalid = [self.row(), self.row('-repeat', '2')]
        with self.assertRaisesRegex(ConfigError, 'nightly.list:2:'):
            self.prepare(invalid)
        self.assertEqual(list(self.out.iterdir()), [])
        for row in ([self.row(), ['-rl', str(self.list)]],
                    [self.row(), self.row('-jobs', '0')],
                    [self.row(), self.row('--plusargs', '+ntb_random_seed=1')]):
            with self.assertRaises(ConfigError):
                self.prepare(row)
            self.assertEqual(list(self.out.iterdir()), [])

    def test_list_source_change_blocks_execution(self):
        batch, entries = self.prepare([self.row()])
        self.list.write_text('# changed\n')
        with self.assertRaises(IntegrityError):
            run_batch(batch, entries)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_paths_cannot_escape_regression_or_overlap_metadata(self):
        for options in (['-sim-dir', str(self.out / 'base/rtl/sim_dir')],
                        ['-com-dir', '../../../other/com_dir'], ['-sim-dir', '../../../report']):
            with self.assertRaisesRegex(ConfigError, 'stay inside'):
                self.prepare([self.row(*options)])
        self.data['name'] = 'report'
        self.save_submit()
        with self.assertRaisesRegex(ConfigError, 'metadata'):
            self.prepare([self.row()])

    def test_cli_plusargs_reject_conflicts_and_managed_keys(self):
        for args in (['+TRACE=1', '+TRACE=2'], ['+NSIM_RESULT=elsewhere']):
            with self.assertRaises(ConfigError):
                create_plan(self.project, caselist=self.cases, submit_paths=[self.submit], plusargs=args)
        base = create_plan(self.project, caselist=self.cases, submit_paths=[self.submit], seeds=[42])
        changed = create_plan(self.project, caselist=self.cases, submit_paths=[self.submit], seeds=[42], plusargs=['+TRACE=2'])
        self.assertEqual(base['tasks'][0]['build_id'], changed['tasks'][0]['build_id'])

    def test_unresolved_child_stops_dispatch_without_terminal_batch(self):
        batch, entries = self.prepare([self.row(), self.row()])
        plan = entries[0]['plan']
        tasks = [dict(t, status='completed', submit_name='base', attempts=[dict(verdict='LSF_UNKNOWN', terminal=False)])
                 for t in plan['tasks']]
        child = dict(id=plan['id'], terminal=False, passed=False, tasks=tasks,
                     scheduler_jobs={}, scheduler_errors=['unknown job'], report_errors=[])
        with patch('nsim.service.run', return_value=(self.out / 'unused', child)) as runner:
            journal, state = run_batch(batch, entries)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual([r['status'] for r in state['regressions']], ['unresolved', 'not_started'])
        self.assertEqual(state['totals']['unknown'], 2)
        self.assertEqual(state['totals']['pending'], 2)
        self.assertFalse((journal / 'final.json').exists())

    def test_interrupt_preserves_batch_state_and_pending_tasks(self):
        batch, entries = self.prepare([self.row(), self.row()])
        with patch('nsim.service.run', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            run_batch(batch, entries)
        journal = self.out / 'regr_batch_dir' / batch['id'] / 'report'
        state = json.loads((journal / 'state.json').read_text())
        self.assertEqual(state['lifecycle'], 'interrupted')
        self.assertFalse(state['passed'])
        self.assertFalse(state['terminal'])
        self.assertEqual(state['task_count'], 4)
        self.assertFalse((journal / 'final.json').exists())

    def test_batch_validation_detects_missing_or_changed_child_evidence(self):
        batch, entries = self.prepare([self.row()])
        journal, _ = run_batch(batch, entries)
        sim = simulation_directory(self.out, entries[0]['plan'], entries[0]['plan']['tasks'][0])
        (sim / 'run.log').write_text('modified evidence')
        self.assertFalse(load_final(journal)[1]['passed'])

    def test_progress_binds_run_id_and_counts_all_planned_rows(self):
        batch, entries = self.prepare([self.row(), self.row()])
        seen = []
        from nsim.batch import write_json as original
        def write(path, value):
            if Path(path).name == 'live.json' and value.get('kind') == 'batch':
                seen.append(copy.deepcopy(value))
                if value.get('terminal'):
                    self.assertTrue((Path(path).parent / 'final.json').is_file())
            return original(path, value)
        with patch('nsim.batch.write_json', side_effect=write):
            journal, _ = run_batch(batch, entries)
        self.assertEqual(seen[0]['totals']['pending'], 4)
        self.assertTrue(any(s['totals']['passed'] == 2 and not s.get('partial') and s['regressions'][1]['status'] == 'planned' for s in seen))
        self.assertTrue(seen[-1]['passed'])
        self.assertEqual(seen[-1]['regression_counts']['passed'], 2)


    def test_child_progress_does_not_rewrite_full_batch_state(self):
        self.cases.write_text('\n'.join('case_%d' % i for i in range(12)))
        batch, entries = self.prepare([self.row(), self.row()])
        from nsim.batch import write_json as original
        full, brief = [], []
        def write(path, value):
            if Path(path).parent.parent.parent.name == 'regr_batch_dir':
                if Path(path).name == 'state.json':
                    full.append(copy.deepcopy(value))
                elif Path(path).name == 'live.json' and value.get('partial'):
                    brief.append(copy.deepcopy(value))
            return original(path, value)
        with patch('nsim.batch.write_json', side_effect=write):
            record, state = run_batch(batch, entries)
        self.assertTrue(state['passed'])
        self.assertEqual(len(full), 7)  # initial, two row boundaries each, finalizing, final
        self.assertGreater(len(brief), len(full))
        self.assertTrue(all(len(s['regressions']) == 1 for s in brief))
        self.assertTrue(all(s['task_count'] == 24 and s['regression_count'] == 2 for s in brief))
        self.assertTrue(load_final(record)[1]['passed'])

    def test_batch_dashboard_write_failure_does_not_cancel_child(self):
        batch, entries = self.prepare([self.row()])
        from nsim.batch import write_json as original
        def fail_display(path, value):
            if Path(path).name == 'live.json' and value.get('kind') == 'batch':
                raise OSError('batch display unavailable')
            return original(path, value)
        with patch('nsim.batch.write_json', side_effect=fail_display):
            journal, state = run_batch(batch, entries)
        self.assertTrue(state['passed'])
        self.assertTrue((journal / 'final.json').exists())
        self.assertTrue(load_final(journal)[1]['passed'])

    def test_batch_requires_one_explicit_output_root(self):
        batch, entries = self.prepare([self.row()])
        batch['output_roots'] = [str(self.out), str(self.backup)]
        with self.assertRaisesRegex(ConfigError, 'select exactly one output root'):
            run_batch(batch, entries)

    def test_casefold_conflict_in_later_row_is_prevalidated(self):
        other = self.project / 'invalid.list'
        other.write_text('smoke\nSMOKE\n')
        with self.assertRaisesRegex(ConfigError, 'duplicate simulation destinations'):
            self.prepare([self.row(), ['-sub', str(self.submit), '-l', str(other)]])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_final_publication_failure_has_no_batch_pass_or_completion(self):
        batch, entries = self.prepare([self.row()])
        from nsim.batch import write_json as original
        def write(path, value):
            if Path(path).name == 'final.json':
                raise OSError('injected batch publication failure')
            return original(path, value)
        with patch('nsim.batch.write_json', side_effect=write), self.assertRaises(OSError):
            run_batch(batch, entries)
        journal = self.out / 'regr_batch_dir' / batch['id'] / 'report'
        self.assertFalse((journal / 'final.json').exists())
        self.assertFalse(json.loads((journal / 'live.json').read_text())['passed'])
        self.assertFalse((journal / 'report.html').exists())


class BatchLsfTests(Fixture):
    def test_rows_use_their_own_lsf_queue_resources_and_limit(self):
        from test_lsf import LsfTests
        farm = LsfTests()
        farm.setUp()
        self.addCleanup(farm.doCleanups)
        farm.cases.write_text('smoke\nfast\n')
        rows = []
        for queue, jobs in (('short', '1'), ('normal', '2')):
            rows.append(shlex.join(['-sub', str(farm.submit), '-l', str(farm.cases), '-seed', '42',
                                   '-queue', queue, '-jobs', jobs, '--resource', 'select[mem>1024]']))
        source = farm.project / 'batch.list'
        source.write_text('\n'.join(rows))
        args = build_parser().parse_args(['regr', '-rl', str(source), '--project', str(farm.project), '-o', str(farm.out)])
        batch, entries = prepare_batch(args)
        journal, state = run_batch(batch, entries)
        self.assertTrue(state['passed'])
        jobs = list(farm.farm_state()['jobs'].values())
        self.assertEqual([j['args'][j['args'].index('-q') + 1] for j in jobs], ['short', 'short', 'normal', 'normal'])
        for job in jobs:
            self.assertIn('select[mem>1024]', job['args'])
            self.assertIn('/regr_dir/', job['cwd'])
        self.assertLessEqual(farm.farm_state()['peak'], 2)
        self.assertTrue(load_final(journal)[1]['passed'])


class SimulationRegressionTests(Fixture):
    def test_sim_caselist_copies_validated_build_and_replay_preserves_snapshot(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        compile_plan = create_plan(self.project, submit_paths=[self.submit], phase='com')
        original_build, _ = run(compile_plan, [self.out])
        plan = create_plan(self.project, caselist=self.cases, submit_paths=[self.submit], seeds=[42], phase='sim')
        record, state = run(plan, [self.out])
        self.assertFalse(state['passed'])
        copied = build_directory(self.out, plan, plan['tasks'][0])
        self.assertNotEqual(copied, original_build)
        self.assertNotEqual((copied / 'fake-model.json').stat().st_ino, (original_build / 'fake-model.json').stat().st_ino)
        self.assertTrue(json.loads((copied / 'build.json').read_text())['snapshot_source'])
        original_hash = digest(copied / 'build.json')
        run(create_plan(self.project, submit_paths=[self.submit], phase='com'), [self.out])
        repeated, result = replay(record)
        self.assertNotEqual(repeated, record)
        self.assertEqual(digest(copied / 'build.json'), original_hash)
        self.assertEqual(len(result['tasks']), 1)
        self.assertEqual(result['tasks'][0]['effective_verdict'], 'FAIL')
        self.assertEqual(next(t for t in load_final(record)[1]['tasks'] if t['case'] == 'fast')['effective_verdict'], 'PASS')
