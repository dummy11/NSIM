"""Cross-project, runtime and user ownership at shared output locations."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from unittest.mock import patch

from test_regression import Fixture
from nsim.compilation import Builds
from nsim.config import ConfigError
from nsim.layout import build_directory
from nsim.planning import create_plan
from nsim.service import run
from nsim.storage import digest


class ConcurrencyTests(Fixture):
    def single(self, project=None, **kwargs):
        project = project or self.project
        return create_plan(project, cases=['smoke'], seeds=[42],
                           submit_paths=[project / self.submit.name], **kwargs)

    def snapshot(self):
        return {str(p): digest(p) for p in self.out.rglob('*') if p.is_file()}

    def test_other_project_cannot_replace_completed_outputs(self):
        run(self.single(), [self.out])
        original = self.snapshot()
        other = self.base / 'other-project'
        shutil.copytree(self.project, other)
        with self.assertRaisesRegex(ConfigError, 'owner'):
            run(self.single(other), [self.out])
        self.assertEqual(self.snapshot(), original)

    def test_other_version_cannot_replace_completed_outputs(self):
        run(self.single(), [self.out])
        original = self.snapshot()
        with patch('nsim.planning.get_version', return_value='99.0.0'):
            with self.assertRaisesRegex(ConfigError, 'owner'):
                run(self.single(), [self.out])
        self.assertEqual(self.snapshot(), original)

    def test_other_user_cannot_replace_group_writable_outputs(self):
        run(self.single(), [self.out])
        original = self.snapshot()
        # Exercise ownership even when filesystem permissions would permit writes.
        with patch('nsim.ownership.os.getuid', return_value=os.getuid() + 1):
            with self.assertRaisesRegex(ConfigError, 'owner'):
                run(self.single(), [self.out])
        self.assertEqual(self.snapshot(), original)

    def test_compile_lock_failure_preserves_previous_simulation(self):
        run(self.single(), [self.out])
        original = self.snapshot()
        plan = self.single(phase='sim')
        policy = dict(min_free_bytes=0, min_free_inodes=0)
        with Builds(plan) as reader:
            reader.ensure(self.out, plan['tasks'][0], threading.Event(), policy)
            with self.assertRaisesRegex(ConfigError, 'compile directory is in use'):
                run(self.single(), [self.out])
        self.assertEqual(self.snapshot(), original)

    def test_unowned_nonempty_directory_is_not_adopted_or_deleted(self):
        plan = self.single()
        build = build_directory(self.out, plan, plan['tasks'][0])
        build.mkdir(parents=True)
        sentinel = build / 'someone-elses-result'
        sentinel.write_text('keep me')
        with self.assertRaisesRegex(ConfigError, 'owner'):
            run(plan, [self.out])
        self.assertEqual(sentinel.read_text(), 'keep me')

    def test_nested_product_from_another_run_is_rejected(self):
        run(self.single(), [self.out])
        original = self.snapshot()
        build = self.out / 'base/rtl/com_dir'
        with self.assertRaisesRegex(ConfigError, 'nested|owner'):
            run(self.single(directory='other', com_dir=str(build / 'nested')), [self.out])
        self.assertEqual(self.snapshot(), original)

    def test_product_kind_cannot_be_swapped_by_another_plan(self):
        directory, _ = run(self.single(sim_dir='results'), [self.out])
        original = self.snapshot()
        with self.assertRaisesRegex(ConfigError, 'owner'):
            run(self.single(directory='other', com_dir=str(directory)), [self.out])
        self.assertEqual(self.snapshot(), original)

    def test_projects_with_separate_workspaces_run_in_parallel_processes(self):
        self.data['fake'] = {'durations': {'smoke': .2}}
        self.save_submit()
        other = self.base / 'other-project'
        shutil.copytree(self.project, other)
        def invoke(project):
            return subprocess.run([sys.executable, '-m', 'nsim', 'run', '--project', str(project),
                                   '--submit', str(project / self.submit.name), '--case', 'smoke',
                                   '--seed', '42', '-dir', project.name, '-o', str(self.out)],
                                  capture_output=True, text=True, timeout=15)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(invoke, [self.project, other]))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_builtin_worker_cannot_be_shadowed_by_simulation_directory(self):
        self.data['hooks'] = {'pre_sim': {'name': 'local-python-package', 'argv': [sys.executable, '-c',
            "from pathlib import Path; p=Path('nsim'); p.mkdir(); "
            "(p/'__init__.py').write_text('raise RuntimeError(\"wrong nsim package\")')"]}}
        self.save_submit()
        _, state = run(self.single(), [self.out])
        self.assertTrue(state['passed'], state)
