"""OS-enforced denial of writes through env, absolute paths and child processes."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_regression import Fixture
from nsim.config import ConfigError
from nsim.planning import create_plan
from nsim.readonly import ReadOnlyEnvironment, source_paths
from nsim.service import run


PROBE = '''import json, os, subprocess, sys
from pathlib import Path
phase, *args = sys.argv[1:]
source = Path('env/input.txt')
assert source.read_text() == 'stable input\\n'
actions = [
    lambda: source.write_text('changed'),
    lambda: source.resolve().write_text('absolute write'),
    lambda: source.open('a').write('append'),
    lambda: Path('env/new-file').write_text('new'),
    lambda: Path('env/new-dir').mkdir(),
    lambda: Path('env/new-link').symlink_to('input.txt'),
    lambda: source.rename('env/renamed'),
    lambda: source.rename('moved-source'),
    lambda: source.unlink(),
    lambda: source.chmod(0o777),
    lambda: os.link(source, 'hard-alias'),
    lambda: Path('env/empty').rmdir(),
    lambda: Path('env/external/value').write_text('external write'),
    lambda: Path('env/external/new').write_text('external create'),
]
for index, action in enumerate(actions):
    try:
        action()
    except OSError:
        continue
    raise AssertionError('write was not blocked: ' + str(index))
child = subprocess.run([sys.executable, '-c', "from pathlib import Path; Path('env/child').write_text('child')"],
                       capture_output=True)
assert child.returncode != 0
assert source.read_text() == 'stable input\\n'
assert Path('env/external/value').read_text() == 'shared'
Path(phase + '-read-only-checked').write_text('writes blocked; output works')
if phase == 'compile':
    Path('model').write_text('compiled')
elif phase == 'simulate':
    case, seed, result = args
    Path(result).write_text(json.dumps({'schema_version': 1, 'case': case, 'seed': int(seed),
        'complete': True, 'checks': {'functional': {'executed': True, 'passed': True}}}))
'''


class ReadOnlyEnvironmentTests(Fixture):
    def configure(self):
        (self.project / 'empty').mkdir()
        shared = self.base / 'external resources'
        shared.mkdir()
        (shared / 'value').write_text('shared')
        (self.project / 'external').symlink_to(shared, target_is_directory=True)
        (self.project / 'probe.py').write_text(PROBE)
        self.descriptor.write_text('schema_version=1\nname="demo"\ninputs=["probe.py", "input.txt"]\n')
        self.data.update(adapter='command', required_checks=['functional'],
                         compile={'argv': [sys.executable, 'env/probe.py', 'compile'], 'artifacts': ['model']},
                         argv=[sys.executable, 'env/probe.py', 'simulate', '{case}', '{seed}', '{result}'],
                         hooks={phase: {'name': 'check_read_only',
                                        'argv': [sys.executable, 'env/probe.py', phase]}
                                for phase in ('pre_compile', 'post_compile', 'pre_sim', 'post_sim')})
        self.save_submit()

    def one(self):
        return create_plan(self.project, cases=['smoke'], seeds=[42], submit_paths=[self.submit])

    def test_all_commands_and_hooks_block_writes_but_keep_outputs_writable(self):
        self.configure()
        mode = (self.project / 'input.txt').stat().st_mode
        record, state = run(self.one(), [self.out])
        self.assertTrue(state['passed'], state)
        attempt = state['tasks'][0]['attempts'][0]
        build = Path(attempt['build_dir'])
        for work, phases in ((build, ('compile', 'pre_compile', 'post_compile')),
                              (record, ('simulate', 'pre_sim', 'post_sim'))):
            for phase in phases:
                self.assertTrue((work / (phase + '-read-only-checked')).is_file())
        self.assertEqual((self.project / 'input.txt').read_text(), 'stable input\n')
        self.assertEqual((self.project / 'input.txt').stat().st_mode, mode)
        self.assertEqual((self.base / 'external resources/value').read_text(), 'shared')
        self.assertIn(str(self.project), attempt['read_only_environment']['protected_paths'])
        # Source editability outside the job is unchanged, including permission bits.
        (self.project / 'manual-edit').write_text('editor can still write')

    def test_missing_linux_backend_does_not_launch_user_command(self):
        self.configure()
        plan = self.one()
        with patch('nsim.readonly.sys.platform', 'linux'), patch('nsim.readonly.shutil.which', return_value=None):
            record, state = run(plan, [self.out])
        self.assertFalse(state['passed'])
        self.assertIn('bubblewrap', state['tasks'][0]['attempts'][0]['reason'])
        self.assertFalse((record / 'result.json').exists())
        self.assertEqual((self.project / 'input.txt').read_text(), 'stable input\n')

    def test_backend_refusal_fails_closed(self):
        self.configure()
        work = self.out / 'probe'
        work.mkdir()
        protection = ReadOnlyEnvironment([self.project], work, os.environ)
        with patch('nsim.readonly.subprocess.run', return_value=subprocess.CompletedProcess([], 71, '', 'unsupported')):
            with self.assertRaisesRegex(ConfigError, 'no unprotected fallback'):
                protection.verify(os.environ)

    def test_external_symlink_to_work_directory_is_rejected(self):
        (self.project / 'outputs').symlink_to(self.out, target_is_directory=True)
        with self.assertRaisesRegex(ConfigError, 'overlaps execution'):
            source_paths([self.project], self.out / 'work')

    def test_link_cycles_do_not_recurse_forever_and_missing_targets_fail_closed(self):
        (self.project / 'loop').symlink_to(self.project, target_is_directory=True)
        self.assertEqual(source_paths([self.project], self.out), [str(self.project)])
        (self.project / 'missing').symlink_to(self.base / 'absent')
        with self.assertRaisesRegex(ConfigError, 'cannot resolve read-only'):
            source_paths([self.project], self.out)

    def test_linux_mounts_include_external_targets_and_keep_product_locks(self):
        self.configure()
        with patch('nsim.readonly.sys.platform', 'linux'), patch('nsim.readonly.shutil.which', return_value='/usr/bin/true'):
            protection = ReadOnlyEnvironment([self.project], self.out, os.environ)
        command = protection.command(['/bin/sh', '-c', 'exit 0'], pass_fds=(7, 9))
        for path in (str(self.project), str(self.base / 'external resources')):
            self.assertIn(['--ro-bind', path, path], [command[i:i+3] for i in range(len(command) - 2)])
        self.assertEqual(command[-8:], ['--sync-fd', '7', '--sync-fd', '9', '--', '/bin/sh', '-c', 'exit 0'])
