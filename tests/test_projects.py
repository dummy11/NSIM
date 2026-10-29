"""Project commands exercise real directories, registration and historical plans."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nsim import get_version
from nsim.config import ConfigError, MODES, load_project, load_submit
from nsim.planning import create_plan, check_identity
from nsim.project_paths import ProjectError, descriptor_path, find_root, load_registry, registry_path
from nsim import projects
from nsim.service import run, replay
from nsim.storage import IntegrityError

REPO = Path(__file__).resolve().parent.parent


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'installation'
        self.env = patch.dict(os.environ, NSIM_HOME=str(self.home), XDG_CONFIG_HOME=str(self.base / 'config'))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cwd = Path.cwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, self.cwd)
        self.rtl = self.base / 'rtl'
        self.rtl.mkdir()
        (self.rtl / 'dut.sv').write_text('module dut; endmodule\n')
        (self.rtl / 'rtl.f').write_text('dut.sv\n')
        self.install(get_version())

    def install(self, version, capable=True):
        directory = self.home / 'versions' / version
        executable = directory / 'venv' / 'bin' / 'nsim'
        executable.parent.mkdir(parents=True)
        executable.write_text(f'#!{sys.executable}\nimport json, sys\n'
            "if sys.argv[1:] == ['capabilities', '--json']:\n"
            f" print(json.dumps({{'version': '{version}', 'project_schemas': {[1, 2] if capable else [1]}}}))\n"
            "elif sys.argv[1:] == ['--version']:\n"
            f" print('nsim {version}')\n"
            "else:\n from nsim.cli import main\n sys.exit(main())\n")
        executable.chmod(0o755)
        (directory / 'receipt.json').write_text(json.dumps({'version': version, 'wheel_sha256': 'a' * 64}))

    def options(self, **kwargs):
        values = dict(name=None, verification_root=None, rtl_root=None, rtl_filelist=None,
                      verification_filelist=None, nsim_version=None, project=None,
                      output_root=None)
        values.update(kwargs)
        return argparse.Namespace(**values)

    def boot(self, **kwargs):
        return projects.bootstrap(self.options(rtl_root=str(self.rtl), rtl_filelist=str(self.rtl / 'rtl.f'), **kwargs))

    def cli(self, *args, success=True, cwd=None):
        result = subprocess.run([sys.executable, '-m', 'nsim', *map(str, args)], cwd=cwd or self.base,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, result.stderr + result.stdout)
        return result

    def test_default_layout_registry_and_subdirectory_detection(self):
        self.boot()
        root = self.base / 'verification'
        self.assertTrue((root / 'submit').is_dir())
        self.assertTrue((root / 'filelist' / 'tb.f').is_file())
        self.assertEqual((root / '.nsim' / '.nsim_version').read_text(), get_version() + '\n')
        project = load_project(root)
        self.assertEqual(project.rtl_root, self.rtl)
        self.assertEqual(project.root, root)
        sub = root / 'testcase' / 'smoke'
        sub.mkdir(parents=True)
        os.chdir(sub)
        self.assertEqual(find_root(), root)
        self.assertEqual(projects.bootstrap(self.options())['action'], 'already_registered')
        self.assertFalse((sub / 'verification').exists())
        self.assertEqual(projects.status()['name'], 'verification')
        self.assertTrue(projects.status()['registered'])
        self.assertIn('empty', str(projects.status()['issues']))
        self.assertEqual(len(projects.list_projects()), 1)

    def test_bootstrap_storage_defaults_resolve_from_calling_directory(self):
        self.boot(output_root=['output', 'backup'])
        root = self.base / 'verification'
        project = load_project(root)
        self.assertEqual(project.output_roots, [self.base / 'output', self.base / 'backup'])
        self.assertIn('output_roots = ["../output", "../backup"]', project.descriptor.read_text())
        self.assertNotIn('event_root', projects.status(root))
        self.assertFalse((self.base / 'output').exists())
        self.assertFalse((self.base / 'events').exists())
        os.chdir(root / 'submit')
        self.assertEqual(projects.bootstrap(self.options())['action'], 'already_registered')
        before = project.descriptor.read_bytes()
        with self.assertRaisesRegex(ProjectError, 'storage configuration differs'):
            projects.bootstrap(self.options(output_root=['different']))
        self.assertEqual(project.descriptor.read_bytes(), before)

    def test_update_and_switch_preserve_storage_defaults(self):
        self.boot(output_root=['output', 'backup'])
        root = self.base / 'verification'
        original_map = (root / 'filelist' / 'sim.f').read_bytes()
        self.cli('update', '--project', 'verification', '--output-root', 'new-output',
                 '--output-root', 'new-backup')
        project = load_project(root)
        self.assertEqual(project.output_roots, [self.base / 'new-output', self.base / 'new-backup'])
        self.assertEqual((root / 'filelist' / 'sim.f').read_bytes(), original_map)
        self.cli('update', '--project', 'verification', '--rtl-filelist', self.rtl / 'rtl.f')
        self.cli('switch', get_version(), '--project', 'verification')
        status = json.loads(self.cli('status', '--project', 'verification', '--json').stdout)
        self.assertNotIn('event_root', status)
        self.assertEqual(status['output_roots'], [str(self.base / p) for p in ('new-output', 'new-backup')])

    def test_stable_launcher_runs_with_saved_storage_and_cli_overrides(self):
        spec = importlib.util.spec_from_file_location('nsim_storage_installer', REPO / 'nsim-install.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        installer.bootstrap(self.home)
        launcher = self.home / 'bin' / 'nsim'
        def launch(*args):
            result = subprocess.run([launcher, *map(str, args)], cwd=self.base,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        launch('bootstrap', '--rtl-root', self.rtl, '--rtl-filelist', self.rtl / 'rtl.f',
               '--output-root', 'output', '--output-root', 'backup', '--json')
        root = self.base / 'verification'
        submit = root / 'submit' / 'fake.json'
        submit.write_text(json.dumps({'schema_version': 1, 'name': 'fake', 'adapter': 'fake', 'scheduler': {'kind': 'local'}}))
        cases = root / 'smoke.caselist'
        cases.write_text('smoke\n')
        for name in ('output', 'backup', 'override-output', 'override-backup'):
            (self.base / name).mkdir()
        args = ('run', '--project', 'verification', '--caselist', cases,
                '--submit', submit, '--seed', '42')
        # Project defaults win over stale shell values, and paths remain project-relative
        # even though the launcher is called from outside the verification directory.
        with patch.dict(os.environ, NSIM_OUTPUT_ROOT=str(self.base / 'stale-output'),
                        NSIM_EVENT_ROOT=str(self.base / 'stale-events')):
            ambiguous = subprocess.run([launcher, *map(str, args)], cwd=self.base,
                                       capture_output=True, text=True, timeout=20)
            self.assertEqual(ambiguous.returncode, 2)
            self.assertIn('select exactly one output root', ambiguous.stderr)
            result = launch(*args, '--output-root', 'output')
            self.assertTrue(result['passed'])
            journal = Path(result['journal'])
            self.assertEqual(journal.parent.parent, self.base / 'output' / 'regr_dir')
            plan = json.loads((journal / 'plan.json').read_text())
            self.assertEqual(plan['policy']['output_roots'], [str(self.base / 'output')])
            self.assertTrue(launch('report', 'show', journal, '--json')['passed'])
            result = launch(*args, '--output-root', 'override-output')
            journal = Path(result['journal'])
            plan = json.loads((journal / 'plan.json').read_text())
            self.assertEqual(plan['policy']['output_roots'], [str(self.base / 'override-output')])
            self.assertEqual(journal.parent.parent, self.base / 'override-output' / 'regr_dir')
            run_dir = next((self.base / 'output' / 'regr_dir').glob('*/fake/rtl/sim_dir/*'))
            self.assertTrue(launch('report', 'show', run_dir, '--json')['passed'])
        self.assertFalse((self.base / 'stale-events').exists())

    def test_storage_defaults_reject_invalid_config_types(self):
        self.boot()
        descriptor = self.base / 'verification' / '.nsim' / 'nsim.toml'
        original = descriptor.read_text()
        for setting in ('output_roots = "out"', 'output_roots = []', 'output_roots = [1]',
                        'output_roots = [""]', 'event_root = 1', 'event_root = ""'):
            with self.subTest(setting=setting):
                descriptor.write_text(original + setting + '\n')
                with self.assertRaises(ConfigError):
                    load_project(descriptor)

    def test_obsolete_event_root_is_ignored_and_removed_on_output_update(self):
        self.boot(output_root=['output'])
        root = self.base / 'verification'
        descriptor = root / '.nsim' / 'nsim.toml'
        descriptor.write_text(descriptor.read_text() + 'event_root = "/obsolete/events"\n')
        self.assertNotIn('event_root', projects.status(root))
        self.cli('update', '--project', 'verification', '--output-root', 'new-output')
        self.assertNotIn('event_root', descriptor.read_text())

    def test_saved_storage_enforces_isolation_and_existing_roots(self):
        self.boot(output_root=['output'])
        root = self.base / 'verification'
        submit = root / 'submit' / 'fake.json'
        submit.write_text(json.dumps({'schema_version': 1, 'name': 'fake', 'adapter': 'fake', 'scheduler': {'kind': 'local'}}))
        args = ('run', '--project', 'verification', '--case', 'smoke', '--submit', submit)
        result = self.cli(*args, success=False)
        self.assertFalse((self.base / 'events').exists())
        self.assertFalse((self.base / 'output').exists())
        self.cli('update', '--project', 'verification', '--output-root', root / 'results')
        result = self.cli(*args, success=False)
        self.assertIn('non-nested', result.stderr)
        self.assertFalse((root / 'results').exists())

    def test_changed_storage_defaults_do_not_redirect_historical_replay(self):
        self.boot(output_root=['output'])
        root = self.base / 'verification'
        submit = root / 'submit' / 'fake.json'
        submit.write_text(json.dumps({'schema_version': 1, 'name': 'fake', 'adapter': 'fake', 'scheduler': {'kind': 'local'},
                                     'fake': {'outcomes': {'smoke': 'fail'}}}))
        for name in ('output',):
            (self.base / name).mkdir()
        result = self.cli('run', '--project', 'verification', '--case', 'smoke',
                          '--submit', submit, '--seed', '42', success=False)
        journal = Path(json.loads(result.stdout)['journal'])
        original = json.loads((journal / 'run.json').read_text())['plan']
        self.cli('update', '--project', 'verification', '--output-root', 'new-output')
        result = self.cli('rerun', journal, success=False)
        replay_journal = Path(json.loads(result.stdout)['journal'])
        self.assertEqual(replay_journal, self.base / 'output' / 'fake' / 'rtl' / 'sim_dir' / 'smoke_42')
        self.assertEqual(json.loads((replay_journal / 'run.json').read_text())['plan']['policy']['output_roots'],
                         [str(self.base / 'output')])
        replay_plan = json.loads((journal / 'run.json').read_text())['plan']
        self.assertEqual(replay_plan['snapshots'], original['snapshots'])
        self.assertEqual(replay_plan['source']['id'], original['id'])
        self.assertFalse((self.base / 'new-output').exists())
        self.assertFalse((self.base / 'new-events').exists())

    def test_latest_semantic_sort_explicit_version_and_no_implicit_upgrade(self):
        for version in ('0.9.0', '0.10.0', '1.0.0rc1'):
            self.install(version)
        self.install('2.0.0', capable=False)
        self.boot(name='custom')
        root = self.base / 'custom'
        self.assertEqual(projects.status(root)['nsim_version'], '0.10.0')
        self.install('0.11.0')
        os.chdir(root)
        self.assertEqual(projects.bootstrap(self.options())['action'], 'already_registered')
        with self.assertRaisesRegex(ProjectError, 'version differs'):
            projects.bootstrap(self.options(nsim_version='latest'))
        projects.update(self.options(nsim_version='latest'))
        self.assertEqual(projects.status()['nsim_version'], '0.11.0')
        with self.assertRaisesRegex(ProjectError, 'not installed'):
            projects.update(self.options(nsim_version='99.0.0'))
        self.assertEqual(projects.status()['nsim_version'], '0.11.0')

    def test_existing_files_preserved_and_name_must_match(self):
        root = self.base / 'existing'
        (root / 'filelist').mkdir(parents=True)
        (root / 'submit').mkdir()
        tb = root / 'filelist' / 'tb.f'
        tb.write_text('existing.sv\n')
        (root / 'submit' / 'mine.json').write_text('custom')
        with self.assertRaisesRegex(ProjectError, '--name'):
            self.boot(name='different', verification_root=root)
        self.boot(verification_root=root)
        self.assertEqual(tb.read_text(), 'existing.sv\n')
        self.assertEqual((root / 'submit' / 'mine.json').read_text(), 'custom')
        self.boot(verification_root=root)
        self.assertEqual(len(load_registry()['projects']), 1)

    def test_bootstrap_generates_mode_common_submits_and_valid_demo_preview(self):
        self.boot()
        root = self.base / 'verification'
        common = root / 'submit' / 'common'
        self.assertEqual({p.name for p in common.iterdir()}, {mode + '.json' for mode in MODES})
        for mode in MODES:
            raw = json.loads((common / (mode + '.json')).read_text())
            self.assertEqual(raw['mode'], mode)
            self.assertNotIn('name', raw)
        demo = root / 'submit' / 'submit_demo.json'
        self.assertEqual(load_submit(demo)['mode'], 'rtl')
        self.assertTrue((root / 'submit' / 'submit_demo.md').is_file())
        (root / 'tb.sv').write_text('module tb; endmodule\n')
        (root / 'filelist' / 'tb.f').write_text('tb.sv\n')
        output = self.base / 'output'
        output.mkdir()
        preview = self.cli('run', '--case', 'smoke', '-sub', 'submit_demo', '-mode', 'rtl',
                           '-o', output, '--dry-run', cwd=root)
        record = Path(json.loads(preview.stdout)['record_dir'])
        plan = json.loads((record / 'plan.json').read_text())
        self.assertEqual(plan['submits'][0]['source_chain'][:2], [str(common / 'rtl.json'), str(demo)])
        self.assertIn(str(common / 'rtl.json'), plan['input_hashes'])
        self.assertIn(str(root / 'submit/demo_inputs/vcs/coverage.hier'), plan['submits'][0]['build_inputs'])
        self.assertNotIn(str(root / 'submit/demo_inputs/ucli/run.tcl'), plan['submits'][0]['build_inputs'])
        self.assertIn(str(root / 'submit/demo_inputs/ucli/includes/waves.tcl'), plan['input_hashes'])
        self.assertTrue((root / 'submit/examples/vcs_full.json').is_file())
        self.assertFalse(json.loads((record / 'commands.json').read_text())['executed'])

    def test_bootstrap_preserves_existing_common_and_demo_and_is_idempotent(self):
        root = self.base / 'existing'
        common = root / 'submit' / 'common'
        common.mkdir(parents=True)
        paths = [common / 'rtl.json', root / 'submit' / 'submit_demo.json', root / 'submit' / 'submit_demo.md']
        for path in paths:
            path.write_text('user owned, do not change\n')
        self.boot(verification_root=root)
        before = {p: p.read_bytes() for p in (root / 'submit').rglob('*') if p.is_file()}
        self.boot(verification_root=root)
        for path in paths:
            self.assertEqual(path.read_text(), 'user owned, do not change\n')
        self.assertEqual(before, {p: p.read_bytes() for p in (root / 'submit').rglob('*') if p.is_file()})
        self.assertTrue((common / 'gate.json').is_file())

    def test_bootstrap_caselist_demos_preview_and_preserve_existing_files(self):
        root = self.base / 'verification'
        folder = root / 'caselist'
        folder.mkdir(parents=True)
        keep = folder / '00_legacy.caselist'
        keep.write_text('my_existing_test\n')
        self.boot()
        self.assertEqual(keep.read_text(), 'my_existing_test\n')
        lists = sorted(folder.glob('*.caselist'))
        self.assertEqual(len(lists), 8)
        self.assertTrue((folder / 'case.yaml').is_file())
        self.assertIn('seq', (folder / 'README.md').read_text())
        (root / 'tb.sv').write_text('module tb; endmodule\n')
        (root / 'filelist' / 'tb.f').write_text('tb.sv\n')
        output = self.base / 'output'
        output.mkdir()
        preview = self.cli('regr', '-l', *lists, '-sub', 'submit_demo', '-seed', '42',
                           '-o', output, '--dry-run', cwd=root)
        record = Path(json.loads(preview.stdout)['record_dir'])
        result = json.loads((record / 'commands.json').read_text())
        self.assertFalse(result['executed'])
        mapped = next(task for task in result['tasks'] if task['case'] == 'override_debug')
        self.assertIn('+UVM_SEQNAME=debug_seq', mapped['simulate'])
        self.assertIn('+STRIDE=4', mapped['simulate'])
        before = {p: p.read_bytes() for p in folder.iterdir()}
        self.boot()
        self.assertEqual(before, {p: p.read_bytes() for p in folder.iterdir()})
        (folder / '07_overrides.caselist').unlink()
        self.boot()
        self.assertFalse((folder / '07_overrides.caselist').exists())

    def test_caselist_scaffold_failure_rolls_back_and_symlink_is_rejected(self):
        root = self.base / 'existing'
        root.mkdir()
        keep = root / 'keep'
        keep.write_text('preserve me')
        original = projects.atomic_write
        def fail_case(path, content):
            if path.name == '02_seq.caselist':
                raise OSError('injected caselist failure')
            original(path, content)
        with patch.object(projects, 'atomic_write', side_effect=fail_case):
            with self.assertRaisesRegex(OSError, 'caselist failure'):
                self.boot(verification_root=root)
        self.assertEqual(list(root.iterdir()), [keep])
        self.assertEqual(load_registry()['projects'], [])
        outside = self.base / 'outside'
        outside.mkdir()
        (root / 'caselist').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ProjectError):
            self.boot(verification_root=root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_submit_scaffold_failure_rolls_back_and_rejects_symlink_common(self):
        root = self.base / 'existing'
        root.mkdir()
        keep = root / 'keep'
        keep.write_text('user file')
        original = projects.atomic_write
        def fail_demo(path, content):
            if path.name == 'submit_demo.md':
                raise OSError('injected demo failure')
            original(path, content)
        with patch.object(projects, 'atomic_write', side_effect=fail_demo):
            with self.assertRaisesRegex(OSError, 'demo failure'):
                self.boot(verification_root=root)
        self.assertEqual(list(root.iterdir()), [keep])
        self.assertEqual(load_registry()['projects'], [])
        (root / 'submit').mkdir()
        external = self.base / 'external'
        external.mkdir()
        (root / 'submit' / 'common').symlink_to(external, target_is_directory=True)
        with self.assertRaises(ProjectError):
            self.boot(verification_root=root)
        self.assertEqual(list(external.iterdir()), [])
        self.assertFalse((root / '.nsim').exists())

    def test_duplicate_names_are_rejected_before_target_creation(self):
        self.boot(name='same')
        other = self.base / 'other' / 'same'
        with self.assertRaisesRegex(ProjectError, 'already registered'):
            self.boot(verification_root=other)
        self.assertFalse(other.exists())
        link = self.base / 'alias'
        link.symlink_to(self.base / 'same')
        self.assertEqual(self.boot(verification_root=link)['action'], 'already_registered')

    def test_malformed_marker_stops_ancestor_search_and_nested_bootstrap(self):
        self.boot()
        root = self.base / 'verification'
        child = root / 'child'
        child.mkdir()
        (child / '.nsim').mkdir()
        os.chdir(child)
        self.assertEqual(find_root(), child)
        self.assertIn('expected exactly one', str(projects.status()['issues']))
        with self.assertRaisesRegex(ProjectError, 'nested project'):
            projects.bootstrap(self.options())
        with self.assertRaisesRegex(ProjectError, 'nested project'):
            self.boot(verification_root=root / 'another')
        self.boot(verification_root=self.base / 'outside')
        self.assertEqual(len(projects.list_projects()), 2)

    def test_conflicting_layout_missing_and_non_directory_marker(self):
        self.boot()
        root = self.base / 'verification'
        (root / 'nsim.toml').write_text('schema_version=1\nname="legacy"')
        with self.assertRaisesRegex(ProjectError, 'conflicting'):
            descriptor_path(root)
        (root / 'nsim.toml').unlink()
        target = self.base / 'broken'
        target.mkdir()
        (target / '.nsim').write_text('not directory')
        self.cli('bootstrap', '--verification-root', target, success=False)
        self.assertEqual((target / '.nsim').read_text(), 'not directory')
        (root / '.nsim' / 'nsim.toml').unlink()
        self.cli('bootstrap', '--verification-root', root, success=False)
        self.assertEqual(len(projects.list_projects()), 1)

    def test_reregister_valid_config_and_refuse_unregistered_update(self):
        self.boot()
        root = self.base / 'verification'
        registry_path().unlink()
        self.assertFalse(projects.status(root)['registered'])
        with self.assertRaisesRegex(ProjectError, 'not registered'):
            projects.update(self.options(project=root, nsim_version=get_version()))
        self.assertEqual(self.boot(verification_root=root)['action'], 'registered')

    def test_update_only_requested_fields_and_validate_all_before_writing(self):
        self.boot()
        root = self.base / 'verification'
        before = (root / '.nsim' / 'nsim.toml').read_bytes()
        other = self.base / 'other-rtl'
        other.mkdir()
        (other / 'rtl.f').write_text('new.sv\n')
        with self.assertRaisesRegex(ProjectError, 'not installed'):
            projects.update(self.options(project=root, rtl_root=other, nsim_version='9.0.0'))
        self.assertEqual((root / '.nsim' / 'nsim.toml').read_bytes(), before)
        projects.update(self.options(project=root, rtl_root=other))
        project = load_project(root)
        self.assertEqual(project.rtl_root, other)
        self.assertEqual(project.rtl_filelist, self.rtl / 'rtl.f')
        with self.assertRaisesRegex(ProjectError, 'provide --rtl-root'):
            projects.update(self.options(project=root))
        (root / 'filelist' / 'sim.f').write_text('user maintained\n')
        with self.assertRaisesRegex(ProjectError, 'user file'):
            projects.update(self.options(project=root, rtl_filelist=other / 'rtl.f'))
        self.assertEqual(load_project(root).rtl_filelist, self.rtl / 'rtl.f')

    def test_update_rolls_back_on_write_failure(self):
        self.boot()
        root = self.base / 'verification'
        config = root / '.nsim' / 'nsim.toml'
        before = config.read_bytes()
        new_list = self.rtl / 'new.f'
        new_list.write_text('dut.sv\n')
        original = projects.atomic_write
        failed = []
        def fail_once(path, data):
            if path.name == 'sim.f' and not failed:
                failed.append(True)
                raise OSError('injected publication failure')
            original(path, data)
        with patch.object(projects, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                projects.update(self.options(project=root, rtl_filelist=new_list))
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse((registry_path().parent / 'projects.transaction.json').exists())

    def test_cli_status_list_update_and_switch_from_subdirectory(self):
        self.cli('bootstrap', '--name', 'cli', '--rtl-root', self.rtl, '--rtl-filelist', self.rtl / 'rtl.f')
        sub = self.base / 'cli' / 'submit'
        result = json.loads(self.cli('status', '--json', cwd=sub).stdout)
        self.assertEqual(result['name'], 'cli')
        self.assertEqual(len(json.loads(self.cli('list', '--json').stdout)), 1)
        self.install('0.2.0')
        self.cli('switch', '0.2.0', cwd=sub)
        self.assertEqual(projects.status('cli')['nsim_version'], '0.2.0')
        self.cli('update', '--project', 'cli', '--nsim-version', get_version())
        self.cli('update', cwd=sub, success=False)
        self.cli('status', success=False)

    def test_concurrent_same_name_registration(self):
        roots = [self.base / 'first' / 'same', self.base / 'second' / 'same']
        processes = [subprocess.Popen([sys.executable, '-m', 'nsim', 'bootstrap', '--verification-root', str(root),
                     '--rtl-root', str(self.rtl), '--rtl-filelist', str(self.rtl / 'rtl.f')],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for root in roots]
        results = [p.communicate(timeout=30) for p in processes]
        self.assertEqual(sorted(p.returncode for p in processes), [0, 2], results)
        self.assertEqual(len(load_registry()['projects']), 1)
        self.assertEqual(sum((r / '.nsim').exists() for r in roots), 1)

    def test_split_filelists_snapshot_replay_and_output_isolation(self):
        self.boot()
        root = self.base / 'verification'
        (root / 'tb.sv').write_text('module tb; endmodule\n')
        (root / 'filelist' / 'tb.f').write_text('tb.sv\n')
        submit = root / 'submit' / 'vcs.json'
        submit.write_text(json.dumps({'schema_version': 1, 'name': 'native', 'adapter': 'vcs',
                                     'required_checks': ['functional'], 'vcs': {}}))
        plan = create_plan(root, cases=['smoke'], submit_paths=[submit], seeds=[42], allow_missing_tools=True)
        self.assertIn(str(self.rtl / 'dut.sv'), plan['input_hashes'])
        self.assertIn(str(root / 'tb.sv'), plan['input_hashes'])
        self.assertNotIn(str(root / '.nsim' / 'nsim.toml'), plan['input_hashes'])
        self.assertIn(str(root / '.nsim' / 'nsim.toml'), plan['snapshots'])
        self.assertEqual(set(plan['project']['sources']), {'rtl', 'verification'})
        output, events = self.base / 'output', self.base / 'events'
        output.mkdir()
        events.mkdir()
        with self.assertRaisesRegex(ValueError, 'separate'):
            run(plan, [self.rtl / 'output'], dry_run=True)
        other = self.base / 'rtl-next'
        other.mkdir()
        (other / 'rtl.f').write_text('new.sv\n')
        (other / 'new.sv').write_text('module new; endmodule\n')
        projects.update(self.options(project=root, rtl_root=other, rtl_filelist=other / 'rtl.f'))
        check_identity(plan)
        journal, _ = run(plan, [output], dry_run=True)
        preview = json.loads((journal / 'commands.json').read_text())
        self.assertIn(str(self.rtl / 'dut.sv'), preview['tasks'][0]['compile']['argv'])
        next_plan = create_plan(root, cases=['smoke'], submit_paths=[submit], seeds=[42], allow_missing_tools=True)
        self.assertIn(str(other / 'new.sv'), next_plan['input_hashes'])
        (self.rtl / 'dut.sv').write_text('changed')
        with self.assertRaises(IntegrityError):
            check_identity(plan)

    def test_real_failed_run_replays_after_registration_edit(self):
        self.boot()
        root = self.base / 'verification'
        submit = root / 'submit' / 'fake.json'
        submit.write_text(json.dumps({'schema_version': 1, 'name': 'fake', 'adapter': 'fake', 'scheduler': {'kind': 'local'},
                                    'fake': {'outcomes': {'smoke': 'fail'}}}))
        output, events = self.base / 'output', self.base / 'events'
        output.mkdir()
        events.mkdir()
        plan = create_plan(root, cases=['smoke'], submit_paths=[submit], seeds=[42])
        journal, state = run(plan, [output])
        self.assertFalse(state['passed'])
        original = json.loads((journal / 'run.json').read_text())['plan']
        new_list = self.rtl / 'new.f'
        new_list.write_text('dut.sv\n')
        projects.update(self.options(project=root, rtl_filelist=new_list))
        new_journal, _ = replay(journal)
        self.assertEqual(new_journal, journal)
        replay_plan = json.loads((journal / 'run.json').read_text())['plan']
        self.assertEqual(replay_plan['snapshots'], original['snapshots'])
        self.assertEqual(replay_plan['input_hashes'], original['input_hashes'])
        self.assertEqual(replay_plan['source']['id'], original['id'])

    def test_launcher_queries_without_runtime_and_uses_shared_layout(self):
        spec = importlib.util.spec_from_file_location('nsim_installer_test', REPO / 'nsim-install.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.PROJECT_LAYOUT_SOURCE, (REPO / 'src/nsim/project_paths.py').read_text())
        module.bootstrap(self.home)
        launcher = self.home / 'bin' / 'nsim'
        def launch(*args, cwd=None):
            return subprocess.run([launcher, *map(str, args)], cwd=cwd or self.base,
                                  capture_output=True, text=True, timeout=20)
        result = launch('bootstrap', '--rtl-root', self.rtl, '--rtl-filelist', self.rtl / 'rtl.f')
        self.assertEqual(result.returncode, 0, result.stderr)
        root = self.base / 'verification'
        self.assertTrue((root / '.nsim' / 'nsim.toml').exists())
        (self.home / 'versions' / get_version() / 'receipt.json').unlink()
        for args in [('list', '--json'), ('status', '--project', 'verification', '--json')]:
            result = launch(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('not installed', result.stdout)
        result = launch('update', '--bundle', 'file.tar.gz')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nsim install', result.stderr)

    def test_interrupted_transaction_is_recovered_before_next_update(self):
        self.boot()
        root = self.base / 'verification'
        before = (root / '.nsim' / 'nsim.toml').read_bytes()
        new_list = self.rtl / 'new.f'
        new_list.write_text('dut.sv\n')
        script = """
import os, sys
from nsim import projects
from nsim.cli import main
original = projects.atomic_write
def interrupt(path, data):
    if path.name == 'sim.f':
        os._exit(99)
    original(path, data)
projects.atomic_write = interrupt
sys.exit(main(sys.argv[1:]))
"""
        result = subprocess.run([sys.executable, '-c', script, 'update', '--project', str(root),
                                 '--rtl-filelist', str(new_list)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 99, result.stderr)
        self.assertNotEqual((root / '.nsim' / 'nsim.toml').read_bytes(), before)
        with self.assertRaisesRegex(ProjectError, 'interrupted'):
            projects.status(root)
        projects.update(self.options(project=root, rtl_filelist=self.rtl / 'rtl.f'))
        self.assertEqual((root / '.nsim' / 'nsim.toml').read_bytes(), before)
        self.assertFalse((registry_path().parent / 'projects.transaction.json').exists())

    def test_filelist_roots_spaces_nested_lists_and_new_directory_members(self):
        self.boot(name='lists')
        root = self.base / 'lists'
        (self.rtl / 'with space').mkdir()
        (self.rtl / 'with space' / 'defs.svh').write_text('`define X 1\n')
        (self.rtl / 'with space' / 'nested.f').write_text('../dut.sv\n')
        (self.rtl / 'rtl.f').write_text('-F "with space/nested.f"\n+incdir+"with space"\n')
        (root / 'tb.sv').write_text('module tb; endmodule\n')
        (root / 'filelist' / 'tb.f').write_text('-f filelist/nested.f\n')
        (root / 'filelist' / 'nested.f').write_text('tb.sv\n')
        submit = root / 'submit' / 'vcs.json'
        submit.write_text(json.dumps({'schema_version': 1, 'name': 'native', 'adapter': 'vcs',
                                     'required_checks': ['functional'], 'vcs': {}}))
        plan = create_plan(root, cases=['smoke'], submit_paths=[submit], allow_missing_tools=True)
        tokens = plan['submits'][0]['vcs']['filelist_tokens']
        self.assertEqual(tokens[0], str(self.rtl / 'dut.sv'))
        self.assertEqual(tokens[-1], str(root / 'tb.sv'))
        check_identity(plan)
        (self.rtl / 'with space' / 'new.svh').write_text('`define NEW 1\n')
        with self.assertRaisesRegex(ValueError, 'membership changed'):
            check_identity(plan)

    def test_missing_runtime_does_not_hide_projects_or_block_repair(self):
        self.boot()
        root = self.base / 'verification'
        self.install('0.2.0')
        (root / '.nsim' / '.nsim_version').write_text('9.0.0\n')
        self.assertFalse(projects.status(root)['version_installed'])
        self.assertEqual(len(projects.list_projects()), 1)
        projects.update(self.options(project=root, nsim_version='0.2.0'))
        self.assertTrue(projects.status(root)['version_installed'])

    def test_user_sim_file_blocks_bootstrap_without_partial_files(self):
        root = self.base / 'user'
        (root / 'filelist').mkdir(parents=True)
        (root / 'filelist' / 'sim.f').write_text('user sources\n')
        with self.assertRaisesRegex(ProjectError, 'user file'):
            self.boot(verification_root=root)
        self.assertFalse((root / '.nsim').exists())
        self.assertFalse((root / 'filelist' / 'tb.f').exists())
        self.assertEqual(load_registry()['projects'], [])

    def test_invalid_new_paths_and_unavailable_versions_do_not_create_project(self):
        with self.assertRaisesRegex(ProjectError, 'not a file'):
            projects.bootstrap(self.options(rtl_root=self.rtl, rtl_filelist=self.rtl / 'missing.f'))
        with self.assertRaisesRegex(ProjectError, 'not installed'):
            self.boot(nsim_version='99.0.0')
        with self.assertRaisesRegex(ProjectError, 'invalid nsim version'):
            self.boot(nsim_version='1!0.1.0')
        self.assertFalse((self.base / 'verification').exists())

    def test_failed_bootstrap_preserves_existing_directory_and_rolls_back_new_files(self):
        root = self.base / 'existing'
        root.mkdir()
        (root / 'user.txt').write_text('keep me')
        original = projects.atomic_write
        failed = []
        def fail_once(path, data):
            if path.name == 'sim.f' and not failed:
                failed.append(True)
                raise OSError('injected bootstrap failure')
            original(path, data)
        with patch.object(projects, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                self.boot(verification_root=root)
        self.assertEqual([p.name for p in root.iterdir()], ['user.txt'])
        self.assertEqual(load_registry()['projects'], [])

    def test_recovery_never_overwrites_external_edits(self):
        self.boot()
        root = self.base / 'verification'
        config = root / '.nsim' / 'nsim.toml'
        # Simulate an interrupted publication followed by an unrelated user edit.
        import base64
        original = config.read_bytes()
        pending = {'schema_version': 1, 'root': str(root), 'directories': [], 'files': [{
            'path': str(config), 'before': base64.b64encode(original).decode(),
            'after': base64.b64encode(original + b'# proposed\n').decode()}]}
        transaction = registry_path().parent / 'projects.transaction.json'
        transaction.write_text(json.dumps(pending))
        config.write_bytes(original + b'# user edit\n')
        with self.assertRaisesRegex(ProjectError, 'external edit'):
            projects.update(self.options(project=root, nsim_version=get_version()))
        self.assertTrue(config.read_bytes().endswith(b'# user edit\n'))
        self.assertTrue(transaction.exists())

    def test_missing_pin_and_symlinked_control_directory_are_rejected(self):
        self.boot()
        root = self.base / 'verification'
        (root / '.nsim' / '.nsim_version').unlink()
        with self.assertRaisesRegex(ValueError, 'requires a selected'):
            create_plan(root, cases=['smoke'])
        link_root = self.base / 'link-root'
        link_root.mkdir()
        (link_root / '.nsim').symlink_to(root / '.nsim')
        for value in (link_root, link_root / '.nsim' / 'nsim.toml'):
            with self.assertRaisesRegex(ProjectError, 'real directory'):
                descriptor_path(value)

    def test_explicit_prerelease_works_through_stable_launcher(self):
        (self.home / 'versions' / get_version() / 'receipt.json').unlink()
        self.install('0.2.0rc1')
        result = subprocess.run([sys.executable, str(REPO / 'nsim-install.py'), 'bootstrap', '--root', str(self.home)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        launcher = self.home / 'bin' / 'nsim'
        args = [str(launcher), 'bootstrap', '--rtl-root', str(self.rtl), '--rtl-filelist', str(self.rtl / 'rtl.f')]
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        result = subprocess.run(args + ['--nsim-version', '0.2.0rc1'], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(projects.status('verification')['nsim_version'], '0.2.0rc1')

    def test_metadata_directory_and_descendants_resolve_to_project(self):
        self.boot()
        root = self.base / 'verification'
        nested = root / '.nsim' / 'notes'
        nested.mkdir()
        for directory in (root / '.nsim', nested):
            os.chdir(directory)
            self.assertEqual(find_root(), root)
            self.assertTrue(projects.status()['registered'])
            self.assertEqual(projects.status()['verification_root'], str(root))
            projects.update(self.options(nsim_version=get_version()))
            self.assertEqual(projects.bootstrap(self.options())['action'], 'already_registered')

    def test_unreadable_project_does_not_hide_other_registered_projects(self):
        self.boot(name='good')
        self.boot(name='unreadable')
        unavailable = self.base / 'unreadable'
        original = projects.project_lock
        def lock(root):
            if root == unavailable:
                raise PermissionError('project directory access denied')
            return original(root)
        with patch.object(projects, 'project_lock', side_effect=lock):
            values = {p['name']: p for p in projects.list_projects()}
            self.assertEqual(set(values), {'good', 'unreadable'})
            self.assertTrue(values['unreadable']['registered'])
            self.assertIn('access denied', str(values['unreadable']['issues']))
            self.assertIn('access denied', str(projects.status(unavailable)['issues']))

    def test_update_rejects_relocated_managed_directory_before_writing(self):
        self.boot()
        root = self.base / 'verification'
        external = self.base / 'external-filelist'
        (root / 'filelist').rename(external)
        (root / 'filelist').symlink_to(external)
        config_before = (root / '.nsim' / 'nsim.toml').read_bytes()
        map_before = (external / 'sim.f').read_bytes()
        replacement = self.rtl / 'replacement.f'
        replacement.write_text('dut.sv\n')
        with self.assertRaisesRegex(ProjectError, 'symlink|relocated'):
            projects.update(self.options(project=root, rtl_filelist=replacement))
        self.assertEqual((root / '.nsim' / 'nsim.toml').read_bytes(), config_before)
        self.assertEqual((external / 'sim.f').read_bytes(), map_before)

    def test_recovery_rejects_parent_symlink_even_when_file_bytes_match(self):
        import base64
        self.boot()
        root = self.base / 'verification'
        config = root / '.nsim' / 'nsim.toml'
        old = config.read_bytes()
        proposed = old + b'# transaction content\n'
        config.write_bytes(proposed)
        transaction = {'schema_version': 1, 'root': str(root), 'directories': [], 'files': [{
            'path': str(config), 'before': base64.b64encode(old).decode(),
            'after': base64.b64encode(proposed).decode()}]}
        external = self.base / 'external-metadata'
        (root / '.nsim').rename(external)
        (root / '.nsim').symlink_to(external)
        with self.assertRaisesRegex(ProjectError, 'symlink|relocated'):
            projects._restore(transaction)
        self.assertEqual((external / 'nsim.toml').read_bytes(), proposed)

    def test_malformed_capabilities_are_skipped_without_traceback(self):
        self.install('0.2.0')
        executable = self.home / 'versions' / '0.2.0' / 'venv' / 'bin' / 'nsim'
        executable.write_text('#!' + sys.executable + '\nprint(\'{"version": "0.2.0", "project_schemas": null}\')\n')
        self.assertEqual(projects.select_version(), get_version())
        with self.assertRaises(ProjectError):
            projects.select_version('0.2.0')

    def test_standalone_manager_skips_malformed_receipts_and_capabilities(self):
        self.install('0.2.0')
        self.install('0.3.0')
        (self.home / 'versions' / '0.3.0' / 'receipt.json').write_text('[]')
        executable = self.home / 'versions' / '0.2.0' / 'venv' / 'bin' / 'nsim'
        executable.write_text('#!' + sys.executable + '\nprint(\'{"version": "0.2.0", "project_schemas": null}\')\n')
        spec = importlib.util.spec_from_file_location('nsim_installer_review', REPO / 'nsim-install.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.management_version(self.home), get_version())
        with self.assertRaises(module.InstallError):
            module.receipt(self.home, '0.3.0')

    def test_installer_version_order_matches_numeric_pep440_order(self):
        from packaging.version import Version
        spec = importlib.util.spec_from_file_location('nsim_installer_order', REPO / 'nsim-install.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        values = ['1.0.0.post2', '1.0.0.post10', '1.0.0rc2', '1.0.0rc10', '1.0.0',
                  '1.0.0.dev2', '1.0.0.dev10', '1.0.0a2', '1.0.0b1', '1.0.0a2.dev1',
                  '1.0.0.post1.dev2', '1.0.0.post1', '2.0.0', '0.10.0', '0.9.0']
        self.assertEqual(sorted(values, key=module.version_key), sorted(values, key=Version))

    def test_invalid_recovery_record_never_modifies_project(self):
        self.boot()
        root = self.base / 'verification'
        config = root / '.nsim' / 'nsim.toml'
        before = config.read_bytes()
        pending = registry_path().parent / 'projects.transaction.json'
        for invalid in ('{', '{"schema_version": 1}', '{"schema_version": 99}'):
            pending.write_text(invalid)
            with self.assertRaisesRegex(ProjectError, 'invalid recovery record'):
                projects.update(self.options(project=root, nsim_version=get_version()))
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(pending.read_text(), invalid)


if __name__ == '__main__':
    unittest.main()
