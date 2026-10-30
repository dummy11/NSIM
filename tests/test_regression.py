"""Local end-to-end regression, evidence and failure recovery contracts."""

import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nsim.checks import evaluate
from nsim.config import ConfigError, discover, load_cases, load_project, load_submit
from nsim.planning import check_identity, create_plan
from nsim.reports import load_final
from nsim.service import replay, run
from nsim.storage import IntegrityError, atomic_write, digest, preflight, write_json


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir()
        self.descriptor = self.project / "nsim.toml"
        self.descriptor.write_text('schema_version = 1\nname = "demo"\ninputs = ["input.txt"]\n')
        (self.project / "input.txt").write_text("stable input\n")
        self.cases = self.project / "smoke.caselist"
        self.cases.write_text("smoke\nfast\nslow\n")
        self.submit = self.project / "submit.json"
        self.data = {"schema_version": 1, "name": "base", "adapter": "fake", "settle_seconds": 0,
                     "required_checks": ["functional", "golden", "performance"]}
        self.save_submit()
        self.out = self.base / "out"
        self.backup = self.base / "backup"
        self.events = self.base / "events"
        for path in (self.out, self.backup):
            path.mkdir()

    def save_submit(self):
        self.data.setdefault('scheduler', {'kind': 'local'})
        write_json(self.submit, self.data)

    def plan(self, **kwargs):
        return create_plan(self.project, caselist=self.cases, submit_paths=[self.submit], seeds=[42], **kwargs)

    def launch(self, **kwargs):
        return run(self.plan(), [self.out], **kwargs)


class ConfigurationTests(Fixture):
    def test_legacy_storage_environment_fallback_and_missing_default_guidance(self):
        args = [sys.executable, '-m', 'nsim', 'run', '--project', str(self.project),
                '--case', 'smoke', '--submit', str(self.submit), '--seed', '42']
        environment = {k: v for k, v in os.environ.items() if k not in ('NSIM_OUTPUT_ROOT', 'NSIM_EVENT_ROOT')}
        missing = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(missing.returncode, 2)
        self.assertIn('nsim update --project', missing.stderr)
        self.assertEqual(list(self.out.iterdir()), [])
        environment.update(NSIM_OUTPUT_ROOT=str(self.out), NSIM_EVENT_ROOT=str(self.events))
        result = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        journal = Path(json.loads(result.stdout)['journal'])
        self.assertEqual(journal, self.out / 'base' / 'rtl' / 'sim_dir' / 'smoke_42')
        self.assertFalse((journal / 'live.json').exists())
        self.assertFalse((journal / 'report.html').exists())
        self.assertFalse((self.out / 'regr_dir').exists())
        self.assertFalse(self.events.exists())
        self.assertEqual(json.loads((journal / 'run.json').read_text())['plan']['policy']['output_roots'], [str(self.out)])

    def test_toml_yaml_and_discovery(self):
        self.assertEqual(load_project(self.project).name, "demo")
        self.assertEqual(len(discover([self.base])), 1)
        self.descriptor.unlink()
        (self.project / "nsim.yaml").write_text('schema_version: 1\nname: demo\ninputs: [input.txt]\n')
        self.assertEqual(load_project(self.project).inputs, [(self.project / "input.txt").resolve()])

    def test_ambiguous_descriptors(self):
        (self.project / "nsim.yaml").write_text('schema_version: 1\nname: demo\n')
        with self.assertRaises(ConfigError):
            load_project(self.project)

    def test_yaml_duplicate_rejected(self):
        self.descriptor.unlink()
        (self.project / "nsim.yaml").write_text('schema_version: 1\nname: a\nname: b\n')
        with self.assertRaises(ConfigError):
            load_project(self.project)

    def test_old_version_requirement_is_rejected(self):
        self.descriptor.write_text('schema_version = 1\nname = "demo"\nrequires_nsim = ">=99"\n')
        with self.assertRaises(ConfigError):
            load_project(self.project)

    def test_project_pin_rejects_wrong_runtime(self):
        (self.project / ".nsim_version").write_text("99.0.0\n")
        with self.assertRaisesRegex(ConfigError, "selected nsim version"):
            self.plan()

    def test_replay_identity_binds_current_python_environment(self):
        plan = self.plan()
        with patch("nsim.planning.sys.executable", str(self.base / "other-python")):
            with self.assertRaisesRegex(ConfigError, "current Python environment"):
                check_identity(plan)

    def test_unknown_fields_boolean_version_and_nan_rejected(self):
        for update in ({"typo": 1}, {"schema_version": True}, {"timeout_seconds": float("nan")}, {"required_checks": []}):
            self.submit.write_text(json.dumps(dict(self.data, **update)))
            with self.assertRaises(ConfigError):
                load_submit(self.submit)

    def test_duplicate_json_keys_rejected(self):
        self.submit.write_text('{"schema_version":1,"name":"x","name":"y","adapter":"fake"}')
        with self.assertRaises(ConfigError):
            load_submit(self.submit)

    def test_caselist_comments_dedup_and_path_traversal(self):
        self.cases.write_text("# empty\n\nsmoke # note\nsmoke\nother\n")
        self.assertEqual(load_cases(self.cases), ["smoke", "other"])
        for value in ("../oops", "two names", "# empty"):
            self.cases.write_text(value)
            with self.assertRaises(ConfigError):
                load_cases(self.cases)

    def test_output_inside_project_rejected(self):
        with self.assertRaises(ConfigError):
            run(self.plan(), [self.project / "output"])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_nested_output_roots_rejected(self):
        with self.assertRaises(ConfigError):
            run(self.plan(), [self.out, self.out / 'nested'])

    def test_dry_run_records_plan_without_starting_process(self):
        plan = self.plan()
        with patch("nsim.runner.subprocess.Popen") as process:
            journal, state = run(plan, [self.out], dry_run=True)
        process.assert_not_called()
        self.assertTrue(state["dry_run"])
        self.assertTrue((journal / "plan.json").exists())
        self.assertFalse((journal / "final.json").exists())


class RegressionTests(Fixture):
    def test_live_state_is_running_and_interrupt_has_no_final(self):
        self.cases.write_text("slow\n")
        self.data["fake"] = {"durations": {"slow": 10}}
        self.save_submit()
        process = subprocess.Popen([sys.executable, "-m", "nsim", "regr",
                                    "--project", str(self.project), "--caselist", str(self.cases),
                                    "--submit", str(self.submit), "--output-root", str(self.out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            live = None
            while time.monotonic() < deadline:
                files = list(self.out.rglob("live.json"))
                if files:
                    live = json.loads(files[0].read_text())
                    if live["totals"]["running"] == 1 and list(self.out.rglob("run.log")):
                        break
                time.sleep(.02)
            self.assertIsNotNone(live)
            self.assertEqual(live["totals"]["running"], 1)
            os.kill(process.pid, signal.SIGINT)
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 130, stderr)
            self.assertEqual(list(self.out.rglob("final.json")), [])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_multi_submit_seed_isolation(self):
        second = self.project / "second.json"
        write_json(second, dict(self.data, name="other"))
        plan = create_plan(self.project, caselist=self.cases, submit_paths=[self.submit, second], seeds=[42, 43])
        journal, state = run(plan, [self.out], jobs=3)
        self.assertTrue(state["passed"], state)
        self.assertEqual(len(state["tasks"]), 12)
        paths = [t["attempts"][-1]["result"] for t in state["tasks"]]
        self.assertEqual(len(set(paths)), 12)
        self.assertEqual(len({t["build_id"] for t in state["tasks"]}), 2)
        self.assertTrue(load_final(journal)[1]["passed"])
        self.assertTrue(json.loads((journal / "live.json").read_text())["terminal"])

    def test_all_failure_classes_are_not_passes(self):
        outcomes = ["pass", "fail", "no_compare", "missing", "malformed", "crash", "performance_fail"]
        self.cases.write_text("\n".join(outcomes))
        self.data["fake"] = {"outcomes": {name: name for name in outcomes}}
        self.save_submit()
        journal, state = self.launch(jobs=3)
        results = {t["case"]: t["effective_verdict"] for t in state["tasks"]}
        self.assertEqual(results, {
            "pass": "PASS", "fail": "FAIL", "no_compare": "NO_COMPARE", "missing": "INCOMPLETE",
            "malformed": "INCOMPLETE", "crash": "CRASH", "performance_fail": "FAIL"})
        self.assertEqual(len(json.loads((journal / "failed.json").read_text())), 6)
        self.assertFalse(state["passed"])

    def test_liveness_policy_is_explicit(self):
        self.data["required_checks"] = ["liveness"]
        self.data["fake"] = {"outcomes": {"smoke": "no_compare"}}
        self.save_submit()
        _, state = self.launch()
        self.assertTrue(state["passed"])
        self.assertEqual(state["tasks"][0]["effective_verdict"], "PASS_LIVE")

    def test_rolling_window_does_not_wait_for_slowest(self):
        self.cases.write_text("slow\nfast\nthird\n")
        self.data["fake"] = {"durations": {"slow": .8, "fast": .02, "third": .02}}
        self.save_submit()
        _, state = self.launch(jobs=2)
        results = {t["case"]: t["attempts"][0] for t in state["tasks"]}
        self.assertLess(results["third"]["started_at"], results["slow"]["ended_at"])
        self.assertGreaterEqual(results["third"]["started_at"], results["fast"]["ended_at"])

    def test_timeout_terminates_child(self):
        self.data["fake"] = {"durations": {"slow": 10}}
        self.data["timeout_seconds"] = .3
        self.save_submit()
        before = time.monotonic()
        _, state = self.launch(jobs=3)
        self.assertLess(time.monotonic() - before, 3)
        slow = next(t for t in state["tasks"] if t["case"] == "slow")
        self.assertEqual(slow["effective_verdict"], "TIMEOUT")
        self.assertIsNotNone(slow["attempts"][0]["exit_code"])

    def test_rerun_preserves_source_seed_and_only_failures(self):
        self.data["fake"] = {"outcomes": {"fast": "fail"}}
        self.save_submit()
        journal, state = self.launch()
        before = {str(p): digest(p) for p in journal.rglob("*") if p.is_file()}
        other, rerun = replay(journal)
        self.assertNotEqual(other, journal)
        self.assertEqual(len(rerun["tasks"]), 1)
        task = rerun["tasks"][0]
        self.assertEqual((task["case"], task["seed"]), ("fast", 42))
        self.assertEqual(task["parent"]["run"], state["id"])
        self.assertEqual(before, {str(p): digest(p) for p in journal.rglob("*") if p.is_file()})

    def test_changed_inputs_are_replanned_for_rerun_but_not_recovery(self):
        self.data["fake"] = {"outcomes": {"fast": "fail"}}
        self.save_submit()
        journal, _ = self.launch()
        (self.project / "input.txt").write_text("changed\n")
        repeated, _ = replay(journal)
        self.assertEqual(load_final(repeated)[0]['input_hashes'][str(self.project / 'input.txt')],
                         digest(self.project / 'input.txt'))
        with self.assertRaises(IntegrityError):
            replay(journal, recovery=True)

    def test_rerun_requires_current_project_runtime(self):
        from nsim import get_version
        pin = self.project / ".nsim_version"
        pin.write_text(get_version() + "\n")
        self.data["fake"] = {"outcomes": {"fast": "fail"}}
        self.save_submit()
        journal, _ = self.launch()
        pin.write_text("99.0.0\n")
        with self.assertRaisesRegex(ConfigError, 'selected nsim version'):
            replay(journal)

    def test_corrupted_result_invalidates_pass_and_can_recover(self):
        journal, state = self.launch()
        original = Path(state["tasks"][0]["attempts"][0]["result"])
        original.write_text("broken")
        self.assertFalse(load_final(journal)[1]["passed"])
        new, recovered = replay(journal, roots=[self.backup], recovery=True)
        self.assertTrue(recovered["passed"])
        self.assertEqual(len(recovered["tasks"]), 1)
        self.assertEqual(original.read_text(), "broken")
        self.assertNotEqual(new, journal)

    def test_damaged_journal_refuses_replay(self):
        journal, _ = self.launch()
        (journal / "state.json").write_text("{}")
        with self.assertRaises(IntegrityError):
            replay(journal)

    def test_malformed_completion_record_is_a_clear_integrity_error(self):
        journal, _ = self.launch()
        (journal / "final.json").write_text("[]")
        with self.assertRaises(IntegrityError):
            load_final(journal)

    def test_nonterminal_journal_refuses_replay(self):
        journal, _ = self.launch(dry_run=True)
        with self.assertRaises(IntegrityError):
            replay(journal)

    def test_cli_executes_from_arbitrary_working_directory(self):
        completed = subprocess.run([sys.executable, "-m", "nsim", "regr",
                                    "--project", str(self.project), "--caselist", str(self.cases),
                                    "--submit", str(self.submit), "--seed", "17", "--jobs", "2",
                                    "--output-root", str(self.out)],
                                   cwd=self.base, capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        self.assertTrue(data["passed"])
        self.assertEqual(len(load_final(Path(data["journal"]))[1]["tasks"]), 3)


class StorageRecoveryTests(Fixture):
    def test_missing_mount_is_not_recreated(self):
        missing = self.base / "missing-mount"
        with self.assertRaisesRegex(OSError, 'storage root missing'):
            run(self.plan(), [missing])
        self.assertFalse(missing.exists())
        self.assertEqual(list(self.out.iterdir()), [])

    def test_space_inode_and_quota_failures(self):
        from types import SimpleNamespace
        stats = SimpleNamespace(f_bavail=100, f_frsize=1, f_favail=1, f_files=100)
        with patch("nsim.storage.os.statvfs", return_value=stats):
            for free_bytes, free_inodes in ((101, 0), (0, 2)):
                with self.assertRaises(OSError) as failure:
                    preflight(self.out, free_bytes, free_inodes)
                self.assertEqual(failure.exception.errno, errno.ENOSPC)
        with patch("nsim.storage.tempfile.mkstemp", side_effect=OSError(errno.EDQUOT, "quota")):
            with self.assertRaises(OSError) as failure:
                preflight(self.out)
        self.assertEqual(failure.exception.errno, errno.EDQUOT)

    def test_os_errors_preserve_errno_without_disk_diagnosis(self):
        for code in (errno.EROFS, errno.EIO, errno.EDQUOT):
            with patch("nsim.runner.preflight", side_effect=OSError(code, "injected")):
                journal, state = self.launch()
            self.assertFalse(state["passed"])
            self.assertEqual(state['tasks'][0]['attempts'][0]['failure']['errno'], code)
            self.assertNotIn('storage_error', state['tasks'][0]['attempts'][0])
            self.assertEqual(len(state['tasks'][0]['attempts']), 1)
            self.assertTrue((journal / "final.json").exists())

    def test_multiple_roots_require_explicit_selection(self):
        with self.assertRaisesRegex(ConfigError, 'select exactly one output root'):
            run(self.plan(), [self.out, self.backup])
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_explicit_recovery_selects_a_new_root(self):
        self.cases.write_text("smoke\n")
        with patch('nsim.runner.preflight', side_effect=OSError(errno.EIO, 'runtime failure')):
            journal, state = run(self.plan(), [self.out])
        self.assertFalse(state["passed"])
        self.assertEqual(len(state["tasks"][0]["attempts"]), 1)
        result, recovered = replay(journal, roots=[self.backup], recovery=True)
        self.assertEqual(result.parent.parent, self.backup / 'regr_dir' / journal.parent.name)
        self.assertTrue(recovered['passed'])

    def test_removed_automatic_retry_options_are_rejected(self):
        with self.assertRaisesRegex(ConfigError, 'automatic infrastructure retries'):
            self.launch(retries=1)
        with self.assertRaisesRegex(ConfigError, 'automatic infrastructure retries'):
            self.launch(retry_seconds=1)

    def test_atomic_write_keeps_old_file_on_replace_failure(self):
        target = self.out / "report.json"
        target.write_bytes(b"old")
        with patch("nsim.storage.os.replace", side_effect=OSError(errno.EIO, "injected")):
            with self.assertRaises(OSError):
                atomic_write(target, b"new")
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(list(self.out.iterdir()), [target])

    def test_one_case_record_write_failure_does_not_stop_other_cases(self):
        from nsim import runner
        original = runner.write_json
        def fail_one_case(path, value):
            if path.name == 'run.json' and path.parent.name == 'smoke_42':
                raise OSError(errno.ENOSPC, 'case record full', str(path))
            return original(path, value)
        with patch('nsim.runner.write_json', side_effect=fail_one_case), \
                patch('nsim.service.write_json', side_effect=fail_one_case):
            journal, state = self.launch(jobs=1)
        self.assertTrue(state['terminal'])
        self.assertFalse(state['passed'])
        self.assertEqual([t['effective_verdict'] for t in state['tasks']],
                         ['INFRA_ERROR', 'PASS', 'PASS'])
        self.assertIn('case record', state['tasks'][0]['attempts'][0]['reason'])
        self.assertEqual(state['tasks'][0]['attempts'][0]['case_record_failure']['errno'], errno.ENOSPC)
        self.assertTrue(state['tasks'][0]['attempts'][0]['case_record_failure']['path'].endswith('run.json'))
        self.assertTrue((journal / 'final.json').exists())
        self.assertEqual(len(load_final(journal)[1]['tasks']), 3)

    def test_regression_state_write_failure_stops_new_dispatch(self):
        from nsim import service
        self.data['fake'] = {'durations': {'smoke': .02, 'fast': .5, 'slow': .02}}
        self.save_submit()
        original = service.write_json
        def fail_checkpoint(path, value):
            if path.name == 'state.json' and any(t['status'] == 'completed' for t in value['tasks']):
                raise OSError(errno.ENOSPC, 'regression journal full', str(path))
            return original(path, value)
        with patch('nsim.service.write_json', side_effect=fail_checkpoint):
            with self.assertRaisesRegex(OSError, 'regression state NOT persisted'):
                self.launch(jobs=2)
        self.assertFalse(list(self.out.rglob('final.json')))
        self.assertEqual(len(list(self.out.rglob('run.log'))), 2)

    def test_final_report_write_failure_never_claims_pass(self):
        from nsim import service
        original = service.atomic_write
        def fail_report(path, data):
            if path.name == "report.md" and self.out in path.parents:
                raise OSError(errno.ENOSPC, "output storage full")
            return original(path, data)
        with patch("nsim.service.atomic_write", side_effect=fail_report):
            journal, state = self.launch()
        self.assertFalse(state["passed"])
        self.assertTrue(state["report_errors"])
        self.assertFalse(load_final(journal)[1]["passed"])
        self.assertEqual(list(self.out.rglob("complete.json")), [])
        result, recovered = replay(journal, roots=[self.backup], recovery=True)
        self.assertIsNone(result)
        self.assertEqual(recovered['tasks'], [])
        from nsim.report_command import report
        summary, code = report(journal, rebuild=True, as_json=True)
        self.assertEqual(code, 0, summary)

    def test_local_launch_error_does_not_block_later_cases(self):
        from nsim import runner
        original = runner.execute_stage
        def launch(request, *args):
            if request['phase'] == 'simulate' and request['task']['case'] == 'smoke':
                raise OSError(errno.EAGAIN, 'process creation failed')
            return original(request, *args)
        with patch('nsim.runner.execute_stage', side_effect=launch):
            journal, state = self.launch(jobs=1)
        self.assertEqual([t['effective_verdict'] for t in state['tasks']],
                         ['INFRA_ERROR', 'PASS', 'PASS'])
        self.assertEqual(state['tasks'][0]['attempts'][0]['failure']['errno'], errno.EAGAIN)
        self.assertEqual(state['quarantined'], {})
        self.assertTrue((journal / 'final.json').exists())

    def test_local_compile_launch_error_does_not_block_later_cases(self):
        from nsim import hooks
        original = hooks.execute_stage
        failed = False
        def launch(request, *args):
            nonlocal failed
            if request['phase'] == 'compile' and not failed:
                failed = True
                raise OSError(errno.EAGAIN, 'compile process creation failed')
            return original(request, *args)
        with patch('nsim.hooks.execute_stage', side_effect=launch):
            _, state = self.launch(jobs=1)
        self.assertEqual([t['effective_verdict'] for t in state['tasks']],
                         ['INFRA_ERROR', 'PASS', 'PASS'])
        self.assertEqual(state['tasks'][0]['attempts'][0]['failure']['operation'], 'compilation')

    def test_local_run_log_error_does_not_block_later_cases(self):
        from nsim import runner
        original = runner.execute_stage
        def fail_log(request, *args):
            if request['phase'] == 'simulate' and request['task']['case'] == 'smoke':
                return dict(exit_code=None, started_at=time.time(), timed_out=False,
                            error='run.log cannot be written', error_errno=errno.EIO)
            return original(request, *args)
        with patch('nsim.runner.execute_stage', side_effect=fail_log):
            _, state = self.launch(jobs=1)
        self.assertEqual([t['effective_verdict'] for t in state['tasks']],
                         ['INFRA_ERROR', 'PASS', 'PASS'])
        self.assertFalse(state['tasks'][0]['attempts'][0]['failure']['run.log_exists'])

    def test_reading_report_detects_deleted_log(self):
        journal, state = self.launch()
        attempt = state["tasks"][0]["attempts"][0]
        log = next(Path(p) for p in attempt["evidence"] if p.endswith("run.log"))
        log.unlink()
        self.assertFalse(load_final(journal)[1]["passed"])


class CommandAdapterTests(Fixture):
    def make_command(self, content):
        script = self.project / "wrapper.py"
        script.write_text(content)
        self.descriptor.write_text('schema_version=1\nname="demo"\ninputs=["input.txt", "wrapper.py"]\n')
        self.data = {"schema_version": 1, "name": "external", "adapter": "command",
                     "argv": [sys.executable, "{project}/wrapper.py", "{case}", "{seed}", "{result}"],
                     "required_checks": ["functional"], "settle_seconds": 0, "timeout_seconds": 2}
        self.save_submit()

    def test_external_wrapper_uses_argv_and_result_contract(self):
        self.make_command('''import json,sys
from pathlib import Path
case,seed,result=sys.argv[1:4]
Path(result).write_text(json.dumps({"schema_version":1,"case":case,"seed":int(seed),"complete":True,
"checks":{"functional":{"executed":True,"passed":True}}}))
print(" ".join(sys.argv[4:]))
''')
        self.data["plusargs"] = ["+literal=$(not-a-shell-command)"]
        self.save_submit()
        _, state = self.launch(jobs=2)
        self.assertTrue(state["passed"])
        log = next(Path(p) for p in state["tasks"][0]["attempts"][0]["evidence"] if p.endswith("run.log"))
        self.assertIn("$(not-a-shell-command)", log.read_text())

    def test_unknown_placeholder_fails_before_execution(self):
        self.make_command("print('not reached')\n")
        self.data["argv"].append("{not_supported}")
        self.save_submit()
        with self.assertRaises(ConfigError):
            self.launch()
        self.assertEqual(list(self.out.iterdir()), [])

    def test_wrapper_source_write_is_blocked_before_mutation(self):
        self.make_command('''import json,sys
from pathlib import Path
Path(__file__).with_name("input.txt").write_text("mutated")
Path(sys.argv[3]).write_text(json.dumps({"schema_version":1,"case":sys.argv[1],"seed":int(sys.argv[2]),
"complete":True,"checks":{"functional":{"executed":True,"passed":True}}}))
''')
        self.cases.write_text("smoke\n")
        _, state = self.launch()
        self.assertEqual(state["tasks"][0]["effective_verdict"], "CRASH")
        self.assertFalse(state["passed"])
        self.assertEqual((self.project / 'input.txt').read_text(), 'stable input\n')
        work = Path(state['tasks'][0]['attempts'][0]['cwd'])
        self.assertFalse((work / 'result.json').exists())


class ResultContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "result.json"
        self.task = {"case": "smoke", "seed": 42}
        self.result = {"schema_version": 1, "case": "smoke", "seed": 42, "complete": True,
                       "checks": {"golden": {"executed": True, "passed": True, "comparisons": 1}}}

    def check(self, returncode=0, settle=0):
        return evaluate(self.path, self.task, ["golden"], returncode, False, settle)

    def test_exit_code_cannot_be_masked_by_success_record(self):
        write_json(self.path, self.result)
        self.assertEqual(self.check(returncode=1)["verdict"], "CRASH")

    def test_identity_and_schema_must_match(self):
        for update in ({"case": "other"}, {"seed": 17}, {"seed": True}, {"schema_version": 2}, {"complete": False}):
            write_json(self.path, dict(self.result, **update))
            self.assertEqual(self.check()["verdict"], "INCOMPLETE")

    def test_zero_comparisons_never_pass(self):
        self.result["checks"]["golden"]["comparisons"] = 0
        write_json(self.path, self.result)
        self.assertEqual(self.check()["verdict"], "NO_COMPARE")

    def test_missing_required_check_never_passes(self):
        self.result["checks"] = {}
        write_json(self.path, self.result)
        self.assertEqual(self.check()["verdict"], "INCOMPLETE")

    def test_delayed_missing_and_truncated_record_can_settle(self):
        for initial in (None, '{"complete":'):
            if initial is not None:
                self.path.write_text(initial)
            elif self.path.exists():
                self.path.unlink()
            timer = threading.Timer(.05, write_json, args=(self.path, self.result))
            timer.start()
            try:
                self.assertEqual(self.check(settle=.5)["verdict"], "PASS")
            finally:
                timer.join()


if __name__ == "__main__":
    unittest.main()
