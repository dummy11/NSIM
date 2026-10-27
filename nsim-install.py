#!/usr/bin/env python3
"""Dependency-free NSIM bootstrap, version manager and stable CLI launcher.

The script is also copied to <root>/bin/nsim and <root>/bin/nsim-install.
Versioned NSIM wheels never manage their own installation or selection.
"""

import argparse
from email.parser import BytesParser
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile


REPO = os.environ.get("NSIM_RELEASE_REPO", "dummy11/NSIM")
VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){2}(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?")
PIN = ".nsim_version"
MANAGEMENT = {"bootstrap", "install", "update", "switch", "versions", "bundle"}


# Generated from src/nsim/project_paths.py; checked by tests.
PROJECT_LAYOUT_SOURCE = '"""Project locations and registry reads; usable without simulation dependencies."""\nimport json\nimport os\nfrom pathlib import Path\n\n\nclass ProjectError(ValueError):\n    pass\n\n\ndef registry_path():\n    return Path(os.environ.get(\'XDG_CONFIG_HOME\') or \'~/.config\').expanduser().resolve() / \'nsim\' / \'projects.json\'\n\n\ndef load_registry():\n    path = registry_path()\n    if not path.exists():\n        return {\'schema_version\': 1, \'projects\': []}\n    try:\n        data = json.loads(path.read_text(encoding=\'utf-8\'))\n    except ValueError as exc:\n        raise ProjectError(f\'invalid project registry: {path}: {exc}\') from exc\n    if not isinstance(data, dict) or data.get(\'schema_version\') != 1 or not isinstance(data.get(\'projects\'), list):\n        raise ProjectError(f\'invalid project registry: {path}\')\n    names, roots = set(), set()\n    for entry in data[\'projects\']:\n        if not isinstance(entry, dict) or set(entry) != {\'name\', \'root\', \'descriptor\'} or any(not isinstance(v, str) or not v for v in entry.values()):\n            raise ProjectError(f\'invalid project registry entry: {path}\')\n        if (entry[\'name\'] in names or entry[\'root\'] in roots or\n                any(\'\\0\' in v for v in entry.values()) or\n                not Path(entry[\'root\']).is_absolute() or not Path(entry[\'descriptor\']).is_absolute()):\n            raise ProjectError(f\'duplicate or invalid project registry entry: {path}\')\n        names.add(entry[\'name\'])\n        roots.add(entry[\'root\'])\n    return data\n\n\ndef marker(root):\n    return any(os.path.lexists(root / p) for p in (\'.nsim\', \'nsim.toml\', \'nsim.yaml\', \'.nsim_version\'))\n\n\ndef find_root(start=None):\n    start = Path(start or Path.cwd()).expanduser().resolve()\n    for root in (start, *start.parents):\n        # nsim.toml inside the control directory belongs to its parent project.\n        if root.name == \'.nsim\':\n            continue\n        if marker(root):\n            return root\n    return None\n\n\ndef resolve_root(value=None):\n    if value is None:\n        root = find_root()\n        if root is None:\n            raise ProjectError(\'no project found; run nsim bootstrap or specify --project\')\n        return root\n    value = str(value)\n    if \'/\' not in value and value not in (\'.\', \'..\'):\n        matches = [e for e in load_registry()[\'projects\'] if e[\'name\'] == value]\n        if matches:\n            return Path(matches[0][\'root\'])\n    path = Path(value).expanduser()\n    if path.name in (\'nsim.toml\', \'nsim.yaml\'):\n        path = path.parent\n    if path.name == \'.nsim\':\n        path = path.parent\n    return path.resolve()\n\n\ndef descriptor_path(value):\n    root = resolve_root(value)\n    hidden = root / \'.nsim\'\n    legacy = [root / n for n in (\'nsim.toml\', \'nsim.yaml\') if os.path.lexists(root / n)]\n    if os.path.lexists(hidden):\n        if hidden.is_symlink() or not hidden.is_dir():\n            raise ProjectError(f\'{hidden}: must be a real directory\')\n        if legacy or os.path.lexists(root / \'.nsim_version\'):\n            raise ProjectError(f\'{root}: conflicting legacy and .nsim project configuration\')\n        candidates = [hidden / n for n in (\'nsim.toml\', \'nsim.yaml\') if os.path.lexists(hidden / n)]\n    else:\n        candidates = legacy\n    if len(candidates) != 1 or not candidates[0].is_file() or candidates[0].is_symlink():\n        raise ProjectError(f\'{root}: expected exactly one regular nsim.toml or nsim.yaml\')\n    return candidates[0]\n\n\ndef read_pin(root):\n    path = descriptor_path(root).parent / \'.nsim_version\'\n    if path.is_symlink() or not path.is_file():\n        raise ProjectError(f\'{path}: missing regular version file\')\n    content = path.read_text(encoding=\'utf-8\')\n    from re import fullmatch\n    if not fullmatch(r\'[0-9]+\\.[0-9]+\\.[0-9]+(?:(?:a|b|rc)[0-9]+)?(?:\\.post[0-9]+)?(?:\\.dev[0-9]+)?\\n\', content):\n        raise ProjectError(f\'{path}: expected one exact version and newline\')\n    return content.strip()\n'


def project_layout():
    import types
    module = types.ModuleType('nsim_project_layout')
    exec(PROJECT_LAYOUT_SOURCE, module.__dict__)
    return module


class InstallError(ValueError):
    pass


def valid_version(value):
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise InstallError(f"invalid nsim version: {value!r}")
    return value


def default_root():
    here = Path(__file__).resolve()
    if here.parent.name == "bin" and (here.parent.parent / "versions").is_dir():
        return here.parent.parent
    return Path(os.environ.get("NSIM_HOME") or "~/.local/share/nsim").expanduser().resolve()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_bytes(path, content, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def portable_script():
    return b"#!/usr/bin/env python3\n" + Path(__file__).read_bytes().partition(b"\n")[2]


def bootstrap(root):
    root = Path(root).expanduser().resolve()
    (root / "versions").mkdir(parents=True, exist_ok=True)
    original = portable_script()
    interpreter = os.fsencode(str(Path(sys.executable).resolve()))
    if b"\n" in interpreter or b" " in interpreter:
        raise InstallError("installer Python path cannot be represented in a launcher shebang")
    script = b"#!" + interpreter + b"\n" + original.partition(b"\n")[2]
    for command in ("nsim", "nsim-install"):
        atomic_bytes(root / "bin" / command, script, 0o755)
    return root


def wheel_identity(wheel):
    wheel = Path(wheel)
    if not wheel.name.endswith(".whl") or wheel.name != wheel.name.split("/")[-1]:
        raise InstallError("expected a wheel file")
    try:
        with zipfile.ZipFile(wheel) as archive:
            metadata_files = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata_files) != 1:
                raise InstallError("wheel must contain exactly one METADATA file")
            metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
            name, version = metadata["Name"], metadata["Version"]
            if name != "nsim":
                raise InstallError(f"wheel belongs to {name!r}, not nsim")
            valid_version(version)
            if not wheel.name.startswith(f"nsim-{version}-"):
                raise InstallError("wheel filename and metadata version differ")
            entry = metadata_files[0].removesuffix("METADATA") + "entry_points.txt"
            if entry not in archive.namelist() or "nsim = nsim.cli:main" not in archive.read(entry).decode("utf-8"):
                raise InstallError("wheel does not contain the nsim CLI entry point")
            return version
    except (OSError, zipfile.BadZipFile, UnicodeError) as exc:
        raise InstallError(f"invalid wheel: {exc}") from exc


def wheel_requires_dependencies(wheel):
    with zipfile.ZipFile(wheel) as archive:
        metadata_file = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_file))
        return bool(metadata.get_all("Requires-Dist"))


def check_expected(path, expected):
    if expected and sha256(path) != expected.lower():
        raise InstallError(f"SHA-256 mismatch: {path}")


def checksum_for(path, sums):
    entries = {}
    for line in Path(sums).read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})  \*?(.+)", line)
        if not match:
            raise InstallError("invalid SHA256SUMS line")
        if match.group(2) in entries:
            raise InstallError("duplicate SHA256SUMS entry")
        entries[match.group(2)] = match.group(1).lower()
    if path.name not in entries:
        raise InstallError(f"SHA256SUMS lacks {path.name}")
    return entries[path.name]


def receipt(root, version):
    path = root / "versions" / valid_version(version) / "receipt.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("version") != version or
            not isinstance(data.get('wheel_sha256'), str) or
            not re.fullmatch(r"[a-f0-9]{64}", data['wheel_sha256'])):
        raise InstallError(f"invalid installation receipt: {path}")
    executable = path.parent / "venv" / "bin" / "nsim"
    if not executable.is_file():
        raise InstallError(f"incomplete nsim installation: {path.parent}")
    return data


def installed_versions(root):
    folder = root / "versions"
    if not folder.is_dir():
        return []
    return sorted((p.name for p in folder.iterdir() if p.is_dir() and VERSION_RE.fullmatch(p.name)
                   and receipt(root, p.name)), key=version_key)


def version_key(value):
    valid_version(value)
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?(?:\.dev(\d+))?', value)
    major, minor, patch, pre, number, post, dev = match.groups()
    phase = -1 if pre is None and post is None and dev is not None else (0 if pre else 1)
    return (int(major), int(minor), int(patch), phase, {'a': 0, 'b': 1, 'rc': 2}.get(pre, 0),
            int(number or 0), int(post) if post is not None else -1, int(dev) if dev is not None else float('inf'))


def install_wheel(root, wheel, version=None, offline=True, links=(), expected=None):
    root = bootstrap(root)
    wheel = Path(wheel).resolve()
    actual = wheel_identity(wheel)
    if version and valid_version(version) != actual:
        raise InstallError(f"requested {version}, wheel contains {actual}")
    version = actual
    check_expected(wheel, expected)
    digest = sha256(wheel)
    with (root / ".install.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = receipt(root, version)
        if current:
            if current["wheel_sha256"] != digest:
                raise InstallError(f"{version} is already installed from a different wheel")
            print(f"nsim {version} already installed at {root / 'versions' / version}")
            return version
        destination = root / "versions" / version
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir()
        try:
            environment = destination / "venv"
            venv.create(environment, with_pip=True, clear=False)
            python = environment / "bin" / "python"
            command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
            if offline:
                command.append("--no-index")
                for folder in dict.fromkeys([wheel.parent, *(Path(p).resolve() for p in links)]):
                    command.extend(["--find-links", str(folder)])
            command.append(str(wheel))
            env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1")
            if offline:
                env["PIP_NO_INDEX"] = "1"
            subprocess.run(command, env=env, check=True)
            cli = environment / "bin" / "nsim"
            result = subprocess.run([str(cli), "--version"], capture_output=True, text=True, check=True)
            if result.stdout.strip() != f"nsim {version}":
                raise InstallError("installed nsim reports a different version")
            record = {"schema_version": 1, "version": version, "wheel_sha256": digest,
                      "python": str(python), "source": "local" if offline else "github-release"}
            atomic_bytes(destination / "receipt.json", (json.dumps(record, sort_keys=True) + "\n").encode())
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise
    print(f"installed nsim {version} at {destination}")
    return version


def read_bundle(bundle, destination, expected=None):
    bundle = Path(bundle).resolve()
    check_expected(bundle, expected)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(bundle, "r:gz") as archive:
            members = archive.getmembers()
            names = [m.name for m in members]
            if len(names) != len(set(names)) or not {"manifest.json", "nsim-install.py"}.issubset(names):
                raise InstallError("bundle lacks a unique manifest or installer")
            if any(not m.isfile() or (m.name not in {"manifest.json", "nsim-install.py"}
                    and not re.fullmatch(r"wheels/[A-Za-z0-9_.+!-]+\.whl", m.name)) for m in members):
                raise InstallError("bundle contains unexpected members")
            manifest = json.loads(archive.extractfile("manifest.json").read())
            if set(manifest) != {"schema_version", "version", "wheel", "wheels", "target",
                                 "installer_sha256"} or manifest["schema_version"] != 1:
                raise InstallError("unsupported bundle manifest")
            installer = archive.extractfile("nsim-install.py").read()
            if hashlib.sha256(installer).hexdigest() != manifest["installer_sha256"]:
                raise InstallError("bundle installer hash differs from manifest")
            valid_version(manifest["version"])
            if not isinstance(manifest["wheels"], dict) or set(manifest["wheels"]) != {
                    name.removeprefix("wheels/") for name in names if name.startswith("wheels/")}:
                raise InstallError("bundle wheel list differs from archive")
            if manifest["wheel"] not in manifest["wheels"]:
                raise InstallError("bundle lacks the nsim wheel")
            for filename, digest in manifest["wheels"].items():
                if Path(filename).name != filename or not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise InstallError("invalid bundle filename or hash")
                member = archive.extractfile("wheels/" + filename)
                output = destination / filename
                with output.open("wb") as stream:
                    shutil.copyfileobj(member, stream)
                check_expected(output, digest)
            wheel = destination / manifest["wheel"]
            if wheel_identity(wheel) != manifest["version"]:
                raise InstallError("bundle version and wheel differ")
            return manifest, wheel
    except (OSError, tarfile.TarError, json.JSONDecodeError, UnicodeError, KeyError, TypeError) as exc:
        raise InstallError(f"invalid bundle: {exc}") from exc


def make_bundle(wheel, output, wheelhouse=None, download_deps=False, target=()):
    wheel = Path(wheel).resolve()
    version = wheel_identity(wheel)
    if wheelhouse and not Path(wheelhouse).is_dir():
        raise InstallError(f"wheelhouse is not a directory: {wheelhouse}")
    if not wheelhouse and not download_deps and wheel_requires_dependencies(wheel):
        raise InstallError("wheel has dependencies; provide --wheelhouse or --download-deps")
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise InstallError(f"bundle already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        wheels = Path(temporary) / "wheels"
        wheels.mkdir()
        shutil.copy2(wheel, wheels / wheel.name)
        if wheelhouse:
            for dependency in Path(wheelhouse).glob("*.whl"):
                if dependency.name != wheel.name:
                    shutil.copy2(dependency, wheels / dependency.name)
        if download_deps:
            command = [sys.executable, "-m", "pip", "download", "--only-binary=:all:",
                       "--dest", str(wheels), *target, str(wheel)]
            subprocess.run(command, check=True)
        installer = portable_script()
        manifest = {"schema_version": 1, "version": version, "wheel": wheel.name,
                    "wheels": {p.name: sha256(p) for p in sorted(wheels.glob("*.whl"))},
                    "target": list(target), "installer_sha256": hashlib.sha256(installer).hexdigest()}
        with tarfile.open(output, "w:gz") as archive:
            payloads = {"manifest.json": (json.dumps(manifest, sort_keys=True) + "\n").encode(),
                        "nsim-install.py": installer}
            for name, content in payloads.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(content))
            for path in sorted(wheels.glob("*.whl")):
                archive.add(path, arcname="wheels/" + path.name, recursive=False)
    atomic_bytes(output.with_name(output.name + ".sha256"),
                 f"{sha256(output)}  {output.name}\n".encode())
    print(f"bundle {version}: {output}")


def gh(*args, capture=False):
    try:
        result = subprocess.run(["gh", *args], check=True, text=True,
                                stdout=subprocess.PIPE if capture else None)
        return result.stdout if capture else None
    except FileNotFoundError as exc:
        raise InstallError("GitHub CLI is required for remote downloads") from exc
    except subprocess.CalledProcessError as exc:
        raise InstallError(f"GitHub CLI failed: {exc}") from exc


def remote_versions():
    data = json.loads(gh("release", "list", "--repo", REPO, "--limit", "1000",
                         "--json", "tagName,isDraft,isPrerelease", capture=True))
    if len(data) >= 1000:
        raise InstallError("release list reached 1000 entries; refusing an incomplete --all operation")
    return sorted((entry["tagName"][1:] for entry in data if not entry["isDraft"]
                   and not entry["isPrerelease"] and entry["tagName"].startswith("v")
                   and VERSION_RE.fullmatch(entry["tagName"][1:])), key=version_key)


def install_remote(root, version):
    valid_version(version)
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        gh("release", "download", "v" + version, "--repo", REPO,
           "--pattern", f"nsim-{version}-*.whl", "--pattern", "SHA256SUMS", "--dir", str(directory))
        wheels = list(directory.glob("nsim-*.whl"))
        if len(wheels) != 1 or not (directory / "SHA256SUMS").is_file():
            raise InstallError("release must contain one nsim wheel and SHA256SUMS")
        expected = checksum_for(wheels[0], directory / "SHA256SUMS")
        install_wheel(root, wheels[0], version, offline=False, expected=expected)


def install_bundle(root, bundle, version=None, expected=None):
    root = bootstrap(root)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        manifest, wheel = read_bundle(bundle, Path(temporary), expected)
        if version and valid_version(version) != manifest["version"]:
            raise InstallError(f"requested {version}, bundle contains {manifest['version']}")
        return install_wheel(root, wheel, manifest["version"], offline=True,
                             links=[wheel.parent], expected=manifest["wheels"][wheel.name])


def project_directory(path):
    layout = project_layout()
    try:
        root = layout.resolve_root(path)
        layout.descriptor_path(root)
        return root
    except (OSError, ValueError) as exc:
        raise InstallError(str(exc)) from exc


def read_pin(project):
    try:
        return project_layout().read_pin(project)
    except (OSError, ValueError) as exc:
        raise InstallError(str(exc)) from exc


def find_pin_from_cwd():
    root = project_layout().find_root()
    return read_pin(root) if root is not None else None


def version_from_journal(path):
    plan = json.loads((Path(path).resolve() / "plan.json").read_text(encoding="utf-8"))
    return valid_version(plan["runtime"]["version"])


def selected_version(args, root):
    if args and args[0] in {"rerun", "recover"} and len(args) > 1:
        return version_from_journal(args[1])
    if args[:2] == ["report", "show"] and len(args) > 2:
        return version_from_journal(args[2])
    project_arg = next((item.partition("=")[2] for item in args if item.startswith("--project=")), None)
    if project_arg is None and "--project" in args:
        index = args.index("--project")
        if index + 1 >= len(args):
            raise InstallError("--project requires a path")
        project_arg = args[index + 1]
    if project_arg is not None:
        return read_pin(project_directory(project_arg))
    if args[:2] == ["project", "inspect"] and len(args) > 2:
        return read_pin(project_directory(args[2]))
    pin = find_pin_from_cwd()
    if pin:
        return pin
    value = os.environ.get("NSIM_VERSION")
    if value:
        return valid_version(value)
    versions = installed_versions(root)
    if len(versions) == 1:
        return versions[0]
    raise InstallError("no project version selected; use --project, set NSIM_VERSION, or enter a project")


def launch(root, args):
    version = selected_version(args, root)
    record = receipt(root, version)
    if not record:
        raise InstallError(f"nsim {version} is not installed; run nsim install {version}")
    executable = root / "versions" / version / "venv" / "bin" / "nsim"
    environment = dict(os.environ, NSIM_HOME=str(root), NSIM_WHEEL_SHA256=record["wheel_sha256"])
    os.execve(executable, [str(executable), *args], environment)


def management_version(root):
    # Management must still work when the project's selected runtime is missing.
    folder = root / 'versions'
    versions = sorted((p.name for p in folder.iterdir() if VERSION_RE.fullmatch(p.name)), key=version_key) if folder.is_dir() else []
    ordered = list(reversed(versions))
    ordered = [v for v in ordered if not re.search(r'(?:a|b|rc|dev)', v)] + [v for v in ordered if re.search(r'(?:a|b|rc|dev)', v)]
    for version in ordered:
        exe = root / 'versions' / version / 'venv' / 'bin' / 'nsim'
        try:
            if not receipt(root, version):
                continue
            probe = subprocess.run([str(exe), 'capabilities', '--json'], capture_output=True, text=True, timeout=10)
            data = json.loads(probe.stdout)
            if (probe.returncode == 0 and isinstance(data, dict) and data.get('version') == version and
                    isinstance(data.get('project_schemas'), list) and 2 in data['project_schemas']):
                return version
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    return None


def basic_query(args, root):
    """Read-only diagnosis even when no versioned management runtime is installed."""
    layout = project_layout()
    parser = argparse.ArgumentParser(prog='nsim ' + args[0])
    parser.add_argument('--json', action='store_true')
    if args[0] == 'status':
        parser.add_argument('--project')
    options = parser.parse_args(args[1:])
    pending = layout.registry_path().parent / 'projects.transaction.json'
    if pending.exists():
        raise InstallError('interrupted project update; install a management runtime and rerun bootstrap/update')
    try:
        registry = layout.load_registry()
        def info(directory):
            entries = [p for p in registry['projects'] if p['root'] == str(directory)]
            value = {'name': entries[0]['name'] if entries else directory.name,
                     'verification_root': str(directory), 'registered': bool(entries), 'issues': []}
            try:
                config = layout.descriptor_path(directory)
                value['config'] = str(config)
                version = layout.read_pin(directory)
                value['nsim_version'] = version
                value['version_installed'] = bool(receipt(root, version))
                if not value['version_installed']:
                    value['issues'].append('selected nsim version is not installed')
                try:
                    import tomllib
                except ImportError:
                    try:
                        import tomli as tomllib
                    except ImportError:
                        tomllib = None
                if config.suffix == '.toml' and tomllib is not None:
                    data = tomllib.loads(config.read_text())
                    for key in ('rtl_root', 'rtl_filelist', 'verification_filelist'):
                        if key in data:
                            value[key] = str((directory / data[key]).resolve())
            except (OSError, ValueError, TypeError) as exc:
                value['issues'].append(str(exc))
            value['issues'].append('no compatible management runtime installed; full configuration validation unavailable')
            value['configuration'] = 'attention_required'
            return value
        result = [info(Path(e['root'])) for e in registry['projects']] if args[0] == 'list' else info(layout.resolve_root(options.project))
    except (OSError, ValueError) as exc:
        raise InstallError(str(exc)) from exc
    if options.json:
        print(json.dumps(result, indent=2))
    elif isinstance(result, list):
        for item in result:
            print(f"{item['name']}  {item['verification_root']}  {item.get('nsim_version', '-')}  attention_required")
    else:
        for key, value in result.items():
            print(f'{key}: {value}')
    return 0


def project_command(root, args):
    if args[0] == 'update' and any(a in ('--bundle', '--to', '--sha256') for a in args):
        raise InstallError('nsim update edits project configuration; use nsim install for tool installation')
    version = management_version(root)
    if version is None:
        if args[0] in ('list', 'status'):
            return basic_query(args, root)
        raise InstallError('no installed runtime supports project management; run nsim install VERSION')
    exe = root / 'versions' / version / 'venv' / 'bin' / 'nsim'
    record = receipt(root, version)
    environment = dict(os.environ, NSIM_HOME=str(root), NSIM_WHEEL_SHA256=record['wheel_sha256'])
    os.execve(exe, [str(exe), *args], environment)


def parser_for(root):
    parser = argparse.ArgumentParser(prog="nsim-install", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap_cmd = commands.add_parser("bootstrap", help="install the stable nsim launcher")
    bootstrap_cmd.add_argument("--root", type=Path, default=root)
    install = commands.add_parser("install", help="install a version from GitHub, a bundle, or local wheels")
    install.add_argument("version", nargs="?")
    install.add_argument("--all", action="store_true", help="install all published stable releases")
    install.add_argument("--bundle", type=Path)
    install.add_argument("--wheel", type=Path)
    install.add_argument("--wheelhouse", type=Path)
    install.add_argument("--checksums", type=Path)
    install.add_argument("--sha256", help="expected SHA-256 of the bundle or local wheel")
    install.add_argument("--root", type=Path, default=root)
    update = commands.add_parser("update", help="download/install a version without switching a project")
    update.add_argument("--to", dest="version")
    update.add_argument("--bundle", type=Path)
    update.add_argument("--sha256")
    update.add_argument("--root", type=Path, default=root)
    switch = commands.add_parser("switch", help="select an installed version for one project")
    switch.add_argument("version")
    switch.add_argument("--project", type=Path, required=True)
    switch.add_argument("--root", type=Path, default=root)
    versions = commands.add_parser("versions", help="list installed versions")
    versions.add_argument("--remote", action="store_true")
    versions.add_argument("--root", type=Path, default=root)
    bundle = commands.add_parser("bundle", help="prepare an offline installation bundle")
    actions = bundle.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create")
    create.add_argument("--wheel", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--wheelhouse", type=Path)
    create.add_argument("--download-deps", action="store_true")
    for name in ("platform", "python-version", "implementation", "abi"):
        create.add_argument("--" + name)
    return parser


def manage(args, root):
    options = parser_for(root).parse_args(args)
    root = Path(getattr(options, "root", root)).expanduser().resolve()
    if options.command == "bootstrap":
        print(f"launcher installed: {bootstrap(root) / 'bin' / 'nsim'}")
        return
    if options.command == "bundle":
        target = []
        selected = [getattr(options, name) for name in ("platform", "python_version", "implementation", "abi")]
        if any(selected) and not all(selected):
            raise InstallError("cross-target download requires platform, python-version, implementation, and abi")
        for name in ("platform", "python_version", "implementation", "abi"):
            value = getattr(options, name)
            if value:
                target.extend(["--" + name.replace("_", "-"), value])
        if target and not options.download_deps:
            raise InstallError("target options require --download-deps")
        make_bundle(options.wheel, options.output, options.wheelhouse, options.download_deps, target)
        return
    if options.command == "versions":
        print("installed: " + (", ".join(installed_versions(root)) or "none"))
        if options.remote:
            print("remote: " + (", ".join(remote_versions()) or "none"))
        return
    if options.command == "switch":
        version = valid_version(options.version)
        if not receipt(root, version):
            raise InstallError(f"nsim {version} is not installed; run nsim install {version}")
        executable = root / "versions" / version / "venv" / "bin" / "nsim"
        probe = subprocess.run([str(executable), "--version"], capture_output=True, text=True, check=True)
        if probe.stdout.strip() != f"nsim {version}":
            raise InstallError(f"nsim {version} is not runnable")
        project = project_directory(options.project)
        if project_layout().descriptor_path(project).parent.name == '.nsim':
            project_command(root, ['switch', version, '--project', str(project)])
            return
        atomic_bytes(project / PIN, (version + "\n").encode())
        print(f"{project}: nsim {version}")
        return
    if options.command == "update":
        if options.sha256 and not options.bundle:
            raise InstallError("--sha256 requires --bundle")
        if options.bundle:
            install_bundle(root, options.bundle, options.version, options.sha256)
        elif options.version:
            install_remote(root, options.version)
        else:
            print("remote: " + (", ".join(remote_versions()) or "none"))
        return
    if options.command == "install":
        sources = int(bool(options.bundle)) + int(bool(options.wheel)) + int(bool(options.all))
        if sources > 1:
            raise InstallError("choose one of --bundle, --wheel, or --all")
        if (options.all or not options.bundle and not options.wheel) and any(
                (options.wheelhouse, options.checksums, options.sha256)):
            raise InstallError("wheelhouse/checksums/sha256 require a local wheel or bundle")
        if options.bundle and (options.wheelhouse or options.checksums):
            raise InstallError("bundle already contains its wheelhouse and hashes")
        if options.all:
            if options.version:
                raise InstallError("--all cannot be combined with a version")
            available = remote_versions()
            if not available:
                raise InstallError("no published stable nsim releases found")
            for version in available:
                install_remote(root, version)
        elif options.bundle:
            install_bundle(root, options.bundle, options.version, options.sha256)
        elif options.wheel:
            expected = options.sha256 or (checksum_for(options.wheel, options.checksums)
                                          if options.checksums else None)
            install_wheel(root, options.wheel, options.version, offline=True,
                          links=[options.wheelhouse] if options.wheelhouse else (), expected=expected)
        elif options.version:
            install_remote(root, options.version)
        else:
            raise InstallError("specify VERSION, --bundle, --wheel, or --all")


def main(argv=None):
    if sys.version_info < (3, 9):
        print("nsim installer requires Python 3.9 or newer", file=sys.stderr)
        return 2
    argv = list(sys.argv[1:] if argv is None else argv)
    root = default_root()
    try:
        if Path(sys.argv[0]).name == 'nsim' and argv and argv[0] in ('bootstrap', 'list', 'status', 'update', 'switch'):
            return project_command(root, argv)
        if argv and argv[0] in MANAGEMENT:
            manage(argv, root)
            return 0
        if Path(sys.argv[0]).name == "nsim":
            launch(root, argv)
        parser_for(root).parse_args(argv)
        return 0
    except (InstallError, OSError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError,
            zipfile.BadZipFile, UnicodeError) as exc:
        print(f"nsim install error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
