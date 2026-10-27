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
from nsim.project_paths import descriptor_path, resolve_root


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
            "wheel_sha256": os.environ.get("NSIM_WHEEL_SHA256"),
            "dependencies": {p: version(p) for p in ["PyYAML", "packaging"] + (["tomli"] if sys.version_info < (3, 11) else [])}}


def check_identity(plan):
    verify_files(plan["input_hashes"])
    for submit in plan['submits']:
        for directory, expected in submit.get('vcs', {}).get('input_directories', {}).items():
            if not Path(directory).is_dir():
                raise ConfigError(f'input directory unavailable: {directory}')
            current = sorted(str(p.resolve()) for p in Path(directory).rglob('*')
                             if p.is_file() and p.suffix in ('.v', '.sv', '.vh', '.svh'))
            if current != expected:
                raise ConfigError(f'input directory membership changed: {directory}')
    verify_files(plan["runtime"]["files"])
    source = Path(__file__).parent
    current_files = {str(p): digest(p) for p in sorted(source.glob("*.py"))}
    if current_files != plan["runtime"]["files"]:
        raise ConfigError("current NSIM source differs from the recorded run")
    if str(Path(sys.executable).absolute()) != plan["runtime"]["python"]:
        raise ConfigError("current Python environment differs from the recorded run")
    if sys.version != plan["runtime"]["python_version"]:
        raise ConfigError("Python version changed; exact replay is unavailable")
    if digest(Path(plan["runtime"]["python"])) != plan["runtime"]["executable_hash"]:
        raise ConfigError("Python executable changed; exact replay is unavailable")
    if plan["runtime"]["version"] != get_version():
        raise ConfigError("NSIM version differs from the recorded run")
    if plan["runtime"].get("wheel_sha256") != os.environ.get("NSIM_WHEEL_SHA256"):
        raise ConfigError("NSIM distribution differs from the recorded run")
    for package, expected in plan["runtime"]["dependencies"].items():
        if version(package) != expected:
            raise ConfigError(f"dependency version changed: {package}")


def create_plan(project_path, caselist=None, cases=None, submit_paths=(), seeds=None, allow_missing_tools=False, scheduler_overrides=None):
    from nsim.projects import registry_lock, project_lock
    root = resolve_root(project_path)
    with registry_lock(), project_lock(root):
        return _create_plan(root, caselist, cases, submit_paths, seeds, allow_missing_tools, scheduler_overrides)


def _create_plan(project_path, caselist=None, cases=None, submit_paths=(), seeds=None, allow_missing_tools=False, scheduler_overrides=None):
    # Capture configuration bytes before parsing; reject any read/parse race below.
    descriptors = [descriptor_path(project_path)]
    config_paths = descriptors + [Path(p).resolve() for p in submit_paths]
    if caselist:
        config_paths.append(Path(caselist).resolve())
    config_bytes = {str(p): p.read_bytes() for p in config_paths}
    project = load_project(Path(project_path))
    pin = project.descriptor.parent / ".nsim_version"
    selected_version = None
    if pin.is_symlink():
        raise ConfigError(f"{pin}: version pin must be a regular file")
    if project.schema_version == 2 and not pin.is_file():
        raise ConfigError(f'{pin}: schema 2 requires a selected NSIM version')
    if pin.exists():
        content = pin.read_text(encoding="utf-8")
        selected_version = content.strip()
        if content != get_version() + "\n":
            raise ConfigError(f"{pin}: selected nsim version does not match installed {get_version()}")
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
    # v2 registration is snapshotted, not a live execution dependency. Old plans
    # retain their original, stricter descriptor-hash contract.
    inputs = set(project.inputs + ([project.descriptor] if project.schema_version == 1 else []))
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
    if any(digest(Path(p)) != hashlib.sha256(content).hexdigest() for p, content in config_bytes.items()):
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
    sources = {'verification': {'root': str(project.root), **git_identity(project.root)}}
    if project.rtl_root is not None:
        sources['rtl'] = {'root': str(project.rtl_root), **git_identity(project.rtl_root)}
    return {"schema_version": project.schema_version, "id": uuid.uuid4().hex, "project": {"name": project.name,
            "root": str(project.root), "descriptor": str(project.descriptor),
            "selected_nsim": selected_version, 'sources': sources, **git_identity(project.root)},
            "runtime": runtime_identity(), "input_hashes": hashes, "snapshots": snapshots,
            "environment": environment, "submits": submits, "tasks": tasks, "source": None}
