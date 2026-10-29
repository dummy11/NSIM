"""Persistent product ownership, shared by all cooperating NSIM controllers."""

from contextlib import ExitStack
import fcntl
import os
from pathlib import Path

from nsim.config import ConfigError, read_json
from nsim.storage import write_json

OWNER_FILE = '.nsim-owner.json'


def identity(plan):
    return dict(project_root=plan['project']['root'], uid=os.getuid(),
                nsim_version=plan['runtime']['version'],
                wheel_sha256=plan['runtime'].get('wheel_sha256'))


def claim_product(path, plan, kind, reuse=False):
    """Claim an empty directory, or verify ownership before any cleanup.

    Directory locks serialize parent/child claims, including absolute overrides
    and callers using different output roots. Product locks still protect the
    subsequent lifetime; this marker survives every cleanup.
    """
    path = Path(path)
    expected = dict(schema_version=1, kind=kind, **identity(plan))
    marker = path / OWNER_FILE
    advice = '; choose a separate --output-root or -dir (also change absolute directory overrides)'
    with ExitStack() as locks:
        for directory in (*reversed(path.parents), path):
            fd = os.open(directory, os.O_RDONLY)
            locks.callback(os.close, fd)
            writing = directory == path and not marker.exists()
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if writing else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConfigError(f'output directory is in use: {path}') from exc
            if directory != path and os.path.lexists(directory / OWNER_FILE):
                raise ConfigError(f'nested product directory under {directory}: {path}' + advice)
        if path.stat().st_uid != os.getuid():
            raise ConfigError(f'output directory owner is another user: {path}' + advice)
        if os.path.lexists(marker):
            if marker.is_symlink() or not marker.is_file():
                raise ConfigError(f'invalid output owner record: {marker}' + advice)
            try:
                actual = read_json(marker)
            except (OSError, ValueError) as exc:
                raise ConfigError(f'cannot read output owner record: {marker}' + advice) from exc
            if actual != expected:
                raise ConfigError(f'output owner mismatch (user/project/NSIM version/product kind): {path}' + advice)
        else:
            # Existing output from an older release has no trustworthy owner.
            # Never infer ownership merely from a writable directory or a name.
            if reuse or any(p.name not in ('.build.lock', '.run.lock') for p in path.iterdir()):
                raise ConfigError(f'output owner unknown for existing products: {path}' + advice)
            write_json(marker, expected)
