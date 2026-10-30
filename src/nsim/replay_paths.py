"""Choose replay locations without changing the source execution."""

from pathlib import Path

from nsim.config import ConfigError
from nsim.identifiers import new_replay_name
from nsim.layout import is_regression, isolated_regression, workspace_directory


def directory_name(value, mode):
    if value is None:
        return new_replay_name(mode)
    if (not isinstance(value, str) or not value or value in ('.', '..') or
            '/' in value or '\\' in value or '\0' in value):
        raise ConfigError('-dir requires one directory name without path separators')
    return value


def anchor(original):
    """Return the source's stable parent relative to its recorded output root."""
    roots = [Path(path).resolve() for path in original['policy']['output_roots']]
    journal = Path(original['policy']['record_dir']).resolve()
    matching = [root for root in roots if root in journal.parents]
    if not matching and len(roots) == 1 and not (is_regression(original) or isolated_regression(original)):
        matching = roots  # A standalone case may have an external -sim-dir.
    if len(matching) != 1:
        raise ConfigError('cannot locate the original record inside one recorded output root')
    root = matching[0]
    saved = original['execution'].get('replay_parent', original['execution'].get('rerun_parent'))
    if saved is not None:
        parent = Path(saved)
    elif is_regression(original) or isolated_regression(original):
        parent = (journal.parent if original['execution'].get('layout') else journal).relative_to(root)
    else:
        parent = workspace_directory(root, original, original['tasks'][0]).relative_to(root)
    if parent.is_absolute() or not parent.parts or '..' in parent.parts:
        raise ConfigError('invalid original replay parent')
    return root, str(parent)
