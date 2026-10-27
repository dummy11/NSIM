"""Registered projects, explicit edits and recoverable multi-file publication."""
import base64
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import json
import os
import re
from pathlib import Path
import subprocess

from packaging.version import InvalidVersion, Version

from nsim.config import Project, load_project, name
from nsim.project_paths import (ProjectError, descriptor_path, find_root, load_registry,
                               marker, read_pin, registry_path, resolve_root)
from nsim.storage import atomic_write, encode

SIM_HEADER = '# NSIM managed source map v1; compiled filelist is frozen per run.\n'
TB_TEMPLATE = '# Verification sources, relative to the verification root.\n'
EDIT_FIELDS = ('rtl_root', 'rtl_filelist', 'verification_filelist')


def installation_root():
    return Path(os.environ.get('NSIM_HOME') or '~/.local/share/nsim').expanduser().resolve()


def installed(version):
    directory = installation_root() / 'versions' / version
    receipt = directory / 'receipt.json'
    if not receipt.is_file():
        raise ProjectError(f'nsim {version} is not installed; run nsim install {version}')
    try:
        data = json.loads(receipt.read_text())
    except ValueError as exc:
        raise ProjectError(f'invalid installation receipt: {receipt}') from exc
    executable = directory / 'venv' / 'bin' / 'nsim'
    if not isinstance(data, dict) or data.get('version') != version or not re.fullmatch(r'[a-f0-9]{64}', str(data.get('wheel_sha256', ''))) or not executable.is_file():
        raise ProjectError(f'incomplete nsim installation: {directory}')
    return executable


def select_version(requested=None, schema=2):
    requested = requested or 'latest'
    if requested == 'latest':
        directory = installation_root() / 'versions'
        versions = []
        for entry in directory.iterdir() if directory.is_dir() else []:
            try:
                parsed = Version(entry.name)
                if not parsed.is_prerelease and not parsed.is_devrelease:
                    versions.append((parsed, entry.name))
            except InvalidVersion:
                continue
        failures = []
        for _, value in sorted(versions, reverse=True):
            try:
                return select_version(value, schema)
            except (ProjectError, OSError, ValueError) as exc:
                failures.append(str(exc))
        raise ProjectError('no installed stable version supports this project; run nsim install VERSION' +
                           (': ' + '; '.join(failures) if failures else ''))
    try:
        Version(requested)
    except InvalidVersion as exc:
        raise ProjectError(f'invalid nsim version: {requested}') from exc
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?', requested):
        raise ProjectError('invalid nsim version')
    executable = installed(requested)
    try:
        probe = subprocess.run([str(executable), 'capabilities', '--json'], capture_output=True, text=True, timeout=10)
        data = json.loads(probe.stdout) if probe.returncode == 0 else {}
    except (subprocess.TimeoutExpired, ValueError) as exc:
        raise ProjectError(f'nsim {requested} does not support project management') from exc
    if (not isinstance(data, dict) or data.get('version') != requested or
            not isinstance(data.get('project_schemas'), list) or schema not in data['project_schemas']):
        raise ProjectError(f'nsim {requested} does not support project schema {schema}')
    return requested


def check_destination(path):
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise ProjectError(f'refusing symlink or relocated managed path: {path}')


def read_transaction(path):
    try:
        transaction = json.loads(path.read_text())
        if (not isinstance(transaction, dict) or transaction.get('schema_version') != 1 or
                not isinstance(transaction.get('root'), str) or not Path(transaction['root']).is_absolute() or
                not isinstance(transaction.get('files'), list) or not isinstance(transaction.get('directories'), list)):
            raise ValueError('invalid transaction structure')
        root = Path(transaction['root'])
        seen = set()
        for item in transaction['files']:
            if not isinstance(item, dict) or set(item) != {'path', 'before', 'after'}:
                raise ValueError('invalid transaction file')
            if not isinstance(item['path'], str) or not Path(item['path']).is_absolute():
                raise ValueError('invalid transaction path')
            destination = Path(item['path'])
            if destination in seen or (destination != registry_path() and root not in destination.parents):
                raise ValueError('duplicate or unrelated transaction path')
            seen.add(destination)
            for key in ('before', 'after'):
                if item[key] is None and key == 'before':
                    continue
                if not isinstance(item[key], str):
                    raise ValueError('invalid transaction content')
                base64.b64decode(item[key], validate=True)
        for directory in transaction['directories']:
            if not isinstance(directory, str) or not Path(directory).is_absolute():
                raise ValueError('invalid transaction directory')
            target = Path(directory)
            if target != root and root not in target.parents and target not in root.parents:
                raise ValueError('unrelated transaction directory')
        return transaction
    except (ValueError, TypeError) as exc:
        raise ProjectError(f'invalid recovery record {path}: {exc}; no recovery writes performed') from exc


def _restore(transaction):
    # Never undo an unrelated edit made after an interrupted transaction.
    for item in transaction['files']:
        path = Path(item['path'])
        check_destination(path)
        current = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        old = base64.b64decode(item['before']) if item['before'] is not None else None
        new = base64.b64decode(item['after'])
        if path.is_symlink() or (path.exists() and not path.is_file()) or current not in (old, new):
            raise ProjectError(f'cannot recover transaction after an external edit: {path}')
    for directory in transaction['directories']:
        check_destination(Path(directory))
    for item in reversed(transaction['files']):
        path = Path(item['path'])
        if item['before'] is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, base64.b64decode(item['before']))
    for directory in reversed(transaction['directories']):
        path = Path(directory)
        if path.exists():
            try:
                path.rmdir()
            except OSError:
                pass  # Preserve files not created by this transaction.


@contextmanager
def registry_lock(write=False):
    folder = registry_path().parent
    if not folder.exists() and not write:
        yield
        return
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'projects.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        pending = folder / 'projects.transaction.json'
        if pending.exists():
            if not write:
                raise ProjectError('interrupted project update; rerun bootstrap/update to recover before querying')
            transaction = read_transaction(pending)
            root = Path(transaction['root'])
            with project_lock(root):
                _restore(transaction)
            pending.unlink()
        yield


@contextmanager
def project_lock(root):
    # flock the nearest existing directory, without creating a lock inside user sources.
    directory = root
    while not directory.exists():
        directory = directory.parent
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def publish(root, files, directories=()):
    needed = set()
    for directory in [*directories, *(p.parent for p in files)]:
        while not directory.exists():
            needed.add(directory)
            directory = directory.parent
        if not directory.is_dir():
            raise ProjectError(f'not a directory: {directory}')
    created = sorted(needed, key=lambda p: len(p.parts))
    transaction = {'schema_version': 1, 'root': str(root), 'directories': [str(p) for p in created], 'files': []}
    for path, content in files.items():
        check_destination(path)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ProjectError(f'refusing to replace non-regular file: {path}')
        old = path.read_bytes() if path.exists() else None
        transaction['files'].append({'path': str(path),
            'before': base64.b64encode(old).decode() if old is not None else None,
            'after': base64.b64encode(content).decode()})
    pending = registry_path().parent / 'projects.transaction.json'
    atomic_write(pending, encode(transaction))
    try:
        for directory in created:
            directory.mkdir()
        for path, content in files.items():
            atomic_write(path, content)
    except BaseException:
        _restore(transaction)
        pending.unlink()
        raise
    pending.unlink()


def validate_project(project, missing_tb=None):
    if not project.rtl_root.is_dir():
        raise ProjectError(f'rtl_root is not a directory: {project.rtl_root}')
    for path in [project.rtl_filelist, project.verification_filelist, *project.inputs]:
        if path == missing_tb and not path.exists():
            continue
        if not path.is_file():
            raise ProjectError(f'project input is not a file: {path}')
        with path.open('rb') as stream:
            stream.read(1)


def config_bytes(project):
    def relative(path):
        return os.path.relpath(path, project.root)
    values = {'schema_version': 2, 'name': project.name,
              **{key: relative(getattr(project, key)) for key in EDIT_FIELDS},
              'inputs': [relative(p) for p in project.inputs]}
    return ('\n'.join(f'{k} = {json.dumps(v, ensure_ascii=False)}' for k, v in values.items()) + '\n').encode()


def sim_bytes(project):
    # An NSIM source map, not a raw VCS -f: each list has its own resolution root.
    return (SIM_HEADER + '# Edit source lists, not this generated map.\n' +
            '# RTL root: ' + str(project.rtl_root) + '\n' +
            '# RTL filelist: ' + str(project.rtl_filelist) + '\n' +
            '# Verification root: ' + str(project.root) + '\n' +
            '# Verification filelist: ' + str(project.verification_filelist) + '\n').encode()


def check_map(path):
    if path.is_symlink() or (path.exists() and (not path.is_file() or not path.read_text().startswith(SIM_HEADER))):
        raise ProjectError(f'refusing to overwrite user file: {path}')


def check_registration(registry, root, project_name):
    same = None
    for entry in registry['projects']:
        if entry['name'] == project_name and entry['root'] != str(root):
            raise ProjectError(f"project name {project_name} already registered at {entry['root']}")
        if entry['root'] == str(root):
            if entry['name'] != project_name:
                raise ProjectError(f"directory already registered as {entry['name']}: {root}")
            same = entry
    return same


def bootstrap(options):
    with registry_lock(write=True):
        if options.verification_root is not None:
            root = Path(options.verification_root).expanduser().resolve()
        else:
            root = find_root() or (Path.cwd() / (options.name or 'verification')).resolve()
        if options.name is not None and options.name != root.name:
            raise ProjectError('--name must match verification directory name; use --verification-root for another project')
        project_name = name(root.name, 'project name')
        parent = find_root(root.parent)
        if parent is not None:
            raise ProjectError(f'cannot create a nested project inside {parent}')
        registry = load_registry()
        entry = check_registration(registry, root, project_name)
        with project_lock(root):
            if marker(root):
                project = load_project(root, validate=False)
                if entry and entry['descriptor'] != str(project.descriptor):
                    raise ProjectError('registration does not match project configuration')
                if project.schema_version != 2:
                    raise ProjectError('legacy project: migrate explicitly to .nsim schema 2 before registering')
                validate_project(project)
                for key in EDIT_FIELDS:
                    value = getattr(options, key)
                    if value is not None and Path(value).expanduser().resolve() != getattr(project, key):
                        raise ProjectError('configuration differs; register existing configuration first, then use nsim update')
                pin = read_pin(root)
                if options.nsim_version is not None and select_version(options.nsim_version) != pin:
                    raise ProjectError('version differs; use nsim switch or nsim update --nsim-version')
                select_version(pin)
                if entry:
                    return {'action': 'already_registered', 'project': project_info(root, registry)}
                files, directories = {}, []
            else:
                if entry:
                    raise ProjectError('registered project configuration is missing; restore it before bootstrap')
                if options.rtl_root is None or options.rtl_filelist is None:
                    raise ProjectError('new projects require --rtl-root and --rtl-filelist')
                pin = select_version(options.nsim_version)
                tb = Path(options.verification_filelist).expanduser().resolve() if options.verification_filelist else root / 'filelist' / 'tb.f'
                project = Project(root / '.nsim' / 'nsim.toml', project_name, [], root, 2,
                                  Path(options.rtl_root).expanduser().resolve(),
                                  Path(options.rtl_filelist).expanduser().resolve(), tb)
                validate_project(project, missing_tb=tb if options.verification_filelist is None else None)
                files = {project.descriptor: config_bytes(project), root / '.nsim' / '.nsim_version': (pin + '\n').encode()}
                if options.verification_filelist is None and not tb.exists():
                    files[tb] = TB_TEMPLATE.encode()
                check_map(root / 'filelist' / 'sim.f')
                files[root / 'filelist' / 'sim.f'] = sim_bytes(project)
                directories = [root / 'submit', root / 'filelist']
                for directory in directories:
                    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                        raise ProjectError(f'expected a real directory: {directory}')
            registry['projects'].append({'name': project.name, 'root': str(root), 'descriptor': str(project.descriptor)})
            files[registry_path()] = encode(registry)
            publish(root, files, directories)
            return {'action': 'registered', 'project': project_info(root, registry)}


def update(options):
    if not any(getattr(options, key) is not None for key in (*EDIT_FIELDS, 'nsim_version')):
        raise ProjectError('provide --rtl-root, --rtl-filelist, --verification-filelist or --nsim-version; no changes made')
    with registry_lock(write=True):
        root = resolve_root(options.project)
        registry = load_registry()
        with project_lock(root):
            project = load_project(root, validate=False)
            if project.schema_version != 2:
                raise ProjectError('legacy project: migrate explicitly to .nsim schema 2 before update')
            entry = check_registration(registry, root, project.name)
            if entry is None:
                raise ProjectError('project is not registered; run nsim bootstrap first')
            if entry['descriptor'] != str(project.descriptor):
                raise ProjectError('registration does not match project configuration')
            before = project_info(root, registry)
            changes = {key: Path(getattr(options, key)).expanduser().resolve() for key in EDIT_FIELDS if getattr(options, key) is not None}
            revised = replace(project, **changes)
            validate_project(revised)
            files = {}
            if changes:
                if project.descriptor.suffix != '.toml':
                    raise ProjectError('schema 2 updates require nsim.toml')
                files[project.descriptor] = config_bytes(revised)
                check_map(root / 'filelist' / 'sim.f')
                files[root / 'filelist' / 'sim.f'] = sim_bytes(revised)
            pin = root / '.nsim' / '.nsim_version'
            if options.nsim_version is not None:
                selected = select_version(options.nsim_version)
                files[pin] = (selected + '\n').encode()
            else:
                read_pin(root)
            publish(root, files)
            return {'action': 'updated', 'before': before, 'project': project_info(root, registry)}


def project_info(root, registry=None):
    registry = registry or load_registry()
    entries = [e for e in registry['projects'] if e['root'] == str(root)]
    result = {'name': entries[0]['name'] if entries else root.name, 'verification_root': str(root),
              'registered': bool(entries), 'issues': []}
    issues = result['issues']
    try:
        path = descriptor_path(root)
        result['config'] = str(path)
        project = load_project(path, validate=False)
        result['name'] = project.name
        result['schema_version'] = project.schema_version
        if entries and (entries[0]['name'] != project.name or entries[0]['descriptor'] != str(path)):
            issues.append('registration does not match project configuration')
        if project.schema_version == 2:
            result.update({key: str(getattr(project, key)) for key in EDIT_FIELDS})
            try:
                validate_project(project)
            except (OSError, ValueError) as exc:
                issues.append(str(exc))
            if project.verification_filelist.is_file():
                lines = project.verification_filelist.read_text().splitlines()
                if not any(line.split('//', 1)[0].split('#', 1)[0].strip() for line in lines):
                    issues.append('verification filelist is empty; add testbench sources')
        version = read_pin(root)
        result['nsim_version'] = version
        try:
            installed(version)
            result['version_installed'] = True
        except (OSError, ValueError) as exc:
            result['version_installed'] = False
            issues.append(str(exc))
    except (OSError, ValueError) as exc:
        issues.append(str(exc))
    if not entries:
        issues.append('project is not registered')
    result['configuration'] = 'valid' if not issues else 'attention_required'
    return result


def inspect_registered(root, registry):
    try:
        with project_lock(root):
            return project_info(root, registry)
    except OSError as exc:
        entries = [e for e in registry['projects'] if e['root'] == str(root)]
        return {'name': entries[0]['name'] if entries else root.name,
                'verification_root': str(root), 'registered': bool(entries),
                'configuration': 'attention_required', 'issues': [str(exc)]}


def status(value=None):
    with registry_lock():
        return inspect_registered(resolve_root(value), load_registry())


def list_projects():
    with registry_lock():
        registry = load_registry()
        result = []
        for entry in sorted(registry['projects'], key=lambda e: e['name']):
            root = Path(entry['root'])
            result.append(inspect_registered(root, registry))
        return result


def add_commands(commands):
    boot = commands.add_parser('bootstrap', help='initialize and register a verification project')
    boot.add_argument('--name')
    boot.add_argument('--verification-root', type=Path)
    for key in (*EDIT_FIELDS, 'nsim_version'):
        boot.add_argument('--' + key.replace('_', '-'))
    edit = commands.add_parser('update', help='update registered project configuration')
    edit.add_argument('--project')
    for key in (*EDIT_FIELDS, 'nsim_version'):
        edit.add_argument('--' + key.replace('_', '-'))
    switch = commands.add_parser('switch', help='alias of update --nsim-version')
    switch.add_argument('version')
    switch.add_argument('--project')
    inspect = commands.add_parser('status', help='show the current project, including configuration problems')
    inspect.add_argument('--project')
    listing = commands.add_parser('list', help='list all projects registered for this user')
    for parser in (boot, edit, switch, inspect, listing):
        parser.add_argument('--json', action='store_true')


def dispatch(options):
    if options.command == 'bootstrap':
        value = bootstrap(options)
    elif options.command in ('update', 'switch'):
        if options.command == 'switch':
            options.nsim_version = options.version
            for key in EDIT_FIELDS:
                setattr(options, key, None)
        value = update(options)
    elif options.command == 'list':
        value = list_projects()
    else:
        value = status(options.project)
    if options.json:
        print(json.dumps(value, indent=2, ensure_ascii=False))
    elif isinstance(value, list):
        print('NAME\tVERIFICATION ROOT\tRTL ROOT\tNSIM\tSTATE')
        for p in value:
            print('\t'.join([p['name'], p['verification_root'], p.get('rtl_root', '-'), p.get('nsim_version', '-'), p['configuration']]))
    else:
        if 'action' in value:
            print(value['action'].replace('_', ' '))
        if 'before' in value:
            for key in (*EDIT_FIELDS, 'nsim_version'):
                if value['before'].get(key) != value['project'].get(key):
                    print(f"{key}: {value['before'].get(key)} -> {value['project'].get(key)}")
        p = value.get('project', value)
        for key, field in p.items():
            if key != 'issues':
                print(f'{key}: {field}')
        for issue in p['issues']:
            print(f'! {issue}')
    return 0
