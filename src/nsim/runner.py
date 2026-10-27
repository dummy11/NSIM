"""Execute one local attempt with isolated subprocesses and bounded lifetime."""

import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import uuid

from nsim.checks import evaluate
from nsim.planning import check_identity
from nsim.storage import digest, error_record, preflight, write_json


def command(plan, task, result, wave_dir):
    submit = plan["submits"][task["submit"]]
    if submit["adapter"] == "fake":
        return [plan["runtime"]["python"], "-m", "nsim.fake", "--case", task["case"], "--seed", str(task["seed"]),
                "--result", str(result), "--outcome", submit["fake"].get("outcomes", {}).get(task["case"], "pass"),
                "--duration", str(submit["fake"].get("durations", {}).get(task["case"], .02)), *submit["plusargs"]]
    values = {"case": task["case"], "seed": task["seed"], "result": str(result),
              "project": plan["project"]["root"], "wave_dir": str(wave_dir)}
    return [arg.format(**values) for arg in submit["argv"] + submit["plusargs"]]


def kill_group(process):
    if getattr(process, "_nsim_group_closed", False):
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    process._nsim_group_closed = True


def execute(plan, task, root, policy, cancel):
    attempt = {"id": uuid.uuid4().hex, "task_id": task["id"], "root": str(root),
               "host": socket.gethostname(), "started_at": None, "ended_at": None,
               "exit_code": None, "pid": None, "verdict": "INFRA_ERROR", "checks": {}, "evidence": {}}
    submit = plan["submits"][task["submit"]]
    process = None
    try:
        attempt["storage"] = preflight(root, policy["min_free_bytes"], policy["min_free_inodes"])
        check_identity(plan)
        base = root / "regressions" / plan["id"]
        for directory in ("config", "build", "logs", "waves", "reports", "tasks"):
            (base / directory).mkdir(parents=True, exist_ok=True)
        work = base / "tasks" / task["id"] / attempt["id"]
        work.mkdir(parents=True, exist_ok=False)
        log_dir = base / "logs" / task["id"] / attempt["id"]
        log_dir.mkdir(parents=True, exist_ok=False)
        wave_dir = base / "waves" / task["id"] / attempt["id"]
        wave_dir.mkdir(parents=True, exist_ok=False)
        result = work / "result.json"
        argv = command(plan, task, result, wave_dir)
        attempt.update(command=argv, cwd=str(work), result=str(result))
        env = dict(plan["environment"], **submit["env"])
        # Never inherit credentials or unspecified environment into the child.
        timed_out = False
        with (log_dir / "run.log").open("wb") as stdout, (log_dir / "launch.log").open("wb") as stderr:
            if cancel.is_set():
                attempt.update(verdict="CANCELLED", reason="controller cancelled")
                return attempt
            process = subprocess.Popen(argv, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            attempt.update(pid=process.pid, started_at=time.time())
            deadline = time.monotonic() + submit["timeout_seconds"]
            while process.poll() is None:
                if cancel.is_set() or time.monotonic() >= deadline:
                    timed_out = not cancel.is_set()
                    kill_group(process)
                    break
                cancel.wait(.02)
            attempt["exit_code"] = process.returncode
            # A wrapper must not leave background writers after it returns.
            kill_group(process)
            stdout.flush()
            stderr.flush()
            os.fsync(stdout.fileno())
            os.fsync(stderr.fileno())
        attempt["ended_at"] = time.time()
        # A successful initial probe does not guarantee the disk stayed healthy.
        attempt["storage_after"] = preflight(root, policy["min_free_bytes"], policy["min_free_inodes"])
        if cancel.is_set():
            attempt.update(verdict="CANCELLED", reason="controller cancelled")
        else:
            attempt.update(evaluate(result, task, submit["required_checks"], process.returncode,
                                    timed_out, submit["settle_seconds"], cancel))
        # Detect input changes during execution before issuing a PASS.
        check_identity(plan)
        evidence = [log_dir / "run.log", log_dir / "launch.log"]
        if result.is_file():
            evidence.append(result)
        attempt["evidence"] = {str(p): digest(p) for p in evidence}
        write_json(work / "attempt.json", attempt)
    except OSError as exc:
        attempt.update(verdict="INFRA_ERROR", reason=str(exc), storage_error=error_record(exc, root))
    except ValueError as exc:
        attempt.update(verdict="INPUT_CHANGED", reason=str(exc))
    finally:
        if process is not None:
            kill_group(process)
        if attempt["ended_at"] is None:
            attempt["ended_at"] = time.time()
    return attempt
