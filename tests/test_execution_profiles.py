"""Scenario defaults and explicit VCS stage overrides in saved command previews."""

import json
from contextlib import redirect_stderr
from io import StringIO

from test_regression import Fixture
from nsim.cli import build_parser, execution_options, prepare_execution
from nsim.config import ConfigError
from nsim.eda import commands
from nsim.layout import build_directory
from nsim.planning import create_plan
from nsim.service import run
import test_execution_stages


class ExecutionProfileTests(Fixture):
    def setUp(self):
        super().setUp()
        (self.project / 'top.sv').write_text('module tb; endmodule\n')
        (self.project / 'top.f').write_text('top.sv\n')
        (self.project / 'hier.txt').write_text('+tree tb\n')
        self.data.update(adapter='vcs', required_checks=['functional'],
                         vcs={'filelist': 'top.f', 'coverage': {
                             'compile_args': ['-cm', 'line+cond', '-cm_hier', str(self.project / 'hier.txt')],
                             'run_args': ['-cm', 'line+cond', '-cm_dir', 'old-vdb']},
                              'fsdb_plusargs': ['+fsdb', '+WAVE={wave}']})
        self.save_submit()

    def selected(self, phase='run', options=None):
        plan = create_plan(self.project, cases=['smoke'] if phase in ('run', 'sim') else None,
                           caselist=self.cases if phase == 'regr' else None,
                           submit_paths=[self.submit], seeds=None if phase == 'com' else [42], phase=phase,
                           allow_missing_tools=True, execution_options=options)
        return plan, commands(plan, plan['tasks'][0], self.out)

    def test_scenario_defaults_and_explicit_switches(self):
        single, preview = self.selected()
        self.assertEqual(single['submits'][0]['effective_profile']['coverage'], False)
        self.assertEqual(single['submits'][0]['effective_profile']['waves'], True)
        self.assertNotIn('-cm', preview['compile']['argv'])
        self.assertNotIn('-cm', preview['simulate'])
        self.assertIn('+fsdb', preview['simulate'])

        regression, preview = self.selected('regr')
        self.assertIn('-cm', preview['compile']['argv'])
        self.assertIn('-cm', preview['simulate'])
        self.assertNotIn('+fsdb', preview['simulate'])
        self.assertNotEqual(single['tasks'][0]['build_id'], regression['tasks'][0]['build_id'])

        enabled, preview = self.selected(options={'coverage': True, 'waves': False})
        self.assertIn('-cm', preview['compile']['argv'])
        self.assertNotIn('+fsdb', preview['simulate'])
        self.assertEqual(enabled['tasks'][0]['build_id'], regression['tasks'][0]['build_id'])

        no_waves, preview = self.selected(options={'waves': False})
        self.assertEqual(single['tasks'][0]['build_id'], no_waves['tasks'][0]['build_id'])
        self.assertNotIn('+fsdb', preview['simulate'])

    def test_coverage_recipe_overrides_and_compile_identity(self):
        options = {'coverage': True, 'cm_metrics': 'line+tgl',
                   'cm_hier': 'hier.txt', 'cm_dir': 'new-vdb',
                   'defines': ['TRACE=1'], 'compile_args': ['-debug_access+all'],
                   'sim_args': ['+VERBOSE=1']}
        plan, preview = self.selected(options=options)
        compile_argv, simulate_argv = preview['compile']['argv'], preview['simulate']
        self.assertEqual(compile_argv[compile_argv.index('-cm') + 1], 'line+tgl')
        self.assertEqual(simulate_argv[simulate_argv.index('-cm') + 1], 'line+tgl')
        self.assertEqual(compile_argv[compile_argv.index('-cm_hier') + 1], str(self.project / 'hier.txt'))
        self.assertEqual(simulate_argv[simulate_argv.index('-cm_dir') + 1], 'new-vdb')
        self.assertIn('+define+TRACE=1', compile_argv)
        self.assertIn('-debug_access+all', compile_argv)
        self.assertIn('+VERBOSE=1', simulate_argv)
        self.assertIn(str(self.project / 'hier.txt'), plan['submits'][0]['build_inputs'])
        with self.assertRaisesRegex(ConfigError, 'require --coverage on'):
            self.selected(options={'coverage': False, 'cm_metrics': 'line'})
        with self.assertRaisesRegex(ConfigError, 'coverage settings'):
            self.selected(options={'compile_args': ['-cm']})

    def test_sim_arg_overrides_submit_but_plusargs_remain_highest(self):
        self.data['vcs']['run_args'] = ['+VERBOSE=0']
        self.save_submit()
        plan, preview = self.selected(options={'sim_args': ['+VERBOSE=1']})
        self.assertIn('+VERBOSE=1', preview['simulate'])
        self.assertNotIn('+VERBOSE=0', preview['simulate'])
        selected = create_plan(self.project, cases=['smoke'], seeds=[42], submit_paths=[self.submit],
                               allow_missing_tools=True, phase='run', plusargs=['+VERBOSE=2'],
                               execution_options={'sim_args': ['+VERBOSE=1']})
        preview = commands(selected, selected['tasks'][0], self.out)
        self.assertIn('+VERBOSE=2', preview['simulate'])
        self.assertNotIn('+VERBOSE=1', preview['simulate'])
        with self.assertRaisesRegex(ConfigError, 'managed by NSIM'):
            self.selected(options={'sim_args': ['+ntb_random_seed=7']})

    def test_legacy_submit_switch_and_cm_args_are_explicitly_overridable(self):
        self.data['vcs'].update(fsdb=False, compile_args=['-cm', 'line'],
                                run_args=['-cm', 'line'])
        self.data['vcs'].pop('coverage')
        self.save_submit()
        _, preview = self.selected()
        self.assertNotIn('-cm', preview['compile']['argv'])
        self.assertNotIn('+fsdb', preview['simulate'])
        _, preview = self.selected(options={'coverage': True, 'waves': True})
        self.assertIn('-cm', preview['compile']['argv'])
        self.assertIn('+fsdb', preview['simulate'])

    def test_cli_stage_surface(self):
        parser = build_parser()
        args = parser.parse_args(['run', '--case', 'smoke', '-sub', str(self.submit),
                                  '--coverage', 'on', '--waves', 'off',
                                  '--compile-arg=-debug_access+all', '--define', 'TRACE',
                                  '--sim-arg=+VERBOSE=1'])
        selected = execution_options(args)
        self.assertIs(selected['coverage'], True)
        self.assertIs(selected['waves'], False)
        self.assertEqual(selected['compile_args'], ['-debug_access+all'])
        self.assertEqual(selected['defines'], ['TRACE'])
        self.assertEqual(selected['sim_args'], ['+VERBOSE=1'])
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(['sim', '--case', 'smoke', '-sub', str(self.submit),
                               '--compile-arg=-debug_access+all'])

    def test_sim_uses_recorded_compile_instrumentation(self):
        parser = build_parser()
        source, _ = self.selected('com', {'coverage': True})
        build = build_directory(self.out, source, source['tasks'][0])
        build.mkdir(parents=True)
        manifest = build / 'build.json'
        manifest.write_text(json.dumps({'config': source['submits'][0]['build_config']}))
        args = parser.parse_args(['sim', '--project', str(self.project), '-sub', str(self.submit),
                                  '--case', 'smoke', '--seed', '42', '-o', str(self.out),
                                  '--coverage', 'off', '--dry-run'])
        plan, _, _ = prepare_execution(args)
        profile = plan['submits'][0]['effective_profile']
        self.assertIs(profile['compile_coverage'], True)
        self.assertIs(profile['coverage'], False)
        self.assertEqual(plan['tasks'][0]['build_id'], source['tasks'][0]['build_id'])
        source, _ = self.selected('com', {'coverage': False})
        manifest.write_text(json.dumps({'config': source['submits'][0]['build_config']}))
        args.coverage = 'on'
        with self.assertRaisesRegex(ConfigError, 'coverage-instrumented compilation'):
            prepare_execution(args)


class CommandExecutionProfileTests(Fixture):
    def test_wrapper_recipes_follow_the_same_switches(self):
        test_execution_stages.ExecutionStagesTests.command_submit(self)
        simulator = self.project / 'simulator.py'
        simulator.write_text(simulator.read_text().replace('case, seed, result = sys.argv[1:]\n',
                                                           'case, seed, result = sys.argv[1:4]\n'))
        compiler = self.project / 'compiler.py'
        compiler.write_text(compiler.read_text().replace('map(Path, sys.argv[1:])',
                                                         'map(Path, sys.argv[1:4])'))
        self.data['coverage'] = {'compile_args': ['--instrument'], 'run_args': ['--collect']}
        self.data['waveform'] = {'run_args': ['+DUMP=1']}
        self.save_submit()
        single = create_plan(self.project, cases=['smoke'], seeds=[42], submit_paths=[self.submit], phase='run')
        record, state = run(single, [self.out])
        self.assertTrue(state['passed'], state)
        preview = commands(single, single['tasks'][0], self.out)
        self.assertNotIn('--instrument', preview['compile']['argv'])
        self.assertNotIn('--collect', preview['simulate'])
        self.assertIn('+DUMP=1', preview['simulate'])

        self.cases.write_text('smoke\n')
        regression = create_plan(self.project, caselist=self.cases, seeds=[42],
                                 submit_paths=[self.submit], phase='regr')
        preview = commands(regression, regression['tasks'][0], self.out)
        self.assertIn('--instrument', preview['compile']['argv'])
        self.assertIn('--collect', preview['simulate'])
        self.assertNotIn('+DUMP=1', preview['simulate'])
        _, state = run(regression, [self.out])
        self.assertTrue(state['passed'], state)

        parser = build_parser()
        args = parser.parse_args(['sim', '--project', str(self.project), '-sub', str(self.submit),
                                  '--case', 'smoke', '--seed', '42', '-o', str(self.out),
                                  '--coverage', 'on'])
        with self.assertRaisesRegex(ConfigError, 'coverage-instrumented compilation'):
            prepare_execution(args)
        compiled = create_plan(self.project, submit_paths=[self.submit], phase='com',
                               execution_options={'coverage': True})
        _, state = run(compiled, [self.out])
        self.assertTrue(state['passed'], state)
        args.coverage = 'off'
        plan, roots, settings = prepare_execution(args)
        self.assertTrue(plan['submits'][0]['effective_profile']['compile_coverage'])
        self.assertFalse(plan['submits'][0]['effective_profile']['coverage'])
        _, state = run(plan, roots, **settings)
        self.assertTrue(state['passed'], state)
