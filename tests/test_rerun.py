"""Rebuild changed inputs while retaining the exact failed task identities."""

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_regression import Fixture
import test_execution_stages
from nsim.config import ConfigError
from nsim.layout import build_directory, simulation_directory
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import replay, run
from nsim.storage import IntegrityError, digest


class RerunTests(Fixture):
    def command_setup(self):
        test_execution_stages.ExecutionStagesTests.command_submit(self)
        (self.project / 'simulator.py').write_text('''import json, sys
from pathlib import Path
case, seed, result, data, *args = sys.argv[1:]
passed = Path(data).read_text() == 'fixed' or (case, int(seed)) not in {('fast', 42), ('smoke', 43)}
Path(result).write_text(json.dumps({'schema_version': 1, 'case': case, 'seed': int(seed),
    'complete': True, 'checks': {'functional': {'executed': True, 'passed': passed}}}))
''')
        self.data['argv'].append('{project}/data.txt')
        self.data['files'] = {'data': {'path': 'data.txt', 'phases': ['simulate']}}
        (self.project / 'data.txt').write_text('broken')
        self.save_submit()

    def test_current_sources_data_and_configuration_rebuild_only_failed_pairs(self):
        self.command_setup()
        self.cases.write_text('@config case.yaml\nfast\nsmoke\n')
        config = self.project / 'case.yaml'
        config.write_text('schema_version: 1\ncases:\n  fast:\n    plusargs: [+SETTING=old]\n')
        original = create_plan(self.project, caselist=self.cases, submit_paths=[self.submit], seeds=[42, 43])
        record, old_state = run(original, [self.out], jobs=4)
        old_files = {p: digest(p) for p in record.parent.rglob('*') if p.is_file() and not p.is_symlink()}
        (self.project / 'data.txt').write_text('fixed')
        simulator = self.project / 'simulator.py'
        simulator.write_text(simulator.read_text() + '\n# testbench fix\n')
        compiler = self.project / 'compiler.py'
        compiler.write_text(compiler.read_text() + '\n# compile flow fix\n')
        config.write_text('schema_version: 1\ncases:\n  fast:\n    plusargs: [+SETTING=new]\n')
        self.cases.write_text('@config case.yaml\nfast\nsmoke\nadded\n')
        self.data['env'] = {'REVISION': 'new'}
        self.save_submit()
        repeated, state = replay(record.parent, jobs=4)
        plan, _ = load_final(repeated)
        self.assertTrue(state['passed'], state)
        self.assertEqual({(t['case'], t['seed']) for t in state['tasks']}, {('fast', 42), ('smoke', 43)})
        self.assertEqual(repeated.parent.parent, record.parent)
        self.assertEqual((self.base / 'count').read_text(), 'compile\ncompile\n')
        self.assertEqual({t['attempts'][0]['build_instance'] for t in state['tasks']},
                         {state['tasks'][0]['attempts'][0]['build_instance']})
        self.assertEqual(plan['submits'][0]['env']['REVISION'], 'new')
        self.assertIn('+SETTING=new', next(t for t in plan['tasks'] if t['case'] == 'fast')['case_config']['argv'])
        self.assertEqual(plan['input_hashes'][str(simulator)], digest(simulator))
        self.assertEqual(plan['file_snapshots'][str(self.project / 'data.txt')]['sha256'], digest(self.project / 'data.txt'))
        self.assertEqual({p: digest(p) for p in old_files}, old_files)
        self.assertFalse(load_final(record)[1]['passed'])
        self.assertEqual({t['parent']['run'] for t in state['tasks']}, {old_state['id']})
        build = build_directory(self.out, plan, plan['tasks'][0])
        self.assertTrue(json.loads((build / 'build.json').read_text())['compiled'])
        self.assertEqual((build / 'simulator.py').read_text(), simulator.read_text())
        third, _ = replay(record)
        self.assertNotEqual(third, repeated)
        self.assertEqual(third.parent.parent, repeated.parent.parent)
        self.assertEqual((self.base / 'count').read_text(), 'compile\ncompile\ncompile\n')

    def test_case_reference_and_repeated_failure_keep_anchor_and_parent(self):
        self.data['fake'] = {'outcomes': {'fast': 'fail'}}
        self.save_submit()
        original = self.plan()
        record, _ = run(original, [self.out])
        task = next(t for t in original['tasks'] if t['case'] == 'fast')
        first, _ = replay(simulation_directory(self.out, original, task))
        second, result = replay(first)
        self.assertEqual(first.parent.parent, record.parent)
        self.assertEqual(second.parent.parent, first.parent.parent)
        self.assertEqual(result['tasks'][0]['parent']['run'], load_final(first)[0]['id'])

    def test_sim_source_runs_a_real_new_compile(self):
        self.command_setup()
        run(create_plan(self.project, submit_paths=[self.submit], phase='com'), [self.out])
        original = create_plan(self.project, cases=['fast'], seeds=[42], submit_paths=[self.submit], phase='sim')
        record, _ = run(original, [self.out])
        old_build = build_directory(self.out, original, original['tasks'][0])
        before = digest(old_build / 'build.json')
        (self.project / 'data.txt').write_text('fixed')
        repeated, state = replay(record, rebuild=True)
        plan, _ = load_final(repeated)
        self.assertTrue(state['passed'])
        self.assertEqual((self.base / 'count').read_text(), 'compile\ncompile\n')
        self.assertEqual(digest(old_build / 'build.json'), before)
        self.assertEqual(plan['execution']['phase'], 'regr')
        self.assertNotEqual(build_directory(self.out, plan, plan['tasks'][0]), old_build)

    def test_recover_from_single_sim_reuses_compilation_before_simulation(self):
        self.command_setup()
        run(create_plan(self.project, submit_paths=[self.submit], phase='com'), [self.out])
        original = create_plan(self.project, cases=['smoke'], seeds=[42],
                               submit_paths=[self.submit], phase='sim')
        record, state = run(original, [self.out])
        self.assertTrue(state['passed'])
        old_build = build_directory(self.out, original, original['tasks'][0])
        old_hash = digest(old_build / 'build.json')
        (record / 'run.log').write_text('damaged evidence')
        recovered, state = replay(record, recovery=True)
        plan, _ = load_final(recovered)
        self.assertTrue(state['passed'], state)
        self.assertEqual(plan['execution']['phase'], 'sim')
        self.assertEqual((self.base / 'count').read_text(), 'compile\n')
        self.assertNotEqual(build_directory(self.out, plan, plan['tasks'][0]), old_build)
        self.assertEqual(digest(old_build / 'build.json'), old_hash)

    def test_recover_rebuild_requires_compile_step_and_compile_directory_only_compiles(self):
        test_execution_stages.ExecutionStagesTests.command_submit(self)
        del self.data['compile']
        self.data['argv'] = [sys.executable, '{project}/simulator.py', '{case}', '{seed}', '{result}']
        self.save_submit()
        original = create_plan(self.project, cases=['smoke'], seeds=[42], submit_paths=[self.submit])
        record, state = run(original, [self.out])
        self.assertTrue(state['passed'])
        (record / 'run.log').write_text('damaged evidence')
        with self.assertRaisesRegex(ConfigError, 'requires a compile step'):
            replay(record, recovery=True, rebuild=True)
        self.assertFalse(list((self.out / 'base' / 'rtl').glob('recover_*')))

        self.command_setup()
        self.data['compile']['argv'] = [sys.executable, '-c', 'raise SystemExit(7)']
        self.save_submit()
        compile_record, _ = run(create_plan(self.project, submit_paths=[self.submit], phase='com'), [self.backup])
        recovered, state = replay(compile_record, recovery=True)
        self.assertEqual(state['phase'], 'com')
        self.assertFalse(state['passed'])
        self.assertFalse(list(recovered.parent.rglob('sim_dir')))

    def test_lsf_rebuild_uses_nested_layout_and_current_inputs(self):
        from test_lsf import LsfTests
        farm = LsfTests()
        farm.setUp()
        self.addCleanup(farm.doCleanups)
        scheduler = farm.data['scheduler']
        test_execution_stages.ExecutionStagesTests.command_submit(farm)
        farm.data['scheduler'] = scheduler
        simulator = farm.project / 'simulator.py'
        simulator.write_text(simulator.read_text().replace("'passed': True", "'passed': False"))
        farm.save_submit()
        record, state = farm.launch(jobs=2)
        self.assertFalse(state['passed'])
        simulator.write_text(simulator.read_text().replace("'passed': False", "'passed': True"))
        repeated, state = replay(record, jobs=2)
        self.assertTrue(state['passed'], state)
        self.assertEqual((farm.base / 'count').read_text(), 'compile\ncompile\n')
        self.assertEqual(repeated.parent.parent, record.parent)
        self.assertTrue(load_final(repeated)[1]['passed'])
        jobs = list(farm.farm_state()['jobs'].values())
        self.assertEqual(sum(str(repeated.parent) in job['cwd'] for job in jobs), 4)

    def test_compile_failure_blocks_simulation_and_prebuilt_only_is_rejected(self):
        self.command_setup()
        record, _ = self.launch()
        self.data['compile']['argv'] = [sys.executable, '-c', 'raise SystemExit(7)']
        self.save_submit()
        repeated, state = replay(record)
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'COMPILE_FAIL')
        self.assertFalse(list(repeated.parent.rglob('run.log')))
        del self.data['compile']
        self.save_submit()
        before = list(record.parent.glob('rerun_*'))
        with self.assertRaisesRegex(ConfigError, 'requires a compile step'):
            replay(record)
        self.assertEqual(list(record.parent.glob('rerun_*')), before)

    def test_removed_case_and_renamed_submit_are_not_silently_substituted(self):
        self.data['fake'] = {'outcomes': {'fast': 'fail'}}
        self.save_submit()
        record, _ = self.launch()
        self.cases.write_text('smoke\n')
        with self.assertRaisesRegex(ConfigError, 'cases missing'):
            replay(record)
        self.cases.write_text('fast\n')
        self.data['name'] = 'renamed'
        self.save_submit()
        with self.assertRaisesRegex(ConfigError, 'submit names'):
            replay(record)
        self.assertFalse(list(record.parent.glob('rerun_*')))

    def test_seed_and_root_cli_overrides_are_rejected_including_aliases(self):
        for args in (['rerun', '/record', '--seed', '42'], ['rr', '/record', '-S42'],
                     ['rerun', '/record', '-o', str(self.out)], ['-o', str(self.out), 'rr', '/record']):
            result = subprocess.run([sys.executable, '-m', 'nsim', *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_new_input_identity_is_still_checked_before_execution(self):
        self.data['fake'] = {'outcomes': {'fast': 'fail'}}
        self.save_submit()
        record, _ = self.launch()
        def changed(plan, roots, **settings):
            (self.project / 'input.txt').write_text('changed after planning')
            return run(plan, roots, **settings)
        with patch('nsim.service.run', side_effect=changed), self.assertRaises(IntegrityError):
            replay(record)
        self.assertFalse(list(record.parent.glob('rerun_*')))

    def test_named_symlink_rerun_directory_is_rejected_without_writing_elsewhere(self):
        self.data['fake'] = {'outcomes': {'fast': 'fail'}}
        self.save_submit()
        record, _ = self.launch()
        (record.parent / 'occupied').symlink_to(self.backup, target_is_directory=True)
        with self.assertRaisesRegex(ConfigError, 'symlink'):
            replay(record, output_dir='occupied')
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_single_external_simulation_rebuilds_inside_original_workspace(self):
        self.data['fake'] = {'outcomes': {'fast': 'fail'}}
        self.save_submit()
        plan = create_plan(self.project, cases=['fast'], seeds=[42], submit_paths=[self.submit],
                           sim_dir=str(self.base / 'external'))
        record, _ = run(plan, [self.out])
        before = digest(record / 'run.json')
        repeated, _ = replay(record)
        self.assertEqual(repeated.parent.parent, self.out / 'base' / 'rtl')
        self.assertEqual(digest(record / 'run.json'), before)

    def test_no_failed_tasks_does_not_compile_or_create_rerun_directory(self):
        record, _ = self.launch()
        result, state = replay(record)
        self.assertIsNone(result)
        self.assertTrue(state['passed'])
        self.assertEqual(state['tasks'], [])
        self.assertFalse(list(record.parent.glob('rerun_*')))

    def test_explicit_directory_name_is_never_reused(self):
        self.data['fake'] = {'outcomes': {'fast': 'fail'}}
        self.save_submit()
        record, _ = self.launch()
        first, _ = replay(record, output_dir='chosen_rerun')
        self.assertEqual(first.parent.name, 'chosen_rerun')
        before = digest(first / 'final.json')
        with self.assertRaisesRegex(FileExistsError, 'already exists'):
            replay(record, output_dir='chosen_rerun')
        self.assertEqual(digest(first / 'final.json'), before)

    def test_recover_nested_rerun_uses_its_own_historical_plan(self):
        self.data['fake'] = {'outcomes': {'fast': 'fail'}}
        self.save_submit()
        record, _ = self.launch()
        self.data['fake'] = {}
        self.save_submit()
        repeated, state = replay(record)
        Path(state['tasks'][0]['attempts'][0]['result']).write_text('broken')
        recovered, result = replay(repeated, recovery=True, roots=[self.backup])
        self.assertTrue(result['passed'])
        self.assertEqual(recovered.parent.parent, self.backup / 'regr_dir' / record.parent.name)
