"""File identity, phase separation, inheritance and actual local consumption."""

import base64
import json
from pathlib import Path
import sys

from test_regression import Fixture
from nsim.config import ConfigError, load_submit
from nsim.eda import commands
from nsim.planning import check_identity, create_plan
from nsim.service import run
from nsim.storage import IntegrityError, write_json
from nsim.submit_commands import inspect


class SubmitFileTests(Fixture):
    def setUp(self):
        super().setUp()
        self.compile_input = self.project / 'coverage.hier'
        self.runtime_input = self.project / 'run.tcl'
        self.analysis_input = self.project / 'threshold.json'
        for path in (self.compile_input, self.runtime_input, self.analysis_input):
            path.write_text('original\n')
        self.data['files'] = {
            'hier': {'path': 'coverage.hier', 'phases': ['compile']},
            'ucli': {'path': 'run.tcl', 'phases': ['simulate']},
            'threshold': {'path': 'threshold.json', 'phases': ['analysis']},
        }
        self.save_submit()

    def test_phase_hashes_and_exact_replay(self):
        first = self.plan()
        build = first['submits'][0]['build_inputs']
        self.assertIn(str(self.compile_input), build)
        self.assertNotIn(str(self.runtime_input), build)
        self.assertNotIn(str(self.analysis_input), build)
        self.runtime_input.write_text('changed Tcl\n')
        self.analysis_input.write_text('changed threshold\n')
        second = self.plan()
        self.assertEqual(first['tasks'][0]['build_id'], second['tasks'][0]['build_id'])
        with self.assertRaises(IntegrityError):
            check_identity(first)
        self.compile_input.write_text('changed hierarchy\n')
        third = self.plan()
        self.assertNotEqual(second['tasks'][0]['build_id'], third['tasks'][0]['build_id'])

    def test_multiple_submits_do_not_share_compile_dependencies(self):
        other_input = self.project / 'other.hier'
        other_input.write_text('other')
        other = self.project / 'other.json'
        other_data = dict(self.data, name='other', files={'hier': {'path': 'other.hier', 'phases': ['compile']}})
        write_json(other, other_data)
        first = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit, other], seeds=[42])
        self.compile_input.write_text('changed first submit')
        second = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit, other], seeds=[42])
        self.assertNotEqual(first['tasks'][0]['build_id'], second['tasks'][0]['build_id'])
        self.assertEqual(first['tasks'][1]['build_id'], second['tasks'][1]['build_id'])

    def test_legacy_project_inputs_remain_conservative(self):
        self.descriptor.write_text('schema_version=1\nname="demo"\ninputs=["run.tcl"]\n')
        first = self.plan()
        self.runtime_input.write_text('changed legacy input')
        second = self.plan()
        self.assertNotEqual(first['tasks'][0]['build_id'], second['tasks'][0]['build_id'])

    def test_vcs_and_case_references_render_once(self):
        special = self.project / 'data {seed} with spaces.hex'
        special.write_text('00\n')
        (self.project / 'top.sv').write_text('module tb; endmodule\n')
        (self.project / 'top.f').write_text('top.sv\n')
        self.data.update(adapter='vcs', required_checks=['functional'],
                         vcs={'filelist': 'top.f', 'compile_args': ['-cm_hier', '{file:hier}'],
                              'run_args': ['-ucli', '-do', '{file:ucli}', '+LITERAL={{file:ucli}}']})
        self.data['files']['data'] = {'path': special.name, 'phases': ['simulate']}
        self.data['case_defaults'] = {'c_test': '{file:data}'}
        self.save_submit()
        plan = self.plan(allow_missing_tools=True)
        preview = commands(plan, plan['tasks'][0], self.out)
        self.assertIn(str(self.compile_input), preview['compile']['argv'])
        self.assertIn(str(self.runtime_input), preview['simulate'])
        self.assertIn('+C_TEST=' + str(special), preview['simulate'])
        self.assertIn('+LITERAL={file:ucli}', preview['simulate'])

    def test_unknown_and_wrong_phase_references_rejected(self):
        for reference in ('{file:missing}', '{file:hier}', '{file:ucli!r}', '{file:}'):
            with self.subTest(reference=reference):
                self.data['plusargs'] = ['+FILE=' + reference]
                self.save_submit()
                with self.assertRaises(ConfigError):
                    load_submit(self.submit)

    def test_references_in_environment_and_output_paths_are_rejected(self):
        self.data['env'] = {'DATA': '{file:ucli}'}
        self.save_submit()
        with self.assertRaisesRegex(ConfigError, 'not supported in env'):
            load_submit(self.submit)

    def test_invalid_declarations(self):
        for value in ([], {'bad': {}}, {'bad': {'path': 'x', 'phases': ['bad']}},
                      {'bad': {'path': 'x', 'phases': ['compile', 'compile']}},
                      {'bad': {'path': 'x', 'phases': ['compile'], 'snapshot': 1}}):
            with self.subTest(value=value):
                self.data['files'] = value
                self.save_submit()
                with self.assertRaises(ConfigError):
                    load_submit(self.submit)

    def test_missing_inputs_and_empty_patterns_fail_preflight(self):
        for path in ('missing', 'missing-*.tcl'):
            self.data['files']['ucli']['path'] = path
            self.save_submit()
            with self.assertRaises(ConfigError):
                self.plan()

    def test_directory_and_dependency_pattern_membership(self):
        scripts = self.project / 'scripts'
        scripts.mkdir()
        (scripts / 'a.tcl').write_text('run\n')
        self.data['files']['ucli']['dependencies'] = ['scripts/*.tcl']
        self.data['files']['tree'] = {'path': 'scripts', 'phases': ['simulate']}
        self.save_submit()
        plan = self.plan()
        self.assertIn(str(scripts / 'a.tcl'), plan['input_hashes'])
        (scripts / 'b.tcl').write_text('quit\n')
        with self.assertRaisesRegex(ConfigError, 'membership'):
            check_identity(plan)
        second = self.plan()
        self.assertEqual(plan['tasks'][0]['build_id'], second['tasks'][0]['build_id'])

    def test_glob_cannot_be_single_argv_reference(self):
        self.data['files']['ucli']['path'] = '*.tcl'
        self.data['plusargs'] = ['+FILE={file:ucli}']
        self.save_submit()
        with self.assertRaisesRegex(ConfigError, 'pattern'):
            load_submit(self.submit)

    def test_symlink_retarget_to_same_bytes_is_detected(self):
        other = self.project / 'other.tcl'
        other.write_bytes(self.runtime_input.read_bytes())
        link = self.project / 'link.tcl'
        link.symlink_to(self.runtime_input)
        self.data['files']['ucli']['path'] = 'link.tcl'
        self.save_submit()
        plan = self.plan()
        link.unlink()
        link.symlink_to(other)
        with self.assertRaisesRegex(ConfigError, 'link target'):
            check_identity(plan)

    def test_directory_symlink_cycle_rejected(self):
        tree = self.project / 'tree'
        tree.mkdir()
        (tree / 'cycle').symlink_to(tree, target_is_directory=True)
        self.data['files']['ucli']['path'] = 'tree'
        self.save_submit()
        with self.assertRaisesRegex(ConfigError, 'directory symlink'):
            self.plan()

    def test_snapshots_binary_and_opt_out(self):
        self.runtime_input.write_bytes(b'\x00\xffbinary')
        self.data['files']['hier']['snapshot'] = False
        self.save_submit()
        plan = self.plan()
        saved = plan['file_snapshots'][str(self.runtime_input)]
        self.assertEqual(base64.b64decode(saved['content']), self.runtime_input.read_bytes())
        self.assertNotIn(str(self.compile_input), plan['file_snapshots'])
        self.assertIn(str(self.compile_input), plan['input_hashes'])

    def test_inheritance_override_remove_and_project_relative_paths(self):
        folder = self.project / 'submit' / 'common'
        folder.mkdir(parents=True)
        parent = folder / 'base.json'
        write_json(parent, self.data)
        child = self.project / 'submit' / 'child.json'
        write_json(child, {'schema_version': 1, 'name': 'child', 'extends': 'common/base.json',
                          'files': {'hier': None, 'ucli': {'path': 'threshold.json'}},
                          'plusargs': ['+FILE={file:ucli}']})
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[child], seeds=[42])
        self.assertNotIn('hier', plan['submits'][0]['files'])
        self.assertIn('+FILE=' + str(self.analysis_input), commands(plan, plan['tasks'][0], self.out)['simulate'])

    def test_error_filter_reference_uses_rule_phase_for_build_identity(self):
        rule_path = self.project / 'rules.json'
        rules = {'schema_version': 1, 'rules': [dict(phase='simulate', code='KNOWN', message='one', reason='reviewed')]}
        write_json(rule_path, rules)
        self.data['files']['errors'] = {'path': 'rules.json', 'phases': ['analysis']}
        self.data['log_analysis'] = {'error_filter': '{file:errors}'}
        self.save_submit()
        first = self.plan()
        self.assertIn(str(rule_path), first['input_hashes'])
        rules['rules'][0]['message'] = 'two'
        write_json(rule_path, rules)
        second = self.plan()
        self.assertEqual(first['tasks'][0]['build_id'], second['tasks'][0]['build_id'])
        rules['rules'][0]['phase'] = 'compile'
        write_json(rule_path, rules)
        third = self.plan()
        self.assertNotEqual(second['tasks'][0]['build_id'], third['tasks'][0]['build_id'])

    def test_caselist_and_cli_references(self):
        self.cases.write_text('smoke +DATA={file:ucli}\n')
        plan = self.plan(plusargs=['+LIMITS={file:ucli}'])
        argv = commands(plan, plan['tasks'][0], self.out)['simulate']
        self.assertIn('+DATA=' + str(self.runtime_input), argv)
        self.assertIn('+LIMITS=' + str(self.runtime_input), argv)

    def test_inspect_exposes_resolved_inputs(self):
        result = inspect(self.project, self.submit)
        self.assertIn('resolved_files', result)
        self.assertEqual(result['resolved_files']['ucli']['resolved_path'], str(self.runtime_input))

    def test_external_empty_input_directory_cannot_be_an_output(self):
        external = self.base / 'empty_inputs'
        external.mkdir()
        self.data['files']['directory'] = {'path': str(external), 'phases': ['simulate']}
        self.save_submit()
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42],
                           com_dir=str(external / 'compile'))
        with self.assertRaisesRegex(ConfigError, 'overlap'):
            run(plan, [self.out], dry_run=True)

    def test_command_file_references_execute_with_separate_build_and_run_inputs(self):
        build_script = self.project / 'build.py'
        runner = self.project / 'run.py'
        build_script.write_text('from pathlib import Path\nPath("model").write_text("built")\n')
        runner.write_text('import json,sys\nfrom pathlib import Path\n'
                          'assert Path(sys.argv[1]).read_text() == "original\\n"\n'
                          'Path(sys.argv[2]).write_text(json.dumps({"schema_version":1,"case":sys.argv[3],'
                          '"seed":int(sys.argv[4]),"complete":True,"checks":{"functional":{"executed":True,"passed":True}}}))\n')
        self.descriptor.write_text('schema_version=1\nname="demo"\ninputs=[]\n')
        self.data['files'].update(builder={'path': 'build.py', 'phases': ['compile']},
                                  runner={'path': 'run.py', 'phases': ['simulate']})
        self.data.update(adapter='command', required_checks=['functional'],
                         compile={'argv': [sys.executable, '{file:builder}'], 'artifacts': ['model']},
                         argv=[sys.executable, '{file:runner}', '{file:ucli}', '{result}', '{case}', '{seed}'])
        self.save_submit()
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        _, state = run(plan, [self.out])
        self.assertTrue(state['passed'], state)
        runner.write_text(runner.read_text() + '# runtime-only change\n')
        second = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42], phase='sim')
        self.assertEqual(plan['tasks'][0]['build_id'], second['tasks'][0]['build_id'])
        _, state = run(second, [self.out])
        self.assertTrue(state['passed'], state)

    def test_sim_reuses_build_after_runtime_input_change(self):
        compiled = create_plan(self.project, submit_paths=[self.submit], phase='com')
        _, state = run(compiled, [self.out])
        self.assertTrue(state['passed'])
        self.runtime_input.write_text('new runtime configuration\n')
        simulated = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42], phase='sim')
        self.assertEqual(compiled['tasks'][0]['build_id'], simulated['tasks'][0]['build_id'])
        _, state = run(simulated, [self.out])
        self.assertTrue(state['passed'], state)

    def test_external_file_is_read_only_for_hooks(self):
        external = self.base / 'external.json'
        external.write_text('original')
        self.data['files']['external'] = {'path': str(external), 'phases': ['simulate']}
        self.data['hooks'] = {'pre_sim': {'name': 'write_input', 'argv': [sys.executable, '-c',
            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("changed")', '{file:external}']}}
        self.save_submit()
        _, state = run(create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42]), [self.out])
        self.assertFalse(state['passed'])
        self.assertEqual(external.read_text(), 'original')
