"""Multi-list/submit/seed regressions and random-repeat provenance."""

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_regression import Fixture
from nsim.cli import build_parser
from nsim.config import ConfigError
from nsim.layout import simulation_directory
from nsim.planning import create_plan
from nsim.service import replay, run
from nsim.storage import IntegrityError, write_json


class RegressionCliTests(Fixture):
    def setUp(self):
        super().setUp()
        (self.project / 'submit').mkdir()
        self.submits = []
        for index in range(2):
            path = self.project / 'submit' / f'submit_{index}.json'
            write_json(path, dict(self.data, name=f'submit_{index}'))
            self.submits.append(path)
        self.cases.write_text('smoke\nfast\nsmoke\n')
        self.other_cases = self.project / 'other.list'
        self.other_cases.write_text('fast\nslow\n')

    def cli(self, *args, expected=0, cwd=None):
        result = subprocess.run([sys.executable, '-m', 'nsim', *map(str, args)],
                                cwd=cwd or self.project, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else result

    def plan(self, **kwargs):
        return create_plan(self.project, caselist=[self.cases, self.other_cases],
                           submit_paths=self.submits, phase='regr', **kwargs)

    def test_multiple_lists_submits_seeds_expand_once_per_unique_task(self):
        result = self.cli('-o', self.out, 'regr', '-dir', 'nightly', '-l', self.cases, self.other_cases,
                          '-sub', 'submit_0', 'submit_1', 'submit_0', '-seed', 42, 43, 42, '--jobs', 4)
        self.assertTrue(result['passed'])
        self.assertEqual(result['phase'], 'regr')
        self.assertEqual(result['tasks'], 12)
        record = Path(result['record_dir'])
        self.assertEqual(record.parent.parent, self.out / 'regr_dir')
        plan = json.loads((record / 'plan.json').read_text())
        self.assertEqual(len({t['id'] for t in plan['tasks']}), 12)
        self.assertEqual(plan['seed_selection']['values'], [42, 43])
        self.assertEqual(plan['caselists'], [str(self.cases), str(self.other_cases)])
        for task in plan['tasks']:
            self.assertTrue((simulation_directory(self.out, plan, task) / 'result.json').is_file())
            expected = [str(self.cases), str(self.other_cases)] if task['case'] == 'fast' else (
                [str(self.cases)] if task['case'] == 'smoke' else [str(self.other_cases)])
            self.assertEqual(task['caselists'], expected)
        for path in (self.cases, self.other_cases):
            self.assertIn(str(path), plan['input_hashes'])
            self.assertEqual(plan['snapshots'][str(path)], path.read_text())

    def test_repeat_generates_unique_seeds_shared_across_all_cases_and_submits(self):
        plan = self.plan(repeat=5)
        seeds = plan['seed_selection']['values']
        self.assertEqual(plan['seed_selection']['source'], 'random')
        self.assertEqual(len(set(seeds)), 5)
        self.assertTrue(all(1 <= s <= 2147483647 for s in seeds))
        self.assertEqual(len(plan['tasks']), 30)
        for submit in range(2):
            for case in ('smoke', 'fast', 'slow'):
                self.assertEqual([t['seed'] for t in plan['tasks'] if t['submit'] == submit and t['case'] == case], seeds)
        with patch('nsim.planning.secrets.SystemRandom.sample', side_effect=AssertionError('must not regenerate seeds')):
            record, state = run(plan, [self.out], jobs=4)
        self.assertTrue(state['passed'])
        self.assertEqual(json.loads((record / 'plan.json').read_text())['seed_selection']['values'], seeds)

    def test_repeat_cli_and_seed_mutual_exclusion(self):
        result = self.cli('regr', '-o', self.out, '-l', self.cases, '-sub', 'submit_0', '-repeat', 3)
        self.assertEqual(result['tasks'], 6)
        self.assertTrue(result['passed'])
        parser = build_parser()
        for invalid in (['-seed', '42', '-repeat', '3'], ['-repeat', '3', '-seed', '42'],
                        ['-seed', '42', '--seed', '43', '-repeat', '2']):
            with patch('sys.stderr'), self.assertRaises(SystemExit):
                parser.parse_args(['regr', '-l', str(self.cases), '-sub', 'submit_0', *invalid])
        for value in (0, -1, True, 2147483648):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                self.plan(repeat=value)
        with self.assertRaisesRegex(ConfigError, 'explicit seeds'):
            self.plan(seeds=[42], repeat=3)

    def test_repeat_replay_retains_failed_seeds_without_random_generation(self):
        for path in self.submits:
            data = json.loads(path.read_text())
            data['fake'] = {'outcomes': {'smoke': 'fail'}}
            write_json(path, data)
        record, _ = run(self.plan(repeat=2), [self.out], jobs=4)
        original = json.loads((record / 'plan.json').read_text())
        seeds = original['seed_selection']['values']
        with patch('nsim.planning.secrets.SystemRandom.sample', side_effect=AssertionError('must preserve seed')):
            retried, state = replay(record)
        self.assertFalse(state['passed'])
        self.assertEqual(len(state['tasks']), 4)
        self.assertEqual({task['seed'] for task in state['tasks']}, set(seeds))
        saved = json.loads((retried / 'plan.json').read_text())
        self.assertEqual(saved['seed_selection'], {'source': 'replay', 'repeat': None, 'values': seeds})
        self.assertEqual(saved['execution']['phase'], 'regr')
        replayed, _ = replay(retried, seed=7)
        saved = json.loads((replayed / 'plan.json').read_text())
        self.assertEqual(saved['seed_selection'], {'source': 'replay_override', 'repeat': None, 'values': [7]})
        self.assertEqual(len(saved['tasks']), 2)

    def test_changed_second_caselist_prevents_exact_replay(self):
        record, _ = run(self.plan(seeds=[42]), [self.out])
        self.other_cases.write_text('new_case\n')
        with self.assertRaises(IntegrityError):
            replay(record)

    def test_single_case_regr_keeps_summary_and_default_random_seed(self):
        self.cases.write_text('smoke\n')
        result = self.cli('regr', '-l', self.cases, '-sub', 'submit_0', '-o', self.out)
        record = Path(result['record_dir'])
        self.assertEqual(result['tasks'], 1)
        self.assertTrue((record / 'report.html').exists())
        plan = json.loads((record / 'plan.json').read_text())
        self.assertEqual(plan['kind'], 'regression')
        self.assertEqual(len(plan['seed_selection']['values']), 1)

    def test_output_alias_position_and_local_override_do_not_change_project_config(self):
        original = self.descriptor.read_bytes()
        for prefix, suffix, selected in ((['-o', self.out], [], self.out),
                                          ([], ['-o', self.backup], self.backup),
                                          (['-o', self.out], ['-o', self.backup], self.backup)):
            result = self.cli(*prefix, 'regr', '-l', self.cases, '-sub', 'submit_0', '-seed', 42, *suffix)
            plan = json.loads((Path(result['record_dir']) / 'plan.json').read_text())
            self.assertEqual(plan['policy']['output_roots'], [str(selected)])
        self.assertEqual(self.descriptor.read_bytes(), original)

    def test_custom_sim_dir_relative_absolute_replay_and_conflicts(self):
        for value in ('results', str(self.base / 'external-results')):
            plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42], sim_dir=value)
            record, state = run(plan, [self.out])
            self.assertTrue(state['passed'])
            expected = Path(value) if Path(value).is_absolute() else self.out / 'base/rtl' / value
            self.assertEqual(record, expected / 'smoke_42')
            (record / 'run.log').write_text('changed evidence')
            repeated, state = replay(record)
            self.assertTrue(state['passed'])
            self.assertEqual(repeated, record)
        for value in ('.', '..', 'com_dir', str(self.project), str(self.out / 'regr_dir')):
            plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42], sim_dir=value)
            with self.subTest(value=value), self.assertRaises(ConfigError):
                run(plan, [self.out])
        with self.assertRaisesRegex(ConfigError, 'duplicate simulation destinations'):
            run(self.plan(seeds=[42], sim_dir=str(self.base / 'shared-results')), [self.out])

    def test_cli_sim_dir_and_submit_resolution_outside_project(self):
        result = self.cli('run', '--project', self.project, '-sub', 'submit_0', '--case', 'smoke',
                          '-seed', 42, '-dir', 'issue', '-sim-dir', 'results', '-o', self.out, cwd=self.backup)
        self.assertEqual(Path(result['record_dir']), self.out / 'submit_0/issue/results/smoke_42')
        error = self.cli('com', '--project', self.project, '-sub', 'missing', '-o', self.out,
                         expected=2, cwd=self.backup)
        self.assertIn('submit not found', error.stderr)

    def test_multi_list_dry_run_is_non_destructive(self):
        result = self.cli('regr', '-l', self.cases, self.other_cases, '-sub', 'submit_0', 'submit_1',
                          '-seed', 42, 43, '-sim-dir', 'results', '-o', self.out, '--dry-run')
        self.assertEqual(result['tasks'], 12)
        preview = json.loads((Path(result['record_dir']) / 'commands.json').read_text())
        self.assertTrue(all(t['phase'] == 'regr' for t in preview['tasks']))
        self.assertTrue(all('/results/' in t['cwd'] for t in preview['tasks']))
        self.assertEqual({p.name for p in self.out.iterdir()}, {'regr_dir'})

    def test_regr_custom_compile_and_simulation_dirs_apply_per_submit(self):
        result = self.cli('regr', '-dir', 'nightly', '-l', self.cases, self.other_cases,
                          '-sub', 'submit_0', 'submit_1', '-repeat', 2,
                          '-com-dir', 'build', '-sim-dir', 'results', '-o', self.out, '--jobs', 4)
        self.assertTrue(result['passed'])
        self.assertEqual(result['tasks'], 12)
        record = Path(result['record_dir'])
        plan = json.loads((record / 'plan.json').read_text())
        seeds = plan['seed_selection']['values']
        for submit in ('submit_0', 'submit_1'):
            workspace = record.parent / submit / 'nightly'
            build = workspace / 'build'
            self.assertTrue((build / 'build.json').is_file())
            for case in ('smoke', 'fast', 'slow'):
                for seed in seeds:
                    run_record = json.loads((workspace / 'results' / f'{case}_{seed}' / 'run.json').read_text())
                    self.assertEqual(run_record['build_dir'], str(build))
            self.assertFalse((workspace / 'com_dir').exists())
            self.assertFalse((workspace / 'sim_dir').exists())

    def test_external_sim_directory_preserves_storage_thresholds(self):
        from nsim.layout import SimulationDirectories
        external = self.base / 'external-results'
        external.mkdir()
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42],
                           sim_dir=str(external))
        with patch('nsim.storage.preflight', side_effect=OSError('storage threshold')) as probe:
            with SimulationDirectories(plan, 123, 456) as directories:
                with self.assertRaisesRegex(OSError, 'storage threshold'):
                    directories.reserve(self.out, plan['tasks'][0])
        probe.assert_called_once_with(external, 123, 456)
        self.assertEqual(list(external.iterdir()), [])
