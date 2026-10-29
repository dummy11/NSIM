"""VCS/UVM parsing and bounded, stable log consumption."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nsim.log_analysis import brief, case_summary, config, scan, validate_custom, wait_stable
from nsim.error_filter import load as load_filter
from nsim.config import ConfigError
from nsim.storage import digest


class LogAnalysisTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name).resolve()
        self.log = self.work / 'run.log'
        self.settings = config({'files': ['run.log'], 'settle_seconds': .4})
        self.task = {'case': 'test', 'seed': 42}

    def analyze(self, text, **settings):
        self.log.write_text(text)
        return scan(self.work, dict(self.settings, **settings), 'simulate', self.task)[0]

    def test_uvm_details_and_summary_are_not_double_counted(self):
        result = self.analyze('UVM_ERROR(UVM_NONE) tb/checker.sv(27) @ 120 ns: top.env [MISMATCH] expected 5 got 6\n'
                              'UVM_ERROR : 1\nUVM_FATAL : 0\nUVM_WARNING : 0\n')
        self.assertTrue(result['complete'])
        self.assertEqual(result['counts'], {'warning': 0, 'error': 1, 'fatal': 0})
        first = result['first_failure']
        self.assertEqual((first['source_file'], first['source_line'], first['log_line']), ('tb/checker.sv', 27, 1))
        self.assertEqual(first['code'], 'MISMATCH')

    def test_zero_summary_and_info_mentions_do_not_fail(self):
        result = self.analyze('UVM_INFO tb.sv(1) @ 0: top [MSG] expecting UVM_ERROR in another test\n'
                              'There are no errors.\nUVM_ERROR : 0\nUVM_FATAL : 0\n')
        self.assertEqual(sum(result['counts'].values()), 0)
        self.assertIsNone(result['first_failure'])

    def test_positive_summary_without_details_is_failure_without_invented_location(self):
        result = self.analyze('UVM_ERROR : 7\nUVM_FATAL : 1\n')
        self.assertEqual(result['counts']['error'], 7)
        self.assertNotIn('source_file', result['first_failure'])
        self.assertEqual(result['first_failure']['log_line'], 1)

    def test_filter_cannot_waive_unreported_uvm_summary_or_fatal(self):
        rule = {'schema_version': 1, 'rules': [
            {'phase': 'simulate', 'log_file': 'run.log', 'code': 'KNOWN',
             'message': 'reviewed message', 'reason': 'approved exception'}]}
        settings = dict(self.settings, error_filter=load_filter(self.work / 'rules.json', json.dumps(rule).encode()))
        report = self.analyze('UVM_ERROR tb.sv(1) @ 1: top [KNOWN] reviewed message\n'
                              'UVM_ERROR : 2\nUVM_FATAL : 1\n', **settings)
        self.assertEqual(report['counts'], {'warning': 0, 'error': 2, 'fatal': 1})
        self.assertEqual(report['waived_counts'], {'error': 1, 'fatal': 0})
        self.assertEqual(report['blocking_counts'], {'error': 1, 'fatal': 1})
        self.assertEqual(report['waivers'][0]['log_file'], 'run.log')

    def test_filter_does_not_waive_a_different_scanned_log(self):
        rule = {'schema_version': 1, 'rules': [
            {'phase': 'simulate', 'log_file': 'run.log', 'code': 'KNOWN',
             'message': 'reviewed', 'reason': 'approved exception'}]}
        filter_data = load_filter(self.work / 'rules.json', json.dumps(rule).encode())
        self.log.write_text('UVM_ERROR : 0\n')
        (self.work / 'other.log').write_text('Error-[KNOWN] reviewed\n')
        settings = dict(self.settings, files=['run.log', 'other.log'], error_filter=filter_data)
        report, _ = scan(self.work, settings, 'simulate', self.task)
        self.assertEqual(report['blocking_counts']['error'], 1)
        self.assertEqual(report['waived_counts']['error'], 0)

    def test_custom_filter_requires_each_reported_error_to_have_a_matching_diagnostic(self):
        rule = {'schema_version': 1, 'rules': [
            {'phase': 'simulate', 'log_file': 'run.log', 'code': 'KNOWN',
             'message': 'reviewed', 'reason': 'approved exception'}]}
        settings = dict(self.settings, error_filter=load_filter(self.work / 'rules.json', json.dumps(rule).encode()))
        custom = {'complete': True, 'counts': {'warning': 0, 'error': 2, 'fatal': 0},
                  'diagnostics': [{'severity': 'error', 'code': 'KNOWN', 'message': 'reviewed',
                                   'log_file': 'run.log', 'log_line': 1}]}
        self.log.write_text('Error-[KNOWN] reviewed\nError-[OTHER] hidden by analyzer\n')
        report, _ = scan(self.work, settings, 'simulate', self.task, custom=custom)
        self.assertEqual(report['waived_counts']['error'], 1)
        self.assertEqual(report['blocking_counts']['error'], 1)

    def test_vcs_multiline_and_runtime_fatal_locations(self):
        result = self.analyze('Error-[SE] Syntax error\n  "rtl/top.sv", 19: token is bad\n  offending statement\n\n'
                              'Fatal: "tb.sv", 61: assertion failed\n')
        self.assertEqual((result['counts']['error'], result['counts']['fatal']), (1, 1))
        self.assertEqual(result['diagnostics'][0]['source_line'], 19)
        self.assertEqual(result['diagnostics'][1]['source_file'], 'tb.sv')

    def test_warnings_and_ansi_and_no_filename(self):
        result = self.analyze('\x1b[31mUVM_FATAL @ 5: top [STOP] bad\x1b[0m\r\nWarning-[WIDTH] mismatch\n')
        self.assertEqual(result['counts']['fatal'], 1)
        self.assertEqual(result['counts']['warning'], 1)
        self.assertNotIn('source_file', result['first_failure'])

    def test_diagnostic_cap_preserves_total_counts_and_first_failure(self):
        result = self.analyze('Warning-[W] note\n\n' * 20 + 'Error-[E] failed\n', max_diagnostics=2)
        self.assertEqual(len(result['diagnostics']), 2)
        self.assertEqual(result['counts']['warning'], 20)
        self.assertEqual(result['counts']['error'], 1)
        self.assertEqual(result['first_failure']['code'], 'E')
        self.assertEqual(result['diagnostics_truncated'], 19)

    def test_oversized_line_is_streamed_and_following_error_is_found(self):
        self.log.write_bytes(b'x' * (2 * 1024 * 1024) + b'\nError-[E] failure\n')
        result, evidence = scan(self.work, self.settings, 'simulate', self.task)
        self.assertTrue(result['complete'])
        self.assertEqual(result['first_failure']['log_line'], 2)
        self.assertEqual(evidence[str(self.log)], digest(self.log))

    def test_scan_budget_is_explicitly_incomplete(self):
        result = self.analyze('hello\n' * 30, max_scan_bytes=10)
        self.assertFalse(result['complete'])
        self.assertEqual(result['files'][0]['bytes'], 10)

    def test_missing_symlink_and_cancelled_logs_cannot_pass(self):
        result, _ = scan(self.work, self.settings, 'simulate', self.task)
        self.assertFalse(result['complete'])
        target = self.work / 'target'
        target.write_text('safe')
        self.log.symlink_to(target)
        self.assertFalse(scan(self.work, self.settings, 'simulate', self.task)[0]['complete'])
        self.log.unlink()
        self.log.write_text('hello\n')
        cancel = threading.Event(); cancel.set()
        self.assertFalse(scan(self.work, self.settings, 'simulate', self.task, cancel)[0]['complete'])

    def test_delayed_visibility_waits_for_stable_file(self):
        def publish():
            time.sleep(.06)
            self.log.write_text('UVM_ERROR : 0\n')
        writer = threading.Thread(target=publish); writer.start()
        stable, error = wait_stable(self.work, self.settings, 'simulate', threading.Event())
        writer.join()
        self.assertIsNone(error)
        self.assertEqual(stable[str(self.log)][0], self.log.stat().st_size)

    def test_writer_changes_during_scan_prevent_complete_result(self):
        self.log.write_text('first\n')
        original = Path.stat
        calls = [0]
        def changing(path, *args, **kwargs):
            if path == self.log:
                calls[0] += 1
                if calls[0] == 2:
                    with self.log.open('a') as stream:
                        stream.write('Error-[E] appended\n')
            return original(path, *args, **kwargs)
        with patch('pathlib.Path.stat', changing):
            result, evidence = scan(self.work, self.settings, 'simulate', self.task)
        self.assertFalse(result['complete'])
        self.assertNotIn(str(self.log), evidence)

    def test_custom_contract_rejects_false_completion_and_inconsistent_counts(self):
        valid = {'schema_version':1, 'complete':True, 'counts':dict(warning=0,error=0,fatal=0), 'diagnostics':[]}
        for changes in ({'complete':'true'}, {'counts':dict(warning=0,error=True,fatal=0)},
                        {'diagnostics':[{'severity':'error','message':'bad'}]}, {'schema_version':2}):
            with self.assertRaises(ConfigError):
                validate_custom(dict(valid, **changes), 100)

    def test_continually_changing_metadata_has_bounded_wait(self):
        calls = [0]
        def changing(*args):
            calls[0] += 1
            return {str(self.log): (calls[0], 0, 1)}
        started = time.monotonic()
        with patch('nsim.log_analysis.snapshot', changing):
            stable, error = wait_stable(self.work, dict(self.settings, settle_seconds=.12), 'simulate', threading.Event())
        self.assertIsNone(stable)
        self.assertIn('stable', error)
        self.assertLess(time.monotonic() - started, 1)


    def test_summary_drops_diagnostic_context_without_changing_log_or_counts(self):
        result = self.analyze('Error-[SE] Syntax error\n  "rtl/top.sv", 19: bad token\n  offending statement\n')
        original = self.log.read_bytes()
        report = brief(result, self.work/'analysis.json')
        self.assertEqual(report['failure_reason'], 'Syntax error')
        self.assertNotIn('message', report['first_failure'])
        self.assertNotIn('diagnostics', report)
        summary = case_summary(self.task, dict(verdict='FAIL', reason='failed\ncontext', log_analysis=report))
        self.assertEqual(summary['reason'], 'failed')
        self.assertEqual(summary['first_error']['source_line'], 19)
        self.assertEqual(summary['first_error']['phase'], 'simulation')
        self.assertEqual(summary['counts']['simulation']['error'], 1)
        self.assertIsNone(summary['counts']['compile'])
        self.assertEqual(self.log.read_bytes(), original)

    def test_absent_scan_and_missing_error_location_are_not_invented(self):
        summary = case_summary(self.task, dict(verdict='CANCELLED', reason='cancelled'))
        self.assertEqual(summary['counts'], dict(compile=None, simulation=None))
        self.assertEqual(summary['scan_complete'], dict(compile=None, simulation=None))
        self.assertIsNone(summary['first_error'])
        report = dict(complete=False, counts=dict(warning=0,error=3,fatal=0), first_failure=None)
        summary = case_summary(self.task, dict(verdict='FAIL', reason='custom failure', log_analysis=report))
        self.assertFalse(summary['scan_complete']['simulation'])
        self.assertEqual(summary['counts']['simulation']['error'], 3)
        self.assertIsNone(summary['first_error'])
