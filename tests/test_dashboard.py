"""Controller-driven terminal updates, persistence boundaries and static reports."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import time
from unittest.mock import patch

from test_regression import Fixture
from nsim.dashboard import CLIENT, Dashboard, TerminalDisplay, PAGE_SIZE, page, view, terminal_text
from nsim.layout import simulation_directory
from nsim.planning import create_plan
from nsim.reports import assessed
from nsim.service import run


def live_view(directory):
    data = json.loads((directory/'live.json').read_text())
    data['rows'] = data['recent_cases']
    return data


class DashboardTests(Fixture):
    def state(self):
        tasks = []
        for index, case in enumerate(('fast','slow')):
            tasks.append(dict(id=str(index),case=case,submit_name='base',seed=42,status='running',attempts=[]))
        return dict(id='test-run',terminal=False,passed=False,report_errors=[],tasks=tasks,scheduler_jobs={})

    def test_each_case_is_published_after_analysis_before_batch_finishes(self):
        self.cases.write_text('fast\nslow\n')
        self.data['fake'] = {'durations':{'slow':2}, 'outcomes':{'fast':'fail'}}
        self.save_submit()
        captured = []
        original = Dashboard.publish
        def publish(dashboard, state, lifecycle='running', message=''):
            for task in state['tasks']:
                for attempt in task['attempts']:
                    if attempt.get('cwd'):
                        self.assertTrue((Path(attempt['record_path'])).is_file())
                        self.assertEqual(json.loads(Path(attempt['record_path']).read_text())['task']['attempts'][-1]['id'], attempt['id'])
            if lifecycle == 'complete':
                self.assertTrue((dashboard.journal/'final.json').exists())
            elif lifecycle == 'finalizing':
                self.assertFalse((dashboard.journal/'final.json').exists())
            if original(dashboard, state, lifecycle, message):
                captured.append(live_view(dashboard.journal))
        with patch.object(Dashboard, 'publish', publish):
            record, state = run(self.plan(), [self.out], jobs=2)
        partial = [item for item in captured if item['totals']['completed'] == 1 and item['totals']['running'] == 1]
        self.assertTrue(partial, 'a completed case must be visible while the slower case is still running')
        self.assertEqual(next(row for row in partial[0]['rows'] if row['case']=='fast')['verdict'], 'FAIL')
        self.assertEqual([item['revision'] for item in captured], list(range(1, len(captured)+1)))
        self.assertEqual(captured[-1]['lifecycle'], 'complete')
        self.assertEqual(json.loads((record/'live.json').read_text())['lifecycle'], 'complete')

    def test_tool_exit_waits_for_post_hook_and_analysis_before_case_completion(self):
        self.cases.write_text('fast\n')
        script = self.project/'wait_hook.py'
        script.write_text("from pathlib import Path\nimport time\nPath('hook_started').touch()\nwhile not Path('release').exists(): time.sleep(.01)\n")
        self.descriptor.write_text('schema_version=1\nname="demo"\ninputs=["input.txt", "wait_hook.py"]\n')
        self.data['hooks'] = {'post_sim':{'name':'wait','argv':[sys.executable,'{project}/wait_hook.py'],'timeout_seconds':5}}
        self.save_submit()
        plan = self.plan()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run, plan, [self.out])
            work = simulation_directory(self.out, plan, plan['tasks'][0])
            try:
                deadline = time.monotonic()+5
                while not (work/'hook_started').exists() and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertTrue((work/'result.json').exists())
                self.assertFalse((work/'analysis.json').exists())
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    data = live_view(self.out/'regr_dir'/plan['id']/'report')
                    if data['rows'][0]['verdict'] == 'RUNNING':
                        break
                    time.sleep(.02)
                self.assertEqual(data['totals']['completed'], 0)
                self.assertEqual(data['rows'][0]['verdict'], 'RUNNING')
            finally:
                (work/'release').touch()
            record, state = future.result(timeout=8)
        self.assertTrue(state['passed'])
        self.assertEqual(json.loads((record/'live.json').read_text())['totals']['completed'], 1)

    def test_single_case_and_dry_run_do_not_create_dashboard(self):
        plan = create_plan(self.project,cases=['fast'],submit_paths=[self.submit],seeds=[42])
        record, _ = run(plan,[self.out])
        self.assertFalse(list(record.glob('live*')))
        record, _ = run(self.plan(),[self.out],dry_run=True)
        self.assertFalse(list(record.glob('live*')))

    def test_job_terminal_status_does_not_invent_a_case_verdict(self):
        state = self.state()
        state['scheduler_jobs'] = {'x':dict(job_id='12',phase='simulate',status='DONE',terminal=True)}
        data = view(assessed(state), self.out, 1, 'running', '')
        self.assertEqual(data['totals']['completed'], 0)
        self.assertEqual(data['rows'][0]['verdict'], 'RUNNING')

    def test_interrupt_publishes_stopped_dashboard_without_final(self):
        self.cases.write_text('slow\n')
        self.data['fake'] = {'durations':{'slow':10}}
        self.save_submit()
        process = subprocess.Popen([sys.executable,'-m','nsim','regr','--project',str(self.project),'-sub',str(self.submit),
                                    '-l',str(self.cases),'-o',str(self.out),'-seed','42'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+5
            while not list(self.out.rglob('run.log')) and time.monotonic()<deadline:
                time.sleep(.02)
            process.send_signal(signal.SIGINT)
            _, stderr=process.communicate(timeout=8)
            self.assertEqual(process.returncode,130,stderr)
            data=json.loads(next(self.out.rglob('live.json')).read_text())
            self.assertEqual(data['lifecycle'],'interrupted')
            self.assertFalse(data['terminal'])
            self.assertFalse(list(self.out.rglob('final.json')))
        finally:
            if process.poll() is None:
                process.kill(); process.communicate()

    def test_failed_case_record_persistence_is_reported_without_a_false_pass(self):
        from nsim import service
        original=service.write_json
        def fail_record(path,value):
            if path.name == 'run.json' and value.get('lifecycle') == 'attempt_complete': raise OSError('attempt record unavailable')
            return original(path,value)
        with patch('nsim.service.write_json',fail_record), patch('nsim.runner.write_json',fail_record):
            journal, state = run(self.plan(),[self.out])
        data=json.loads(next(self.out.rglob('live.json')).read_text())
        self.assertEqual(data['lifecycle'],'complete')
        self.assertTrue(data['terminal'])
        self.assertFalse(data['passed'])
        self.assertEqual(data['totals']['failed'],3)
        self.assertTrue((journal/'final.json').exists())
        self.assertTrue(all(t['effective_verdict']=='INFRA_ERROR' for t in state['tasks']))

    def test_dashboard_write_failure_does_not_cancel_cases(self):
        with patch.object(Dashboard, 'publish', side_effect=OSError('display unavailable')) as publish:
            journal, state = run(self.plan(), [self.out], jobs=1)
        self.assertTrue(state['passed'])
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(len(state['tasks']), 3)
        self.assertTrue((journal / 'final.json').exists())
        self.assertEqual(state['display_errors'], ['display unavailable'])

    def test_dashboard_data_cannot_break_out_of_script(self):
        data=view(assessed(self.state()),self.out,1,'running','</script><script>alert(1)</script>')
        html=page(data).decode()
        self.assertNotIn(data['message'],html)
        self.assertIn('\\u003c/script\\u003e',html)
        self.assertNotIn('innerHTML',CLIENT)

    def test_incremental_totals_match_final_assessment_and_shared_compile_is_counted_once(self):
        state = self.state()
        dashboard = Dashboard(self.out)
        dashboard.publish(state)
        compile_report = dict(path='/tmp/compile/log_analysis.json', complete=True,
                              counts=dict(warning=2,error=0,fatal=0), first_failure=None)
        for i, task in enumerate(state['tasks']):
            task.update(status='completed', attempts=[dict(verdict='PASS' if i == 0 else 'FAIL', evidence={},
                compile_log_analysis=compile_report, log_analysis=dict(path=f'/tmp/case_{i}/analysis.json',
                complete=i == 0, counts=dict(warning=0,error=i,fatal=0), first_failure=None))])
            dashboard.update(state, tasks=[task])
        expected = assessed(state, verify=False)
        self.assertEqual(dashboard.log_summary, expected['log_summary'])
        self.assertEqual(dashboard.totals['completed'], 2)
        state['tasks'][0]['evidence_error'] = 'changed'
        state['terminal'] = True
        dashboard.publish(state, 'finalizing')
        self.assertEqual(dashboard.totals['passed'], 0)
        self.assertEqual(dashboard.totals['failed'], 2)
        self.assertEqual(dashboard.log_summary['compile']['warning'], 2)

    def test_idle_has_no_output_no_write_and_no_log_scan(self):
        output = io.StringIO()
        dashboard = Dashboard(self.out, stream=output)
        state = self.state()
        dashboard.publish(state)
        before = output.getvalue(), (self.out/'live.json').read_bytes()
        with patch('nsim.dashboard.write_json', side_effect=AssertionError('idle disk write')), \
             patch('nsim.reports.assessed', side_effect=AssertionError('idle log scan')):
            for _ in range(10):
                self.assertFalse(dashboard.publish(state))
        self.assertEqual(before, (output.getvalue(), (self.out/'live.json').read_bytes()))
        self.assertEqual([p.name for p in self.out.glob('live*')], ['live.json'])

    def test_summary_write_failure_keeps_old_revision_and_terminal_display(self):
        output = io.StringIO()
        dashboard = Dashboard(self.out, stream=output)
        state = self.state()
        dashboard.publish(state)
        old = output.getvalue(), (self.out/'live.json').read_bytes()
        state['tasks'][0].update(status='completed', attempts=[dict(verdict='FAIL', evidence={}, reason='bad')])
        dashboard.update(state, tasks=[state['tasks'][0]])
        with patch('nsim.dashboard.write_json', side_effect=OSError('summary unavailable')):
            with self.assertRaises(OSError): dashboard.publish(state)
        self.assertEqual(dashboard.revision, 1)
        self.assertEqual(old, (output.getvalue(), (self.out/'live.json').read_bytes()))
        self.assertTrue(dashboard.publish(state))
        self.assertEqual(dashboard.revision, 2)
        self.assertIn('fast  base  seed=42  FAIL', output.getvalue())

    def test_ten_thousand_cases_update_incrementally_without_time_throttle(self):
        from nsim import dashboard as module
        output = io.StringIO()
        dashboard = Dashboard(self.out, stream=output)
        state = self.state()
        state['tasks'] = [dict(id=str(i),case=f'case_{i}',submit_name='base',seed=42,status='planned',attempts=[])
                          for i in range(10000)]
        dashboard.publish(state)
        with patch('nsim.dashboard.task_row', wraps=module.task_row) as convert, \
             patch('nsim.reports.copy.deepcopy', side_effect=AssertionError('full state copy')), \
             patch('nsim.dashboard.time.time', return_value=10):
            for task in state['tasks'][200:250]:
                task.update(status='completed',attempts=[dict(verdict='PASS',evidence={},reason='ok')])
                dashboard.update(state,tasks=[task])
                self.assertTrue(dashboard.publish(state))
                self.assertEqual(json.loads((self.out/'live.json').read_text())['totals']['completed'], int(task['id'])-199)
            self.assertEqual(convert.call_count,50)
        data=json.loads((self.out/'live.json').read_text())
        self.assertEqual(data['totals']['pending'],9950)
        self.assertEqual(data['revision'],51)
        self.assertEqual(len(data['recent_cases']),8)
        self.assertNotIn('tasks',data)
        self.assertLess((self.out/'live.json').stat().st_size,10000)
        self.assertEqual([p.name for p in self.out.glob('live*')], ['live.json'])

    def test_tty_panel_is_bounded_and_diagnostic_control_characters_are_removed(self):
        class Tty(io.StringIO):
            def isatty(self): return True
            def fileno(self): return 12345
        output=Tty()
        with patch.dict(os.environ,{'TERM':'xterm'}), \
             patch('nsim.dashboard.os.get_terminal_size',return_value=os.terminal_size((55,12))):
            dashboard=Dashboard(self.out,stream=output)
            state=self.state()
            dashboard.publish(state)
            output.seek(0); output.truncate()
            state['tasks'][0].update(status='completed',attempts=[dict(verdict='FAIL',evidence={},
                reason='\x1b[2J\x1b]52;c;secret\x07\n伪造\r状态\u202e'*10)])
            dashboard.update(state,tasks=[state['tasks'][0]])
            dashboard.publish(state)
        text=output.getvalue()
        self.assertIn('\x1b[J',text)  # redraw only our own panel
        self.assertNotIn('\x1b[2J',text)
        self.assertNotIn('\x1b]52',text)
        self.assertNotIn('\u202e',text)
        self.assertLessEqual(len(text.splitlines()),12)
        for line in text.split('\x1b[J',1)[1].splitlines():
            self.assertEqual(terminal_text(line,54),line)
        self.assertEqual(terminal_text('中文abc',5),'中文a')
        dashboard.close()
        before=output.getvalue()
        dashboard.display.render(json.loads((self.out/'live.json').read_text()))
        self.assertEqual(output.getvalue(),before)

    def test_redirected_terminal_contains_no_ansi_and_closed_pipe_does_not_fail_run(self):
        output=io.StringIO()
        dashboard=Dashboard(self.out,stream=output)
        state=self.state()
        dashboard.publish(state)
        self.assertNotIn('\x1b',output.getvalue())
        output.close()
        state['tasks'][0].update(status='completed',attempts=[dict(verdict='FAIL',evidence={},reason='bad')])
        dashboard.update(state,tasks=[state['tasks'][0]])
        self.assertTrue(dashboard.publish(state))
        self.assertEqual(json.loads((self.out/'live.json').read_text())['totals']['failed'],1)
        self.assertTrue(dashboard.display.closed)

    def test_terminal_phase_never_claims_final_pass_early(self):
        for lifecycle in ('running','finalizing','interrupted','error','unresolved','complete'):
            with self.subTest(lifecycle=lifecycle):
                state=self.state()
                state['terminal']=True
                for task in state['tasks']:
                    task.update(status='completed',attempts=[dict(verdict='PASS',evidence={})])
                output=io.StringIO()
                dashboard=Dashboard(self.out,stream=output)
                dashboard.publish(state,lifecycle)
                data=json.loads((self.out/'live.json').read_text())
                self.assertEqual(data['passed'],lifecycle=='complete')
                if lifecycle!='complete': self.assertIn('尚未最终确认',output.getvalue())
                else: self.assertIn('运行结束 | PASS',output.getvalue())

    def test_cli_pty_updates_before_completion_and_keeps_stdout_json(self):
        self.cases.write_text('fast\nslow\n')
        self.data['fake']={'durations':{'slow':2},'outcomes':{'fast':'fail'}}
        self.save_submit()
        master,slave=os.openpty()
        env=dict(os.environ,TERM='xterm-256color')
        process=subprocess.Popen([sys.executable,'-m','nsim','regr','--project',str(self.project),
            '-sub',str(self.submit),'-l',str(self.cases),'-o',str(self.out),'-seed','42','-jobs','2'],
            stdout=subprocess.PIPE,stderr=slave,env=env,text=True)
        os.close(slave)
        output=b''
        partial=False
        try:
            deadline=time.monotonic()+10
            while process.poll() is None and time.monotonic()<deadline:
                if select.select([master],[],[],.05)[0]:
                    try: output+=os.read(master,65536)
                    except OSError: break
                if not partial and '完成 1/2' in output.decode(errors='replace'):
                    self.assertIsNone(process.poll())
                    self.assertEqual(len(list((self.out/'regr_dir').glob('*/base/rtl/sim_dir/fast_42/run.json'))), 1)
                    partial=True
                # Keep draining the PTY: otherwise its bounded output buffer can
                # block the child before communicate() reads its separate stdout.
            stdout,_=process.communicate(timeout=8)
            while select.select([master],[],[],0)[0]:
                try:
                    chunk=os.read(master,65536)
                    if not chunk: break
                    output+=chunk
                except OSError: break
            self.assertTrue(partial,output.decode(errors='replace'))
            self.assertEqual(process.returncode,1)
            self.assertFalse(json.loads(stdout)['passed'])
            text=output.decode()
            self.assertIn('运行结束 | FAIL',text)
            self.assertIn('\x1b[J',text)
            self.assertNotIn('Dashboard',stdout)
            self.assertFalse(list(self.out.rglob('live.html')))
            self.assertFalse(list(self.out.rglob('live-*.js')))
        finally:
            os.close(master)
            if process.poll() is None: process.kill(); process.communicate()

    def test_static_html_pagination_without_timers_or_file_requests(self):
        node=shutil.which('node')
        if not node: self.skipTest('Node.js required for report pagination test')
        state=self.state()
        state['terminal']=True
        for task in state['tasks']:
            task.update(status='completed',attempts=[dict(verdict='FAIL',reason='<img src=x onerror=alert(1)>',evidence={})])
        data=view(assessed(state),self.out,1,'complete','')
        result=subprocess.run([node,str(Path(__file__).with_name('dashboard_client_test.js'))],
            input=json.dumps(dict(client=CLIENT,complete=data)),text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('static pagination passed',result.stdout)
