"""Turn reviewed inputs into an immutable, replayable local run plan."""

import hashlib
from importlib.metadata import version
import os
from pathlib import Path
import secrets
import shutil
import string
import subprocess
import sys
import uuid

from nsim import get_version
from nsim.config import ConfigError, integer, load_cases, load_project, load_submit, name
from nsim.storage import digest, encode, verify_files


def git_identity(root):
    def git(*args):
        try:
            p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
            return p.stdout.strip() if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    return {"commit": git("rev-parse", "HEAD"), "tag": git("describe", "--tags", "--exact-match", "HEAD"),
            "dirty": None if git("rev-parse", "HEAD") is None else bool(git("status", "--porcelain", "--", "."))}


def runtime_identity():
    source = Path(__file__).parent
    return {"version": get_version(), "python": str(Path(sys.executable).absolute()),
            "python_version": sys.version, "files": {str(p): digest(p) for p in sorted(source.glob("*.py"))},
            "executable_hash": digest(Path(sys.executable)),
            "dependencies": {p: version(p) for p in ["PyYAML", "packaging"] + (["tomli"] if sys.version_info < (3, 11) else [])}}


def check_identity(plan):
    verify_files(plan["input_hashes"])
    verify_files(plan["runtime"]["files"])
    if digest(Path(plan["runtime"]["python"])) != plan["runtime"]["executable_hash"]:
        raise ConfigError("Python executable changed; exact replay is unavailable")
    if plan["runtime"]["version"] != get_version():
        raise ConfigError("NSIM version differs from the recorded run")
    for package, expected in plan["runtime"]["dependencies"].items():
        if version(package) != expected:
            raise ConfigError(f"dependency version changed: {package}")


def create_plan(project_path, caselist=None, cases=None, submit_paths=(), seeds=None, allow_missing_tools=False, scheduler_overrides=None):
    # Capture configuration bytes before parsing; reject any read/parse race below.
    candidate = Path(project_path).resolve()
    descriptors = [candidate / n for n in ("nsim.toml", "nsim.yaml") if (candidate / n).is_file()] if candidate.is_dir() else [candidate]
    config_paths = descriptors + [Path(p).resolve() for p in submit_paths]
    if caselist:
        config_paths.append(Path(caselist).resolve())
    config_bytes = {str(p): p.read_bytes() for p in config_paths}
    project = load_project(Path(project_path))
    if caselist:
        caselist = Path(caselist).resolve()
        cases = load_cases(caselist)
    if not cases:
        raise ConfigError("at least one case is required")
    for value in cases:
        name(value, "case")
    seeds = list(dict.fromkeys(seeds or [secrets.randbelow(2147483646) + 1]))
    for seed in seeds:
        integer(seed, "seed", 1, 2147483647)
    inputs = set(project.inputs + [project.descriptor])
    if caselist:
        inputs.add(caselist)
    submits = []
    snapshots = {p: content.decode("utf-8") for p, content in config_bytes.items()}
    for path in submit_paths:
        path = Path(path).resolve()
        submit = load_submit(path)
        inputs.add(path)
        submit["source"] = str(path)
        if submit["adapter"] == "command":
            if not project.inputs:
                raise ConfigError("command adapter requires explicit project inputs for replay verification")
            exe = submit["argv"][0]
            if "{" in exe or "}" in exe:
                raise ConfigError("argv[0] must be a literal executable")
            exe = str((path.parent / exe).resolve()) if "/" in exe else shutil.which(exe)
            if not exe or not Path(exe).is_file():
                raise ConfigError(f"executable unavailable: {submit['argv'][0]}")
            submit["argv"][0] = exe
            inputs.add(Path(exe))
            for arg in submit["argv"] + submit["plusargs"]:
                try:
                    for _, key, spec, conv in string.Formatter().parse(arg):
                        if key is not None and (key not in {"case", "seed", "result", "project", "wave_dir"} or spec or conv):
                            raise ConfigError(f"invalid command placeholder: {arg}")
                except ValueError as exc:
                    raise ConfigError(f"invalid command format: {arg}") from exc
        if scheduler_overrides:
            from nsim.eda_config import scheduler_config
            submit['scheduler'] = scheduler_config(dict(submit['scheduler'], **scheduler_overrides))
        from nsim.eda import prepare
        prepare(submit, project, inputs, allow_missing_tools)
        submits.append(submit)
    if not submits or len({s["name"] for s in submits}) != len(submits):
        raise ConfigError("provide one or more submits with unique names")
    hashes = {str(p): digest(p) for p in sorted(inputs)}
    if any(hashes.get(p) != hashlib.sha256(content).hexdigest() for p, content in config_bytes.items()):
        raise ConfigError("configuration changed during planning; retry with stable files")
    tasks = []
    for index, submit in enumerate(submits):
        # command adapters consume prebuilt inputs; this is an input identity, not a claimed VCS build.
        build_id = hashlib.sha256(encode({"submit": submit, "inputs": hashes})).hexdigest()
        for case in dict.fromkeys(cases):
            for seed in seeds:
                task_id = hashlib.sha256(encode([submit["name"], case, seed])).hexdigest()[:24]
                tasks.append({"id": task_id, "case": case, "seed": seed, "submit": index,
                              "build_id": build_id, "parent": None})
    environment = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TZ") if k in os.environ}
    return {"schema_version": 1, "id": uuid.uuid4().hex, "project": {"name": project.name,
            "root": str(project.root), "descriptor": str(project.descriptor), **git_identity(project.root)},
            "runtime": runtime_identity(), "input_hashes": hashes, "snapshots": snapshots,
            "environment": environment, "submits": submits, "tasks": tasks, "source": None}
