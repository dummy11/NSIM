"""Daily submit authoring, read-only inspection and implicit execution modes."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_regression import Fixture
from nsim.config import ConfigError, load_project, load_submit
from nsim.planning import create_plan
from nsim.project_paths import ProjectError
from nsim import projects
from nsim.storage import write_json
from nsim.submit_commands import create, inspect
from nsim.submit_templates import common_submit, scaffold


class SubmitCommandsTests(Fixture):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, XDG_CONFIG_HOME=str(self.base / 'config'))
        env.start()
        self.addCleanup(env.stop)
        self.folder = self.project / 'submit'

    def cli(self, *args, success=True, cwd=None):
        result = subprocess.run([sys.executable, '-m', 'nsim', *map(str, args)], cwd=cwd or self.project,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0 if success else 2, result.stdout + result.stderr)
        return json.loads(result.stdout) if success else result.stderr

    def test_new_defaults_and_missing_common_are_created_without_copying_options(self):
        result = self.cli('submit', 'new', 'smoke')
        target = self.folder / 'smoke.json'
        self.assertEqual(json.loads(target.read_text()),
                         {'schema_version': 1, 'name': 'smoke', 'extends': 'common/rtl.json'})
        self.assertEqual(result['needs_configuration'], [])
        self.assertEqual({p.name for p in (self.folder / 'common').iterdir()}, {'rtl.json'})
        self.assertEqual(load_submit(target)['mode'], 'rtl')
        self.assertEqual(load_project(self.project).schema_version, 1)
        self.assertFalse((self.project / '.nsim').exists())
        self.assertEqual(list(self.out.iterdir()), [])

    def test_mode_parent_and_name_suffix(self):
        result = self.cli('submit', 'new', 'gate_debug.json', '--mode', 'gate')
        target = self.folder / 'gate_debug.json'
        self.assertEqual(result['mode'], 'gate')
        self.assertEqual(load_submit(target)['name'], 'gate_debug')
        self.assertEqual(json.loads(target.read_text())['extends'], 'common/gate.json')

    def test_custom_parent_mode_inferred_and_shared_edits_flow_through(self):
        parent = self.folder / 'common' / 'special.json'
        parent.parent.mkdir(parents=True)
        raw = dict(self.data, mode='gate', plusargs=['+BASE=1'])
        write_json(parent, raw)
        self.cli('submit', 'new', 'custom', '--extends', 'common/special.json', '--project', self.project, cwd=self.base)
        target = self.folder / 'custom.json'
        self.assertEqual(load_submit(target)['mode'], 'gate')
        self.assertEqual(load_submit(target)['plusargs'], ['+BASE=1'])
        raw['plusargs'] = ['+BASE=2']
        write_json(parent, raw)
        self.assertEqual(load_submit(target)['plusargs'], ['+BASE=2'])
        error = self.cli('submit', 'new', 'wrong', '--extends', 'common/special.json', '--mode', 'rtl', success=False)
        self.assertIn('does not match', error)
        self.assertFalse((self.folder / 'wrong.json').exists())

    def test_hardware_skeleton_explains_missing_argv_and_can_use_common_command(self):
        result = self.cli('submit', 'new', 'board', '--mode', 'fpga')
        self.assertEqual(result['needs_configuration'], ['argv'])
        target = self.folder / 'board.json'
        self.assertNotIn('argv', json.loads(target.read_text()))
        self.assertIn('argv', self.cli('submit', 'check', 'board', success=False))
        parent = self.folder / 'common' / 'fpga.json'
        raw = json.loads(parent.read_text())
        raw['argv'] = ['platform_runner', '{case}']
        write_json(parent, raw)
        self.assertTrue(self.cli('submit', 'check', 'board')['configuration_valid'])

    def test_new_preserves_existing_files_and_rejects_bad_parent_and_name(self):
        self.cli('submit', 'new', 'existing')
        before = {p: p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        self.assertIn('already exists', self.cli('submit', 'new', 'existing', success=False))
        self.assertIn('cannot read submit', self.cli('submit', 'new', 'bad', '--extends', 'absent.json', success=False))
        for ident in ('../escape', 'regr_dir', '.', '/tmp/outside'):
            with self.subTest(ident=ident):
                self.cli('submit', 'new', ident, success=False)
        self.assertEqual(before, {p: p.read_bytes() for p in self.folder.rglob('*') if p.is_file()})

    def test_invalid_parent_fields_do_not_publish_a_child(self):
        parent = self.folder / 'common' / 'fpga.json'
        parent.parent.mkdir(parents=True)
        for invalid in ({}, None, 'wrong'):
            write_json(parent, dict(common_submit('fpga'), argv=invalid))
            with self.assertRaises(ConfigError):
                create(self.project, 'bad', mode='fpga')
            self.assertFalse((self.folder / 'bad.json').exists())

    def test_show_and_check_are_read_only_and_resolve_project_from_subdirectory(self):
        self.cli('submit', 'new', 'view')
        before = {p: p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        sub = self.project / 'work'
        sub.mkdir()
        show = self.cli('submit', 'show', 'view', cwd=sub)
        self.assertEqual(show['effective']['vcs']['compile_args'], ['-full64', '-sverilog'])
        self.assertEqual(show['source_chain'], [str(self.folder / 'common' / 'rtl.json'), str(self.folder / 'view.json')])
        checked = self.cli('submit', 'check', 'view', cwd=sub)
        self.assertTrue(checked['configuration_valid'])
        self.assertNotIn('effective', checked)
        self.assertEqual(before, {p: p.read_bytes() for p in self.project.rglob('*') if p.is_file()})
        self.assertEqual(list(self.out.iterdir()), [])
        with patch('subprocess.Popen', side_effect=AssertionError('must not run tools')):
            inspect(self.project, self.folder / 'view.json')

    def test_new_failure_rolls_back_common_and_child_together(self):
        original = projects.atomic_write
        def fail_child(path, content):
            if path.name == 'broken.json':
                raise OSError('injected submit write failure')
            original(path, content)
        with patch.object(projects, 'atomic_write', side_effect=fail_child):
            with self.assertRaisesRegex(OSError, 'injected'):
                create(self.project, 'broken')
        self.assertFalse(self.folder.exists())
        self.assertFalse((self.project / '.nsim').exists())
        load_project(self.project)

    def test_new_rejects_symlink_destination(self):
        outside = self.base / 'outside'
        outside.mkdir()
        self.folder.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ProjectError):
            create(self.project, 'bad')
        self.assertEqual(list(outside.iterdir()), [])

    def test_runtime_infers_mode_and_workspace_but_explicit_mismatch_fails(self):
        self.data['mode'] = 'gate'
        self.save_submit()
        result = self.cli('run', '--case', 'smoke', '-sub', self.submit, '-seed', 42, '-o', self.out)
        self.assertTrue(result['passed'])
        self.assertEqual(Path(result['record_dir']), self.out / 'base' / 'gate' / 'sim_dir' / 'smoke_42')
        saved = json.loads((Path(result['record_dir']) / 'run.json').read_text())['plan']
        self.assertEqual(saved['execution']['mode'], 'gate')
        self.assertEqual(saved['execution']['workspace'], 'gate')
        self.assertIn('does not match', self.cli('com', '-sub', self.submit, '-mode', 'rtl', '-o', self.out, success=False))

    def test_multiple_modes_rejected_and_shared_mode_inferred(self):
        second = self.project / 'second.json'
        write_json(second, dict(self.data, name='second', mode='gate'))
        with self.assertRaisesRegex(ConfigError, 'different modes separately'):
            create_plan(self.project, cases=['smoke'], submit_paths=[self.submit, second])
        self.data['mode'] = 'gate'
        self.save_submit()
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit, second])
        self.assertEqual(plan['execution']['mode'], 'gate')
        self.assertEqual(plan['execution']['workspace'], 'gate')

    def test_full_demo_shows_files_and_new_submit_keeps_common_defaults(self):
        for relative, content in scaffold().items():
            path = self.folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        parent = self.folder / 'common' / 'rtl.json'
        raw = json.loads(parent.read_text())
        raw['plusargs'] = ['+COMMON_OPTION=1']
        raw['vcs'].update(fsdb=True, run_args=['+TRACE=1'])
        write_json(parent, raw)
        effective = load_submit(self.folder / 'submit_demo.json')
        self.assertIn('coverage_hier', effective['files'])
        self.assertIn('{file:ucli_script}', effective['vcs']['run_args'])
        create(self.project, 'daily')
        effective = load_submit(self.folder / 'daily.json')
        self.assertEqual(effective['plusargs'], ['+COMMON_OPTION=1'])
        self.assertEqual(effective['vcs']['run_args'], ['+TRACE=1'])
        self.assertTrue(effective['vcs']['fsdb'])
