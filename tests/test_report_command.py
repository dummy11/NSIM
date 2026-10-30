"""Saved reports are read-only with respect to execution and completion records."""

import json
from pathlib import Path
import subprocess
import sys

from test_regression import Fixture
from nsim.layout import simulation_directory
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import replay, run
from nsim.storage import IntegrityError, digest, write_json


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
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertIn('汇总报告', rebuilt.stdout)
        rebuilt_dirs = list(record.parent.glob('report_*'))
        self.assertEqual(len(rebuilt_dirs), 1)
        self.assertIn('smoke', (rebuilt_dirs[0] / 'report.html').read_text())
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
        self.assertIn('INCOMPLETE', next(record.parent.glob('report_*/report.html')).read_text())

    def test_damaged_control_record_cannot_be_rebuilt(self):
        record = self.regression()
        (record / 'state.json').write_text('{}')
        result = self.command(record, '--rebuild')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('报告不完整', result.stdout)
        self.assertFalse(list(record.parent.glob('report_*')))

    def test_rebuild_uses_latest_rerun_and_rejects_named_directory_collision(self):
        record = self.regression(failing=True)
        original_hash = digest(record / 'final.json')
        first, _ = replay(record)
        second, _ = replay(record)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent.parent, record.parent)
        result = self.command(record, '--rebuild', '-dir', 'review', '--json')
        self.assertEqual(result.returncode, 1, result.stderr)
        summary = json.loads((record.parent / 'review' / 'summary.json').read_text())
        self.assertEqual(summary['sources']['rerun'], str(second.parent))
        self.assertEqual(summary['sources']['original'], str(record.parent))
        self.assertEqual(digest(record / 'final.json'), original_hash)
        collision = self.command(record, '--rebuild', '-dir', 'review')
        self.assertEqual(collision.returncode, 2)
        self.assertIn('File exists', collision.stderr)

    def test_include_overrides_latest_selection_and_requires_matching_origin(self):
        record = self.regression(failing=True)
        first, _ = replay(record)
        second, _ = replay(record)
        self.assertNotEqual(first, second)
        result = self.command(record, '--rebuild', '--include', first.parent, '--json')
        self.assertEqual(result.returncode, 1, result.stderr)
        path = Path(json.loads(result.stdout)['rebuilt_report_path'])
        summary = json.loads((path.parent / 'summary.json').read_text())
        self.assertEqual(summary['sources']['rerun'], str(first.parent))
        other = self.regression(failing=True)
        other_replay, _ = replay(other)
        wrong = self.command(record, '--rebuild', '--include', other_replay.parent)
        self.assertEqual(wrong.returncode, 2)

    def test_ambiguous_latest_requires_include_before_creating_report(self):
        record = self.regression(failing=True)
        first, _ = replay(record)
        second, _ = replay(record)
        first_state = json.loads((first / 'state.json').read_text())
        second_state = json.loads((second / 'state.json').read_text())
        second_state['ended_at'] = first_state['ended_at']
        write_json(second / 'state.json', second_state)
        final = json.loads((second / 'final.json').read_text())
        final['files']['state.json'] = digest(second / 'state.json')
        write_json(second / 'final.json', final)
        ambiguous = self.command(record, '--rebuild', '-dir', 'chosen_report')
        self.assertEqual(ambiguous.returncode, 2)
        self.assertIn('latest rerun', ambiguous.stderr)
        self.assertFalse((record.parent / 'chosen_report').exists())
        selected = self.command(record, '--rebuild', '-dir', 'chosen_report', '--include', first.parent)
        self.assertEqual(selected.returncode, 1, selected.stderr)
        self.assertTrue((record.parent / 'chosen_report' / 'summary.json').is_file())

    def test_summary_uses_latest_rerun_and_recover_for_their_own_cases(self):
        record = self.regression(failing=True)
        original = json.loads((record / 'state.json').read_text())
        fast = next(task for task in original['tasks'] if task['case'] == 'fast')
        Path(fast['attempts'][-1]['result']).write_text('damaged evidence')
        recovered, recovery_state = replay(record, recovery=True)
        self.assertEqual([task['case'] for task in recovery_state['tasks']], ['fast'])
        self.data['fake'] = {}
        self.save_submit()
        rerun, rerun_state = replay(record)
        self.assertEqual([task['case'] for task in rerun_state['tasks']], ['smoke'])
        rebuilt = self.command(record, '--rebuild', '--json')
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        summary = json.loads(rebuilt.stdout)
        self.assertTrue(summary['passed'])
        manifest = json.loads((Path(summary['rebuilt_report_path']).parent / 'summary.json').read_text())
        self.assertEqual(manifest['sources']['rerun'], str(rerun.parent))
        self.assertEqual(manifest['sources']['recover'], str(recovered.parent))
