"""A versioned result contract, with fail-closed verdicts."""

from pathlib import Path
import time

from nsim.config import ConfigError, fields, integer, read_json


ACCEPTED = {"PASS", "PASS_LIVE"}


def parse_result(path: Path, task):
    result = read_json(path)
    fields(result, {"schema_version", "case", "seed", "complete", "checks"},
           {"schema_version", "case", "seed", "complete", "checks"}, "result")
    if integer(result["schema_version"], "result.schema_version") != 1:
        raise ConfigError("unsupported result schema")
    if result["case"] != task["case"] or type(result["seed"]) is not int or result["seed"] != task["seed"]:
        raise ConfigError("result identity does not match case/seed")
    if result["complete"] is not True:
        raise ConfigError("result is not complete")
    if not isinstance(result["checks"], dict):
        raise ConfigError("checks must be an object")
    for name, check in result["checks"].items():
        fields(check, {"executed", "passed", "comparisons", "message"}, {"executed", "passed"}, name)
        if type(check["executed"]) is not bool or type(check["passed"]) is not bool:
            raise ConfigError("check executed/passed must be booleans")
        if "comparisons" in check:
            integer(check["comparisons"], "comparisons")
        if "message" in check and not isinstance(check["message"], str):
            raise ConfigError("check message must be a string")
    return result


def evaluate(path: Path, task, required, returncode, timeout, settle_seconds, cancel=None):
    if timeout:
        return {"verdict": "TIMEOUT", "reason": "process deadline exceeded", "checks": {}}
    if returncode != 0:
        return {"verdict": "CRASH", "reason": f"process exit {returncode}", "checks": {}}
    deadline = time.monotonic() + settle_seconds
    while True:
        try:
            result = parse_result(path, task)
            break
        except (FileNotFoundError, ConfigError) as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (cancel and cancel.is_set()):
                return {"verdict": "INCOMPLETE", "reason": str(exc), "checks": {}}
            time.sleep(min(.05, remaining))
    checks = result["checks"]
    if any(name not in checks for name in required):
        return {"verdict": "INCOMPLETE", "reason": "required check missing", "checks": checks}
    if any(not checks[name]["executed"] for name in required):
        return {"verdict": "NO_COMPARE", "reason": "required check did not execute", "checks": checks}
    if "golden" in required and checks["golden"].get("comparisons", 0) == 0:
        return {"verdict": "NO_COMPARE", "reason": "no golden comparisons", "checks": checks}
    if any(not checks[name]["passed"] for name in required):
        return {"verdict": "FAIL", "reason": "required check failed", "checks": checks}
    return {"verdict": "PASS_LIVE" if required == ["liveness"] else "PASS",
            "reason": "all required checks passed", "checks": checks}
