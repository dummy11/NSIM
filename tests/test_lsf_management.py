"""Safety/recovery and small-file contracts, using the process-level LSF stub."""

from contextlib import ExitStack, redirect_stdout
import copy
import errno
import fcntl
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from test_lsf import LsfFixture
from nsim.case_records import checksum
from nsim.cli import main
from nsim.config import ConfigError
from nsim.layout import SimulationDirectories, build_directory, simulation_directory
from nsim.lsf import check_reservations
from nsim.lsf_commands import inspect, release
from nsim.lsf_records import resolution_path
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import run
from nsim.storage import digest, write_json


class LsfManagementTests(LsfFixture):
    def unknown(self, jobs=1, no_id=False):
        self.configure(query_mode='unknown', submit_mode='no_id' if no_id else None)
        plan = self.plan()
        journal, state = run(plan, [self.out], jobs=jobs)
        self.assertFalse(state['terminal'])
        paths = [Path(t['attempts'][0]['record_path']) for t in state['tasks'] if t['attempts']]
        return plan, journal, state, paths

    def wait_finished(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if all(j['status'] in ('DONE', 'EXIT') for j in self.farm_state()['jobs'].values()):
                return
            time.sleep(.03)
        self.fail('local LSF worker did not finish')

    def snapshots(self, paths):
        return {str(p): p.read_bytes() for p in paths}

    def test_compact_jobs_share_log_and_keep_no_per_case_sidecars(self):
        plan = self.plan()
        journal, state = run(plan, [self.out], jobs=2)
        self.assertTrue(state['passed'], state)
        self.assertTrue(load_final(journal)[1]['passed'])
        self.assertTrue((journal / 'lsf.log').is_file())
        self.assertEqual(list((journal / '.lsf').iterdir()), [])
        self.assertFalse(list(self.out.rglob('.lsf-active.json')))
        self.assertFalse(list(self.out.rglob('job.json')))
        self.assertFalse(list(self.out.rglob('request.json')))
        self.assertFalse(list(self.out.rglob('controller.log')))
        self.assertFalse(list(self.out.rglob('.lsf-readers/*.json')))
        for task in state['tasks']:
            path = Path(task['attempts'][0]['record_path'])
            record = json.loads(path.read_text())
            self.assertTrue(record['lsf']['terminal'])
            self.assertEqual(record['lsf']['request']['plan_ref']['path'], str(journal / 'plan.json'))
            self.assertNotIn('plan', record['lsf']['request'])
            self.assertEqual(record['lsf']['request_sha256'], checksum(record['lsf']['request']))
            self.assertNotIn(str(path), record['task']['attempts'][0]['evidence'])
            self.assertFalse(any('/.lsf/' in p for p in task['attempts'][0]['evidence']))
            text = (path.parent / 'run.log').read_text()
            self.assertIn('token=' + record['lsf']['token'], text)
            for other in state['tasks']:
                if other['id'] != task['id']:
                    self.assertNotIn('token=' + other['attempts'][0]['scheduler']['token'], text)
        self.assertEqual(inspect(journal.parent)['jobs'], [])
        self.assertTrue(all(j['include_postproc'] == 'Y' for j in self.farm_state()['jobs'].values()))

    def test_unknown_case_is_durable_and_pending_directories_do_not_exist(self):
        plan, journal, state, paths = self.unknown()
        self.assertEqual(len(paths), 1)
        for task in plan['tasks'][1:]:
            self.assertFalse(simulation_directory(self.out, plan, task).exists())
        saved = json.loads(paths[0].read_text())
        self.assertFalse(saved['lsf']['terminal'])
        self.assertEqual(saved['lsf']['name'], 'nsim_' + saved['lsf']['token'])
        self.assertFalse((paths[0].parent / '.lsf-active.json').exists())
        with self.assertRaisesRegex(ConfigError, 'reservation'):
            check_reservations(paths[0].parent)
        self.wait_finished()
        before = self.snapshots([p for p in self.out.rglob('*') if p.is_file() and not p.is_symlink()])
        result = inspect(journal.parent)
        self.assertTrue(result['complete'], result)
        self.assertEqual(len(result['jobs']), 1)
        self.assertEqual(len(result['uncreated_cases']), 2)
        self.assertEqual(before, self.snapshots(map(Path, before)))
        self.assertFalse(result['jobs'][0]['released'])

    def test_release_keeps_original_case_and_regression_evidence(self):
        plan, journal, state, paths = self.unknown()
        self.wait_finished()
        self.configure(query_mode=None)
        token = json.loads(paths[0].read_text())['lsf']['token']
        original = self.snapshots([paths[0], journal / 'plan.json', journal / 'state.json'])
        build = build_directory(self.out, plan, plan['tasks'][0])
        for source in (paths[0].parent, build, journal.parent):
            result = inspect(source, check_lsf=True)
            self.assertEqual(result['jobs'][0]['token'], token)
            self.assertTrue(result['jobs'][0]['checked']['releasable'], result)
        result = release(journal.parent, token)
        self.assertTrue(result['released'])
        self.assertEqual(original, self.snapshots(map(Path, original)))
        self.assertFalse((journal / 'final.json').exists())
        check_reservations(paths[0].parent)
        check_reservations(build, writing=True)
        self.assertEqual(inspect(journal)['jobs'], [])
        self.assertTrue(release(journal, token)['already_released'])
        self.assertEqual(len(self.farm_state()['jobs']), 1)
        self.assertFalse(any(e['kind'] == 'KILL' for e in self.farm_state()['events']))

    def test_lost_id_can_be_found_only_by_exact_unique_identity(self):
        _, journal, _, paths = self.unknown(no_id=True)
        self.wait_finished()
        saved = json.loads(paths[0].read_text())
        self.assertIsNone(saved['lsf']['job_id'])
        token = saved['lsf']['token']
        self.configure(query_mode=None, duplicate_query=True)
        with self.assertRaisesRegex(ConfigError, 'unconfirmed'):
            release(journal, token)
        self.assertFalse(resolution_path(paths[0].parent, token).exists())
        self.configure(duplicate_query=False)
        self.assertEqual(release(paths[0].parent, token)['job_id'], '1')
        self.assertIsNone(json.loads(paths[0].read_text())['lsf']['job_id'])

    def test_unavailable_or_ambiguous_identity_never_releases(self):
        _, journal, _, paths = self.unknown()
        self.wait_finished()
        saved = json.loads(paths[0].read_text())
        token = saved['lsf']['token']
        for settings in ({'query_mode': 'missing'}, {'query_mode': 'malformed'},
                         {'query_mode': None, 'query_overrides': {'JOB_NAME': 'other'}},
                         {'query_mode': None, 'query_overrides': {'USER': 'other'}},
                         {'query_mode': None, 'query_overrides': {'OUTPUT_FILE': '/other'}},
                         {'query_mode': None, 'query_overrides': {'SUBMIT_TIME': 'changed'}},
                         {'query_mode': None, 'query_overrides': {'STAT': 'RUN'}},
                         {'query_mode': None, 'query_overrides': {'STAT': 'ZOMBI'}},
                         {'query_mode': None, 'query_overrides': {'EXIT_CODE': float('nan')}},
                         {'query_mode': None, 'duplicate_query': True}):
            with self.subTest(settings=settings):
                self.configure(query_mode=None, query_overrides={}, duplicate_query=False)
                self.configure(**settings)
                with self.assertRaises(ConfigError):
                    release(journal, token)
                self.assertFalse(resolution_path(paths[0].parent, token).exists())
                with self.assertRaises(ConfigError):
                    check_reservations(paths[0].parent)
        self.assertFalse(any(e['kind'] == 'KILL' for e in self.farm_state()['events']))

    def test_release_one_reader_does_not_release_another(self):
        plan, journal, _, paths = self.unknown(jobs=2)
        self.wait_finished()
        self.configure(query_mode=None)
        tokens = [json.loads(p.read_text())['lsf']['token'] for p in paths]
        build = build_directory(self.out, plan, plan['tasks'][0])
        release(build, tokens[0])
        self.assertFalse((build / '.lsf-readers' / (tokens[0] + '.json')).exists())
        self.assertTrue((build / '.lsf-readers' / (tokens[1] + '.json')).exists())
        with self.assertRaisesRegex(ConfigError, 'still consume'):
            check_reservations(build, writing=True)
        self.assertEqual([j['token'] for j in inspect(build)['jobs']], [tokens[1]])
        release(journal, tokens[1])
        check_reservations(build, writing=True)

    def test_live_controller_lock_and_changed_guard_prevent_release(self):
        plan, journal, _, paths = self.unknown()
        self.wait_finished()
        self.configure(query_mode=None)
        job = json.loads(paths[0].read_text())['lsf']
        with (paths[0].parent / '.run.lock').open('r') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ConfigError, 'in use'):
                release(journal, job['token'])
        build = build_directory(self.out, plan, plan['tasks'][0])
        guard = build / '.lsf-readers' / (job['token'] + '.json')
        value = json.loads(guard.read_text())
        write_json(guard, dict(value, token='f' * 32))
        self.assertFalse(inspect(journal)['complete'])
        with self.assertRaises(ConfigError):
            release(journal, job['token'])
        self.assertTrue(guard.exists())

    def test_corrupt_or_missing_run_record_is_not_an_empty_directory(self):
        plan, journal, _, paths = self.unknown()
        self.wait_finished()
        original = paths[0].read_bytes()
        for data in (b'{broken', None):
            if data is None:
                paths[0].unlink()
            else:
                paths[0].write_bytes(data)
            result = inspect(journal)
            self.assertFalse(result['complete'], result)
            with SimulationDirectories(plan) as directories:
                with self.assertRaises(ValueError):
                    directories.reserve(self.out, plan['tasks'][0])
            paths[0].write_bytes(original)

    def test_release_refuses_symlink_and_forged_receipt(self):
        _, journal, _, paths = self.unknown()
        self.wait_finished()
        self.configure(query_mode=None)
        job = json.loads(paths[0].read_text())['lsf']
        receipt = resolution_path(paths[0].parent, job['token'])
        receipt.symlink_to(paths[0])
        with self.assertRaises(ConfigError):
            release(journal, job['token'])
        receipt.unlink()
        write_json(receipt, dict(schema_version=1, released=True, token=job['token']))
        with self.assertRaises(ConfigError):
            check_reservations(paths[0].parent)

    def test_initial_run_record_failure_prevents_only_that_bsub(self):
        from nsim import runner
        original = runner.write_json
        def fail_initial(path, value):
            if (path.name == 'run.json' and path.parent.name == 'smoke_42'
                    and value.get('lsf', {}).get('status') == 'SUBMITTING'):
                raise OSError(errno.EIO, 'cannot persist submission intent', str(path))
            return original(path, value)
        with patch('nsim.runner.write_json', side_effect=fail_initial):
            _, state = run(self.plan(), [self.out], jobs=1)
        self.assertEqual(len(self.farm_state()['jobs']), 2)
        self.assertEqual([t['effective_verdict'] for t in state['tasks']][1:], ['PASS', 'PASS'])
        self.assertTrue(state['terminal'])

    def test_cli_read_only_output_and_release_name(self):
        _, journal, _, paths = self.unknown()
        self.wait_finished()
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['lsf', 'inspect', str(journal.parent), '--json'])
        self.assertEqual(code, 0)
        token = json.loads(output.getvalue())['jobs'][0]['token']
        self.configure(query_mode=None)
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['lsf', 'release', str(journal.parent), '--token', token, '--json'])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())['released'])
        self.assertEqual(len(self.farm_state()['jobs']), 1)

    def test_legacy_job_and_guards_release_without_migrating_records(self):
        plan, journal, _, paths = self.unknown()
        self.wait_finished()
        record = json.loads(paths[0].read_text())
        job = record.pop('lsf')
        token = job['token']
        old_dir = paths[0].parent / '.lsf-jobs' / token
        old_dir.mkdir(parents=True)
        old_path = old_dir / 'job.json'
        request = job.pop('request')
        request.pop('plan_ref')
        request['plan'] = json.loads((journal / 'plan.json').read_text())
        request['guards'].insert(0, str(paths[0].parent / '.lsf-active.json'))
        write_json(old_dir / 'request.json', request)
        job.update(record=str(old_path), request_sha256=digest(old_dir / 'request.json'))
        job.pop('include_postproc')
        write_json(old_path, job)
        for guard in request['guards']:
            write_json(Path(guard), dict(token=token, record=str(old_path), job_name=job['name']))
        record['task']['attempts'][0].update(scheduler_record=str(old_path), scheduler=job)
        record['checksums']['control'] = checksum({k: v for k, v in record.items() if k not in ('analysis', 'checksums')})
        write_json(paths[0], record)
        original = self.snapshots([old_path, old_dir / 'request.json', paths[0]])
        self.configure(query_mode=None)
        with self.assertRaisesRegex(ConfigError, 'postprocessing'):
            release(journal, token)
        self.configure(query_overrides={'STAT': 'PDONE'})
        release(journal, token)
        self.assertEqual(original, self.snapshots(map(Path, original)))
        check_reservations(paths[0].parent)
        self.assertFalse(list(self.out.rglob('.lsf-active.json')))
        self.assertEqual(inspect(journal)['jobs'], [])

    def test_lsf_record_updates_are_checkpoints_not_poll_writes(self):
        from nsim import runner
        self.configure(terminal_after_queries=12)
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        writes = []
        original = runner.write_json
        def observe(path, value):
            if path.name == 'run.json':
                writes.append(value.get('lsf', {}).get('status'))
            return original(path, value)
        with patch('nsim.runner.write_json', side_effect=observe):
            _, state = run(plan, [self.out])
        self.assertTrue(state['passed'], state)
        self.assertNotIn('PEND', writes)
        self.assertNotIn('RUN', writes)
        self.assertLessEqual(len(writes), 6)
        self.assertGreater(self.farm_state()['jobs']['1']['queries'], len(writes))

    def test_pre_submit_has_durable_identity_and_only_dispatched_case_directories(self):
        from nsim import lsf
        plan = self.plan()
        original = lsf.control
        submitted = []
        def control(argv, *args, **kwargs):
            if argv[0] == self.data['scheduler']['bsub']:
                cwd = Path(argv[argv.index('-cwd') + 1])
                record = json.loads((cwd / 'run.json').read_text())
                job = record['lsf']
                self.assertIsNone(job['job_id'])
                self.assertFalse(job['terminal'])
                self.assertEqual(job['name'], argv[argv.index('-J') + 1])
                for guard in job['request']['guards']:
                    self.assertEqual(json.loads(Path(guard).read_text())['token'], job['token'])
                submitted.append(cwd)
                existing = [simulation_directory(self.out, plan, t) for t in plan['tasks']
                            if simulation_directory(self.out, plan, t).exists()]
                self.assertEqual(existing, submitted)
            return original(argv, *args, **kwargs)
        with patch('nsim.lsf.control', side_effect=control):
            _, state = run(plan, [self.out], jobs=1)
        self.assertTrue(state['passed'], state)
        self.assertEqual(len(submitted), 3)

    def test_release_interruption_is_retryable_and_keeps_other_readers(self):
        plan, journal, _, paths = self.unknown(jobs=2)
        self.wait_finished()
        self.configure(query_mode=None)
        tokens = [json.loads(path.read_text())['lsf']['token'] for path in paths]
        from nsim import lsf_commands
        original = lsf_commands.write_json
        def fail_commit(path, value):
            if value.get('released') is True:
                raise OSError(errno.EIO, 'release receipt commit failed')
            return original(path, value)
        with patch('nsim.lsf_commands.write_json', side_effect=fail_commit):
            with self.assertRaises(OSError):
                release(journal, tokens[0])
        with self.assertRaises(ConfigError):
            check_reservations(paths[0].parent)
        build = build_directory(self.out, plan, plan['tasks'][0])
        self.assertTrue((build / '.lsf-readers' / (tokens[1] + '.json')).exists())
        self.assertTrue(release(journal, tokens[0])['released'])
        check_reservations(paths[0].parent)
        self.assertTrue((build / '.lsf-readers' / (tokens[1] + '.json')).exists())

    def test_missing_record_still_displays_token_from_reader_and_cannot_release(self):
        plan, journal, _, paths = self.unknown()
        self.wait_finished()
        token = json.loads(paths[0].read_text())['lsf']['token']
        paths[0].unlink()
        build = build_directory(self.out, plan, plan['tasks'][0])
        result = inspect(build)
        self.assertFalse(result['complete'])
        self.assertEqual(result['jobs'][0]['token'], token)
        with self.assertRaises(ConfigError):
            release(build, token)
        self.assertTrue((build / '.lsf-readers' / (token + '.json')).exists())

    def test_directory_creation_failure_is_local_to_one_case(self):
        from nsim.layout import SimulationDirectories
        original = SimulationDirectories.prepare
        def fail(directories, root, task):
            if task['case'] == 'smoke':
                raise ConfigError('case directory is in use')
            return original(directories, root, task)
        with patch.object(SimulationDirectories, 'prepare', fail):
            _, state = run(self.plan(), [self.out], jobs=1)
        self.assertEqual([t['effective_verdict'] for t in state['tasks']], ['INFRA_ERROR', 'PASS', 'PASS'])
        self.assertEqual(len(self.farm_state()['jobs']), 2)

    def test_corrupt_case_record_failure_does_not_abort_the_batch(self):
        from nsim.layout import SimulationDirectories
        from nsim.storage import IntegrityError
        original = SimulationDirectories.prepare
        def fail(directories, root, task):
            if task['case'] == 'smoke':
                raise IntegrityError('case control checksum mismatch')
            return original(directories, root, task)
        with patch.object(SimulationDirectories, 'prepare', fail):
            _, state = run(self.plan(), [self.out], jobs=1)
        self.assertEqual([t['effective_verdict'] for t in state['tasks']], ['INFRA_ERROR', 'PASS', 'PASS'])
        self.assertEqual(len(self.farm_state()['jobs']), 2)

    def test_nonfinite_scheduler_reply_does_not_free_a_job_slot(self):
        self.configure(query_overrides={'EXIT_CODE': float('nan')})
        _, state = run(self.plan(), [self.out], jobs=1)
        self.assertFalse(state['terminal'])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'LSF_UNKNOWN')
        self.assertEqual(len(self.farm_state()['jobs']), 1)

    def test_identity_mismatch_prevents_cancelling_a_reused_job_id(self):
        self.configure(query_overrides={'JOB_NAME': 'unrelated-job'}, pending_seconds=.8)
        self.data['scheduler'].update(deadline_seconds=.1, cancel_seconds=.2)
        self.save_submit()
        _, state = run(self.plan(), [self.out])
        self.assertFalse(state['terminal'])
        self.assertFalse(any(e['kind'] == 'KILL' for e in self.farm_state()['events']))

    def test_metadata_directory_sync_failure_prevents_submit(self):
        from nsim import lsf
        original = lsf.sync_directory
        def fail(path):
            if Path(path).name == 'smoke_42':
                raise OSError(errno.EIO, 'cannot persist directory entry')
            return original(path)
        with patch('nsim.lsf.sync_directory', side_effect=fail):
            _, state = run(self.plan(), [self.out])
        self.assertEqual(len(self.farm_state()['jobs']), 2)
        self.assertEqual([t['effective_verdict'] for t in state['tasks']][1:], ['PASS', 'PASS'])

    def command_submit(self):
        self.data.update(adapter='command', required_checks=['functional'],
            compile={'argv': [sys.executable, '-c', 'from pathlib import Path; Path("model").write_text("compiled")'],
                     'artifacts': ['model']},
            argv=[sys.executable, '-c',
                  'import json,sys; from pathlib import Path; '
                  'Path(sys.argv[3]).write_text(json.dumps(dict(schema_version=1,case=sys.argv[1],'
                  'seed=int(sys.argv[2]),complete=True,checks=dict(functional=dict(executed=True,passed=True)))))',
                  '{case}', '{seed}', '{result}'])
        self.data.pop('fake')
        self.save_submit()

    def test_lsf_compile_snapshot_keeps_evidence_but_not_live_occupancy(self):
        self.command_submit()
        compile_plan = create_plan(self.project, submit_paths=[self.submit], phase='com')
        compile_dir, state = run(compile_plan, [self.out])
        self.assertTrue(state['passed'], state)
        self.assertEqual(inspect(compile_dir)['jobs'], [])
        journal, state = run(self.plan(phase='sim'), [self.out], jobs=2)
        self.assertTrue(state['passed'], state)
        self.assertTrue(load_final(journal)[1]['passed'])
        result = inspect(journal.parent)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['jobs'], [])
        self.assertEqual(len(self.farm_state()['jobs']), 4)

    def test_unresolved_compile_job_can_be_inspected_and_released(self):
        from nsim.lsf import LsfError
        self.command_submit()
        self.configure(query_mode='unknown')
        plan = create_plan(self.project, submit_paths=[self.submit], phase='com')
        with self.assertRaises(LsfError):
            run(plan, [self.out])
        build = build_directory(self.out, plan, plan['tasks'][0])
        self.wait_finished()
        result = inspect(build)
        self.assertTrue(result['complete'], result)
        token = result['jobs'][0]['token']
        self.assertEqual(result['jobs'][0]['phase'], 'compile')
        self.configure(query_mode=None)
        release(build, token)
        check_reservations(build, writing=True)
        self.assertFalse((build / '.lsf-active.json').exists())

    def test_schema_damage_cannot_disable_case_occupancy_checksum(self):
        _, journal, _, paths = self.unknown()
        self.wait_finished()
        value = json.loads(paths[0].read_text())
        value['schema_version'] = 999
        value['lsf']['terminal'] = True
        write_json(paths[0], value)
        self.assertFalse(inspect(journal)['complete'])
        with self.assertRaisesRegex(ConfigError, 'unsupported'):
            check_reservations(paths[0].parent)

    def test_missing_reader_is_reported_and_explicit_release_still_checks_job(self):
        _, journal, _, paths = self.unknown()
        self.wait_finished()
        job = json.loads(paths[0].read_text())['lsf']
        Path(job['request']['guards'][0]).unlink()
        result = inspect(journal)
        self.assertFalse(result['complete'])
        self.assertEqual(result['jobs'][0]['missing_markers'], job['request']['guards'])
        with self.assertRaises(ConfigError):
            release(journal, job['token'])
        self.configure(query_mode=None)
        self.assertTrue(release(journal, job['token'])['released'])
