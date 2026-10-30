"""Ordered hook contracts and one analyzer for individual and batch execution."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from test_regression import Fixture
from nsim.config import ConfigError, load_submit
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import run
from nsim.storage import write_json

SCRIPT = '''import json, sys, time
from pathlib import Path
mode, *args = sys.argv[1:]
def mark(text):
    with Path('order.txt').open('a') as output:
        output.write(text + '\\n')
if mode == 'compile':
    mark('compile')
    Path('model').write_text('compiled')
    if args and args[0] == 'bad':
        print('Error-[SE] bad syntax\\n "rtl/top.sv", 33: bad token')
elif mode == 'sim':
    case, seed, result = args
    mark('simulate')
    print('UVM_ERROR tb/checker.sv(27) @ 42: top.env [BAD] mismatch' if case in ('bad', 'no_compare', 'incomplete') else 'UVM_WARNING : 1' if case == 'warn' else 'UVM_ERROR : 0', flush=True)
    if case == 'many':
        for i in range(250):
            print('Error-[E] mismatch ' + str(i) + ' ' + 'x' * 1000 + '\\n  detailed context ' + str(i) + '\\n')
    if case == 'slow':
        time.sleep(2)
    checks = {} if case == 'incomplete' else {'functional':{'executed':case != 'no_compare','passed':True}}
    Path(result).write_text(json.dumps({'schema_version':1,'case':case,'seed':int(seed),'complete':True,
                                      'checks':checks}))
elif mode == 'hook':
    mark(args[0])
    if args[0] == 'fail':
        sys.exit(3)
    if args[0] == 'sleep':
        time.sleep(2)
    if args[0] == 'append':
        with Path('run.log').open('a') as log:
            log.write('UVM_FATAL @ 1: top [POST] hook-added failure\\n')
elif mode == 'analysis':
    path, outcome = args
    mark('custom_analysis')
    if outcome == 'missing':
        sys.exit(0)
    if outcome == 'mutate':
        with Path('run.log').open('a') as log:
            log.write('Error-[LATE] writer still active\\n')
    counts = {'warning':0,'error':1 if outcome == 'fail' else 0,'fatal':0}
    diagnostics = [{'severity':'error','message':'custom failure','source_file':'custom.sv','source_line':99}] if outcome == 'fail' else []
    Path(path).write_text(json.dumps({'schema_version':1,'complete':True,'counts':counts,'diagnostics':diagnostics}))
'''


class HookTests(Fixture):
    def setUp(self):
        super().setUp()
        (self.project / 'workflow.py').write_text(SCRIPT)
        self.descriptor.write_text('schema_version=1\nname="demo"\ninputs=["workflow.py"]\n')
        self.data.update(adapter='command', required_checks=['functional'],
                         compile={'argv':[sys.executable, '{project}/workflow.py', 'compile'], 'artifacts':['model']},
                         argv=[sys.executable, '{project}/workflow.py', 'sim', '{case}', '{seed}', '{result}'])
        self.save_submit()

    def hook(self, ident, mode='hook', *args, timeout=10):
        return dict(name=ident, argv=[sys.executable, '{project}/workflow.py', mode, *(args or (ident,))], timeout_seconds=timeout)

    def one(self, case='good', phase='run', **kwargs):
        return create_plan(self.project, cases=[case] if phase != 'com' else None,
                           submit_paths=[self.submit], seeds=[42] if phase != 'com' else None, phase=phase, **kwargs)

    def filter(self, *rules):
        path = self.project / 'error_filter.json'
        write_json(path, {'schema_version': 1, 'rules': list(rules)})
        self.data['log_analysis'] = {'error_filter': 'error_filter.json'}
        self.save_submit()
        return path

    def test_error_filter_waives_exact_sim_error_and_keeps_raw_evidence(self):
        self.filter({'phase': 'simulate', 'log_file': 'run.log', 'code': 'BAD',
                     'message': 'mismatch', 'reason': 'reviewed test diagnostic'})
        record, state = run(self.one('bad'), [self.out])
        self.assertTrue(state['passed'], state)
        summary = json.loads((record / 'run.json').read_text())['analysis']
        self.assertEqual(summary['result_verdict'], 'PASS')
        self.assertEqual(summary['counts']['simulation']['error'], 1)
        self.assertEqual(summary['blocking_counts']['simulation']['error'], 0)
        self.assertEqual(summary['waived_counts']['simulation']['error'], 1)
        self.assertEqual(summary['waivers']['simulation'][0]['rule'], 1)
        self.assertIn('UVM_ERROR', (record / 'run.log').read_text())
        self.assertEqual(load_final(record)[1]['tasks'][0]['effective_verdict'], 'PASS')
        from nsim.dashboard import task_row
        from nsim.reports import markdown
        self.assertEqual(task_row(state['tasks'][0], record)['waived_errors'], 1)
        self.assertIn('| 1 |', markdown(state))
        for case, expected in [('no_compare', 'NO_COMPARE'), ('incomplete', 'INCOMPLETE')]:
            with self.subTest(case=case):
                record, state = run(self.one(case), [self.out])
                summary = json.loads((record / 'run.json').read_text())['analysis']
                self.assertEqual(state['tasks'][0]['effective_verdict'], expected)
                self.assertEqual(summary['result_verdict'], expected)
                self.assertEqual(summary['waived_counts']['simulation']['error'], 1)

    def test_unmatched_error_and_missing_checks_preserve_their_verdicts(self):
        self.filter({'phase': 'simulate', 'code': 'BAD', 'message': 'different text',
                     'reason': 'unmatched'})
        for case, expected in [('bad', 'FAIL'), ('no_compare', 'NO_COMPARE'), ('incomplete', 'INCOMPLETE')]:
            with self.subTest(case=case):
                record, state = run(self.one(case), [self.out])
                summary = json.loads((record / 'run.json').read_text())['analysis']
                self.assertEqual(state['tasks'][0]['effective_verdict'], expected)
                self.assertEqual(summary['result_verdict'], 'PASS' if case == 'bad' else expected)
                self.assertEqual(summary['blocking_counts']['simulation']['error'], 1)

    def test_compile_filter_affects_readiness_and_build_identity(self):
        path = self.filter({'phase': 'compile', 'log_file': 'compile.log', 'code': 'SE',
                            'message': 'bad syntax', 'reason': 'reviewed tool output'})
        self.data['compile']['argv'].append('bad')
        self.save_submit()
        first = self.one('good')
        build_id = first['tasks'][0]['build_id']
        record, state = run(first, [self.out])
        self.assertTrue(state['passed'], state)
        self.assertEqual(state['tasks'][0]['attempts'][0]['compile_log_analysis']['counts']['error'], 1)
        self.assertEqual(state['tasks'][0]['attempts'][0]['compile_log_analysis']['blocking_counts']['error'], 0)
        compile_rule = {'phase': 'compile', 'log_file': 'compile.log', 'code': 'SE',
                        'message': 'bad syntax', 'reason': 'reviewed tool output'}
        write_json(path, {'schema_version': 1, 'rules': [
            {'phase': 'simulate', 'code': 'BAD', 'message': 'mismatch', 'reason': 'sim only'},
            compile_rule]})
        self.assertEqual(build_id, self.one('good')['tasks'][0]['build_id'])
        write_json(path, {'schema_version': 1, 'rules': []})
        self.assertNotEqual(build_id, self.one('good')['tasks'][0]['build_id'])
        from nsim.planning import check_identity
        with self.assertRaises(Exception):
            check_identity(first)

    def test_all_stages_single_or_multiple_hooks_execute_in_declared_order(self):
        self.data['hooks'] = {'pre_compile':[self.hook('c1'),self.hook('c2')],
                              'post_compile':self.hook('c3'), 'pre_sim':self.hook('s1'),
                              'post_sim':[self.hook('s2'),self.hook('s3')]}
        self.save_submit()
        record, state = run(self.one(), [self.out])
        self.assertTrue(state['passed'], state)
        build = self.out / 'base/rtl/com_dir'
        self.assertEqual((build/'order.txt').read_text().splitlines(), ['c1','c2','compile','c3'])
        self.assertEqual((record/'order.txt').read_text().splitlines(), ['s1','simulate','s2','s3'])
        hooks = json.loads((record/'hooks.json').read_text())['hooks']
        self.assertEqual([h['name'] for h in hooks], ['s1','s2','s3','log_analysis'])
        self.assertEqual(hooks[-1]['kind'], 'builtin')
        self.assertEqual(json.loads((record/'run.json').read_text())['analysis']['verdict'], 'PASS')
        self.assertFalse((record/'log_analysis.json').exists())
        self.assertFalse((record/'.nsim-log-analysis.json').exists())

        self.assertEqual(json.loads((record/'run.json').read_text())['analysis']['scan_complete'], {'compile': True, 'simulation': True})

    def test_single_and_regression_agree_and_report_error_location(self):
        _, single = run(self.one('bad', directory='single'), [self.out])
        self.cases.write_text('bad\nwarn\ngood\n')
        plan = create_plan(self.project, caselist=self.cases, submit_paths=[self.submit], seeds=[42], phase='regr')
        record, batch = run(plan, [self.out], jobs=2)
        self.assertEqual(single['tasks'][0]['effective_verdict'], 'FAIL')
        self.assertEqual([t['effective_verdict'] for t in batch['tasks']], ['FAIL','PASS','PASS'])
        self.assertEqual(batch['log_summary']['simulation'], {'warning':1,'error':1,'fatal':0})
        self.assertIn('tb/checker.sv:27', (record/'report.md').read_text())
        self.assertIn('mismatch', (record/'report.html').read_text())
        for task in batch['tasks']:
            self.assertTrue((Path(task['attempts'][0]['cwd'])/'run.json').is_file())

    def test_all_post_hooks_run_before_builtin_analysis(self):
        self.data['hooks'] = {'post_sim':[self.hook('append'), self.hook('after')]}
        self.save_submit()
        record, state = run(self.one(), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'FAIL')
        self.assertEqual((record/'order.txt').read_text().splitlines(), ['simulate','append','after'])
        self.assertEqual(json.loads((record/'run.json').read_text())['analysis']['counts']['simulation']['fatal'], 1)

    def test_custom_log_analysis_replaces_builtin_and_is_last(self):
        self.data['hooks'] = {'post_sim':[self.hook('before'), self.hook('log_analysis','analysis','{analysis}','pass')]}
        self.save_submit()
        record, state = run(self.one('bad'), [self.out])
        self.assertTrue(state['passed'], state)
        self.assertEqual((record/'order.txt').read_text().splitlines(), ['simulate','before','custom_analysis'])
        report = state['tasks'][0]['attempts'][0]['log_analysis']
        self.assertEqual(report['analyzer'], 'custom')
        self.assertFalse((record/'log_analysis.json').exists())
        self.assertFalse((record/'.nsim-log-analysis.json').exists())
        self.assertEqual(report['counts']['error'], 0)
        self.assertEqual([h['kind'] for h in json.loads((record/'hooks.json').read_text())['hooks']], ['command','command'])

    def test_custom_analyzer_failure_and_missing_output_cannot_pass(self):
        for outcome, verdict in [('fail','FAIL'),('missing','HOOK_FAIL'),('mutate','INCOMPLETE')]:
            self.data['hooks'] = {'post_sim':self.hook('log_analysis','analysis','{analysis}',outcome)}
            self.save_submit()
            _, state = run(self.one(directory=outcome), [self.out])
            self.assertEqual(state['tasks'][0]['effective_verdict'], verdict, state)

    def test_custom_analyzer_must_be_last_without_reordering(self):
        for hooks in [{'post_sim':[self.hook('log_analysis'),self.hook('after')]},
                      {'pre_sim':self.hook('log_analysis')},
                      {'pre_sim':[self.hook('same'),self.hook('same')]}]:
            self.data['hooks'] = hooks
            self.save_submit()
            with self.assertRaises(ConfigError):
                load_submit(self.submit)

    def test_pre_hook_failure_skips_tool_but_runs_post_hooks(self):
        self.data['hooks'] = {'pre_sim':[self.hook('fail'),self.hook('never')], 'post_sim':self.hook('cleanup')}
        self.save_submit()
        record, state = run(self.one(), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'HOOK_FAIL')
        self.assertEqual((record/'order.txt').read_text().splitlines(), ['fail','cleanup'])
        self.assertFalse((record/'result.json').exists())

    def test_post_hook_failure_does_not_skip_remaining_hooks_or_analysis(self):
        self.data['hooks'] = {'post_sim':[self.hook('fail'),self.hook('cleanup')]}
        self.save_submit()
        record, state = run(self.one(), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'HOOK_FAIL')
        self.assertEqual((record/'order.txt').read_text().splitlines(), ['simulate','fail','cleanup'])
        self.assertTrue((record/'run.json').exists())

    def test_sim_timeout_still_runs_post_hook_and_analysis(self):
        self.data['timeout_seconds'] = .1
        self.data['hooks'] = {'post_sim': self.hook('cleanup','hook','timeout={timed_out}')}
        self.save_submit()
        record, state = run(self.one('slow'), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'TIMEOUT')
        self.assertIn('timeout=1', (record/'order.txt').read_text())
        self.assertTrue((record/'run.json').exists())

    def test_hook_timeout_is_bounded_and_reported(self):
        self.data['hooks'] = {'post_sim':self.hook('sleep', timeout=.05)}
        self.save_submit()
        _, state = run(self.one(), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'HOOK_FAIL')
        self.assertTrue(state['tasks'][0]['attempts'][0]['hooks'][0]['timed_out'])

    def test_compile_log_error_blocks_sim_and_each_case_has_reason(self):
        self.data['compile']['argv'].append('bad')
        self.save_submit()
        self.cases.write_text('a\nb\n')
        plan = create_plan(self.project, caselist=self.cases, submit_paths=[self.submit], seeds=[42], phase='regr')
        _, state = run(plan, [self.out], jobs=2)
        self.assertEqual([t['effective_verdict'] for t in state['tasks']], ['COMPILE_FAIL'] * 2)
        self.assertEqual(state['log_summary']['compile']['error'], 1)
        for task in state['tasks']:
            case = json.loads((Path(task['attempts'][0]['cwd'])/'run.json').read_text())['analysis']
            self.assertEqual(case['first_error']['source_line'], 33)
            self.assertEqual(case['first_error']['phase'], 'compile')
            self.assertIsNone(case['counts']['simulation'])
            self.assertIsNone(case['scan_complete']['simulation'])
            self.assertFalse((Path(task['attempts'][0]['cwd'])/'result.json').exists())

    def test_compile_hook_changes_invalidate_build_but_sim_hook_changes_do_not(self):
        run(self.one(phase='com'), [self.out])
        self.data['hooks'] = {'post_sim':self.hook('after')}
        self.save_submit()
        self.assertTrue(run(self.one(phase='sim'), [self.out])[1]['passed'])
        self.data['hooks']['pre_compile'] = self.hook('new')
        self.save_submit()
        with self.assertRaisesRegex(ConfigError, 'incompatible'):
            run(self.one(phase='sim'), [self.out])

    def test_changed_analysis_evidence_invalidates_pass(self):
        record, state = run(self.one(), [self.out])
        self.assertTrue(state['passed'])
        value = json.loads((record/'run.json').read_text())
        value['analysis'] = {}
        write_json(record/'run.json', value)
        self.assertEqual(load_final(record)[1]['tasks'][0]['effective_verdict'], 'INCOMPLETE')

    def test_compile_pre_hook_failure_is_compile_failure_not_storage_failure(self):
        self.data['hooks'] = {'pre_compile':self.hook('fail'), 'post_compile':self.hook('cleanup')}
        self.save_submit()
        record, state = run(self.one(phase='com'), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'COMPILE_FAIL')
        self.assertEqual((record/'order.txt').read_text().splitlines(), ['fail','cleanup'])

    def test_unstable_logs_do_not_launch_custom_analyzer(self):
        self.data['hooks'] = {'post_sim':self.hook('log_analysis','analysis','{analysis}','pass')}
        self.save_submit()
        from nsim import hooks
        original = hooks.wait_stable
        def unstable(work, settings, phase, cancel):
            return (None, 'writer still active') if phase == 'simulate' else original(work, settings, phase, cancel)
        with patch('nsim.hooks.wait_stable', unstable):
            record, state = run(self.one(), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'INCOMPLETE')
        self.assertNotIn('custom_analysis', (record/'order.txt').read_text())

    def test_live_reporting_does_not_rehash_completed_logs(self):
        from nsim import reports
        from nsim.storage import IntegrityError
        record, state = run(self.one(), [self.out])
        with patch('nsim.reports.verify_files', side_effect=AssertionError('live views should use snapshots')):
            reports.html_report(state, verify=False)
        with patch('nsim.reports.verify_files', side_effect=IntegrityError('damaged')):
            invalid = reports.assessed(state)
        self.assertFalse(invalid['passed'])
        self.assertIn('INCOMPLETE', reports.markdown(invalid, verify=False))

    def test_builtin_retries_a_log_change_without_repeating_post_hooks(self):
        from nsim import hooks
        real_scan = hooks.scan
        count = [0]
        phases = []
        def scan_then_append(work, settings, phase, task, *args):
            phases.append(phase)
            result = real_scan(work, settings, phase, task, *args)
            if phase == 'simulate' and count[0] == 0:
                count[0] += 1
                with (Path(work)/'run.log').open('a') as log:
                    log.write('Error-[LATE] delayed failure\n')
            return result
        self.data['hooks'] = {'post_sim':self.hook('once')}
        self.save_submit()
        with patch('nsim.hooks.scan', scan_then_append):
            record, state = run(self.one(), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'FAIL')
        self.assertEqual((record/'order.txt').read_text().splitlines(), ['simulate','once'])
        self.assertEqual(phases.count('simulate'), 2)
        self.assertTrue(json.loads((record/'run.json').read_text())['analysis']['scan_complete']['simulation'])

    def test_lsf_hooks_and_analyzer_run_in_existing_jobs_on_worker(self):
        farm = self.base / 'farm'; farm.mkdir()
        write_json(farm/'state.json', {'jobs':{}, 'events':[], 'peak':0})
        stub = Path(__file__).parent / 'lsf_stub.py'
        scheduler = {'kind':'lsf','poll_seconds':.02,'unknown_seconds':3,'cancel_seconds':3,'deadline_seconds':20}
        for tool in ('bsub','bjobs','bkill'):
            path = farm / tool
            path.write_text('#!' + sys.executable + '\nimport runpy,sys\nsys.argv = ' +
                            repr([str(stub), tool, str(farm)]) + ' + sys.argv[1:]\nrunpy.run_path(' + repr(str(stub)) + ', run_name="__main__")\n')
            path.chmod(0o755); scheduler[tool] = str(path)
        self.data['scheduler'] = scheduler
        self.data['hooks'] = {'pre_compile':self.hook('before_compile'), 'post_compile':self.hook('after_compile'),
                              'pre_sim':self.hook('before_sim'), 'post_sim':self.hook('after_sim')}
        self.save_submit()
        record, state = run(self.one('bad'), [self.out])
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'FAIL', state)
        farm_state = json.loads((farm/'state.json').read_text())
        self.assertEqual(len(farm_state['jobs']), 2)  # compile + sim only, no extra hook/analysis jobs
        saved = json.loads((record/'run.json').read_text())
        analysis = saved['analysis']
        worker = saved['lsf']['worker']
        self.assertFalse((record / '.lsf-jobs').exists())
        self.assertNotIn('simulation_analysis', worker)
        self.assertNotIn('diagnostics', worker['log_analysis'])
        self.assertNotIn('message', worker['log_analysis']['first_failure'])
        self.assertEqual(analysis['counts']['simulation'], worker['log_analysis']['counts'])
        self.assertEqual(analysis['first_error']['log_line'], worker['log_analysis']['first_failure']['log_line'])
        self.assertEqual((record/'order.txt').read_text().splitlines(), ['before_sim','simulate','after_sim'])
        # The same LSF transport also carries a custom analyzer's diagnostics;
        # neither the temporary protocol nor an old simulation sidecar remains.
        self.data['hooks']['post_sim'] = [self.hook('after_sim'),
                                         self.hook('log_analysis', 'analysis', '{analysis}', 'pass')]
        self.save_submit()
        record, state = run(self.one('bad'), [self.out])
        self.assertTrue(state['passed'], state)
        self.assertTrue(load_final(record)[1]['passed'])
        merged = json.loads((record/'run.json').read_text())['analysis']
        self.assertEqual(merged['schema_version'], 3)
        self.assertEqual(state['tasks'][0]['attempts'][0]['log_analysis']['analyzer'], 'custom')
        self.assertFalse((record/'log_analysis.json').exists())
        self.assertFalse((record/'.nsim-log-analysis.json').exists())

        # Built-in filtering is evaluated on the worker with the planned rules.
        self.data['hooks']['post_sim'] = self.hook('after_sim')
        self.filter({'phase': 'simulate', 'log_file': 'run.log', 'code': 'BAD',
                     'message': 'mismatch', 'reason': 'reviewed test diagnostic'})
        record, state = run(self.one('bad'), [self.out])
        self.assertTrue(state['passed'], state)
        summary = json.loads((record / 'run.json').read_text())['analysis']
        self.assertEqual(summary['counts']['simulation']['error'], 1)
        self.assertEqual(summary['waived_counts']['simulation']['error'], 1)


    def test_case_summary_stays_small_with_many_multiline_errors_and_raw_evidence(self):
        record, state = run(self.one('many'), [self.out])
        summary = json.loads((record/'run.json').read_text())['analysis']
        self.assertEqual(set(summary), {'schema_version', 'case', 'seed', 'verdict', 'reason',
                                        'first_error', 'first_blocking', 'counts', 'scan_complete', 'result_verdict',
                                        'result_reason', 'blocking_counts', 'waived_counts',
                                        'waivers', 'error_filter_sha256'})
        self.assertEqual(summary['schema_version'], 3)
        self.assertEqual(summary['verdict'], 'FAIL')
        self.assertEqual(summary['counts']['simulation'], dict(warning=0, error=250, fatal=0))
        self.assertTrue(summary['scan_complete']['simulation'])
        self.assertEqual(summary['first_error']['log_line'], 2)
        self.assertEqual(set(summary['first_error']), {'phase', 'log_file', 'log_line'})
        self.assertLessEqual(len(summary['reason']), 512)
        self.assertNotIn('\n', summary['reason'])
        self.assertLess(len(json.dumps(summary).encode()), 2000)
        self.assertIn('detailed context 249', (record/'run.log').read_text())
        self.assertGreater((record/'run.log').stat().st_size, 250000)
        attempt = state['tasks'][0]['attempts'][0]
        self.assertIn(str(record/'run.log'), attempt['evidence'])
        self.assertNotIn('diagnostics', attempt['log_analysis'])

    def test_summary_only_run_still_invalidates_pass_when_original_log_changes(self):
        record, state = run(self.one(), [self.out])
        self.assertTrue(state['passed'])
        self.assertIsNone(json.loads((record/'run.json').read_text())['analysis']['first_error'])
        with (record/'run.log').open('a') as log:
            log.write('Error-[LATE] changed after completion\n')
        self.assertEqual(load_final(record)[1]['tasks'][0]['effective_verdict'], 'INCOMPLETE')
