"""Bootstrap directory choices persisted with a schema-2 verification project."""

from pathlib import Path

from nsim.config import ConfigError, fields

KINDS = ('filelist', 'caselist', 'regression', 'submit', 'resources', 'scripts')


def default():
    return {kind: kind for kind in KINDS}


def validate(value):
    fields(value, KINDS, set(), 'layout')
    layout = default()
    for kind, entry in value.items():
        if not isinstance(entry, str) or not entry.strip() or '\0' in entry:
            raise ConfigError(f'layout.{kind}: expected a project-relative directory')
        path = Path(entry)
        if (path.is_absolute() or any(part in ('.', '..') for part in entry.split('/')) or
                '.nsim' in path.parts or str(path) != entry):
            raise ConfigError(f'layout.{kind}: expected a directory inside the project, without . or ..')
        layout[kind] = entry
    return layout


def from_options(options):
    return validate({kind: kind if getattr(options, kind + '_dir', None) is None
                     else getattr(options, kind + '_dir') for kind in KINDS})


def directory(project, kind):
    return project.root / project.layout[kind]
