"""Exercise actual bsub/bjobs/bkill subprocesses using a local test service."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import patch

from test_regression import Fixture
from nsim.config import ConfigError, load_submit
from nsim.dashboard import Dashboard
from nsim.layout import build_directory, simulation_directory
from nsim.lsf import check_reservations
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import run
from nsim.storage import write_json


class LsfTests(Fixture):
    def setUp(self):
        super().setUp()
        self.farm = self.base / 'farm'
        self.farm.mkdir()
        write_json(self.farm / 'state.json', {'jobs': {}, 'events': [], 'peak': 0})
        stub = Path(__file__).parent / 'lsf_stub.py'
        scheduler = {'poll_seconds': .02, 'unknown_seconds': .15, 'cancel_seconds': 2,
                     'command_timeout_seconds': 3, 'deadline_seconds': 10, 'result_wait_seconds': .2,
                     'queue': 'simulation'}
        for tool in ('bsub', 'bjobs', 'bkill'):
            path = self.farm / tool
            path.write_text('#!' + sys.executable + '\nimport runpy,sys\nsys.argv = ' +
                            repr([str(stub), tool, str(self.farm)]) + ' + sys.argv[1:]\nrunpy.run_path(' + repr(str(stub)) + ', run_name="__main__")\n')
            path.chmod(0o755)
            scheduler[tool] = str(path)
        self.data['scheduler'] = scheduler  # no kind: LSF is the default
        self.data['fake'] = {'durations': {'slow': 2, 'fast': .02}}
        self.save_submit()
        self.addCleanup(self.stop_workers)

    def farm_state(self):
        from lsf_stub import state_file
        with state_file(self.farm) as state:
            return json.loads(json.dumps(state))

    def configure(self, **values):
        from lsf_stub import state_file
        with state_file(self.farm) as state:
            state.update(values)

    def stop_workers(self):
        state = self.farm_state()
        for job in state['jobs'].values():
            if job.get('worker_pid'):
                try:
                    os.killpg(job['worker_pid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if all(j['status'] in ('DONE', 'EXIT') for j in self.farm_state()['jobs'].values()):
                break
            time.sleep(.03)

    def test_default_lsf_completion_hook_refills_global_window_and_queue(self):
        self.cases.write_text('slow\nfast\nthird\nfourth\nfifth\n')
        plan = self.plan(scheduler_overrides={'queue': 'override'})
        self.assertEqual(plan['submits'][0]['scheduler']['kind'], 'lsf')
        snapshots = []
        original = Dashboard.publish
        def publish(dashboard, state, lifecycle='running', message=''):
            original(dashboard, state, lifecycle, message)
            snapshot = json.loads((dashboard.journal / 'live.json').read_text())
            snapshots.append(snapshot)
            snapshot['tasks'] = [dict(t, effective_verdict=t['attempts'][-1]['verdict'] if t['attempts'] else t['status'].upper()) for t in state['tasks']]
            snapshot['scheduler_jobs'] = dict(state['scheduler_jobs'])
            for task in snapshot['tasks']:
                if task['attempts']:
                    attempt = task['attempts'][-1]
                    self.assertTrue((Path(attempt['record_path'])).is_file())
                    self.assertEqual(json.loads(Path(attempt['record_path']).read_text())['task']['attempts'][-1]['id'], attempt['id'])
        with patch.object(Dashboard, 'publish', publish):
            record, state = run(plan, [self.out], jobs=2)
        self.assertTrue(any(s['tasks'][0]['status'] == 'running' and
                            s['tasks'][1]['effective_verdict'] == 'PASS' for s in snapshots))
        self.assertTrue(any(any(not job['terminal'] for job in s['scheduler_jobs'].values()) for s in snapshots))
        self.assertEqual(snapshots[-1]['lifecycle'], 'complete')
        self.assertTrue(state['passed'], state)
        farm = self.farm_state()
        self.assertEqual(len(farm['jobs']), 5)
        self.assertEqual(farm['peak'], 2)
        active = set()
        for event in farm['events']:
            if event['kind'] == 'SUBMIT':
                active.add(event['job_id'])
                self.assertLessEqual(len(active), 2)
            elif event['kind'] == 'TERMINAL':
                active.discard(event['job_id'])
        submits = [e for e in farm['events'] if e['kind'] == 'SUBMIT']
        terminals = [e for e in farm['events'] if e['kind'] == 'TERMINAL']
        self.assertLess(submits[2]['time'], max(e['time'] for e in terminals if e['job_id'] in ('1', '2')))
        self.assertTrue(all(job['args'][job['args'].index('-q') + 1] == 'override' for job in farm['jobs'].values()))
        self.assertEqual(len(state['scheduler_jobs']), 5)
        self.assertTrue(all(job['terminal'] for job in state['scheduler_jobs'].values()))
        self.assertTrue((record / 'final.json').exists())
        self.assertFalse(list(self.out.rglob('.lsf-active.json')))

    def test_local_override_does_not_submit_lsf_jobs(self):
        plan = self.plan(scheduler_overrides={'kind': 'local'})
        self.assertTrue(run(plan, [self.out], jobs=2)[1]['passed'])
        self.assertEqual(self.farm_state()['jobs'], {})

    def test_unknown_jobs_stop_dispatch_and_preserve_guards(self):
        self.configure(query_mode='unknown')
        record, state = run(self.plan(), [self.out], jobs=1)
        self.assertFalse(state['terminal'])
        self.assertFalse((record / 'final.json').exists())
        self.assertEqual(json.loads((record / 'live.json').read_text())['lifecycle'], 'unresolved')
        self.assertEqual(len(self.farm_state()['jobs']), 1)
        self.assertEqual([task['status'] for task in state['tasks']],
                         ['completed', 'planned', 'planned'])
        self.assertIn('not executed', state['tasks'][1]['pending_reason'])
        self.assertTrue(list(self.out.rglob('.lsf-active.json')))
        self.assertTrue(list(self.out.rglob('.lsf-readers/*.json')))
        saved = json.loads((record / 'plan.json').read_text())
        with self.assertRaisesRegex(ConfigError, 'reservation|still consume'):
            check_reservations(build_directory(self.out, saved, saved['tasks'][0]), writing=True)
        # A new regression has a separate namespace; the old guards stay intact.
        self.assertTrue(run(self.plan(scheduler_overrides={'kind': 'local'}), [self.out])[1]['passed'])

    def test_lost_submission_reply_is_not_resubmitted(self):
        self.configure(submit_mode='no_id')
        record, state = run(self.plan(), [self.out], jobs=1)
        self.assertFalse(state['terminal'])
        self.assertEqual(len(self.farm_state()['jobs']), 1)
        self.assertFalse((record / 'final.json').exists())

    def test_one_unknown_job_keeps_its_slot_while_other_slots_refill(self):
        self.cases.write_text('slow\nfast\nthird\n')
        self.configure(unknown_cases=['slow_42'])
        record, state = run(self.plan(), [self.out], jobs=2)
        self.assertFalse(state['terminal'])
        self.assertFalse((record / 'final.json').exists())
        self.assertEqual([task['effective_verdict'] for task in state['tasks']],
                         ['LSF_UNKNOWN', 'PASS', 'PASS'])
        self.assertEqual(len(self.farm_state()['jobs']), 3)
        self.assertLessEqual(self.farm_state()['peak'], 2)
        self.assertTrue(list(self.out.rglob('.lsf-active.json')))

    def test_explicit_rejection_affects_only_one_job(self):
        self.configure(reject_attempts=[1])
        record, state = run(self.plan(), [self.out], jobs=1)
        self.assertTrue(state['terminal'])
        self.assertFalse(state['passed'])
        self.assertEqual([task['effective_verdict'] for task in state['tasks']],
                         ['SCHEDULER_ERROR', 'PASS', 'PASS'])
        self.assertEqual(len(self.farm_state()['jobs']), 2)
        self.assertTrue((record / 'final.json').exists())

    def test_one_log_collection_failure_does_not_cancel_other_jobs(self):
        from nsim.lsf_logs import Transcript
        original = Transcript.finish
        def fail_first(transcript, *args):
            if 'smoke_42' in str(transcript.main):
                raise OSError('run.log collection failed')
            return original(transcript, *args)
        with patch.object(Transcript, 'finish', fail_first):
            _, state = run(self.plan(), [self.out], jobs=1)
        self.assertEqual([task['effective_verdict'] for task in state['tasks']],
                         ['SCHEDULER_ERROR', 'PASS', 'PASS'])
        self.assertEqual(len(self.farm_state()['jobs']), 3)

    def test_job_record_failure_after_submit_keeps_one_slot(self):
        from nsim import lsf
        original = lsf.write_json
        plan = self.plan()
        first_task = plan['tasks'][0]['id']
        def fail_status(path, value):
            if (path.name == 'job.json' and value.get('task_id') == first_task
                    and value.get('status') == 'SUBMITTED'):
                raise OSError('job status write failed')
            return original(path, value)
        with patch('nsim.lsf.write_json', side_effect=fail_status):
            record, state = run(plan, [self.out], jobs=2)
        self.assertFalse(state['terminal'])
        self.assertFalse((record / 'final.json').exists())
        self.assertEqual([task['effective_verdict'] for task in state['tasks']],
                         ['LSF_UNKNOWN', 'PASS', 'PASS'])
        self.assertTrue(list(self.out.rglob('.lsf-active.json')))
        self.assertLessEqual(self.farm_state()['peak'], 2)

    def test_failed_job_record_read_does_not_cancel_other_jobs(self):
        from nsim import runner
        self.configure(reject_attempts=[1])
        original = runner.digest
        def fail_first_record(path):
            if Path(path).name == 'job.json' and 'smoke_42' in str(path):
                raise OSError('job record read failed')
            return original(path)
        with patch('nsim.runner.digest', side_effect=fail_first_record):
            _, state = run(self.plan(), [self.out], jobs=1)
        self.assertEqual([task['effective_verdict'] for task in state['tasks']],
                         ['SCHEDULER_ERROR', 'PASS', 'PASS'])
        self.assertIn('job record read failed', state['tasks'][0]['attempts'][0]['scheduler_record_error'])

    def test_transient_query_errors_do_not_release_slots(self):
        self.configure(query_mode='transient')
        self.data['scheduler']['unknown_seconds'] = 2
        self.save_submit()
        _, state = run(self.plan(), [self.out], jobs=2)
        self.assertTrue(state['passed'], state)
        self.assertLessEqual(self.farm_state()['peak'], 2)
        self.assertEqual(len(self.farm_state()['jobs']), 3)

    def test_done_without_worker_evidence_is_not_pass(self):
        self.configure(no_worker=True)
        _, state = run(self.plan(), [self.out], jobs=1)
        self.assertFalse(state['passed'])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'SCHEDULER_ERROR')

    def test_malformed_worker_result_is_a_scheduler_error(self):
        self.configure(malformed_worker=True)
        _, state = run(self.plan(), [self.out], jobs=1)
        self.assertFalse(state['passed'])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'SCHEDULER_ERROR')
        self.assertTrue(state['terminal'])

    def test_zero_job_id_is_never_queried_or_cancelled(self):
        plan = self.plan()
        response = subprocess.CompletedProcess([], 0, 'Job <0> is submitted.', '')
        invoke = subprocess.run
        scheduler_tools = {self.data['scheduler'][tool] for tool in ('bsub', 'bjobs', 'bkill')}
        def controlled(argv, *args, **kwargs):
            return response if argv[0] in scheduler_tools else invoke(argv, *args, **kwargs)
        with patch('nsim.lsf.subprocess.run', side_effect=controlled) as control:
            _, state = run(plan, [self.out], jobs=1)
        self.assertFalse(state['terminal'])
        scheduler_calls = [call.args[0][0] for call in control.call_args_list if call.args[0][0] in scheduler_tools]
        self.assertEqual(scheduler_calls, [self.data['scheduler']['bsub']])

    def test_unconfirmed_cancellation_keeps_slot_and_durable_reservations(self):
        self.configure(pending_seconds=10, cancel_refused=True)
        self.data['scheduler'].update(deadline_seconds=.1, cancel_seconds=.5)
        self.save_submit()
        _, state = run(self.plan(), [self.out], jobs=2)
        self.assertFalse(state['terminal'])
        self.assertLessEqual(len(self.farm_state()['jobs']), 2)
        self.assertTrue(list(self.out.rglob('.lsf-active.json')))

    def test_pending_deadline_cancels_and_confirms_before_releasing(self):
        self.configure(pending_seconds=3)
        self.data['scheduler']['deadline_seconds'] = .1
        self.save_submit()
        _, state = run(self.plan(), [self.out], jobs=1)
        self.assertFalse(state['passed'])
        farm = self.farm_state()
        self.assertTrue(any(event['kind'] == 'KILL' for event in farm['events']))
        self.assertTrue(all(job['status'] == 'EXIT' for job in farm['jobs'].values()))
        self.assertFalse(list(self.out.rglob('.lsf-active.json')))

    def test_real_compile_runs_on_worker_once_then_simulation_jobs(self):
        compiler = self.project / 'compiler.py'
        compiler.write_text('from pathlib import Path\nimport sys\n'
                            'assert Path("env/input.txt").read_text() == "stable input\\n"\n'
                            'try:\n    Path("env/input.txt").write_text("forbidden")\n'
                            'except OSError:\n    pass\nelse:\n    raise AssertionError("source write allowed")\n'
                            'Path(sys.argv[1]).write_text("compiled")\n')
        wrapper = self.project / 'wrapper.py'
        wrapper.write_text('''import json,sys
from pathlib import Path
build, case, seed, result = sys.argv[1:]
assert Path('env/input.txt').read_text() == 'stable input\\n'
try:
    Path('env/input.txt').write_text('forbidden')
except OSError:
    pass
else:
    raise AssertionError('source write allowed')
assert (Path(build)/'model').read_text() == 'compiled'
Path(result).write_text(json.dumps({'schema_version':1,'case':case,'seed':int(seed),'complete':True,
    'checks':{'functional':{'executed':True,'passed':True}}}))
''')
        self.descriptor.write_text('schema_version=1\nname="demo"\ninputs=["compiler.py", "wrapper.py", "input.txt"]\n')
        self.data.update(adapter='command', argv=[sys.executable, 'env/wrapper.py', '{build_dir}', '{case}', '{seed}', '{result}'],
                         compile={'argv': [sys.executable, 'env/compiler.py', '{build_dir}/model'], 'artifacts': ['model']},
                         required_checks=['functional'])
        self.data.pop('fake')
        self.save_submit()
        _, state = run(self.plan(), [self.out], jobs=2)
        self.assertTrue(state['passed'], state)
        self.assertEqual(len(self.farm_state()['jobs']), 4)
        self.assertEqual(len({task['attempts'][0]['build_instance'] for task in state['tasks']}), 1)
        self.assertEqual(sum(job['phase'] == 'compile' for job in state['scheduler_jobs'].values()), 1)
        # Regression products no longer populate the single-run compile workspace.
        run(create_plan(self.project, submit_paths=[self.submit], phase='com',
                        scheduler_overrides={'kind': 'local'}), [self.out])
        sim = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[44], phase='sim',
                          scheduler_overrides={'kind': 'local'})
        self.assertTrue(run(sim, [self.out])[1]['passed'])

    def test_multiple_submits_share_one_global_submission_limit(self):
        other = self.project / 'other.json'
        write_json(other, dict(self.data, name='other'))
        plan = create_plan(self.project, cases=['smoke', 'fast'], submit_paths=[self.submit, other], seeds=[42, 43])
        _, state = run(plan, [self.out], jobs=3)
        self.assertTrue(state['passed'], state)
        self.assertEqual(len(self.farm_state()['jobs']), 8)
        self.assertEqual(self.farm_state()['peak'], 3)

    def test_controller_interrupt_cancels_known_jobs_and_publishes_no_final(self):
        self.data['scheduler']['cancel_seconds'] = 2
        self.save_submit()
        self.configure(pending_seconds=3)
        process = subprocess.Popen([sys.executable, '-m', 'nsim', 'regr', '--project', str(self.project),
                                    '-l', str(self.cases), '-sub', str(self.submit), '-seed', '42',
                                    '-o', str(self.out), '-jobs', '2'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while len(self.farm_state()['jobs']) < 2 and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(len(self.farm_state()['jobs']), 2)
            process.send_signal(signal.SIGINT)
            _, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 130, stderr)
            self.assertTrue(all(job['status'] == 'EXIT' for job in self.farm_state()['jobs'].values()))
            self.assertFalse(list(self.out.rglob('final.json')))
            self.assertFalse(list(self.out.rglob('.lsf-active.json')))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_killed_controller_leaves_compile_reader_and_simulation_guards(self):
        self.configure(pending_seconds=.8)
        process = subprocess.Popen([sys.executable, '-m', 'nsim', 'regr', '--project', str(self.project),
                                    '-l', str(self.cases), '-sub', str(self.submit), '-seed', '42',
                                    '-o', str(self.out), '-jobs', '1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not self.farm_state()['jobs'] and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(len(self.farm_state()['jobs']), 1)
            process.kill()
            process.communicate(timeout=5)
            saved = json.loads(next((self.out / 'regr_dir').glob('*/report/plan.json')).read_text())
            with self.assertRaisesRegex(ConfigError, 'reservation|still consume'):
                check_reservations(simulation_directory(self.out, saved, saved['tasks'][0]))
            compile_plan = create_plan(self.project, submit_paths=[self.submit], phase='com',
                                       scheduler_overrides={'kind': 'local'})
            with self.assertRaisesRegex(ConfigError, 'still consume'):
                check_reservations(build_directory(self.out, saved, saved['tasks'][0]), writing=True)
            self.assertTrue(run(compile_plan, [self.out])[1]['passed'])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_missing_or_malformed_status_cannot_free_submission_slots(self):
        for mode in ('missing', 'malformed'):
            self.configure(query_mode=mode)
            plan = self.plan(directory=mode)
            _, state = run(plan, [self.out], jobs=1)
            self.assertFalse(state['terminal'])
            self.assertEqual(state['tasks'][0]['effective_verdict'], 'LSF_UNKNOWN')
            self.stop_workers()
        self.assertEqual(len(self.farm_state()['jobs']), 2)

    def test_execution_node_detects_input_changes_before_tool_launch(self):
        self.configure(pending_seconds=.7)
        from concurrent.futures import ThreadPoolExecutor
        plan = self.plan()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run, plan, [self.out], jobs=1)
            deadline = time.monotonic() + 8
            while not self.farm_state()['jobs'] and time.monotonic() < deadline:
                time.sleep(.02)
            (self.project / 'input.txt').write_text('changed while waiting')
            _, state = future.result(timeout=10)
        self.assertFalse(state['passed'])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'SCHEDULER_ERROR')
        self.assertFalse(list(self.out.rglob('result.json')))

    def test_queue_can_be_omitted_and_worker_timeout_is_a_terminal_failure(self):
        self.data['scheduler'].pop('queue')
        self.data['timeout_seconds'] = .1
        self.save_submit()
        plan = create_plan(self.project, cases=['slow'], submit_paths=[self.submit], seeds=[42])
        _, state = run(plan, [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'TIMEOUT')
        self.assertTrue(state['terminal'])
        self.assertNotIn('-q', self.farm_state()['jobs']['1']['args'])
        self.assertFalse(list(self.out.rglob('.lsf-active.json')))

    def test_execution_node_rechecks_compiled_products_after_queueing(self):
        self.configure(pending_seconds=1)
        from concurrent.futures import ThreadPoolExecutor
        plan = self.plan()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run, plan, [self.out], jobs=1)
            deadline = time.monotonic() + 8
            while not self.farm_state()['jobs'] and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(len(self.farm_state()['jobs']), 1)
            product = next(self.out.rglob('fake-model.json'))
            product.write_text('changed during queueing')
            _, state = future.result(timeout=10)
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'SCHEDULER_ERROR')
        self.assertFalse(list(self.out.rglob('result.json')))

    def single_run(self):
        self.save_submit()
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        return run(plan, [self.out])

    def test_main_log_contains_submission_query_worker_and_farm_output(self):
        self.configure(submit_stderr='submission diagnostic', query_stderr='query diagnostic',
                       farm_stdout='worker startup stdout\n', farm_stderr='worker startup stderr\n',
                       farm_trailer='LSF resource usage: cpu=1\n')
        record, state = self.single_run()
        self.assertTrue(state['passed'], state)
        text = (record / 'run.log').read_text()
        for value in ('Job <1>', 'submission diagnostic', 'query diagnostic', 'worker startup stdout',
                      'worker startup stderr', 'LSF resource usage: cpu=1', 'final scheduler state',
                      'worker result', 'test-node', 'SUBMITTED', 'DONE'):
            self.assertIn(value, text)
        self.assertIn('case=smoke seed=42', text)
        self.assertTrue(load_final(record)[1]['passed'])
        attempt = state['tasks'][0]['attempts'][0]
        self.assertIn(str(record / 'run.log'), attempt['evidence'])
        self.assertTrue(attempt['scheduler']['log_collection']['complete'])
        with (record / 'run.log').open('a') as stream:
            stream.write('changed scheduler transcript\n')
        self.assertEqual(load_final(record)[1]['tasks'][0]['effective_verdict'], 'INCOMPLETE')

    def test_submission_failure_is_visible_without_a_worker(self):
        self.configure(submit_mode='reject')
        record, state = self.single_run()
        text = (record / 'run.log').read_text()
        self.assertIn('Submission refused', text)
        self.assertIn('submission rejected', text)
        self.assertIn('REJECTED', text)
        self.assertFalse(state['passed'])
        self.assertEqual(self.farm_state()['jobs'], {})

    def test_compile_submission_failure_goes_to_compile_log(self):
        compiler = self.project / 'compiler.py'
        compiler.write_text('from pathlib import Path\nPath("model").write_text("compiled")\n')
        self.data.update(adapter='command', argv=[sys.executable, '{project}/compiler.py'],
                         compile={'argv': [sys.executable, '{project}/compiler.py'], 'artifacts': ['model']})
        self.data.pop('fake')
        self.configure(submit_mode='reject')
        _, state = self.single_run()
        log = next(self.out.rglob('compile.log'))
        self.assertIn('Submission refused', log.read_text())
        self.assertFalse(list(self.out.rglob('run.log')))
        self.assertFalse(state['passed'])

    def test_worker_launch_failure_is_in_main_log(self):
        self.configure(launch_error='/shared/python: loader unavailable')
        record, state = self.single_run()
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'SCHEDULER_ERROR')
        text = (record / 'run.log').read_text()
        self.assertIn('/shared/python: loader unavailable', text)
        self.assertIn('LSF launch attempted', text)
        self.assertIn('terminal LSF job has no valid worker result', text)

    def test_pending_cancellation_command_output_is_in_main_log(self):
        self.configure(pending_seconds=3, kill_stdout='Job cancellation accepted')
        self.data['scheduler']['deadline_seconds'] = .1
        record, state = self.single_run()
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'TIMEOUT')
        text = (record / 'run.log').read_text()
        self.assertIn('Job cancellation accepted', text)
        self.assertIn('scheduler deadline', text)
        self.assertIn('bkill', text)
        self.assertFalse(list(self.out.rglob('.lsf-active.json')))

    def test_delayed_farm_files_are_collected_before_pass(self):
        self.configure(farm_delay=.15, farm_trailer='delayed LSF job summary\n')
        self.data['scheduler']['result_wait_seconds'] = 1
        record, state = self.single_run()
        self.assertTrue(state['passed'], state)
        self.assertIn('delayed LSF job summary', (record / 'run.log').read_text())
        self.assertTrue(load_final(record)[1]['passed'])

    def test_missing_farm_files_cannot_publish_pass(self):
        self.configure(withhold_farm=True)
        record, state = self.single_run()
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'SCHEDULER_ERROR')
        self.assertIn('LSF output is missing', (record / 'run.log').read_text())
        self.assertFalse(state['passed'])

    def test_changed_worker_log_cannot_be_resealed_as_pass(self):
        from nsim.lsf_logs import Transcript
        finish = Transcript.finish
        def mutate(transcript, *args):
            with transcript.main.open('a') as stream:
                stream.write('modified after worker analysis\n')
            return finish(transcript, *args)
        with patch.object(Transcript, 'finish', mutate):
            record, state = self.single_run()
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'SCHEDULER_ERROR')
        self.assertIn('stage log changed after worker analysis', (record / 'run.log').read_text())

    def test_submission_timeout_keeps_partial_stdout_and_stderr(self):
        invoke = subprocess.run
        def timeout(argv, *args, **kwargs):
            if argv[0] == self.data['scheduler']['bsub']:
                raise subprocess.TimeoutExpired(argv, .1, output=b'partial submit reply', stderr=b'partial submit error')
            return invoke(argv, *args, **kwargs)
        with patch('nsim.lsf.subprocess.run', side_effect=timeout):
            record, state = self.single_run()
        text = (record / 'run.log').read_text()
        self.assertIn('partial submit reply', text)
        self.assertIn('partial submit error', text)
        self.assertFalse(state['terminal'])
