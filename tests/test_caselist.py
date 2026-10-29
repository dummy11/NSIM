"""Caselist syntax exercised through planning, command rendering and execution."""

import json
from pathlib import Path
import sys

from test_regression import Fixture
from nsim.caselist_templates import scaffold, guide
from nsim.config import ConfigError, load_submit
from nsim.eda import commands
from nsim.planning import check_identity, create_plan
from nsim.runner import command
from nsim.service import run
from nsim.storage import IntegrityError


class CaselistTests(Fixture):
    def params(self, plan, index=0):
        task = plan['tasks'][index]
        return command(plan, task, self.out / 'result.json', self.out / 'waves', self.out / 'build')

    def vcs(self):
        (self.project / 'tb.sv').write_text('module tb; endmodule\n')
        (self.project / 'tb.f').write_text('tb.sv\n')
        self.data = {'schema_version': 1, 'name': 'base', 'adapter': 'vcs',
                     'required_checks': ['functional'], 'vcs': {'filelist': 'tb.f', 'executable': 'missing-demo-vcs'},
                     'scheduler': {'kind': 'local'}}
        self.save_submit()

    def test_defaults_aliases_whitespace_and_distinct_logic_names(self):
        self.cases.write_text('''# shared test
@defaults uvm_test = chip_base_test seq = {case}
basic_seq
stride1 seq=stride_seq +STRIDE=1
stride2 seq = stride_seq +STRIDE=2
''')
        plan = self.plan()
        self.assertEqual([t['case'] for t in plan['tasks']], ['basic_seq', 'stride1', 'stride2'])
        self.assertEqual(len({t['id'] for t in plan['tasks']}), 3)
        self.assertEqual(len({t['build_id'] for t in plan['tasks']}), 1)
        for index, seq in enumerate(('basic_seq', 'stride_seq', 'stride_seq')):
            self.assertIn('+UVM_TESTNAME=chip_base_test', self.params(plan, index))
            self.assertIn('+SEQ_NAME=' + seq, self.params(plan, index))
        self.assertIn('+STRIDE=2', self.params(plan, 2))

    def test_all_layers_override_by_key_and_record_provenance(self):
        self.data['case_defaults'] = {'uvm_test': 'submit_test', 'seq': 'submit_seq'}
        self.data['plusargs'] = ['+STRIDE=0', '+KEEP=1']
        self.save_submit()
        config = self.project / 'case.yaml'
        config.write_text('''schema_version: 1
cases:
  chosen:
    seq: yaml_seq
    plusargs: ["+STRIDE=3", "+TRACE"]
  unselected:
    uvm_test: never_run
''')
        self.cases.write_text('''@defaults uvm_test=list_test seq=default_seq +STRIDE=1
@config case.yaml
chosen +SEQ_NAME=inline_seq +STRIDE=2
''')
        plan = self.plan()
        self.assertEqual(len(plan['tasks']), 1)
        args = self.params(plan)
        for arg in ('+UVM_TESTNAME=list_test', '+SEQ_NAME=yaml_seq', '+STRIDE=3', '+KEEP=1', '+TRACE'):
            self.assertIn(arg, args)
        self.assertEqual(sum(arg.startswith('+SEQ_NAME=') for arg in args), 1)
        self.assertEqual(plan['tasks'][0]['case_config']['origins']['+STRIDE'], 'case.yaml')
        self.assertIn(str(config), plan['input_hashes'])
        self.assertEqual(plan['snapshots'][str(config)], config.read_text())
        self.assertEqual(plan['case_definitions']['chosen'][0]['config'], str(config))
        config.write_text(config.read_text().replace('yaml_seq', 'changed_seq'))
        with self.assertRaises(IntegrityError):
            check_identity(plan)

    def test_null_removes_submit_arguments_and_missing_fields_inherit(self):
        self.data['plusargs'] = ['+UVM_TESTNAME=submit_test', '+SEQ_NAME=submit_seq', '+KEEP=yes']
        self.save_submit()
        self.cases.write_text('''@defaults
inherited
c_only uvm_test=null seq=null c_test=memcpy
no_entry uvm_test=null seq=null
''')
        plan = self.plan()
        self.assertIn('+SEQ_NAME=submit_seq', self.params(plan))
        for index in (1, 2):
            self.assertFalse(any(a.startswith(('+SEQ_NAME=', '+UVM_TESTNAME=')) for a in self.params(plan, index)))
            self.assertIn('+KEEP=yes', self.params(plan, index))
        self.assertIn('+C_TEST=memcpy', self.params(plan, 1))

    def test_quotes_hash_empty_flag_and_literal_braces(self):
        self.cases.write_text('''raw +MESSAGE="hello # world" +EMPTY= +TRACE +TEXT="{{value}}" +NAME={case} # comment
''')
        args = self.params(self.plan())
        for arg in ('+MESSAGE=hello # world', '+EMPTY=', '+TRACE', '+TEXT={value}', '+NAME=raw'):
            self.assertIn(arg, args)

    def test_custom_mapping_replaces_builtin_and_accepts_option_value_groups(self):
        directory = self.project / 'lists'
        directory.mkdir()
        self.cases = directory / 'smoke.list'
        (directory / 'case.yaml').write_text('''schema_version: 1
mappings:
  seq:
    argv: ["+MY_SEQ={value}"]
  scenario:
    argv: ["+SCENARIO={value}", "+OWNER={case}"]
  option:
    argv: ["--payload", "{value}"]
''')
        self.cases.write_text('''@defaults uvm_test=base seq={case}
@config case.yaml
mapped seq=real_seq scenario=overflow option="+literal value"
raw +MY_SEQ=raw_seq
''')
        plan = self.plan()
        args = self.params(plan)
        for arg in ('+MY_SEQ=real_seq', '+SCENARIO=overflow', '+OWNER=mapped', '+UVM_TESTNAME=base'):
            self.assertIn(arg, args)
        self.assertEqual(args[args.index('--payload') + 1], '+literal value')
        self.assertFalse(any(a.startswith('+SEQ_NAME=') for a in args))
        self.assertIn('+MY_SEQ=raw_seq', self.params(plan, 1))

    def test_vcs_legacy_defaults_and_modern_no_implicit_entry(self):
        self.vcs()
        self.cases.write_text('old_test\n')
        plan = self.plan(allow_missing_tools=True)
        self.assertIn('+UVM_TESTNAME=old_test', commands(plan, plan['tasks'][0], self.out)['simulate'])
        self.cases.write_text('@defaults\nfixed +MODE=fixed\n')
        plan = self.plan(allow_missing_tools=True)
        args = commands(plan, plan['tasks'][0], self.out)['simulate']
        self.assertFalse(any(a.startswith('+UVM_TESTNAME=') for a in args))
        self.assertIn('+ntb_random_seed=42', args)
        self.cases.write_text('alias\n')
        self.data['plusargs'] = ['+UVM_TESTNAME=shared_test']
        self.save_submit()
        plan = self.plan(allow_missing_tools=True)
        args = commands(plan, plan['tasks'][0], self.out)['simulate']
        self.assertEqual([a for a in args if a.startswith('+UVM_TESTNAME=')], ['+UVM_TESTNAME=shared_test'])

    def test_vcs_run_args_overridden_and_build_identity_preserved(self):
        self.vcs()
        self.data['vcs']['run_args'] = ['+UVM_TESTNAME=submit_test', '+SEQ_NAME=submit_seq', '+STRIDE=1']
        self.save_submit()
        self.cases.write_text('@defaults uvm_test=base seq=first\nlogic +STRIDE=2\n')
        before = self.plan(allow_missing_tools=True)
        self.cases.write_text('@defaults uvm_test=base seq=second\nlogic +STRIDE=3\n')
        after = self.plan(allow_missing_tools=True)
        self.assertEqual(before['tasks'][0]['build_id'], after['tasks'][0]['build_id'])
        args = commands(after, after['tasks'][0], self.out)['simulate']
        self.assertIn('+STRIDE=3', args)
        self.assertIn('+SEQ_NAME=second', args)
        self.assertNotIn('+SEQ_NAME=submit_seq', args)
        self.assertNotIn(str(self.cases), after['submits'][0]['build_inputs'])

    def test_mapping_yaml_does_not_change_compile_identity(self):
        config = self.project / 'case.yaml'
        config.write_text('schema_version: 1\nmappings:\n  seq:\n    argv: ["+SEQ1={value}"]\n')
        self.cases.write_text('@config case.yaml\nlogic seq=example\n')
        before = self.plan()
        config.write_text(config.read_text().replace('SEQ1', 'SEQ2'))
        after = self.plan()
        self.assertEqual(before['tasks'][0]['build_id'], after['tasks'][0]['build_id'])
        self.assertNotEqual(before['tasks'][0]['case_config'], after['tasks'][0]['case_config'])

    def test_cross_list_defaults_are_local_and_equal_effective_cases_deduplicate(self):
        other = self.project / 'other.list'
        self.cases.write_text('@defaults uvm_test=base seq=shared\nsame\nsame\n')
        other.write_text('same +SEQ_NAME=shared +UVM_TESTNAME=base\nraw +FLAG\n')
        plan = create_plan(self.project, caselist=[self.cases, other], submit_paths=[self.submit], seeds=[42])
        self.assertEqual([t['case'] for t in plan['tasks']], ['same', 'raw'])
        self.assertEqual(plan['tasks'][0]['caselists'], [str(self.cases), str(other)])
        self.assertNotIn('+UVM_TESTNAME=base', self.params(plan, 1))
        other.write_text('same +SEQ_NAME=different +UVM_TESTNAME=base\n')
        with self.assertRaisesRegex(ConfigError, 'conflicting definitions.*same'):
            create_plan(self.project, caselist=[self.cases, other], submit_paths=[self.submit], seeds=[42])

    def test_same_layer_conflicts_and_nsims_managed_arguments_are_rejected(self):
        for line in ('bad seq=a +SEQ_NAME=b', 'bad +X=1 +X=2', 'bad seq=a seq=b',
                     'bad +ntb_random_seed=4', 'bad +NSIM_RESULT=other.json'):
            with self.subTest(line=line):
                self.cases.write_text(line + '\n')
                with self.assertRaises(ConfigError):
                    self.plan()
        self.data['plusargs'] = ['+UVM_TESTNAME=one']
        self.data['case_defaults'] = {'uvm_test': 'two'}
        self.save_submit()
        self.cases.write_text('good\n')
        with self.assertRaisesRegex(ConfigError, 'conflicting values'):
            self.plan()

    def test_invalid_syntax_configs_templates_and_unknown_fields(self):
        for content in ('case sequence=x', 'case unknown=x', 'case seq={seed}', 'case seq={case.__class__}',
                        'case\n@defaults seq=x', '@defaults seq=x\n@defaults seq=y\ncase',
                        '@config missing.yaml\ncase', 'case "unterminated', 'case seq=', '../bad', '@defaults'):
            with self.subTest(content=content):
                self.cases.write_text(content)
                with self.assertRaises(ConfigError):
                    self.plan()
        self.cases.write_text('@config case.yaml\ncase seq=x\n')
        for config in ('schema_version: 2', 'schema_version: 1\nschema_version: 1',
                       'schema_version: 1\nunknown: 1', 'schema_version: 1\ncases: []',
                       'schema_version: 1\nmappings:\n  ? [seq]\n  : {argv: ["+SEQ={value}"]}',
                       'schema_version: 1\nmappings:\n  seq:\n    argv: ["+{value}=foo"]',
                       'schema_version: 1\ncases:\n  case:\n    seq: 42'):
            with self.subTest(config=config):
                (self.project / 'case.yaml').write_text(config)
                with self.assertRaises(ConfigError):
                    self.plan()

    def test_optional_yaml_is_not_loaded_unless_referenced(self):
        (self.project / 'case.yaml').write_text('invalid yaml: [')
        self.cases.write_text('logic seq=real_seq\n')
        plan = self.plan()
        self.assertNotIn(str(self.project / 'case.yaml'), plan['input_hashes'])
        self.assertIn('+SEQ_NAME=real_seq', self.params(plan))

    def test_submit_default_shape_validation(self):
        for value in ([], {'seq': 3}, {'sequence': 'old'}, {'case': 'cannot_rename'}, {'plusargs': ['bad']}):
            self.data['case_defaults'] = value
            self.save_submit()
            with self.assertRaises(ConfigError):
                load_submit(self.submit)

    def test_identical_semantic_and_raw_submit_templates_deduplicate(self):
        self.data['case_defaults'] = {'seq': '{case}'}
        self.data['plusargs'] = ['+SEQ_NAME={case}', '+SEED={seed}']
        self.save_submit()
        self.cases.write_text('logic\n')
        args = self.params(self.plan())
        self.assertEqual(args.count('+SEQ_NAME=logic'), 1)
        self.assertIn('+SEED=42', args)

    def test_command_simulator_argv_retains_nsim_seed_and_result_placeholders(self):
        self.data = {'schema_version': 1, 'name': 'base', 'adapter': 'command',
                     'argv': [sys.executable, '+UVM_TESTNAME={case}',
                              '+ntb_random_seed={seed}', '+NSIM_RESULT={result}'],
                     'scheduler': {'kind': 'local'}, 'required_checks': ['functional']}
        self.save_submit()
        self.cases.write_text('logic uvm_test=shared seq=real_seq\n')
        args = self.params(self.plan())
        self.assertIn('+ntb_random_seed=42', args)
        self.assertIn('+NSIM_RESULT=' + str(self.out / 'result.json'), args)
        self.assertIn('+UVM_TESTNAME=shared', args)
        self.assertNotIn('+UVM_TESTNAME=logic', args)
        for arg in ('+ntb_random_seed=7', '+NSIM_RESULT=wrong.json'):
            self.cases.write_text('logic ' + arg + '\n')
            with self.assertRaisesRegex(ConfigError, 'managed by NSIM'):
                self.plan()

    def test_every_scaffold_demo_parses_and_previews_together(self):
        self.vcs()
        directory = self.project / 'caselist'
        directory.mkdir()
        for filename, content in scaffold().items():
            (directory / filename).write_text(content)
        lists = sorted(directory.glob('*.caselist'))
        self.assertEqual(len(lists), 8)
        plan = create_plan(self.project, caselist=lists, submit_paths=[self.submit], seeds=[42], allow_missing_tools=True)
        self.assertGreater(len(plan['tasks']), 20)
        self.assertEqual(len({t['id'] for t in plan['tasks']}), len(plan['tasks']))
        self.assertEqual(len({t['build_id'] for t in plan['tasks']}), 1)
        self.assertNotIn('unselected_example', [t['case'] for t in plan['tasks']])
        previews = {task['case']: commands(plan, task, self.out)['simulate'] for task in plan['tasks']}
        self.assertIn('+UVM_SEQNAME=debug_seq', previews['override_debug'])
        self.assertIn('+STRIDE=4', previews['override_debug'])
        self.assertIn('max:tb.dut:env/timing/chip.sdf', previews['mapped_sdf'])
        self.assertNotIn('+UVM_SEQNAME=override_c', previews['override_c'])
        self.assertEqual((Path(__file__).resolve().parents[1] / 'docs/caselist.md').read_text(), guide())

    def test_fake_execution_keeps_logical_identity_and_actual_merged_arguments(self):
        self.data['plusargs'] = ['+STRIDE=1']
        self.save_submit()
        self.cases.write_text('@defaults uvm_test=base seq={case}\nlogic seq=real_seq +STRIDE=2\n')
        plan = self.plan()
        _, state = run(plan, [self.out])
        self.assertTrue(state['passed'])
        task = state['tasks'][0]
        self.assertEqual(task['case'], 'logic')
        self.assertIn('+SEQ_NAME=real_seq', task['attempts'][0]['command'])
        self.assertIn('+STRIDE=2', task['attempts'][0]['command'])
        self.assertNotIn('+STRIDE=1', task['attempts'][0]['command'])

    def test_command_execution_overrides_embedded_plusarg_without_shell_expansion(self):
        script = self.project / 'runner.py'
        script.write_text('''import json, pathlib, sys
print(repr(sys.argv[4:]))
pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(schema_version=1, case=sys.argv[2],
    seed=int(sys.argv[3]), complete=True,
    checks=dict(functional=dict(executed=True, passed=True)))))
''')
        self.descriptor.write_text('schema_version = 1\nname = "demo"\ninputs = ["input.txt", "runner.py"]\n')
        self.data = {'schema_version': 1, 'name': 'base', 'adapter': 'command', 'required_checks': ['functional'],
                     'argv': [sys.executable, str(script), '{result}', '{case}', '{seed}', '+SEQ_NAME=submit_seq'],
                     'plusargs': ['+STRIDE=1'], 'scheduler': {'kind': 'local'}, 'settle_seconds': 0}
        self.save_submit()
        self.cases.write_text('logic seq=real_seq +STRIDE=2 +LITERAL="$(touch never)"\n')
        plan = self.plan()
        _, state = run(plan, [self.out])
        self.assertTrue(state['passed'])
        args = state['tasks'][0]['attempts'][0]['command']
        self.assertEqual([a for a in args if a.startswith('+SEQ_NAME=')], ['+SEQ_NAME=real_seq'])
        self.assertIn('+LITERAL=$(touch never)', args)
        self.assertFalse((self.project / 'never').exists())
