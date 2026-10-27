"""NSIM: traceable ASIC simulation regression orchestration."""

from importlib.metadata import PackageNotFoundError, version


def get_version() -> str:
    """Return installed distribution identity; never invent a source version."""
    try:
        return version("nsim")
    except PackageNotFoundError:
        return "unknown (not installed)"
