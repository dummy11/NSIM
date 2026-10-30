"""User-facing output layout, per-run replay and record integrity."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import patch

from test_regression import Fixture
from nsim.config import ConfigError
from nsim.layout import build_directory, simulation_directory
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import replay, run
from nsim.storage import IntegrityError, digest, write_json


class OutputLayoutTests(Fixture):
    def test_single_run_overwrites_the_same_case_seed_directory(self):
        def plan():
            return create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        first_plan = plan()
        first, state = run(first_plan, [self.out])
        self.assertEqual(first, simulation_directory(self.out, first_plan, first_plan['tasks'][0]))
        self.assertEqual(first, self.out / 'base' / 'rtl' / 'sim_dir' / 'smoke_42')
        self.assertTrue(state['passed'])
        attempt = state['tasks'][0]['attempts'][0]
        self.assertTrue((Path(attempt['cwd']) / 'run.log').is_file())
        self.assertTrue((Path(attempt['cwd']) / 'result.json').is_file())
        self.assertEqual(Path(attempt['cwd']), first)
        build = build_directory(self.out, first_plan, first_plan['tasks'][0])
        self.assertEqual(build, self.out / 'base' / 'rtl' / 'com_dir')
        self.assertFalse(json.loads((build / 'build.json').read_text())['compiled'])
        self.assertFalse((self.out / 'regr_dir').exists())
        for filename in ('live.html', 'live.json', 'report.html', 'report.md', 'failed.json'):
            self.assertFalse((first / filename).exists())
        (first / 'obsolete.log').write_text('old run')
        (first / 'waves' / 'old.fsdb').write_text('old wave')
        old_attempt = attempt['id']
        second, repeated = run(plan(), [self.out])
        self.assertEqual(first, second)
        self.assertNotEqual(first_plan['id'], repeated['id'])
        self.assertFalse((second / 'obsolete.log').exists())
        self.assertFalse((second / 'waves' / 'old.fsdb').exists())
        self.assertFalse((second / 'attempts').exists())
        self.assertFalse((second / 'records' / (old_attempt + '.json')).exists())
        self.assertTrue(load_final(second)[1]['passed'])
        self.assertEqual({p.name for p in build.parent.iterdir()}, {'com_dir', 'sim_dir'})

    def test_regression_references_submit_owned_runs_and_per_run_replay(self):
        self.data['fake'] = {'outcomes': {'fast': 'fail'}}
        self.save_submit()
        other = self.project / 'other.json'
        write_json(other, dict(self.data, name='other'))
        plan = create_plan(self.project, caselist=self.cases, submit_paths=[self.submit, other], seeds=[42, 43])
        record, state = run(plan, [self.out], jobs=3)
        self.assertEqual(record.parent.parent, self.out / 'regr_dir')
        self.assertTrue((record / 'report.html').is_file())
        self.assertEqual({p.name for p in self.out.iterdir()}, {'regr_dir'})
        self.assertEqual(len(list((record.parent / 'base' / 'rtl' / 'sim_dir').iterdir())), 6)
        self.assertFalse(list(record.rglob('run.log')))
        task = next(t for t in plan['tasks'] if t['case'] == 'fast' and t['seed'] == 42 and t['submit'] == 0)
        sim = simulation_directory(self.out, plan, task)
        selected, result = load_final(sim)
        self.assertEqual(len(result['tasks']), 1)
        self.assertEqual(selected['kind'], 'case')
        self.assertFalse(result['passed'])
        new, retried = replay(sim)
        self.assertNotEqual(new, sim)
        self.assertEqual(new.name, 'report')
        self.assertEqual(len(retried['tasks']), 1)
        self.assertEqual(retried['tasks'][0]['parent']['run'], state['id'])
        self.assertTrue((new / 'report.html').exists())

    def test_multiple_seeds_on_case_command_use_regression_summary(self):
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42, 43])
        record, _ = run(plan, [self.out])
        self.assertEqual(record.parent.parent, self.out / 'regr_dir')
        self.assertTrue((record / 'report.html').exists())

    def test_rerun_keeps_each_original_seed_and_rejects_overrides(self):
        self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
        self.save_submit()
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42, 43])
        record, _ = run(plan, [self.out])
        with self.assertRaisesRegex(ConfigError, 'preserves case/seed'):
            replay(record, seed=7)
        with self.assertRaisesRegex(ConfigError, 'original output root'):
            replay(record, roots=[self.backup])
        new, state = replay(record, jobs=2)
        self.assertEqual({task['seed'] for task in state['tasks']}, {42, 43})
        self.assertEqual(new.parent.parent, record.parent / 'rerun')
        task = state['tasks'][0]
        directory = Path(task['attempts'][0]['cwd'])
        self.assertEqual(directory, new.parent / 'base' / 'rtl' / 'sim_dir' / 'smoke_42')
        selected, result = load_final(directory)
        self.assertEqual(len(result['tasks']), 1)
        self.assertEqual(result['tasks'][0]['seed'], 42)
        self.assertEqual(selected['id'], state['id'])

    def test_old_success_cannot_mask_missing_new_result(self):
        first = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        directory, _ = run(first, [self.out])
        self.data['fake'] = {'outcomes': {'smoke': 'missing'}}
        self.save_submit()
        second = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        same, state = run(second, [self.out])
        self.assertEqual(same, directory)
        self.assertFalse(state['passed'])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'INCOMPLETE')
        self.assertFalse((directory / 'result.json').exists())

    def test_single_run_cannot_invalidate_old_regression_evidence(self):
        old, _ = self.launch()
        original_plan = (old / 'plan.json').read_bytes()
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        run(plan, [self.out])
        _, previous = load_final(old)
        self.assertTrue(previous['passed'])
        self.assertEqual(next(t for t in previous['tasks'] if t['case'] == 'smoke')['effective_verdict'], 'PASS')
        self.assertEqual((old / 'plan.json').read_bytes(), original_plan)

    def test_dry_run_preserves_existing_simulation_results(self):
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        directory, _ = run(plan, [self.out])
        before = {str(p): digest(p) for p in directory.rglob('*') if p.is_file()}
        preview, state = run(create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42]),
                             [self.out], dry_run=True)
        self.assertEqual(preview.parent.parent, self.out / 'regr_dir')
        self.assertTrue(state['dry_run'])
        self.assertEqual(before, {str(p): digest(p) for p in directory.rglob('*') if p.is_file()})
        self.assertTrue(load_final(directory)[1]['passed'])

    def test_running_case_cannot_be_overwritten_by_a_second_process(self):
        self.data['fake'] = {'durations': {'smoke': 1}}
        self.save_submit()
        args = [sys.executable, '-m', 'nsim', 'run', '--project', str(self.project),
                '--case', 'smoke', '--seed', '42', '--submit', str(self.submit), '--output-root', str(self.out)]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        directory = self.out / 'base' / 'rtl' / 'sim_dir' / 'smoke_42'
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (directory / 'run.log').exists():
                time.sleep(.02)
            self.assertTrue((directory / 'run.log').exists())
            original = (directory / 'run.json').read_bytes()
            second = subprocess.run(args, capture_output=True, text=True, timeout=5)
            self.assertEqual(second.returncode, 2, second.stderr)
            self.assertIn('in use', second.stderr)
            self.assertEqual((directory / 'run.json').read_bytes(), original)
            _, error = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, error)
            self.assertTrue(load_final(directory)[1]['passed'])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_cleanup_unlinks_artifact_symlinks_without_following_them(self):
        def plan():
            return create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        directory, _ = run(plan(), [self.out])
        (directory / 'linked-source').symlink_to(self.project, target_is_directory=True)
        run(plan(), [self.out])
        self.assertEqual((self.project / 'input.txt').read_text(), 'stable input\n')
        self.assertFalse((directory / 'linked-source').exists())

    def test_child_keeps_directory_locked_after_controller_is_killed(self):
        self.data.update(adapter='command', argv=[sys.executable, '-c',
                         'import os,time; print(os.getpid(), flush=True); time.sleep(10)'])
        self.save_submit()
        args = [sys.executable, '-m', 'nsim', 'run', '--project', str(self.project),
                '--case', 'smoke', '--seed', '42', '--submit', str(self.submit), '--output-root', str(self.out)]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        log = self.out / 'base' / 'rtl' / 'sim_dir' / 'smoke_42' / 'run.log'
        child = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if log.exists() and log.read_text().strip():
                    child = int(log.read_text().strip())
                    break
                time.sleep(.02)
            self.assertIsNotNone(child)
            process.kill()
            process.communicate(timeout=5)
            second = subprocess.run(args, capture_output=True, text=True, timeout=5)
            self.assertEqual(second.returncode, 2, second.stderr)
            self.assertIn('in use', second.stderr)
            self.assertEqual(log.read_text().strip(), str(child))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            if child is not None:
                try:
                    os.killpg(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_failed_replacement_never_keeps_old_completion_record(self):
        def plan():
            return create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        directory, _ = run(plan(), [self.out])
        with patch('nsim.service.write_json', side_effect=OSError('injected write failure')):
            with self.assertRaises(OSError):
                run(plan(), [self.out])
        self.assertFalse((directory / 'final.json').exists())
        with self.assertRaises(IntegrityError):
            load_final(directory)

    def test_changed_simulation_reference_is_rejected(self):
        plan = self.plan()
        run(plan, [self.out])
        source = simulation_directory(self.out, plan, plan['tasks'][0])
        ref = source / 'run.json'
        content = json.loads(ref.read_text())
        content['task_id'] = plan['tasks'][1]['id']
        write_json(ref, content)
        with self.assertRaises(IntegrityError):
            load_final(source)

    def test_reserved_names_and_casefold_collisions_are_rejected(self):
        self.data['name'] = 'REGR_DIR'
        self.save_submit()
        with self.assertRaisesRegex(ConfigError, 'reserved'):
            self.plan()
        self.data['name'] = 'base'
        self.save_submit()
        other = self.project / 'other.json'
        write_json(other, dict(self.data, name='BASE'))
        with self.assertRaisesRegex(ConfigError, 'unique names'):
            create_plan(self.project, cases=['smoke'], submit_paths=[self.submit, other])
        plan = create_plan(self.project, cases=['smoke', 'SMOKE'], submit_paths=[self.submit], seeds=[42])
        with self.assertRaisesRegex(ConfigError, 'duplicate simulation destinations'):
            run(plan, [self.out])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_managed_submit_symlink_cannot_redirect_output_into_sources(self):
        (self.out / 'base').symlink_to(self.project, target_is_directory=True)
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit])
        with self.assertRaisesRegex(ConfigError, 'symlink'):
            run(plan, [self.out])
        self.assertFalse((self.project / 'sim_dir').exists())

    def test_missing_selected_root_does_not_switch_to_another_root(self):
        missing = self.base / 'offline'
        with self.assertRaisesRegex(ConfigError, 'select exactly one output root'):
            run(self.plan(), [missing, self.backup])
        with self.assertRaisesRegex(OSError, 'storage root missing'):
            run(self.plan(), [missing])
        self.assertFalse(missing.exists())
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_legacy_completion_format_remains_readable(self):
        record, _ = self.launch()
        final = json.loads((record / 'final.json').read_text())
        final['schema_version'] = 1
        write_json(record / 'final.json', final)
        self.assertTrue(load_final(record)[1]['passed'])

    def test_event_root_is_removed_from_all_cli_entry_points(self):
        for args in (['bootstrap'], ['update'], ['com'], ['sim'], ['run'], ['rerun'], ['recover']):
            help_result = subprocess.run([sys.executable, '-m', 'nsim', *args, '--help'],
                                         capture_output=True, text=True, timeout=10)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertNotIn('--event-root', help_result.stdout)
        result = subprocess.run([sys.executable, '-m', 'nsim', 'bootstrap', '--event-root', str(self.events)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('unrecognized arguments', result.stderr)
