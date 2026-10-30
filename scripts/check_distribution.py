"""Check that a built nsim wheel and source archive match this checkout."""

import argparse
from email.parser import BytesParser
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib


def check(directory, release=False):
    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    name, version = project["name"], project["version"]
    if name != "nsim":
        raise ValueError("unexpected distribution name")
    wheels = list(directory.glob(f"{name}-{version}-*.whl"))
    sources = list(directory.glob(f"{name}-{version}.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError(f"expected one wheel and one sdist for {name} {version}")
    modules = sorted((root / "src" / "nsim").glob("*.py"))
    if not modules:
        raise ValueError("no package modules found")
    with zipfile.ZipFile(wheels[0]) as wheel:
        filenames = set(wheel.namelist())
        for module in modules:
            member = "nsim/" + module.name
            if member not in filenames or wheel.read(member) != module.read_bytes():
                raise ValueError(f"wheel module differs from checkout: {member}")
        metadata = BytesParser().parsebytes(wheel.read(f"{name}-{version}.dist-info/METADATA"))
        entry = wheel.read(f"{name}-{version}.dist-info/entry_points.txt").decode("utf-8")
        if metadata["Name"] != name or metadata["Version"] != version or "nsim = nsim.cli:main" not in entry:
            raise ValueError("wheel identity or CLI entry point differs from pyproject.toml")
        unexpected = [member for member in filenames if not (member.startswith("nsim/") or
                      member.startswith(f"{name}-{version}.dist-info/"))]
        if unexpected:
            raise ValueError(f"unexpected wheel content: {unexpected}")
    with tarfile.open(sources[0], "r:gz") as source:
        archive = {member.name: member for member in source.getmembers() if member.isfile()}
        prefix = f"{name}-{version}/"
        for path in [root / "pyproject.toml", root / "README.md", root / "nsim-install.py",
                     root / "user_guide.md", root / 'scripts/sync_project_layout.py',
                     root / 'scripts/check_distribution.py',
                     *sorted((root / 'docs').glob('*.md')),
                     *sorted((root / 'tests').glob('*.py')), *modules]:
            relative = path.relative_to(root).as_posix()
            member = archive.get(prefix + relative)
            if member is None or source.extractfile(member).read() != path.read_bytes():
                raise ValueError(f"sdist file differs from checkout: {relative}")
    if release:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True)
        if status.stdout.strip():
            raise ValueError("release requires a clean committed checkout")
        tags = subprocess.run(["git", "tag", "--points-at", "HEAD"], cwd=root,
                              capture_output=True, text=True, check=True).stdout.splitlines()
        if f"v{version}" not in tags:
            raise ValueError(f"release commit must carry tag v{version}")
    return name, version, wheels[0], sources[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing exactly one wheel and sdist for this version")
    parser.add_argument("--release", action="store_true", help="also require a clean tagged checkout")
    args = parser.parse_args()
    try:
        name, version, wheel, source = check(args.directory, args.release)
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"distribution check failed: {exc}", file=sys.stderr)
        return 1
    print(f"{name} {version}: wheel={wheel} sdist={source}; release_gate={args.release}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
