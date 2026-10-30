"""Create small submit files and inspect configuration without invoking tools."""

import json
import os
from pathlib import Path

from nsim.cli_aliases import add_argument, add_command
from nsim.config import ConfigError, MODES, load_project, load_submit, name
from nsim.project_paths import resolve_root
from nsim.projects import check_destination, project_lock, publish, registry_lock
from nsim.submit_config import resolve_submit
from nsim.submit_templates import common_submit, pretty


def add_commands(commands):
    submit = add_command(commands, 'submit', help='Create, inspect and validate submit configuration')
    actions = submit.add_subparsers(dest='action', required=True)
    new = add_command(actions, 'new', help='Create a submit inheriting the selected mode common template')
    add_argument(new, 'name', help='New submit name; an optional .json suffix is accepted')
    add_argument(new, '--project', type=Path, help='Project name/path; default: current project')
    add_argument(new, '-mode', '--mode', choices=MODES, help='Default: rtl, or the explicit parent mode')
    add_argument(new, '--extends', dest='parent', help='Parent path relative to the configured submit write root, or absolute path')
    for action in ('show', 'check'):
        command = add_command(actions, action, help=('Show merged configuration and source paths' if action == 'show'
                                                   else 'Validate configuration without running a job'))
        add_argument(command, 'submit', help='Submit TOML catalog name or an explicit JSON path')
        add_argument(command, '--project', type=Path, help='Project name/path; default: current project')


def create(root, identifier, mode=None, parent=None):
    identifier = name(identifier[:-5] if identifier.endswith('.json') else identifier, 'submit name')
    if identifier.casefold() == 'regr_dir':
        raise ConfigError('submit name regr_dir is reserved for regression records')
    if mode is not None and mode not in MODES:
        raise ConfigError('invalid submit mode')
    root = Path(root).resolve()
    with registry_lock(write=True), project_lock(root):
        project = load_project(root, validate=False)
        from nsim.catalog import anchored, creation_directory
        directory = creation_directory(project)
        if directory != root and root not in directory.parents:
            raise ConfigError('submit new write_root must be inside the project; external catalogs are read-only')
        if identifier in project.catalog.get('submit', {}).get('aliases', {}):
            raise ConfigError(f'submit alias already exists: {identifier}; choose another name')
        target = directory / (identifier + '.json')
        check_destination(target)
        if os.path.lexists(target):
            raise ConfigError(f'submit already exists; choose another name: {target}')
        if parent is None:
            template = project.catalog.get('submit', {}).get('templates', {}).get(mode or 'rtl')
            if template is None:
                raise ConfigError(f'configure catalog.submit.templates.{mode or "rtl"}, or specify --extends')
            parent_path = anchored(project, template)
        elif not isinstance(parent, str) or not parent.strip() or '\0' in parent:
            raise ConfigError('extends must be a single non-empty parent path')
        else:
            parent_path = (target.parent / parent).resolve()
        reference = os.path.relpath(parent_path, target.parent)
        files = {}
        if parent is None and not parent_path.exists():
            # Also support projects created before common templates were introduced.
            if root not in parent_path.parents:
                raise ConfigError('cannot create a missing common template outside the project')
            check_destination(parent_path)
            files[parent_path] = pretty(common_submit(mode or 'rtl')).encode('utf-8')
        child = {'schema_version': 1, 'name': identifier, 'extends': reference}
        files[target] = pretty(child).encode('utf-8')
        contents = {str(p): data for p, data in files.items()}
        resolved = resolve_submit(target, file_contents=contents, project_root=project.root)
        needs_argv = resolved.get('adapter') == 'command' and resolved.get('argv', []) == []
        validation_contents = dict(contents)
        if needs_argv:
            # Validate the rest of a hardware skeleton without inventing a platform command.
            validation_contents[str(target)] = pretty(dict(child, argv=['platform-command-to-configure'])).encode('utf-8')
        effective = load_submit(target, file_contents=validation_contents, project_root=project.root)
        if mode is not None and effective['mode'] != mode:
            raise ConfigError(f"requested mode {mode} does not match parent mode {effective['mode']}")
        publish(root, files)
        return {'action': 'created', 'path': str(target), 'mode': effective['mode'],
                'parent': str(parent_path), 'created_files': [str(p) for p in files],
                'needs_configuration': ['argv'] if needs_argv else [],
                'message': ('Configure command argv in this submit or its common template, then run nsim submit check.'
                            if needs_argv else 'Edit only differing options; use nsim submit show/check to inspect configuration.')}


def inspect(root, path):
    with registry_lock(), project_lock(root):
        sources = {}
        try:
            project = load_project(root, validate=False)
            from nsim.catalog import resolve
            path, reference = resolve(project, 'submit', path)
            effective = load_submit(path, sources, project_root=project.root)
        except ConfigError as exc:
            raise ConfigError(f'{path}: {exc}') from exc
        from nsim.submit_files import collect
        resolved_files = collect(effective['files'], project.root)
        return {'path': str(path), 'reference': reference, 'configuration_valid': True, 'source_chain': list(sources),
                'effective': effective, 'resolved_files': resolved_files}


def dispatch(args):
    root = resolve_root(args.project)
    if args.action == 'new':
        result = create(root, args.name, args.mode, args.parent)
    else:
        result = inspect(root, args.submit)
        if args.action == 'check':
            effective = result.pop('effective')
            result.update(name=effective['name'], mode=effective['mode'], adapter=effective['adapter'],
                          message='Fields, inheritance and declared files are valid. Use run --dry-run to check source lists and command previews.')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
