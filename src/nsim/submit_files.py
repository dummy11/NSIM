"""Phase-scoped, explicitly declared submit inputs and safe argv references."""

import base64
from copy import deepcopy
import glob
import hashlib
import os
from pathlib import Path
import string

from nsim.config import ConfigError, fields, name, strings

PHASES = ('compile', 'simulate', 'analysis')
SNAPSHOT_FILE_BYTES = 128 * 1024
SNAPSHOT_TOTAL_BYTES = 1024 * 1024


def config(value):
    if not isinstance(value, dict):
        raise ConfigError('files must map names to input declarations')
    result = {}
    for identifier, item in value.items():
        name(identifier, 'file name')
        if item is None:  # Explicitly remove an inherited declaration.
            continue
        fields(item, {'path', 'phases', 'dependencies', 'snapshot'}, {'path', 'phases'}, 'files.' + identifier)
        path = item['path']
        if not isinstance(path, str) or not path.strip() or '\0' in path:
            raise ConfigError(f'files.{identifier}.path must be a non-empty path')
        phases = strings(item['phases'], 'file phases', True)
        if len(set(phases)) != len(phases) or any(p not in PHASES for p in phases):
            raise ConfigError(f'files.{identifier}.phases must contain unique compile/simulate/analysis phases')
        dependencies = strings(item.get('dependencies', []), 'file dependencies')
        if any(not p.strip() for p in dependencies):
            raise ConfigError('file dependencies must be non-empty paths')
        snapshot = item.get('snapshot', True)
        if type(snapshot) is not bool:
            raise ConfigError('file snapshot must be boolean')
        result[identifier] = dict(path=path, phases=phases, dependencies=dependencies, snapshot=snapshot)
    return result


def absolute(root, value):
    # Keep the lexical path too: retargeting a symlink must invalidate the plan.
    return os.path.abspath(Path(root) / value)


def selection(expression):
    """Capture both membership and link targets; never silently follow directory cycles."""
    pattern = Path(expression)
    # pathlib's recursive glob does not traverse directory symlinks (Python 3.9+).
    candidates = (sorted(str(p) for p in Path(pattern.anchor).glob(str(pattern.relative_to(pattern.anchor))))
                  if glob.has_magic(expression) else [expression])
    if not candidates:
        raise ConfigError(f'file input pattern matched nothing: {expression}')
    nodes, members = {}, {}

    def add(path):
        path = Path(path)
        try:
            target = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ConfigError(f'file input unavailable: {path}: {exc}') from exc
        if target.is_file():
            members[str(path)] = str(target)
            nodes[str(path)] = ['file', str(target)]
        elif target.is_dir():
            nodes[str(path)] = ['directory', str(target)]
        else:
            raise ConfigError(f'file input must be a regular file or directory: {path}')
        return target

    def failed(error):
        raise ConfigError(f'cannot enumerate file inputs: {error}') from error

    for candidate in candidates:
        target = add(candidate)
        if target.is_dir():
            for directory, dirs, files in os.walk(candidate, followlinks=False, onerror=failed):
                for entry in sorted(dirs + files):
                    member = Path(directory) / entry
                    if member.is_symlink() and member.is_dir():
                        raise ConfigError(f'directory symlink inside file input tree; declare its target separately: {member}')
                    add(member)
    return {'nodes': dict(sorted(nodes.items())), 'members': dict(sorted(members.items()))}


def collect(declarations, root):
    result = {}
    for identifier, item in declarations.items():
        expressions = list(dict.fromkeys(absolute(root, value) for value in [item['path'], *item['dependencies']]))
        selections = {expression: selection(expression) for expression in expressions}
        members = sorted({target for record in selections.values() for target in record['members'].values()})
        path = expressions[0]
        result[identifier] = dict(item, path=path, resolved_path=None if glob.has_magic(path) else str(Path(path).resolve()),
                                  selections=selections, members=members)
    return result


def verify(records):
    for item in records.values():
        for expression, expected in item['selections'].items():
            if selection(expression) != expected:
                raise ConfigError(f'file input membership or link target changed: {expression}')


def phase_inputs(records, phase):
    return {Path(path) for item in records.values() if phase in item['phases'] for path in item['members']}


def protected_paths(records):
    return sorted({path for item in records.values() for selected in item['selections'].values()
                   for lexical, (_, target) in selected['nodes'].items() for path in (lexical, target)})


def snapshots(records, hashes):
    result, remaining = {}, SNAPSHOT_TOTAL_BYTES
    paths = sorted({path for item in records.values() if item['snapshot'] for path in item['members']})
    for path in paths:
        if Path(path).stat().st_size > min(SNAPSHOT_FILE_BYTES, remaining):
            continue
        with Path(path).open('rb') as stream:
            content = stream.read(min(SNAPSHOT_FILE_BYTES, remaining) + 1)
        if len(content) > min(SNAPSHOT_FILE_BYTES, remaining):
            raise ConfigError(f'file input changed while snapshotting: {path}')
        if hashlib.sha256(content).hexdigest() != hashes[path]:
            raise ConfigError(f'file input changed while snapshotting: {path}')
        result[path] = {'encoding': 'base64', 'content': base64.b64encode(content).decode('ascii'), 'sha256': hashes[path]}
        remaining -= len(content)
    return result


def reference(identifier, declarations, root, phase):
    if identifier not in declarations:
        raise ConfigError(f'unknown file reference: {identifier}')
    item = declarations[identifier]
    if phase not in item['phases']:
        raise ConfigError(f'file {identifier} is not declared for {phase}')
    path = absolute(root, item['path'])
    if glob.has_magic(path):
        raise ConfigError(f'file reference {identifier} is a pattern; reference a file/directory, or use it only as a dependency')
    return path


def bind(value, declarations, root, phase, *, literal=False):
    """Expand file references once, preserving other templates and escaped braces."""
    result = []
    try:
        for text, key, spec, conversion in string.Formatter().parse(value):
            result.append(text if literal else text.replace('{', '{{').replace('}', '}}'))
            if key is None:
                continue
            if key == 'file':
                if conversion or not spec:
                    raise ConfigError(f'invalid file reference: {value}')
                path = reference(spec, declarations, root, phase)
                result.append(path if literal else path.replace('{', '{{').replace('}', '}}'))
            else:
                result.append('{' + key + ('!' + conversion if conversion else '') + (':' + spec if spec else '') + '}')
    except ConfigError:
        raise
    except ValueError as exc:
        raise ConfigError(f'invalid file template: {value}') from exc
    return ''.join(result)


def bind_tree(value, declarations, root, phase):
    if isinstance(value, str):
        return bind(value, declarations, root, phase)
    if isinstance(value, list):
        return [bind_tree(item, declarations, root, phase) for item in value]
    if isinstance(value, dict):
        return {key: bind_tree(item, declarations, root, phase) for key, item in value.items()}
    return value


def bind_submit(submit, root):
    """Only documented argv/input slots accept references; output paths never do."""
    declarations = submit.get('files', {})
    uses = []

    def args(obj, key, phase, executable=False, literal=False):
        if key not in obj:
            return
        values = obj[key]
        for index, value in enumerate(values):
            values[index] = bind(value, declarations, root, phase, literal=literal or (executable and index == 0))
            for _, field, spec, _ in string.Formatter().parse(value):
                if field == 'file':
                    uses.append({'file': spec, 'phase': phase, 'argument': value})

    args(submit, 'argv', 'simulate', executable=True)
    args(submit, 'plusargs', 'simulate')
    submit['case_defaults'] = bind_tree(submit.get('case_defaults', {}), declarations, root, 'simulate')
    if submit.get('compile'):
        args(submit['compile'], 'argv', 'compile', executable=True)
    for stage, hooks in submit.get('hooks', {}).items():
        phase = 'compile' if stage.endswith('compile') else 'analysis' if stage == 'post_sim' else 'simulate'
        for hook in hooks:
            args(hook, 'argv', phase, executable=True)
    vcs = submit.get('vcs', {})
    args(vcs, 'compile_args', 'compile')
    args(vcs, 'run_args', 'simulate')
    args(vcs, 'fsdb_plusargs', 'simulate')
    args(vcs, 'result_argv', 'analysis', executable=True)
    if vcs.get('dpi'):
        vcs['dpi']['makefile'] = bind(vcs['dpi']['makefile'], declarations, root, 'compile', literal=True)
        args(vcs['dpi'], 'args', 'compile', literal=True)
    submit['file_references'] = uses
    return submit


def validate_references(submit):
    # No filesystem access: submit check can still inspect an unprepared environment.
    bound = bind_submit(deepcopy(submit), '/')

    def reject_unbound(value, label):
        if isinstance(value, str) and '{file:' in value:
            try:
                if any(key == 'file' for _, key, _, _ in string.Formatter().parse(value)):
                    raise ConfigError(f'file references are not supported in {label}')
            except ConfigError:
                raise
            except ValueError as exc:
                raise ConfigError(f'invalid file reference in {label}') from exc
        elif isinstance(value, dict):
            for key, item in value.items():
                reject_unbound(item, label + '.' + key)
        elif isinstance(value, list):
            for item in value:
                reject_unbound(item, label)

    for key in ('env', 'scheduler'):
        reject_unbound(bound.get(key, {}), key)
    vcs = bound.get('vcs', {})
    for key in ('executable', 'verdi', 'filelist', 'fsdb_file'):
        reject_unbound(vcs.get(key), 'vcs.' + key)
