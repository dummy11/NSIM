"""Resolve one explicit submit parent before applying validation and defaults."""

from copy import deepcopy
from pathlib import Path

from nsim.config import ConfigError, fields, integer, read_json


SUBMIT_FIELDS = {'schema_version', 'name', 'adapter', 'argv', 'env', 'plusargs', 'required_checks',
                 'timeout_seconds', 'settle_seconds', 'fake', 'vcs', 'scheduler', 'mode', 'compile',
                 'hooks', 'log_analysis', 'extends', 'case_defaults'}


def merge(parent, child, trail=()):
    result = deepcopy(parent)
    for key, value in child.items():
        # A hook stage is an ordered program, in either singleton or array form.
        if isinstance(value, dict) and isinstance(result.get(key), dict) and trail != ('hooks',):
            result[key] = merge(result[key], value, (*trail, key))
        else:
            result[key] = deepcopy(value)
    return result


def anchor_tools(data, directory):
    """Relative executables belong to the file declaring them, even in common/."""
    def anchor(value):
        if isinstance(value, str) and '/' in value and '{' not in value and '}' not in value:
            return str((directory / value).resolve())
        return value

    def command(obj, key):
        if isinstance(obj, dict) and isinstance(obj.get(key), list) and obj[key]:
            obj[key][0] = anchor(obj[key][0])

    def tools(obj, keys):
        if isinstance(obj, dict):
            for key in keys:
                if key in obj:
                    obj[key] = anchor(obj[key])

    command(data, 'argv')
    command(data.get('compile'), 'argv')
    tools(data.get('scheduler'), ('bsub', 'bjobs', 'bkill', 'worker_python'))
    vcs = data.get('vcs')
    tools(vcs, ('executable', 'verdi'))
    command(vcs, 'result_argv')
    if isinstance(vcs, dict):
        tools(vcs.get('dpi'), ('make', 'cc', 'cxx'))
    hooks = data.get('hooks')
    if isinstance(hooks, dict):
        for value in hooks.values():
            for hook in value if isinstance(value, list) else [value]:
                command(hook, 'argv')
    return data


def resolve_submit(path, source_bytes=None, *, file_contents=None):
    path = path.resolve()
    sources = {}

    def read(source):
        try:
            content = file_contents[str(source)] if file_contents and str(source) in file_contents else source.read_bytes()
        except OSError as exc:
            raise ConfigError(f'cannot read submit {source}: {exc}') from exc
        data = read_json(source, content)
        fields(data, SUBMIT_FIELDS, {'schema_version'}, f'submit {source}')
        if integer(data['schema_version'], 'schema_version') != 1:
            raise ConfigError(f'{source}: unsupported submit schema version')
        return data, content

    child, content = read(path)
    if 'name' not in child:
        raise ConfigError(f'{path}: leaf submit must declare its own name')
    parent = {}
    if 'extends' in child:
        ref = child.pop('extends')
        if not isinstance(ref, str) or not ref.strip() or '\0' in ref:
            raise ConfigError('extends must be a single non-empty parent path')
        parent_path = (path.parent / ref).resolve()
        if parent_path == path:
            raise ConfigError('submit cannot extend itself')
        parent, parent_content = read(parent_path)
        if 'extends' in parent:
            raise ConfigError('submit inheritance supports one parent layer; parent cannot extend another submit')
        parent.pop('name', None)
        anchor_tools(parent, parent_path.parent)
        sources[str(parent_path)] = parent_content
    sources[str(path)] = content
    if source_bytes is not None:
        source_bytes.update(sources)
    return merge(parent, anchor_tools(child, path.parent))
