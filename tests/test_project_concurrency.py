"""A project transaction must be visible beyond its writer's private registry."""

import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_projects
from nsim import projects
from nsim.project_paths import ProjectError, registry_path


class ProjectConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_projects.ProjectTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.boot()
        self.root = self.fixture.base / 'verification'

    def interrupt(self, after_private_commit=False):
        replacement = self.fixture.rtl / 'replacement.f'
        replacement.write_text('dut.sv\n')
        script = '''import os,sys
from pathlib import Path
from nsim import projects
from nsim.cli import main
original_write = projects.atomic_write
original_unlink = Path.unlink
def write(path, data):
    if path.name == 'sim.f': os._exit(99)
    original_write(path, data)
def unlink(path, *args, **kwargs):
    if path.name == 'project.transaction.json': os._exit(98)
    return original_unlink(path, *args, **kwargs)
'''
        script += 'Path.unlink = unlink\n' if after_private_commit else 'projects.atomic_write = write\n'
        script += 'main(sys.argv[1:])\n'
        result = subprocess.run([sys.executable, '-c', script, 'update', '--project', str(self.root),
                                 '--rtl-filelist', str(replacement)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 98 if after_private_commit else 99, result.stderr)

    def test_other_user_cannot_read_or_update_an_interrupted_transaction(self):
        self.interrupt()
        before = (self.root / '.nsim/nsim.toml').read_bytes()
        with patch.dict(os.environ, XDG_CONFIG_HOME=str(self.fixture.base / 'other-user-config')):
            with self.assertRaisesRegex(ProjectError, 'another user/registry'):
                with projects.project_lock(self.root):
                    self.fail('partial project configuration must not be exposed')
            with self.assertRaisesRegex(ProjectError, 'another user/registry'):
                projects.update(self.fixture.options(project=self.root, rtl_filelist=self.fixture.rtl / 'rtl.f'))
        self.assertEqual((self.root / '.nsim/nsim.toml').read_bytes(), before)
        projects.update(self.fixture.options(project=self.root, rtl_filelist=self.fixture.rtl / 'rtl.f'))
        self.assertFalse(projects.transaction_guard(self.root).exists())
        with patch.dict(os.environ, XDG_CONFIG_HOME=str(self.fixture.base / 'other-user-config')):
            with projects.project_lock(self.root):
                pass

    def test_guard_left_after_commit_is_finalized_without_rollback(self):
        self.interrupt(after_private_commit=True)
        self.assertFalse((registry_path().parent / 'projects.transaction.json').exists())
        self.assertTrue(projects.transaction_guard(self.root).is_file())
        before = (self.root / '.nsim/nsim.toml').read_bytes()
        with projects.project_lock(self.root):
            pass
        self.assertFalse(projects.transaction_guard(self.root).exists())
        self.assertEqual((self.root / '.nsim/nsim.toml').read_bytes(), before)

    def test_creating_root_does_not_bypass_its_parent_lock(self):
        root = self.fixture.base / 'new-project'
        ready = self.fixture.base / 'ready'
        release = self.fixture.base / 'release'
        entered = self.fixture.base / 'entered'
        writer_script = '''import sys,time
from pathlib import Path
from nsim.projects import project_lock
root,ready,release=map(Path,sys.argv[1:])
with project_lock(root):
    root.mkdir()
    ready.touch()
    while not release.exists(): time.sleep(.01)
'''
        reader_script = '''import sys
from pathlib import Path
from nsim.projects import project_lock
with project_lock(Path(sys.argv[1])): Path(sys.argv[2]).touch()
'''
        writer = subprocess.Popen([sys.executable, '-c', writer_script, str(root), str(ready), str(release)])
        reader = None
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists())
            reader = subprocess.Popen([sys.executable, '-c', reader_script, str(root), str(entered)])
            time.sleep(.3)
            self.assertFalse(entered.exists())
            release.touch()
            self.assertEqual(writer.wait(timeout=5), 0)
            self.assertEqual(reader.wait(timeout=5), 0)
            self.assertTrue(entered.exists())
        finally:
            for process in (writer, reader):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
