"""Filesystem checks, atomic publication and integrity verification."""

import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import time


class IntegrityError(ValueError):
    pass


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(path: Path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def atomic_write(path: Path, data: bytes):
    """Replace only after a complete write; old destination survives a failed write."""
    fd, tmp = tempfile.mkstemp(prefix=".nsim-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def write_json(path: Path, value):
    data = encode(value)
    atomic_write(path, data)
    return hashlib.sha256(data).hexdigest()


def sync_directory(path):
    """Persist a critical rename/directory entry; unsupported filesystems fail closed.

    Used at LSF submission/release boundaries, not on every local checkpoint.
    POSIX fsync cannot promise more durability than the mounted filesystem does.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def preflight(root: Path, min_free_bytes=0, min_free_inodes=0):
    """Roots must already exist: never recreate a missing mount point."""
    from nsim.config import integer
    integer(min_free_bytes, "min_free_bytes")
    integer(min_free_inodes, "min_free_inodes")
    if not root.is_dir():
        raise FileNotFoundError(errno.ENOENT, "storage root missing (check mount)", str(root))
    stats = os.statvfs(root)
    free = stats.f_bavail * stats.f_frsize
    inodes = stats.f_favail
    if free < min_free_bytes or (stats.f_files > 0 and inodes < min_free_inodes):
        raise OSError(errno.ENOSPC, "insufficient free bytes or inodes", str(root))
    fd, tmp = tempfile.mkstemp(prefix=".nsim-probe-", dir=root)
    try:
        with os.fdopen(fd, "w+b") as stream:
            stream.write(b"nsim-storage-probe\n")
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            if stream.read() != b"nsim-storage-probe\n":
                raise OSError(errno.EIO, "storage probe readback mismatch", str(root))
    finally:
        os.unlink(tmp)
    return {"path": str(root), "host": socket.gethostname(), "device": root.stat().st_dev,
            "free_bytes": free, "free_inodes": inodes if stats.f_files else None,
            "quota": "unknown", "checked_at": time.time()}


def error_record(exc, path):
    code = getattr(exc, "errno", None)
    reasons = {errno.ENOSPC: "STORAGE_FULL", errno.EDQUOT: "QUOTA_EXCEEDED", errno.EIO: "IO_ERROR",
               errno.EROFS: "READ_ONLY", errno.EACCES: "PERMISSION_DENIED", errno.ENOENT: "PATH_UNAVAILABLE"}
    return {"reason": reasons.get(code, "STORAGE_ERROR"), "errno": code,
            "path": str(path), "message": str(exc), "host": socket.gethostname(), "time": time.time()}


def verify_files(files):
    for path, expected in files.items():
        try:
            if digest(Path(path)) != expected:
                raise IntegrityError(f"checksum changed: {path}")
        except OSError as exc:
            raise IntegrityError(f"evidence unavailable: {path}: {exc}") from exc
