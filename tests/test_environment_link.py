"""Whole-environment links work before tools, without resource lists or hooks."""

import json
from pathlib import Path
import sys

from test_regression import Fixture
from nsim import get_version
from nsim.config import ConfigError, load_submit
from nsim.layout import build_directory, simulation_directory
from nsim.planning import create_plan
from nsim.service import run


SCRIPT = '''import json, sys
from pathlib import Path
phase, *args = sys.argv[1:]
assert Path('env').is_symlink()
assert Path('env/custom assets/nested/input.txt').read_text() == 'resource'
if phase == 'compile':
    Path('model').write_text('compiled')
elif phase == 'simulate':
    case, seed, result = args
    Path(result).write_text(json.dumps({'schema_version': 1, 'case': case, 'seed': int(seed),
        'complete': True, 'checks': {'functional': {'executed': True, 'passed': True}}}))
elif phase == 'hook':
    Path('hook-saw-env').write_text(str(Path('env').resolve()))
'''


class EnvironmentLinkTests(Fixture):
    def stage(self, phase='run', **kwargs):
        if phase != 'com':
            kwargs.setdefault('cases', ['smoke'])
            kwargs.setdefault('seeds', [42])
        return create_plan(self.project, submit_paths=[self.submit], phase=phase, **kwargs)

    def configure_command(self):
        resource = self.project / 'custom assets/nested/input.txt'
        resource.parent.mkdir(parents=True)
        resource.write_text('resource')
        (self.project / 'workflow.py').write_text(SCRIPT)
        self.descriptor.write_text('schema_version=1\nname="demo"\n'
                                   'inputs=["workflow.py", "custom assets/nested/input.txt"]\n')
        self.data.update(adapter='command', required_checks=['functional'],
                         compile={'argv': [sys.executable, 'env/workflow.py', 'compile'], 'artifacts': ['model']},
                         argv=[sys.executable, 'env/workflow.py', 'simulate', '{case}', '{seed}', '{result}'])
        self.save_submit()

    def assert_link(self, work, target=None):
        self.assertTrue((work / 'env').is_symlink())
        self.assertEqual((work / 'env').resolve(), target or self.project)

    def test_compile_and_parallel_simulations_read_whole_environment_without_hooks(self):
        self.configure_command()
        plan = self.stage(cases=['a', 'b'], seeds=[1, 2])
        _, state = run(plan, [self.out], jobs=4)
        self.assertTrue(state['passed'], state)
        self.assert_link(build_directory(self.out, plan, plan['tasks'][0]))
        for task in plan['tasks']:
            work = simulation_directory(self.out, plan, task)
            self.assert_link(work)
            self.assertFalse((work / 'custom assets').exists())

    def test_environment_is_available_to_optional_pre_hooks(self):
        self.configure_command()
        hook = {'name': 'inspect', 'argv': [sys.executable, 'env/workflow.py', 'hook']}
        self.data['hooks'] = {'pre_compile': hook, 'pre_sim': hook}
        self.save_submit()
        plan = self.stage()
        record, state = run(plan, [self.out])
        self.assertTrue(state['passed'], state)
        for work in (record, build_directory(self.out, plan, plan['tasks'][0])):
            self.assertEqual((work / 'hook-saw-env').read_text(), str(self.project))

    def test_com_and_sim_share_root_with_absolute_directory_overrides(self):
        self.configure_command()
        build, state = run(self.stage('com', com_dir=str(self.base / 'external compile')), [self.out])
        self.assertTrue(state['passed'], state)
        record, state = run(self.stage('sim', directory='issue', com_dir=str(build),
                                       sim_dir=str(self.base / 'external simulations')), [self.out])
        self.assertTrue(state['passed'], state)
        self.assert_link(build)
        self.assert_link(record)

    def test_repeated_run_cleans_outputs_without_following_environment_link(self):
        self.configure_command()
        plan = self.stage()
        record, _ = run(plan, [self.out])
        build = build_directory(self.out, plan, plan['tasks'][0])
        for work in (record, build):
            (work / 'stale-output').write_text('remove me')
        # New environment content is visible without adding another link or hook.
        (self.project / 'another-resource').write_text('keep me')
        record, state = run(self.stage(), [self.out])
        self.assertTrue(state['passed'], state)
        for work in (record, build):
            self.assert_link(work)
            self.assertFalse((work / 'stale-output').exists())
            self.assertEqual((work / 'env/another-resource').read_text(), 'keep me')
        self.assertEqual((self.project / 'custom assets/nested/input.txt').read_text(), 'resource')

    def test_conflicting_environment_entry_preserves_previous_outputs(self):
        for place in ('compile', 'simulation'):
            for kind in ('file', 'directory', 'wrong_link', 'broken_link'):
                with self.subTest(place=place, kind=kind):
                    directory = place + '-' + kind
                    plan = self.stage(directory=directory)
                    record, _ = run(plan, [self.out])
                    work = build_directory(self.out, plan, plan['tasks'][0]) if place == 'compile' else record
                    marker = work / 'keep'
                    marker.write_text('previous output')
                    link = work / 'env'
                    link.unlink()
                    if kind == 'file':
                        link.write_text('user file')
                    elif kind == 'directory':
                        link.mkdir()
                        (link / 'keep').write_text('user directory')
                    else:
                        link.symlink_to(self.backup if kind == 'wrong_link' else self.base / 'missing')
                    with self.assertRaisesRegex(ConfigError, 'environment link'):
                        run(self.stage(directory=directory), [self.out])
                    self.assertEqual(marker.read_text(), 'previous output')
                    self.assertTrue(link.exists() or link.is_symlink())

    def test_sim_reuse_rejects_missing_environment_link_before_clearing_results(self):
        plan = self.stage()
        record, _ = run(plan, [self.out])
        (record / 'keep').write_text('previous output')
        (build_directory(self.out, plan, plan['tasks'][0]) / 'env').unlink()
        with self.assertRaisesRegex(ConfigError, 'environment link is missing'):
            run(self.stage('sim'), [self.out])
        self.assertEqual((record / 'keep').read_text(), 'previous output')

    def test_env_cannot_be_declared_as_compile_output(self):
        self.configure_command()
        for path in ('env', 'env/model', 'ENV/model'):
            self.data['compile']['artifacts'] = [path]
            self.save_submit()
            with self.assertRaisesRegex(ConfigError, 'relative product path'):
                load_submit(self.submit)

    def test_dry_run_describes_links_without_creating_product_directories(self):
        for phase in ('com', 'sim', 'run'):
            plan = self.stage(phase)
            record, _ = run(plan, [self.out], dry_run=True)
            preview = json.loads((record / 'commands.json').read_text())['tasks'][0]
            build = build_directory(self.out, plan, plan['tasks'][0])
            work = simulation_directory(self.out, plan, plan['tasks'][0])
            expected = [build] if phase == 'com' else [work] if phase == 'sim' else [build, work]
            self.assertEqual(preview['environment_links'],
                             [{'path': str(p / 'env'), 'target': str(self.project)} for p in expected])
            self.assertFalse(build.exists())
            self.assertFalse(work.exists())

    def test_legacy_external_source_root_is_link_target(self):
        source = self.base / 'verification root'
        source.mkdir()
        (source / 'resource').write_text('external environment')
        self.descriptor.write_text('schema_version=1\nname="demo"\ninputs=["input.txt"]\n'
                                   'source_root="../verification root"\n')
        plan = self.stage()
        record, state = run(plan, [self.out])
        self.assertTrue(state['passed'], state)
        self.assert_link(record, source)
        self.assert_link(build_directory(self.out, plan, plan['tasks'][0]), source)

    def test_schema_two_links_verification_root_instead_of_control_or_rtl_directory(self):
        self.descriptor.unlink()
        control = self.project / '.nsim'
        control.mkdir()
        (control / '.nsim_version').write_text(get_version() + '\n')
        rtl = self.base / 'rtl'
        rtl.mkdir()
        (rtl / 'rtl.f').write_text('')
        (self.project / 'tb.f').write_text('')
        (control / 'nsim.toml').write_text('schema_version=2\nname="project"\ninputs=["input.txt"]\n'
                                          'rtl_root="../rtl"\nrtl_filelist="../rtl/rtl.f"\n'
                                          'verification_filelist="tb.f"\n')
        plan = self.stage()
        record, state = run(plan, [self.out])
        self.assertTrue(state['passed'], state)
        self.assert_link(record)
        self.assert_link(build_directory(self.out, plan, plan['tasks'][0]))
