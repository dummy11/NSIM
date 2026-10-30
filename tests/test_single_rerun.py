"""Directory-directed replay: compile directories rebuild; cases reuse by default."""

import json
from pathlib import Path
import subprocess
import sys

import test_execution_stages
import test_rerun
from test_regression import Fixture
from nsim.config import ConfigError
from nsim.layout import build_directory, simulation_directory
from nsim.planning import create_plan
from nsim.report_command import report
from nsim.reports import load_final
from nsim.service import replay, run
from nsim.storage import IntegrityError, digest


class SingleRerunTests(Fixture):
    def command_setup(self):
        test_execution_stages.ExecutionStagesTests.command_submit(self)

    def single(self, phase='run'):
        selection = {} if phase == 'com' else dict(cases=['smoke'], seeds=[42])
        return create_plan(self.project, submit_paths=[self.submit], phase=phase, **selection)

    def test_passed_case_reuses_exact_build_snapshot_and_repeated_rerun_stays_single(self):
        self.command_setup()
        original = self.single()
        record, state = run(original, [self.out])
        build = build_directory(self.out, original, original['tasks'][0])
        before = {path: digest(path) for path in (build / 'build.json', build / 'simulator.py', record / 'run.json')}
        old_instance = state['tasks'][0]['attempts'][0]['build_instance']
        first, result = replay(record)
        second, repeated = replay(first)
        self.assertTrue(result['passed'], result)
        self.assertTrue(repeated['passed'], repeated)
        self.assertEqual((self.base / 'count').read_text(), 'compile\n')
        self.assertEqual(result['phase'], 'sim')
        self.assertEqual(repeated['phase'], 'sim')
        self.assertEqual(first.parent.parent, second.parent.parent)
        task = result['tasks'][0]
        self.assertEqual(task['attempts'][0]['build_instance'], old_instance)
        copied = Path(task['attempts'][0]['build_dir'])
        self.assertNotEqual(copied, build)
        self.assertNotEqual((copied / 'simulator.py').stat().st_ino, (build / 'simulator.py').stat().st_ino)
        self.assertEqual(before, {path: digest(path) for path in before})
        self.assertEqual(load_final(second)[0]['source']['scope'], 'case')

    def test_compile_input_change_requires_explicit_rebuild_before_creating_outputs(self):
        self.command_setup()
        original = self.single()
        record, _ = run(original, [self.out])
        build = build_directory(self.out, original, original['tasks'][0])
        before = digest(build / 'build.json')
        simulator = self.project / 'simulator.py'
        simulator.write_text(simulator.read_text() + '\n# changed testbench\n')
        with self.assertRaisesRegex(ConfigError, '--rebuild'):
            replay(record)
        self.assertFalse(list(build.parent.glob('rerun_*')))
        repeated, state = replay(record, rebuild=True)
        self.assertTrue(state['passed'], state)
        self.assertEqual(state['phase'], 'regr')
        self.assertEqual((self.base / 'count').read_text(), 'compile\ncompile\n')
        self.assertEqual(digest(build / 'build.json'), before)
        self.assertTrue(load_final(repeated)[1]['passed'])

    def test_runtime_input_change_reuses_compile_and_current_data(self):
        test_rerun.RerunTests.command_setup(self)
        original = create_plan(self.project, cases=['fast'], seeds=[42], submit_paths=[self.submit])
        record, state = run(original, [self.out])
        self.assertFalse(state['passed'])
        (self.project / 'data.txt').write_text('fixed')
        repeated, state = replay(record)
        self.assertTrue(state['passed'], state)
        self.assertEqual((self.base / 'count').read_text(), 'compile\n')
        self.assertEqual(load_final(repeated)[0]['tasks'][0]['case'], 'fast')

    def test_incomplete_case_uses_unified_rerun_without_recompilation(self):
        self.command_setup()
        record, _ = run(self.single(), [self.out])
        (record / 'run.log').write_text('lost log evidence')
        self.assertEqual(load_final(record)[1]['tasks'][0]['effective_verdict'], 'INCOMPLETE')
        _, state = replay(record)
        self.assertTrue(state['passed'], state)
        self.assertEqual((self.base / 'count').read_text(), 'compile\n')

    def test_failed_compile_case_can_be_fixed_and_rerun_with_rebuild(self):
        test_execution_stages.ExecutionStagesTests.command_submit(self, fail=True)
        record, state = run(self.single(), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'COMPILE_FAIL')
        compiler = self.project / 'compiler.py'
        compiler.write_text(compiler.read_text().replace('raise SystemExit(7)', ''))
        with self.assertRaisesRegex(ConfigError, '--rebuild'):
            replay(record)
        _, state = replay(record, rebuild=True)
        self.assertTrue(state['passed'], state)

    def test_changed_or_missing_compile_products_never_trigger_automatic_compile(self):
        self.command_setup()
        original = self.single()
        record, _ = run(original, [self.out])
        build = build_directory(self.out, original, original['tasks'][0])
        artifact = build / 'simulator.py'
        artifact.write_text('damaged')
        with self.assertRaisesRegex(ConfigError, '--rebuild'):
            replay(record)
        artifact.unlink()
        with self.assertRaisesRegex(ConfigError, '--rebuild'):
            replay(record)
        self.assertEqual((self.base / 'count').read_text(), 'compile\n')
        self.assertTrue(replay(record, rebuild=True)[1]['passed'])

    def test_compile_directory_rebuilds_only_compile_for_com_and_run_sources(self):
        self.command_setup()
        for phase, root in (('com', self.out), ('run', self.backup)):
            with self.subTest(phase=phase):
                original = self.single(phase)
                _, state = run(original, [root])
                self.assertTrue(state['passed'], state)
                build = build_directory(root, original, original['tasks'][0])
                before = digest(build / 'build.json')
                repeated, state = replay(build)
                self.assertTrue(state['passed'], state)
                self.assertEqual(state['phase'], 'com')
                self.assertFalse(list(repeated.parent.rglob('sim_dir')))
                self.assertEqual(digest(build / 'build.json'), before)
                plan, _ = load_final(repeated)
                next_build = build_directory(root, plan, plan['tasks'][0])
                self.assertTrue(replay(next_build)[1]['passed'])
        self.assertEqual((self.base / 'count').read_text(), 'compile\n' * 6)

    def test_failed_compile_directory_rebuilds_after_source_fix(self):
        test_execution_stages.ExecutionStagesTests.command_submit(self, fail=True)
        record, state = run(self.single('com'), [self.out])
        self.assertFalse(state['passed'])
        compiler = self.project / 'compiler.py'
        compiler.write_text(compiler.read_text().replace('raise SystemExit(7)', ''))
        _, state = replay(record)
        self.assertTrue(state['passed'], state)
        self.assertEqual(state['phase'], 'com')

    def test_regression_compile_directory_selects_only_its_submit_and_not_summary(self):
        self.command_setup()
        simulator = self.project / 'simulator.py'
        simulator.write_text(simulator.read_text().replace("'passed': True", "'passed': False"))
        self.cases.write_text('smoke\nfast\n')
        other = self.project / 'other.json'
        other.write_text(json.dumps(dict(self.data, name='other')))
        original = create_plan(self.project, caselist=self.cases, seeds=[42], submit_paths=[self.submit, other])
        record, _ = run(original, [self.out])
        simulator.write_text(simulator.read_text().replace("'passed': False", "'passed': True"))
        self.assertTrue(replay(record)[1]['passed'])
        build = build_directory(self.out, original, original['tasks'][0])
        repeated, state = replay(build)
        self.assertTrue(state['passed'], state)
        self.assertEqual([(task['submit_name'], task['case']) for task in state['tasks']], [('base', 'compile')])
        summary, code = report(record.parent, rebuild=True, as_json=True)
        self.assertEqual(code, 0, summary)
        self.assertEqual(len(json.loads(summary)['tasks']), 4)
        with self.assertRaisesRegex(ConfigError, 'compile-only'):
            report(record.parent, rebuild=True, includes=[repeated.parent])

    def test_regression_case_path_reuses_build_even_when_case_passed(self):
        self.command_setup()
        original = self.plan()
        _, state = run(original, [self.out])
        case = simulation_directory(self.out, original, original['tasks'][0])
        repeated, state = replay(case)
        self.assertTrue(state['passed'], state)
        self.assertEqual(len(state['tasks']), 1)
        self.assertEqual(state['phase'], 'sim')
        self.assertEqual((self.base / 'count').read_text(), 'compile\n')
        build = Path(state['tasks'][0]['attempts'][0]['build_dir'])
        self.assertTrue(replay(build)[1]['passed'])

    def test_sim_dir_parent_and_nonterminal_case_are_not_replay_sources(self):
        self.command_setup()
        record, _ = run(self.single(), [self.out])
        with self.assertRaises(IntegrityError):
            replay(record.parent)
        value = json.loads((record / 'run.json').read_text())
        value['terminal'] = False
        (record / 'run.json').write_text(json.dumps(value))
        with self.assertRaises(IntegrityError):
            replay(record)

    def test_prebuilt_case_can_reuse_but_cannot_rebuild(self):
        self.command_setup()
        del self.data['compile']
        self.data['argv'] = [sys.executable, '{project}/simulator.py', '{case}', '{seed}', '{result}']
        self.save_submit()
        record, _ = run(self.single(), [self.out])
        self.assertTrue(replay(record)[1]['passed'])
        with self.assertRaisesRegex(ConfigError, 'requires a compile step'):
            replay(record, rebuild=True)

    def test_cli_default_reuse_and_rebuild_aliases(self):
        self.command_setup()
        record, _ = run(self.single(), [self.out])
        for options, phase in (([], 'sim'), (['--rebuild'], 'regr'), (['-rebuild'], 'regr')):
            result = subprocess.run([sys.executable, '-m', 'nsim', 'rerun', *options, str(record)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['phase'], phase)
        self.assertEqual((self.base / 'count').read_text(), 'compile\n' * 3)

    def test_single_recover_has_same_current_inputs_selection_and_build_policy(self):
        test_rerun.RerunTests.command_setup(self)
        original = create_plan(self.project, cases=['fast'], seeds=[42], submit_paths=[self.submit])
        record, _ = run(original, [self.out])
        (self.project / 'data.txt').write_text('fixed')
        first, state = replay(record, recovery=True)
        self.assertTrue(state['passed'], state)
        self.assertEqual(state['phase'], 'sim')
        self.assertEqual((self.base / 'count').read_text(), 'compile\n')
        self.assertTrue(replay(first, recovery=True)[1]['passed'])
        simulator = self.project / 'simulator.py'
        simulator.write_text(simulator.read_text() + '\n# new TB\n')
        with self.assertRaisesRegex(ConfigError, '--rebuild'):
            replay(record, recovery=True)
        self.assertTrue(replay(record, recovery=True, rebuild=True)[1]['passed'])
        build = build_directory(self.out, original, original['tasks'][0])
        _, state = replay(build, recovery=True)
        self.assertTrue(state['passed'], state)
        self.assertEqual(state['phase'], 'com')

    def test_lsf_reused_case_submits_only_simulation_and_compile_source_only_compilation(self):
        from test_lsf import LsfTests
        farm = LsfTests()
        farm.setUp()
        self.addCleanup(farm.doCleanups)
        scheduler = farm.data['scheduler']
        test_execution_stages.ExecutionStagesTests.command_submit(farm)
        farm.data['scheduler'] = scheduler
        farm.save_submit()
        original = create_plan(farm.project, cases=['smoke'], seeds=[42], submit_paths=[farm.submit])
        record, state = run(original, [farm.out])
        self.assertTrue(state['passed'], state)
        count = len(farm.farm_state()['jobs'])
        _, state = replay(record, recovery=True)
        self.assertTrue(state['passed'], state)
        self.assertEqual(len(farm.farm_state()['jobs']), count + 1)
        self.assertEqual((farm.base / 'count').read_text(), 'compile\n')
        build = build_directory(farm.out, original, original['tasks'][0])
        _, state = replay(build)
        self.assertTrue(state['passed'], state)
        self.assertEqual(state['phase'], 'com')
        self.assertEqual(len(farm.farm_state()['jobs']), count + 2)

    def test_recover_cli_uses_same_default_and_rebuild_switch(self):
        self.command_setup()
        record, _ = run(self.single(), [self.out])
        for options, phase in (([], 'sim'), (['--rebuild'], 'regr')):
            result = subprocess.run([sys.executable, '-m', 'nsim', 'recover', *options, str(record)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['phase'], phase)
