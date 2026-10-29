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
from nsim.config import MODES, ConfigError, integer, load_project, load_submit, name
from nsim.identifiers import new_regression_id
from nsim.caselist import argument_groups, load_caselist, resolve_case, simple_case
from nsim.storage import digest, encode, verify_files
from nsim.project_paths import descriptor_path, resolve_root
from nsim import submit_files


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
        submit_files.verify(submit.get('resolved_files', {}))
        for directory, expected in submit.get('vcs', {}).get('input_directories', {}).items():
            if not Path(directory).is_dir():
                raise ConfigError(f'input directory unavailable: {directory}')
            current = sorted(str(p.resolve()) for p in Path(directory).rglob('*')
                             if p.is_file() and p.suffix in ('.v', '.sv', '.vh', '.svh'))
            if current != expected:
                raise ConfigError(f'input directory membership changed: {directory}')
    source = Path(__file__).parent
    # One read per tool file also checks the recorded file set; do not hash the
    # same sources twice within this identity check.
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


def create_plan(project_path, caselist=None, cases=None, submit_paths=(), seeds=None, allow_missing_tools=False, scheduler_overrides=None,
                phase='run', mode=None, directory=None, com_dir='com_dir', sim_dir='sim_dir', repeat=None, plusargs=()):
    from nsim.projects import registry_lock, project_lock
    root = resolve_root(project_path)
    with registry_lock(), project_lock(root):
        return _create_plan(root, caselist, cases, submit_paths, seeds, allow_missing_tools, scheduler_overrides,
                            phase, mode, directory, com_dir, sim_dir, repeat, plusargs)


def _create_plan(project_path, caselist=None, cases=None, submit_paths=(), seeds=None, allow_missing_tools=False, scheduler_overrides=None,
                 phase='run', mode=None, directory=None, com_dir='com_dir', sim_dir='sim_dir', repeat=None, plusargs=()):
    if phase not in ('com', 'sim', 'run', 'regr') or (mode is not None and mode not in MODES):
        raise ConfigError('invalid execution stage or mode')
    if directory is not None:
        name(directory, 'workspace name (-dir)')
    for field, value in (('-com-dir', com_dir), ('-sim-dir', sim_dir)):
        if not isinstance(value, str) or not value or '\0' in value:
            raise ConfigError(f'{field} requires a directory path')
    if phase == 'regr' and (not caselist or cases):
        raise ConfigError('regr requires one or more caselists and does not accept --case')
    if cases and caselist:
        raise ConfigError('choose cases or caselists, not both')
    if repeat is not None:
        if phase != 'regr' or seeds is not None:
            raise ConfigError('-repeat is for regr and cannot be combined with explicit seeds')
        integer(repeat, 'repeat', 1, 2147483647)
    if phase == 'com':
        if cases or caselist or seeds:
            raise ConfigError('com does not take cases or seeds')
        cases, seeds = ['compile'], [1]
    # Capture configuration bytes before parsing; reject any read/parse race below.
    descriptors = [descriptor_path(project_path)]
    config_paths = descriptors + [Path(p).resolve() for p in submit_paths]
    caselists = list(dict.fromkeys(Path(p).resolve() for p in (
        caselist if isinstance(caselist, (list, tuple)) else [caselist] if caselist else [])))
    config_paths.extend(caselists)
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
    case_sources, case_records = {}, {}
    if caselists:
        for path in caselists:
            for record in load_caselist(path, config_bytes):
                case = record['case']
                if str(path) not in case_sources.setdefault(case, []):
                    case_sources[case].append(str(path))
                case_records.setdefault(case, []).append(record)
        cases = list(case_sources)
    if not cases:
        raise ConfigError("at least one case is required")
    for value in cases:
        name(value, "case")
        case_records.setdefault(value, [simple_case(value)])
    seed_source = 'random' if seeds is None else 'explicit'
    seeds = list(dict.fromkeys(seeds or (secrets.SystemRandom().sample(range(1, 2147483648), repeat)
                                       if repeat is not None else [secrets.randbelow(2147483646) + 1])))
    for seed in seeds:
        integer(seed, "seed", 1, 2147483647)
    # v2 registration is snapshotted, not a live execution dependency. Old plans
    # retain their original, stricter descriptor-hash contract.
    inputs = set(project.inputs + ([project.descriptor] if project.schema_version == 1 else []))
    inputs.update(Path(p) for p in config_bytes if Path(p) not in descriptors)
    submits = []
    snapshots = {p: content.decode("utf-8") for p, content in config_bytes.items()}
    compile_inputs = []
    for path in submit_paths:
        path = Path(path).resolve()
        submit_sources = {}
        submit = load_submit(path, submit_sources, project_root=project.root)
        if mode is None:
            mode = submit['mode']
        if submit['mode'] != mode:
            raise ConfigError(f"mode {mode} does not match submit {submit['name']} mode {submit['mode']}; "
                              'run submits with different modes separately')
        for source, content in submit_sources.items():
            if source in config_bytes and config_bytes[source] != content:
                raise ConfigError('configuration changed during planning; retry with stable files')
            config_bytes[source] = content
            snapshots[source] = content.decode('utf-8')
            inputs.add(Path(source))
        submit["source"] = str(path)
        submit['source_chain'] = list(submit_sources)
        submit['resolved_files'] = submit_files.collect(submit['files'], project.root)
        submit_files.bind_submit(submit, project.root)
        local_inputs = set(project.inputs)
        local_inputs.update(submit_files.phase_inputs(submit['resolved_files'], 'compile'))
        runtime_inputs = set()
        if submit["adapter"] == "command":
            if not project.inputs and not submit['files']:
                raise ConfigError("command adapter requires explicit project inputs or submit files for replay verification")
            exe = submit["argv"][0]
            generated = submit.get('compile') and exe.startswith('{build_dir}/') and exe[12:] in submit['compile']['artifacts']
            if not generated:
                if "{" in exe or "}" in exe:
                    raise ConfigError("argv[0] must be literal or a declared {build_dir} artifact")
                exe = str((path.parent / exe).resolve()) if "/" in exe else shutil.which(exe)
                if not exe or not Path(exe).is_file():
                    raise ConfigError(f"executable unavailable: {submit['argv'][0]}")
                submit["argv"][0] = exe
                # A runner is a simulation input when compilation is a separate
                # stage. Without compilation it is the prebuilt product itself.
                (runtime_inputs if submit.get('compile') else local_inputs).add(Path(exe))
            arguments = [(arg, {'case', 'seed', 'result', 'project', 'wave_dir', 'build_dir'})
                         for arg in submit['argv'] + submit['plusargs']]
            if submit.get('compile'):
                from nsim.eda import tool
                submit['compile']['argv'][0] = tool(submit['compile']['argv'][0], path.parent, local_inputs, [], False)
                arguments += [(arg, {'project', 'build_dir'}) for arg in submit['compile']['argv']]
            for arg, allowed in arguments:
                try:
                    for _, key, spec, conv in string.Formatter().parse(arg):
                        if key is not None and (key not in allowed or spec or conv):
                            raise ConfigError(f"invalid command placeholder: {arg}")
                except ValueError as exc:
                    raise ConfigError(f"invalid command format: {arg}") from exc
        if scheduler_overrides:
            from nsim.eda_config import scheduler_config
            submit['scheduler'] = scheduler_config(dict(submit['scheduler'], **scheduler_overrides))
        from nsim.eda import prepare
        from nsim.eda import tool
        hook_inputs = set()
        for stage, hooks in submit['hooks'].items():
            compile_hook = stage.endswith('compile')
            targets = local_inputs if compile_hook else hook_inputs
            allowed = {'project', 'build_dir', 'log_dir', 'analysis', 'exit_code', 'timed_out'}
            if not compile_hook:
                allowed |= {'case', 'seed', 'result', 'wave_dir'}
            for hook in hooks:
                # Executables are literal; scripts and all their dependencies belong in project.inputs.
                hook['argv'][0] = tool(hook['argv'][0], path.parent, targets, [], False)
                for arg in hook['argv'][1:]:
                    try:
                        for _, key, spec, conv in string.Formatter().parse(arg):
                            if key is not None and (key not in allowed or spec or conv):
                                raise ConfigError(f'invalid {stage} hook placeholder: {arg}')
                    except ValueError as exc:
                        raise ConfigError(f'invalid {stage} hook template: {arg}') from exc
        build_inputs = prepare(submit, project, local_inputs, allow_missing_tools)
        inputs.update(local_inputs)
        inputs.update(hook_inputs)
        inputs.update(runtime_inputs)
        for file_phase in ('simulate', 'analysis'):
            inputs.update(submit_files.phase_inputs(submit['resolved_files'], file_phase))
        compile_inputs.append(build_inputs)
        submits.append(submit)
    if not submits or len({s["name"].casefold() for s in submits}) != len(submits):
        raise ConfigError("provide one or more submits with unique names")
    directory = mode if directory is None else directory
    hashes = {str(p): digest(p) for p in sorted(inputs)}
    if any(digest(Path(p)) != hashlib.sha256(content).hexdigest() for p, content in config_bytes.items()):
        raise ConfigError("configuration changed during planning; retry with stable files")
    runtime = runtime_identity()
    tool_identity = {'version': runtime['version'], 'wheel_sha256': runtime['wheel_sha256'],
                     'files': {Path(p).name: value for p, value in runtime['files'].items()}}
    tasks = []
    file_snapshots = {}
    for index, submit in enumerate(submits):
        submit_files.verify(submit['resolved_files'])
        file_snapshots.update(submit_files.snapshots(submit['resolved_files'], hashes))
        submit['file_hashes'] = {file_phase: {str(p): hashes[str(p)] for p in
                                  sorted(submit_files.phase_inputs(submit['resolved_files'], file_phase))}
                                for file_phase in submit_files.PHASES}
        # Build identity deliberately excludes testcase, seed, workspace and runtime-only options.
        compile_config = {k: submit[k] for k in ('name', 'adapter', 'mode', 'compile', 'env')}
        compile_config['files'] = {key: {field: item[field] for field in ('path', 'selections')}
                                   for key, item in submit['resolved_files'].items() if 'compile' in item['phases']}
        compile_config['hooks'] = {key: submit['hooks'][key] for key in ('pre_compile', 'post_compile')}
        compile_config['log_analysis'] = {key: submit['log_analysis'][key] for key in
                                          ('compile_files', 'max_diagnostics', 'max_scan_bytes', 'settle_seconds')}
        filter_data = submit['log_analysis']['error_filter']
        compile_config['log_analysis']['error_filter_rules'] = (
            [{key: value for key, value in rule.items() if key != 'number'}
             for rule in filter_data['rules'] if rule['phase'] == 'compile'] if filter_data else [])
        if submit['adapter'] == 'command' and not submit.get('compile'):
            compile_config['prebuilt_argv'] = submit['argv']
        if submit['adapter'] == 'vcs':
            compile_config['vcs'] = {k: v for k, v in submit['vcs'].items()
                                     if k not in {'run_args', 'result_argv', 'verdi', 'fsdb_file', 'fsdb_plusargs'}}
        submit['build_inputs'] = {str(p): hashes[str(p)] for p in sorted(compile_inputs[index])}
        submit['build_config'] = compile_config
        build_id = hashlib.sha256(encode({'config': compile_config, 'inputs': submit['build_inputs'],
                                         'project_root': str(project.root), 'runtime': tool_identity})).hexdigest()
        for case in dict.fromkeys(cases):
            resolved = [resolve_case(submit_files.bind_tree(record, submit['files'], project.root, 'simulate'), submit,
                                     submit_files.bind_tree(list(plusargs), submit['files'], project.root, 'simulate'))
                        for record in case_records[case]] if phase != 'com' else [{'argv': [], 'origins': {}}]
            if any(argument_groups(value['argv']) != argument_groups(resolved[0]['argv']) for value in resolved[1:]):
                sources = ', '.join(record['source'] for record in case_records[case])
                raise ConfigError(f'conflicting definitions for logical case {case} in submit {submit["name"]}: {sources}')
            for seed in seeds:
                task_id = hashlib.sha256(encode([submit["name"], case, seed])).hexdigest()[:24]
                tasks.append({"id": task_id, "case": case, "seed": seed, "submit": index,
                              "build_id": build_id, "parent": None, 'caselists': case_sources.get(case, []),
                              'case_config': resolved[0]})
    environment = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TZ") if k in os.environ}
    sources = {'verification': {'root': str(project.root), **git_identity(project.root)}}
    if project.rtl_root is not None:
        sources['rtl'] = {'root': str(project.rtl_root), **git_identity(project.rtl_root)}
    storage = {}
    if project.output_roots:
        storage['storage_defaults'] = {'output_roots': [str(p) for p in project.output_roots]}
    regression = bool(caselists or phase == 'regr' or len(tasks) > 1)
    run_id = new_regression_id(caselists) if regression else uuid.uuid4().hex
    return {"schema_version": project.schema_version, "id": run_id,
            'execution': {'phase': phase, 'mode': mode, 'workspace': directory,
                          'com_dir': com_dir, 'sim_dir': sim_dir,
                          'layout': 'isolated-regression-v1' if regression and phase != 'com' else 'single-v1'},
            'plusargs': list(plusargs),
            'seed_selection': {'source': seed_source, 'repeat': repeat, 'values': seeds},
            'caselists': [str(p) for p in caselists],
            'case_definitions': case_records,
            "kind": 'regression' if regression else 'case',
            "project": {"name": project.name,
            "root": str(project.root), "descriptor": str(project.descriptor),
            "selected_nsim": selected_version, 'sources': sources, **storage, **git_identity(project.root)},
            "runtime": runtime, "input_hashes": hashes, "snapshots": snapshots, 'file_snapshots': file_snapshots,
            "environment": environment, "submits": submits, "tasks": tasks, "source": None}
