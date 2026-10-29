"""Saved reports are read-only with respect to execution and completion records."""

import json
from pathlib import Path
import subprocess
import sys

from test_regression import Fixture
from nsim.layout import simulation_directory
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import run
from nsim.storage import IntegrityError, digest


class ReportCommandTests(Fixture):
    def command(self, *args):
        return subprocess.run([sys.executable, '-m', 'nsim', 'report', *map(str, args)],
                              cwd=self.project, capture_output=True, text=True, timeout=20)

    def regression(self, failing=False):
        if failing:
            self.data['fake'] = {'outcomes': {'smoke': 'fail'}}
            self.save_submit()
        plan = self.plan()
        return run(plan, [self.out])[0]

    def test_single_case_prints_saved_result_without_creating_report(self):
        plan = create_plan(self.project, cases=['smoke'], submit_paths=[self.submit], seeds=[42])
        record, _ = run(plan, [self.out])
        before = digest(record / 'run.json')
        result = self.command(record)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('smoke', result.stdout)
        self.assertIn('PASS', result.stdout)
        self.assertEqual(before, digest(record / 'run.json'))
        self.assertFalse((record / 'report.html').exists())
        self.assertTrue(json.loads(self.command(record, '--json').stdout)['report_complete'])

    def test_complete_failing_regression_still_has_valid_html_report(self):
        record = self.regression(failing=True)
        result = self.command(record.parent)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('回归结果：FAIL', result.stdout)
        self.assertIn((record / 'report.html').as_uri(), result.stdout)
        self.assertTrue(json.loads(self.command(record, '--json').stdout)['report_complete'])

    def test_case_inside_regression_prints_only_that_case(self):
        plan = self.plan()
        run(plan, [self.out])
        case_dir = simulation_directory(self.out, plan, plan['tasks'][0])
        result = self.command(case_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('| smoke |', result.stdout)
        self.assertNotIn('| fast |', result.stdout)
        self.assertNotIn('浏览器打开', result.stdout)

    def test_missing_html_is_brief_and_rebuild_preserves_final_manifest(self):
        record = self.regression()
        final_hash = digest(record / 'final.json')
        html = record / 'report.html'
        html.unlink()
        missing = self.command(record)
        self.assertEqual(missing.returncode, 2, missing.stderr)
        self.assertIn('报告不完整', missing.stdout)
        self.assertNotIn('浏览器打开', missing.stdout)
        rebuilt = self.command(record, '--rebuild')
        self.assertEqual(rebuilt.returncode, 2, rebuilt.stderr)
        self.assertIn('已重建展示报告', rebuilt.stdout)
        self.assertIn('smoke', (record / 'report.rebuilt.html').read_text())
        self.assertFalse(html.exists())
        self.assertEqual(final_hash, digest(record / 'final.json'))
        with self.assertRaises(IntegrityError):
            load_final(record)

    def test_changed_evidence_is_incomplete_and_rebuild_shows_it(self):
        record = self.regression()
        state = json.loads((record / 'state.json').read_text())
        evidence = next(path for path in state['tasks'][0]['attempts'][-1]['evidence']
                        if path.endswith('run.log'))
        Path(evidence).write_text('changed evidence')
        result = self.command(record)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('报告不完整', result.stdout)
        rebuilt = self.command(record, '--rebuild')
        self.assertEqual(rebuilt.returncode, 2, rebuilt.stderr)
        self.assertIn('INCOMPLETE', (record / 'report.rebuilt.html').read_text())

    def test_damaged_control_record_cannot_be_rebuilt(self):
        record = self.regression()
        (record / 'state.json').write_text('{}')
        result = self.command(record, '--rebuild')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('报告不完整', result.stdout)
        self.assertFalse((record / 'report.rebuilt.html').exists())
