"""Real installed versions dispatch independently of other project environments."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_installer import SCRIPT, test_wheel


class RuntimeIsolationTests(unittest.TestCase):
    def test_parallel_versions_ignore_foreign_python_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'installation'
            projects = []
            for version in ('0.1.0', '0.2.0'):
                wheel = test_wheel(base, version)
                result = subprocess.run([sys.executable, str(SCRIPT), 'install', '--wheel', str(wheel),
                                         '--root', str(root)], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                project = base / ('project-' + version)
                project.mkdir()
                (project / 'nsim.toml').write_text('schema_version=1\nname="demo"\n')
                (project / '.nsim_version').write_text(version + '\n')
                projects.append(project)
            poison = base / 'foreign-python'
            (poison / 'nsim').mkdir(parents=True)
            (poison / 'nsim/__init__.py').write_text('raise RuntimeError("wrong installed version")')

            def invoke(item):
                version, project = item
                home = base / ('user-' + version)
                home.mkdir()
                env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                           PYTHONPATH=str(poison), PYTHONHOME=str(poison), PYTHONUSERBASE=str(poison),
                           NSIM_VERSION='9.9.9')
                # An explicit project's pin wins over another shell's default.
                return subprocess.run([root / 'bin/nsim', '--version', '--project', str(project)],
                                      cwd=base, env=env, capture_output=True, text=True, timeout=10)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(invoke, zip(('0.1.0', '0.2.0'), projects)))
            for version, result in zip(('0.1.0', '0.2.0'), results):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f'{version} --version --project {base / ("project-" + version)}')
