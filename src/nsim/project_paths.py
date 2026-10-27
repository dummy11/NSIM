"""Project locations and registry reads; usable without simulation dependencies."""
import json
import os
from pathlib import Path


class ProjectError(ValueError):
    pass


def registry_path():
    return Path(os.environ.get('XDG_CONFIG_HOME') or '~/.config').expanduser().resolve() / 'nsim' / 'projects.json'


def load_registry():
    path = registry_path()
    if not path.exists():
        return {'schema_version': 1, 'projects': []}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise ProjectError(f'invalid project registry: {path}: {exc}') from exc
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('projects'), list):
        raise ProjectError(f'invalid project registry: {path}')
    names, roots = set(), set()
    for entry in data['projects']:
        if not isinstance(entry, dict) or set(entry) != {'name', 'root', 'descriptor'} or any(not isinstance(v, str) or not v for v in entry.values()):
            raise ProjectError(f'invalid project registry entry: {path}')
        if (entry['name'] in names or entry['root'] in roots or
                any('\0' in v for v in entry.values()) or
                not Path(entry['root']).is_absolute() or not Path(entry['descriptor']).is_absolute()):
            raise ProjectError(f'duplicate or invalid project registry entry: {path}')
        names.add(entry['name'])
        roots.add(entry['root'])
    return data


def marker(root):
    return any(os.path.lexists(root / p) for p in ('.nsim', 'nsim.toml', 'nsim.yaml', '.nsim_version'))


def find_root(start=None):
    start = Path(start or Path.cwd()).expanduser().resolve()
    for root in (start, *start.parents):
        # nsim.toml inside the control directory belongs to its parent project.
        if root.name == '.nsim':
            continue
        if marker(root):
            return root
    return None


def resolve_root(value=None):
    if value is None:
        root = find_root()
        if root is None:
            raise ProjectError('no project found; run nsim bootstrap or specify --project')
        return root
    value = str(value)
    if '/' not in value and value not in ('.', '..'):
        matches = [e for e in load_registry()['projects'] if e['name'] == value]
        if matches:
            return Path(matches[0]['root'])
    path = Path(value).expanduser()
    if path.name in ('nsim.toml', 'nsim.yaml'):
        path = path.parent
    if path.name == '.nsim':
        path = path.parent
    return path.resolve()


def descriptor_path(value):
    root = resolve_root(value)
    hidden = root / '.nsim'
    legacy = [root / n for n in ('nsim.toml', 'nsim.yaml') if os.path.lexists(root / n)]
    if os.path.lexists(hidden):
        if hidden.is_symlink() or not hidden.is_dir():
            raise ProjectError(f'{hidden}: must be a real directory')
        if legacy or os.path.lexists(root / '.nsim_version'):
            raise ProjectError(f'{root}: conflicting legacy and .nsim project configuration')
        candidates = [hidden / n for n in ('nsim.toml', 'nsim.yaml') if os.path.lexists(hidden / n)]
    else:
        candidates = legacy
    if len(candidates) != 1 or not candidates[0].is_file() or candidates[0].is_symlink():
        raise ProjectError(f'{root}: expected exactly one regular nsim.toml or nsim.yaml')
    return candidates[0]


def read_pin(root):
    path = descriptor_path(root).parent / '.nsim_version'
    if path.is_symlink() or not path.is_file():
        raise ProjectError(f'{path}: missing regular version file')
    content = path.read_text(encoding='utf-8')
    from re import fullmatch
    if not fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?\n', content):
        raise ProjectError(f'{path}: expected one exact version and newline')
    return content.strip()
