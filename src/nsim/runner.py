"""Execute one local attempt with isolated subprocesses and bounded lifetime."""

import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import uuid

from nsim.checks import ACCEPTED, evaluate
from nsim.case_records import make_record
from nsim.hooks import execute_stage, kill_group, spec
from nsim.layout import make_directory
from nsim.lsf import LsfError, run_process
from nsim.planning import check_identity
from nsim.runtime import module_command
from nsim.storage import digest, preflight, write_json


def command(plan, task, result, wave_dir, build=None):
    submit = plan["submits"][task["submit"]]
    values = {"case": task["case"], "seed": task["seed"], "result": str(result),
              "project": plan["project"]["root"], "wave_dir": str(wave_dir), 'build_dir': str(build)}
    if submit["adapter"] == "fake":
        return module_command(plan["runtime"]["python"], "nsim.fake", "--case", task["case"], "--seed", str(task["seed"]),
                "--result", str(result), "--outcome", submit["fake"].get("outcomes", {}).get(task["case"], "pass"),
                "--duration", str(submit["fake"].get("durations", {}).get(task["case"], .02)),
                *[arg.format(**values) for arg in task.get('case_config', {}).get('argv', submit['plusargs'])])
    args = ([submit['argv'][0], *task['case_config']['argv']] if 'case_config' in task
            else submit['argv'] + submit['plusargs'])
    return [arg.format(**values) for arg in args]



def execute(plan, task, root, policy, cancel, directories, builds, previous=()):
    attempt = {"id": uuid.uuid4().hex, "task_id": task["id"], "root": str(root),
               "host": socket.gethostname(), "started_at": None, "ended_at": None,
               "exit_code": None, "pid": None, "verdict": "INFRA_ERROR", "checks": {}, "evidence": {}}
    attempt.update(retry_index=len(previous), parent=previous[-1]['id'] if previous else task['parent'])
    submit = plan["submits"][task["submit"]]
    work = None
    operation = 'output_preflight'
    try:
        if cancel.is_set():
            attempt.update(verdict='CANCELLED', reason='controller cancelled')
            return attempt
        attempt["storage"] = preflight(root, policy["min_free_bytes"], policy["min_free_inodes"])
        check_identity(plan)
        operation = 'compilation'
        build, manifest = builds.ensure(root, task, cancel, policy)
        build_evidence = {str(build / 'build.json'): digest(build / 'build.json'),
                          **manifest.get('scheduler_evidence', {}),
                          **manifest.get('stage_evidence', {}),
                          **{str(build / p): h for p, h in manifest['artifacts'].items()}}
        attempt.update(build_dir=str(build), build_id=manifest['build_id'], build_instance=manifest['id'],
                       evidence=build_evidence, compile_log_analysis=manifest.get('log_analysis'))
        if plan.get('execution', {}).get('phase') == 'com':
            attempt.update(verdict='PASS' if manifest['ready'] else 'COMPILE_FAIL',
                           reason='compilation ready' if manifest['ready'] else manifest.get('reason', 'compilation failed'),
                           checks={'compile_products': 'PASS' if manifest['ready'] else 'FAIL'}, exit_code=manifest['exit_code'],
                           started_at=manifest['started_at'])
            if (build / 'compile.log').is_file():
                attempt['evidence'][str(build / 'compile.log')] = digest(build / 'compile.log')
            return attempt
        operation = 'simulation_setup'
        work = directories.prepare(root, task)
        log_dir = work
        wave_dir = make_directory(work / 'waves', work)
        result = work / "result.json"
        argv = command(plan, task, result, wave_dir, build)
        attempt.update(command=argv, cwd=str(work), result=str(result))
        run_record = work / 'run.json'
        attempt['record_path'] = str(run_record)
        if not run_record.exists():
            operation = 'case_record_write'
            write_json(run_record, make_record(plan, dict(task, attempts=[*previous, attempt]), run_record))
        if not manifest['ready']:
            attempt.update(verdict='COMPILE_FAIL', reason=manifest.get('reason', 'compilation did not complete'))
            return attempt
        # Never inherit credentials or unspecified environment into the child.
        timed_out = False
        operation = 'simulation'
        if submit['scheduler']['kind'] == 'lsf':
            stage = run_process(plan, task, root, policy, argv, work, log_dir / 'run.log',
                                 log_dir / 'run.log', submit['timeout_seconds'], cancel,
                                 'simulate', build=build, on_event=builds.on_event)
            attempt.update(exit_code=stage['exit_code'], host=stage.get('host', stage['job'].get('host')),
                           started_at=stage.get('started_at'), scheduler=stage['job'])
            timed_out = stage['timed_out']
        else:
            request = spec(plan, task, 'simulate', build, work, argv, work / 'run.log',
                           work / 'run.log', submit['timeout_seconds'])
            stage = execute_stage(request, cancel, (directories.descriptor(root, task), builds.descriptor(root, task)))
            attempt.update(exit_code=stage['exit_code'], started_at=stage['started_at'], pid=stage.get('pid'))
            timed_out = stage['timed_out']
        attempt.update(log_analysis=stage.get('log_analysis'), hooks=stage.get('hooks', []),
                       read_only_environment=stage.get('read_only_environment'))
        if stage.get('error'):
            raise OSError(stage.get('error_errno'), stage['error'])
        attempt["ended_at"] = time.time()
        operation = 'output_postflight'
        attempt["storage_after"] = preflight(root, policy["min_free_bytes"], policy["min_free_inodes"])
        if cancel.is_set():
            attempt.update(verdict="CANCELLED", reason="controller cancelled")
        elif stage.get('hook_failed'):
            attempt.update(verdict='HOOK_FAIL', reason=stage['hook_reason'])
        else:
            operation = 'result_evaluation'
            attempt.update(evaluate(result, task, submit["required_checks"], attempt['exit_code'],
                                    timed_out, submit["settle_seconds"], cancel))
        analysis = attempt.get('log_analysis')
        if analysis:
            errors = analysis['counts']['error'] + analysis['counts']['fatal']
            if errors:
                attempt['result_reason'] = attempt['reason']
                attempt['reason'] += ' | log: ' + analysis['failure_reason']
                if attempt['verdict'] not in {'TIMEOUT', 'CRASH', 'CANCELLED', 'HOOK_FAIL'}:
                    attempt['verdict'] = 'FAIL'
            elif not analysis['complete'] and attempt['verdict'] in ACCEPTED:
                attempt.update(verdict='INCOMPLETE', reason=analysis['failure_reason'])
        elif attempt['verdict'] in ACCEPTED:
            attempt.update(verdict='INCOMPLETE', reason='log analysis unavailable')
        check_identity(plan)
        builds.validate(build, task)
        evidence = [log_dir / "run.log"]
        if attempt['verdict'] not in ACCEPTED:
            evidence = [path for path in evidence if path.is_file()]
        if result.is_file():
            evidence.append(result)
        # The stage already hashed its stable logs. Only hash additional results.
        attempt["evidence"] = {**build_evidence, **stage['evidence'],
                               **{str(p): digest(p) for p in evidence if str(p) not in stage['evidence']}}
    except LsfError as exc:
        attempt.update(verdict='CANCELLED' if exc.cancelled else 'SCHEDULER_ERROR' if exc.terminal else 'LSF_UNKNOWN',
                       reason=str(exc), terminal=exc.terminal, scheduler_record=exc.record)
        attempt['evidence'].update(exc.evidence)
        try:
            if exc.record and Path(exc.record).is_file():
                attempt['evidence'][exc.record] = digest(Path(exc.record))
        except OSError as record_error:
            attempt['scheduler_record_error'] = str(record_error)
    except OSError as exc:
        failure = {'operation': operation, 'errno': exc.errno, 'path': exc.filename,
                   'output_root': str(root), 'message': str(exc)}
        if work is not None:
            failure['run_log_path'] = str(work / 'run.log')
            failure['result_path'] = str(work / 'result.json')
            for label in ('run.log', 'result.json'):
                try:
                    failure[label + '_exists'] = (work / label).is_file()
                except OSError:
                    failure[label + '_exists'] = None
        attempt.update(verdict='INFRA_ERROR', reason=str(exc), failure=failure)
    except ValueError as exc:
        attempt.update(verdict="INPUT_CHANGED", reason=str(exc))
    finally:
        if attempt["ended_at"] is None:
            attempt["ended_at"] = time.time()
        if work is not None:
            try:
                path = work / 'run.json'
                value = make_record(plan, dict(task, status='completed', attempts=[*previous, attempt]),
                                    path, lifecycle='attempt_complete')
                attempt['evidence'][str(path)] = write_json(path, value)
            except OSError as exc:
                attempt.pop('record_path', None)
                attempt['case_record_error'] = str(exc)
                attempt['case_record_failure'] = {'errno': exc.errno, 'path': exc.filename or str(path),
                                                  'message': str(exc)}
                if attempt.get('terminal') is not False:
                    attempt['verdict'] = 'INFRA_ERROR'
                attempt['reason'] = (attempt.get('reason', '') + ' | case record write failed: ' + str(exc)).strip(' |')
    return attempt
