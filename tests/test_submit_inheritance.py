"""Submit templates preserve merge, mode, path and reproducibility contracts."""

import json
from pathlib import Path
import re
import sys
from unittest.mock import patch

from test_regression import Fixture
from nsim.config import ConfigError, MODES, load_submit
from nsim.planning import check_identity
from nsim.service import run
from nsim.storage import IntegrityError, digest, write_json
from nsim.submit_templates import common_submit, guide, scaffold


class SubmitInheritanceTests(Fixture):
    def setUp(self):
        super().setUp()
        self.common = self.project / 'common' / 'rtl.json'
        self.common.parent.mkdir()
        self.parent = dict(self.data)
        self.parent.pop('name')
        write_json(self.common, self.parent)
        self.data = {'schema_version': 1, 'name': 'child', 'extends': 'common/rtl.json'}
        write_json(self.submit, self.data)

    def save(self):
        write_json(self.common, self.parent)
        write_json(self.submit, self.data)

    def test_nested_merge_arrays_falsy_values_and_defaults(self):
        self.parent.update(env={'KEEP': 'parent', 'REPLACE': 'old'}, plusargs=['+parent'],
                           scheduler={'kind': 'lsf', 'queue': 'normal', 'resources': ['old'], 'licenses': {'sim': 1}},
                           log_analysis={'max_diagnostics': 12, 'settle_seconds': 1}, settle_seconds=3)
        self.data.update(env={'REPLACE': 'new', 'ADD': 'child'}, plusargs=[], settle_seconds=0,
                         scheduler={'resources': [], 'licenses': {'other': 2}}, log_analysis={'settle_seconds': 0})
        self.save()
        loaded = load_submit(self.submit)
        self.assertEqual(loaded['env'], {'KEEP': 'parent', 'REPLACE': 'new', 'ADD': 'child'})
        self.assertEqual(loaded['scheduler']['licenses'], {'sim': 1, 'other': 2})
        self.assertEqual(loaded['scheduler']['resources'], [])
        self.assertEqual(loaded['scheduler']['queue'], 'normal')
        self.assertEqual(loaded['scheduler']['poll_seconds'], 1)
        self.assertEqual(loaded['plusargs'], [])
        self.assertEqual(loaded['settle_seconds'], 0)
        self.assertEqual(loaded['log_analysis']['settle_seconds'], 0)
        self.assertEqual(loaded['log_analysis']['max_diagnostics'], 12)
        self.assertNotIn('extends', loaded)

    def test_error_filter_path_in_parent_is_anchored_and_hashed(self):
        rules = self.project / 'rules' / 'errors.json'
        rules.parent.mkdir()
        write_json(rules, {'schema_version': 1, 'rules': [
            {'phase': 'simulate', 'log_file': 'run.log', 'code': 'KNOWN',
             'message': 'reviewed', 'reason': 'known tool output'}]})
        self.parent['log_analysis'] = {'error_filter': '../rules/errors.json'}
        self.save()
        loaded = load_submit(self.submit)
        self.assertEqual(loaded['log_analysis']['error_filter']['path'], str(rules))
        self.assertEqual(loaded['log_analysis']['error_filter']['sha256'], digest(rules))
        plan = self.plan()
        self.assertIn(str(rules), plan['input_hashes'])
        self.assertEqual(plan['snapshots'][str(rules)], rules.read_text())
        write_json(rules, {'schema_version': 1, 'rules': []})
        with self.assertRaises(IntegrityError):
            check_identity(plan)

    def test_error_filter_rejects_missing_or_unsafe_rules(self):
        self.data['log_analysis'] = {'error_filter': 'errors.json'}
        self.save()
        with self.assertRaisesRegex(ConfigError, 'regular file'):
            load_submit(self.submit)
        rules = self.project / 'errors.json'
        for rule in (
                {'phase': 'simulate', 'code': 'UVM_SUMMARY', 'message': 'any', 'reason': 'unsafe'},
                {'phase': 'compile', 'log_file': 'run.log', 'code': 'E', 'message': 'known', 'reason': 'wrong phase'},
                {'phase': 'simulate', 'code': 'E', 'message': 'known', 'reason': ''}):
            with self.subTest(rule=rule):
                write_json(rules, {'schema_version': 1, 'rules': [rule]})
                with self.assertRaises(ConfigError):
                    load_submit(self.submit)

    def test_boolean_and_dpi_override_before_defaults(self):
        self.parent = common_submit('rtl')
        self.parent['vcs'].update(fsdb=True, dpi={'makefile': 'model/Makefile'})
        self.data['vcs'] = {'fsdb': False, 'dpi': None, 'compile_args': []}
        self.save()
        loaded = load_submit(self.submit)
        self.assertFalse(loaded['vcs']['fsdb'])
        self.assertIsNone(loaded['vcs']['dpi'])
        self.assertEqual(loaded['vcs']['compile_args'], [])
        self.assertEqual(loaded['vcs']['fsdb_plusargs'], ['+fsdb', '+NSIM_FSDB={wave}'])

    def test_hook_stages_replace_in_singleton_and_array_forms(self):
        self.parent['hooks'] = {
            'pre_sim': {'name': 'old', 'argv': ['old'], 'timeout_seconds': 10},
            'post_sim': [{'name': 'collect', 'argv': ['collect']}],
            'pre_compile': {'name': 'prepare', 'argv': ['prepare']}}
        self.data['hooks'] = {'pre_sim': {'name': 'new', 'argv': ['new']}, 'post_sim': []}
        self.save()
        hooks = load_submit(self.submit)['hooks']
        self.assertEqual(hooks['pre_sim'], [{'name': 'new', 'argv': ['new'], 'timeout_seconds': 60}])
        self.assertEqual(hooks['post_sim'], [])
        self.assertEqual(hooks['pre_compile'][0]['name'], 'prepare')

    def test_leaf_must_declare_name_even_if_parent_has_one(self):
        self.parent['name'] = 'parent'
        self.data.pop('name')
        self.save()
        with self.assertRaisesRegex(ConfigError, 'own name'):
            load_submit(self.submit)

    def test_invalid_parent_references_are_rejected(self):
        for ref in ('', '  ', None, [], ['common/rtl.json'], 42, 'missing.json', 'submit.json'):
            with self.subTest(ref=ref):
                self.data['extends'] = ref
                self.save()
                with self.assertRaises(ConfigError):
                    load_submit(self.submit)

    def test_parent_chain_and_cycle_are_rejected(self):
        for ref in ('../submit.json', 'rtl.json', 'another.json'):
            self.parent['extends'] = ref
            self.save()
            with self.assertRaisesRegex(ConfigError, 'one parent layer'):
                load_submit(self.submit)

    def test_strict_json_and_schema_errors_in_parent(self):
        for content in ('[]', '{"schema_version": 1, "schema_version": 1}',
                        '{"schema_version": 1, "unexpected": 1}', '{"schema_version": true}',
                        '{"schema_version": 2}', '{"schema_version": 1, "timeout_seconds": NaN}',
                        '{"schema_version": 1, "adapter": "fake", "scheduler": {"unknown": 1}}'):
            with self.subTest(content=content):
                self.common.write_text(content)
                with self.assertRaises(ConfigError):
                    load_submit(self.submit)

    def test_parent_can_be_partial_but_leaf_must_be_complete(self):
        self.parent = {'schema_version': 1, 'mode': 'fpga', 'adapter': 'command',
                       'compile': {'argv': ['builder']}}
        self.data.update(argv=['{build_dir}/simulator'], compile={'artifacts': ['simulator']})
        self.save()
        self.assertEqual(load_submit(self.submit)['compile']['argv'], ['builder'])
        self.data.pop('argv')
        self.save()
        with self.assertRaises(ConfigError):
            load_submit(self.submit)

    def test_declaring_file_owns_relative_tools_and_cli_cannot_switch_mode(self):
        self.parent.update(mode='gate', adapter='command', argv=['./parent_tool'],
                           compile={'argv': ['./build'], 'artifacts': ['simulator']},
                           hooks={'pre_sim': {'name': 'prepare', 'argv': ['./hook']}})
        self.data.update(scheduler={'bsub': './child_bsub'})
        self.save()
        loaded = load_submit(self.submit)
        self.assertEqual(loaded['argv'][0], str(self.common.parent / 'parent_tool'))
        self.assertEqual(loaded['compile']['argv'][0], str(self.common.parent / 'build'))
        self.assertEqual(loaded['hooks']['pre_sim'][0]['argv'][0], str(self.common.parent / 'hook'))
        self.assertEqual(loaded['scheduler']['bsub'], str(self.project / 'child_bsub'))
        with self.assertRaisesRegex(ConfigError, 'mode rtl does not match'):
            self.plan(mode='rtl')

    def test_explicit_absolute_parent_and_multiple_independent_parents(self):
        alternate = self.project / 'common' / 'special.json'
        write_json(alternate, dict(self.parent, timeout_seconds=123))
        self.data['extends'] = str(alternate)
        self.save()
        loaded = load_submit(self.submit)
        self.assertEqual(loaded['timeout_seconds'], 123)
        self.data['extends'] = 'common/rtl.json'
        self.save()
        self.assertEqual(load_submit(self.submit)['timeout_seconds'], 60)

    def test_plan_snapshots_both_files_and_detects_parent_changes(self):
        plan = self.plan()
        self.assertEqual(plan['submits'][0]['source_chain'], [str(self.common), str(self.submit)])
        for path in (self.common, self.submit):
            self.assertEqual(plan['snapshots'][str(path)], path.read_text())
            self.assertEqual(plan['input_hashes'][str(path)], digest(path))
        check_identity(plan)
        self.parent['timeout_seconds'] = 100
        self.save()
        with self.assertRaises(IntegrityError):
            check_identity(plan)

    def test_parent_edit_during_planning_is_rejected(self):
        from nsim.eda import prepare
        def changed(*args, **kwargs):
            result = prepare(*args, **kwargs)
            self.common.write_text(self.common.read_text() + '\n')
            return result
        with patch('nsim.eda.prepare', side_effect=changed):
            with self.assertRaisesRegex(ConfigError, 'configuration changed during planning'):
                self.plan()

    def test_simulation_parent_edits_do_not_change_compile_identity(self):
        self.parent = common_submit('rtl')
        self.parent['vcs']['filelist'] = 'rtl.f'
        (self.project / 'rtl.f').write_text('dut.sv\n')
        (self.project / 'dut.sv').write_text('module dut; endmodule\n')
        self.save()
        first = self.plan(allow_missing_tools=True)['tasks'][0]['build_id']
        self.parent['vcs']['run_args'] = ['+TRACE=1']
        self.save()
        second = self.plan(allow_missing_tools=True)['tasks'][0]['build_id']
        self.assertEqual(first, second)
        self.parent['vcs']['compile_args'].append('+define+NEW_BUILD')
        self.save()
        self.assertNotEqual(first, self.plan(allow_missing_tools=True)['tasks'][0]['build_id'])

    def test_inherited_command_runs_and_keeps_generated_product_placeholder(self):
        self.parent.update(adapter='command', argv=['./simulator', '{case}', '{seed}', '{result}'],
                           required_checks=['functional'])
        simulator = self.common.parent / 'simulator'
        simulator.write_text('#!' + sys.executable + '\nimport json, sys\nfrom pathlib import Path\n'
                             'case, seed, result = sys.argv[1:]\n'
                             'Path(result).write_text(json.dumps({"schema_version":1,"case":case,"seed":int(seed),'
                             '"complete":True,"checks":{"functional":{"executed":True,"passed":True}}}))\n')
        simulator.chmod(0o755)
        self.save()
        plan = self.plan()
        self.assertEqual(plan['submits'][0]['argv'][0], str(simulator))
        self.assertIn(str(simulator), plan['input_hashes'])
        _, state = run(plan, [self.out])
        self.assertTrue(state['passed'])
        self.data.update(argv=['{build_dir}/simulator'], compile={'argv': ['builder'], 'artifacts': ['simulator']})
        self.save()
        self.assertEqual(load_submit(self.submit)['argv'][0], '{build_dir}/simulator')

    def test_generated_examples_and_all_documented_json_parse(self):
        folder = self.project / 'submit'
        for relative, content in scaffold().items():
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        default = load_submit(folder / 'submit_demo.json')
        self.assertEqual(default['mode'], 'rtl')
        self.assertFalse(default['vcs']['golden_enabled'])
        for mode in MODES:
            child = dict(schema_version=1, name=mode + '_demo', extends='submit/common/' + mode + '.json')
            if mode not in ('rtl', 'gate'):
                child['argv'] = ['platform_runner']
            write_json(self.submit, child)
            self.assertEqual(load_submit(self.submit)['mode'], mode)
        for block in re.findall(r'```json\n(.*?)\n```', guide(), flags=re.S):
            target = folder / 'check.json'
            write_json(target, json.loads(block))
            load_submit(target)
        self.assertEqual(guide(), (Path(__file__).resolve().parent.parent / 'docs' / 'submit.md').read_text())
