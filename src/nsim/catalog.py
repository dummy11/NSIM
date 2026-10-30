"""Project-configured names and explicit paths, without implicit search directories."""

import json
import os
from pathlib import Path
import re

from nsim.config import ConfigError, MODES, fields

SUFFIXES = {'submit': ('.json',), 'caselist': ('.list', '.caselist'), 'regrlist': ('.list', '.regr')}
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


def identifier(value, label):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ConfigError(f'{label}: expected a name containing letters, digits, _, . or -')
    return value


def config(value):
    fields(value, set(SUFFIXES), set(), 'catalog')
    result = {}
    for kind, section in value.items():
        allowed = {'roots', 'aliases'} | ({'write_root', 'templates'} if kind == 'submit' else set())
        fields(section, allowed, set(), f'catalog.{kind}')
        entry = {}
        for key in ('roots', 'aliases', 'templates'):
            if key not in section:
                continue
            mapping = section[key]
            if not isinstance(mapping, dict):
                raise ConfigError(f'catalog.{kind}.{key}: expected a name-to-path table')
            for name, path in mapping.items():
                identifier(name, f'catalog.{kind}.{key} name')
                if key == 'templates' and name not in MODES:
                    raise ConfigError(f'unknown submit template mode: {name}')
                if not isinstance(path, str) or not path.strip() or '\0' in path:
                    raise ConfigError(f'catalog.{kind}.{key}.{name}: expected a non-empty path')
            entry[key] = dict(mapping)
        if 'write_root' in section:
            root = identifier(section['write_root'], 'catalog.submit.write_root')
            if root not in entry.get('roots', {}):
                raise ConfigError('catalog.submit.write_root must name a configured submit root')
            entry['write_root'] = root
        result[kind] = entry
    return result


def defaults():
    """Scaffold policy only. The runtime never falls back to these directories."""
    return {'submit': {'roots': {'local': 'submit'}, 'write_root': 'local',
                       'templates': {mode: f'submit/common/{mode}.json' for mode in MODES}},
            'caselist': {'roots': {'local': 'caselist'}},
            'regrlist': {'roots': {'local': 'regression'}}}


def toml(value):
    lines = []
    for kind, section in config(value).items():
        if 'write_root' in section:
            lines.extend([f'\n[catalog.{kind}]', 'write_root = ' + json.dumps(section['write_root'])])
        for key in ('roots', 'aliases', 'templates'):
            if key in section:
                lines.append(f'\n[catalog.{kind}.{key}]')
                lines.extend(f'{json.dumps(name)} = {json.dumps(path, ensure_ascii=False)}'
                             for name, path in section[key].items())
    return '\n'.join(lines) + ('\n' if lines else '')


def explicit(value):
    # Paths passed by the Python API are already file references. CLI strings must
    # retain ./ until this decision; Path('./rtl.json') would erase that intent.
    return isinstance(value, Path) or (isinstance(value, str) and
           (value.startswith(('/', './', '../', '~/')) or ('/' in value and ':' not in value)))


def anchored(project, path):
    return (project.root / Path(path).expanduser()).resolve()


def resolve(project, kind, value):
    if kind not in SUFFIXES:
        raise ConfigError(f'unknown catalog kind: {kind}')
    raw = str(value)
    if explicit(value):
        target = Path(value).expanduser().resolve()
        via = 'path'
    else:
        if project is None:
            raise ConfigError(f'{kind} name {raw!r} requires a project; use --project or an explicit ./path')
        section = project.catalog.get(kind, {})
        aliases, roots = section.get('aliases', {}), section.get('roots', {})
        if raw in aliases:
            target, via = anchored(project, aliases[raw]), 'alias'
        else:
            namespace, separator, key = raw.partition(':')
            if separator:
                if namespace not in roots:
                    raise ConfigError(f'unknown {kind} namespace {namespace!r}; configured: {", ".join(roots)}')
                selected = {namespace: roots[namespace]}
            else:
                key, selected = raw, roots
            # Qualified names may select a subdirectory, but cannot escape a root.
            parts = key.split('/')
            for part in parts:
                identifier(part, f'{kind} name')
            suffixes = SUFFIXES[kind]
            names = [key] if Path(key).suffix in suffixes else [key + suffix for suffix in suffixes]
            matches = {}
            for namespace, folder in selected.items():
                for filename in names:
                    path = anchored(project, folder) / filename
                    if path.is_file():
                        matches.setdefault(path.resolve(), []).append(namespace + ':' + key)
            if not matches:
                raise ConfigError(f'{kind} not found: {raw}; configure catalog.{kind}.roots/aliases in '
                                  f'{project.descriptor}, or use an explicit ./path')
            if len(matches) > 1:
                choices = '; '.join(f'{"/".join(labels)} -> {path}' for path, labels in matches.items())
                raise ConfigError(f'ambiguous {kind} {raw!r}: {choices}; use a namespace, exact suffix or alias')
            target, labels = next(iter(matches.items()))
            via = labels[0]
    if not target.is_file():
        raise ConfigError(f'{kind} file not found: {raw} -> {target}')
    return target, {'kind': kind, 'reference': raw, 'path': str(target), 'via': via}


def resolve_many(project, kind, values):
    paths, records = [], []
    for value in values:
        path, record = resolve(project, kind, value)
        records.append(record)
        if path not in paths:
            paths.append(path)
    return paths, records


def creation_directory(project):
    section = project.catalog.get('submit', {})
    root = section.get('write_root')
    if root is None:
        raise ConfigError('submit new requires catalog.submit.write_root and a matching roots entry in the project TOML')
    # Keep symlinks visible so the authoring transaction can reject redirected writes.
    return Path(os.path.abspath(project.root / Path(section['roots'][root]).expanduser()))


def add_commands(commands):
    from nsim.cli_aliases import add_argument, add_command
    command = add_command(commands, 'catalog', help='Inspect configured names and their actual files')
    actions = command.add_subparsers(dest='action', required=True)
    for action in ('list', 'resolve'):
        parser = add_command(actions, action)
        add_argument(parser, 'kind', choices=tuple(SUFFIXES))
        if action == 'resolve':
            add_argument(parser, 'reference')
        add_argument(parser, '--project', type=Path)


def dispatch(args):
    from nsim.config import load_project
    from nsim.project_paths import resolve_root
    from nsim.projects import project_lock, registry_lock
    root = resolve_root(args.project)
    with registry_lock(), project_lock(root):
        project = load_project(root, validate=False)
        if args.action == 'resolve':
            _, result = resolve(project, args.kind, args.reference)
        else:
            section = project.catalog.get(args.kind, {})
            entries = []
            for namespace, folder in section.get('roots', {}).items():
                directory = anchored(project, folder)
                for path in sorted(directory.iterdir()) if directory.is_dir() else []:
                    if path.is_file() and path.suffix in SUFFIXES[args.kind]:
                        entries.append({'reference': namespace + ':' + path.name, 'name': path.stem,
                                        'path': str(path.resolve()), 'via': 'root'})
            for alias, path in section.get('aliases', {}).items():
                target = anchored(project, path)
                entries.append({'reference': alias, 'path': str(target), 'via': 'alias', 'exists': target.is_file()})
            result = {'kind': args.kind, 'configuration': section, 'entries': entries}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
