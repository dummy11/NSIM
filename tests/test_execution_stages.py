"""Compile/simulate separation, workspace resolution and build ownership."""

import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from test_regression import Fixture
from nsim.cli import build_parser
from nsim.compilation import Builds
from nsim.config import ConfigError, MODES
from nsim.layout import build_directory, simulation_directory
from nsim.planning import create_plan
from nsim.service import replay, run
from nsim.storage import digest


class ExecutionStagesTests(Fixture):
    def stage(self, phase='run', **kwargs):
        if phase != 'com':
            kwargs.setdefault('cases', ['smoke'])
            kwargs.setdefault('seeds', [42])
        return create_plan(self.project, submit_paths=[self.submit], phase=phase, **kwargs)

    def command_submit(self, fail=False):
        simulator = self.project / 'simulator.py'
        simulator.write_text('''import json, sys
case, seed, result = sys.argv[1:]
with open(result, 'w') as stream:
    json.dump({'schema_version': 1, 'case': case, 'seed': int(seed), 'complete': True,
               'checks': {'functional': {'executed': True, 'passed': True}}}, stream)
''')
        compiler = self.project / 'compiler.py'
        compiler.write_text('''from pathlib import Path
import shutil, sys
source, target, count = map(Path, sys.argv[1:])
count.write_text(count.read_text() + 'compile\\n' if count.exists() else 'compile\\n')
shutil.copyfile(source, target)
''' + ('raise SystemExit(7)\n' if fail else ''))
        self.descriptor.write_text('schema_version=1\nname="demo"\ninputs=["compiler.py", "simulator.py"]\n')
        self.data = {'schema_version': 1, 'name': 'base', 'adapter': 'command',
                     'compile': {'argv': [sys.executable, '{project}/compiler.py', '{project}/simulator.py',
                                          '{build_dir}/simulator.py', str(self.base / 'count')],
                                 'artifacts': ['simulator.py']},
                     'argv': [sys.executable, '{build_dir}/simulator.py', '{case}', '{seed}', '{result}'],
                     'required_checks': ['functional'], 'settle_seconds': 0}
        self.save_submit()

    def test_separate_com_sim_and_cross_workspace_reuse(self):
        self.command_submit()
        compiled, state = run(self.stage('com'), [self.out])
        self.assertTrue(state['passed'])
        self.assertEqual(compiled, self.out / 'base' / 'rtl' / 'com_dir')
        self.assertFalse((compiled.parent / 'sim_dir').exists())
        manifest = json.loads((compiled / 'build.json').read_text())
        self.assertTrue(manifest['compiled'])
        before = digest(compiled / 'build.json')
        plan = self.stage('sim', directory='issue_1234', com_dir='../rtl/com_dir')
        result, state = run(plan, [self.out])
        self.assertTrue(state['passed'])
        self.assertEqual(result, self.out / 'base' / 'issue_1234' / 'sim_dir' / 'smoke_42')
        reference = json.loads((result / 'run.json').read_text())
        self.assertEqual(reference['build_dir'], str(compiled))
        self.assertEqual(reference['build_instance'], manifest['id'])
        self.assertEqual(digest(compiled / 'build.json'), before)
        self.assertEqual((self.base / 'count').read_text(), 'compile\n')

    def test_run_compiles_once_for_multiple_cases_and_seeds(self):
        self.command_submit()
        _, state = run(self.stage(cases=['a', 'b'], seeds=[1, 2]), [self.out], jobs=4)
        self.assertTrue(state['passed'])
        self.assertEqual((self.base / 'count').read_text(), 'compile\n')
        self.assertEqual(len({t['attempts'][0]['build_instance'] for t in state['tasks']}), 1)

    def test_compile_failure_prevents_simulation_and_is_not_reusable(self):
        self.command_submit(fail=True)
        result, state = run(self.stage(), [self.out])
        self.assertFalse(state['passed'])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'COMPILE_FAIL')
        self.assertFalse((result / 'run.log').exists())
        with self.assertRaisesRegex(ConfigError, 'unsuccessful or incompatible'):
            run(self.stage('sim'), [self.out])

    def test_sim_missing_build_does_not_create_or_clear_outputs(self):
        plan = self.stage('sim')
        target = simulation_directory(self.out, plan, plan['tasks'][0])
        target.mkdir(parents=True)
        sentinel = target / 'keep'
        sentinel.write_text('old result')
        with self.assertRaisesRegex(ConfigError, 'nsim com'):
            run(plan, [self.out])
        self.assertEqual(sentinel.read_text(), 'old result')
        self.assertFalse(build_directory(self.out, plan, plan['tasks'][0]).exists())

    def test_sim_rejects_changed_artifact_before_overwriting_simulation(self):
        self.command_submit()
        target, _ = run(self.stage(), [self.out])
        marker = target / 'keep'
        marker.write_text('old result')
        build = target.parent.parent / 'com_dir'
        (build / 'simulator.py').write_text('changed')
        with self.assertRaisesRegex(ConfigError, 'artifacts changed'):
            run(self.stage('sim'), [self.out])
        self.assertTrue(marker.exists())

    def test_sim_rejects_changed_compile_config_and_inputs(self):
        run(self.stage('com'), [self.out])
        self.data['env'] = {'BUILD_SETTING': 'different'}
        self.save_submit()
        with self.assertRaisesRegex(ConfigError, 'incompatible'):
            run(self.stage('sim'), [self.out])
        self.data.pop('env')
        self.save_submit()
        (self.project / 'input.txt').write_text('changed source')
        with self.assertRaisesRegex(ConfigError, 'incompatible'):
            run(self.stage('sim'), [self.out])

    def test_build_identity_is_independent_of_case_seed_caselist_and_runtime_options(self):
        first = self.stage('com')['tasks'][0]['build_id']
        self.data['plusargs'] = ['+trace=1']
        self.save_submit()
        other = self.stage('sim', cases=None, caselist=self.cases, seeds=[88, 99])
        self.assertEqual({t['build_id'] for t in other['tasks']}, {first})

    def test_modes_and_default_custom_workspace_are_independent(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.data['mode'] = mode
                self.save_submit()
                default = self.stage(mode=mode)
                custom = self.stage(mode=mode, directory='issue')
                self.assertEqual(default['execution']['workspace'], mode)
                self.assertEqual(custom['execution']['mode'], mode)
                self.assertEqual(custom['execution']['workspace'], 'issue')
                self.assertTrue(run(default, [self.out])[1]['passed'])
        with self.assertRaisesRegex(ConfigError, 'does not match'):
            self.stage(mode='rtl')
        with self.assertRaises(ConfigError):
            self.stage(mode='post')

    def test_invalid_workspace_and_compile_overlap_are_rejected_without_writes(self):
        for name in ('../elsewhere', '/tmp/escape', '.', 'a/b'):
            with self.assertRaises(ConfigError):
                self.stage(directory=name)
        for value in ('.', '..', 'sim_dir', 'sim_dir/smoke_42', str(self.project), str(self.out / 'regr_dir')):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                run(self.stage(com_dir=value), [self.out])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_absolute_compile_directory_and_relative_compile_name(self):
        external = self.base / 'shared-build'
        run(self.stage('com', com_dir=str(external)), [self.out])
        self.assertTrue(run(self.stage('sim', directory='elsewhere', com_dir=str(external)), [self.out])[1]['passed'])
        plan = self.stage('com', directory='issue', com_dir='compiled')
        directory, _ = run(plan, [self.out])
        self.assertEqual(directory, self.out / 'base' / 'issue' / 'compiled')
        self.assertFalse((self.out / 'base' / 'compiled' / 'com_dir').exists())

    def test_dry_run_preserves_build_products_and_filters_stages(self):
        self.command_submit()
        build, _ = run(self.stage('com'), [self.out])
        before = {p.name: p.read_bytes() for p in build.iterdir() if p.is_file()}
        for phase in ('com', 'sim', 'run'):
            record, _ = run(self.stage(phase), [self.out], dry_run=True)
            preview = json.loads((record / 'commands.json').read_text())['tasks'][0]
            self.assertEqual('compile' in preview, phase != 'sim')
            self.assertEqual('simulate' in preview, phase != 'com')
        self.assertEqual(before, {p.name: p.read_bytes() for p in build.iterdir() if p.is_file()})
        self.assertEqual((self.base / 'count').read_text(), 'compile\n')

    def test_compile_writer_conflicts_with_readers_but_sim_readers_can_share(self):
        run(self.stage('com'), [self.out])
        sim = self.stage('sim')
        policy = {'min_free_bytes': 0, 'min_free_inodes': 0}
        with Builds(sim) as first, Builds(sim) as second:
            first.ensure(self.out, sim['tasks'][0], threading.Event(), policy)
            second.ensure(self.out, sim['tasks'][0], threading.Event(), policy)
            with self.assertRaisesRegex(ConfigError, 'compile directory is in use'):
                run(self.stage('com'), [self.out])
        self.assertTrue(run(self.stage('com'), [self.out])[1]['passed'])

    def test_sim_rerun_rebuilds_even_if_original_compilation_was_replaced(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        run(self.stage('com'), [self.out])
        record, state = run(self.stage('sim'), [self.out])
        self.assertFalse(state['passed'])
        run(self.stage('com'), [self.out])
        repeated, state = replay(record, rebuild=True)
        self.assertEqual(state['phase'], 'regr')
        self.assertNotEqual(repeated, record)

    def test_compile_symlink_and_shared_build_collision_rejected(self):
        external = self.base / 'external'
        external.mkdir()
        link = self.base / 'alias'
        link.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ConfigError, 'symlink'):
            run(self.stage('com', com_dir=str(link)), [self.out])
        other = self.project / 'other.json'
        other.write_text(json.dumps(dict(self.data, name='other')))
        plan = create_plan(self.project, submit_paths=[self.submit, other], phase='com', com_dir=str(external))
        with self.assertRaisesRegex(ConfigError, 'multiple compile configurations'):
            run(plan, [self.out])
        self.assertEqual(list(external.iterdir()), [])

    def test_cli_has_only_direct_stage_entries_and_rejects_post(self):
        parser = build_parser()
        common = ['--project', str(self.project), '--submit', str(self.submit)]
        self.assertEqual(parser.parse_args(['com', *common]).command, 'com')
        self.assertIsNone(parser.parse_args(['sim', *common, '--case', 'smoke']).mode)
        for args in (['run', 'case', *common, '--case', 'smoke'],
                     ['run', 'regression', *common, '--caselist', str(self.cases)],
                     ['run', *common, '--case', 'smoke', '-mode', 'post'],
                     ['com', *common, '--case', 'smoke']):
            with patch('sys.stderr'), self.assertRaises(SystemExit) as exc:
                parser.parse_args(args)
            self.assertEqual(exc.exception.code, 2)

    def test_cli_works_outside_project_and_resolves_com_dir_from_workspace(self):
        common = [sys.executable, '-m', 'nsim']
        options = ['--project', str(self.project), '--submit', str(self.submit), '--output-root', str(self.out)]
        compile_result = subprocess.run([*common, 'com', *options], cwd=self.backup, capture_output=True, text=True)
        self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
        simulation = subprocess.run([*common, 'sim', *options, '--case', 'smoke', '--seed', '42',
                                     '-dir', 'issue', '-com-dir', '../rtl/com_dir'],
                                    cwd=self.backup, capture_output=True, text=True)
        self.assertEqual(simulation.returncode, 0, simulation.stderr)
        self.assertEqual(json.loads(simulation.stdout)['phase'], 'sim')
        self.assertFalse((self.backup / 'com_dir').exists())

    def test_missing_compile_artifact_and_timeout_cannot_pass(self):
        self.command_submit()
        self.data['compile']['artifacts'] = ['missing-binary']
        self.save_submit()
        _, state = run(self.stage('com'), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'COMPILE_FAIL')
        self.assertIn('missing', state['tasks'][0]['attempts'][0]['reason'])
        (self.project / 'compiler.py').write_text('import time\ntime.sleep(10)\n')
        self.data['compile']['timeout_seconds'] = .05
        self.save_submit()
        _, state = run(self.stage(), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'COMPILE_FAIL')
        self.assertIn('timed out', state['tasks'][0]['attempts'][0]['reason'])

    def test_generated_executable_and_artifact_directory(self):
        self.command_submit()
        compiler = self.project / 'compiler.py'
        compiler.write_text(compiler.read_text() + '''target.write_text('#!' + sys.executable + '\\n' + target.read_text())
target.chmod(0o755)
(target.parent / 'database').mkdir()
(target.parent / 'database' / 'design').write_text('compiled design')
''')
        self.data['argv'] = ['{build_dir}/simulator.py', '{case}', '{seed}', '{result}']
        self.data['compile']['artifacts'].append('database')
        self.save_submit()
        self.assertTrue(run(self.stage(), [self.out])[1]['passed'])
        self.assertTrue(run(self.stage('sim', cases=['different'], seeds=[7]), [self.out])[1]['passed'])
        (self.out / 'base/rtl/com_dir/database/new').write_text('changed design')
        with self.assertRaisesRegex(ConfigError, 'artifacts changed'):
            run(self.stage('sim'), [self.out])

    def test_malformed_compile_manifest_is_a_configuration_error(self):
        build, _ = run(self.stage('com'), [self.out])
        (build / 'build.json').write_text('[]')
        with self.assertRaises(ConfigError):
            run(self.stage('sim'), [self.out])
        self.assertFalse((build.parent / 'sim_dir').exists())

    def test_shared_compilation_runs_different_cases_concurrently(self):
        from concurrent.futures import ThreadPoolExecutor
        self.data['fake'] = {'durations': {'a': .2, 'b': .2}}
        self.save_submit()
        run(self.stage('com'), [self.out])
        plans = [self.stage('sim', cases=[case]) for case in ('a', 'b')]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run, plan, [self.out]) for plan in plans]
            self.assertTrue(all(future.result()[1]['passed'] for future in futures))

    def test_sim_rerun_forces_new_build_without_changing_original(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        build, _ = run(self.stage('com'), [self.out])
        before = digest(build / 'build.json')
        record, _ = run(self.stage('sim'), [self.out])
        record, state = replay(record, rebuild=True)
        self.assertFalse(state['passed'])
        self.assertEqual(state['phase'], 'regr')
        self.assertEqual(record.name, 'report')
        self.assertEqual(state['tasks'][0]['seed'], 42)
        self.assertEqual(digest(build / 'build.json'), before)
