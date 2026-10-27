"""Strict project/submit loading, independent of execution."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional

from nsim.project_paths import ProjectError, descriptor_path

import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class ConfigError(ValueError):
    """Invalid user input, before any run is created."""


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"duplicate key: {key}")
        result[key] = value
    return result


class StrictLoader(yaml.SafeLoader):
    pass


def yaml_mapping(loader, node):
    loader.flatten_mapping(node)
    return unique_pairs((loader.construct_object(k), loader.construct_object(v)) for k, v in node.value)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs,
                          parse_constant=lambda value: _invalid(f"invalid number: {value}"))
    except (ValueError, UnicodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _invalid(message):
    raise ConfigError(message)


def fields(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ConfigError(f"{label}: expected an object")
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown or missing:
        raise ConfigError(f"{label}: unknown fields {sorted(unknown, key=str)}, missing {sorted(missing)}")


def name(value, label="name"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ConfigError(f"{label}: expected a safe non-empty identifier")
    return value


def integer(value, label, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ConfigError(f"{label}: expected integer >= {minimum}" + (f" and <= {maximum}" if maximum else ""))
    return value


def number(value, label, minimum=0):
    if type(value) not in (float, int) or not math.isfinite(value) or value < minimum:
        raise ConfigError(f"{label}: expected finite number >= {minimum}")
    return value


def strings(value, label, nonempty=False):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x or "\0" in x for x in value):
        raise ConfigError(f"{label}: expected a list of non-empty strings")
    if nonempty and not value:
        raise ConfigError(f"{label}: cannot be empty")
    return value


@dataclass(frozen=True)
class Project:
    descriptor: Path
    name: str
    inputs: List[Path]
    source_root: Path
    schema_version: int = 1
    rtl_root: Optional[Path] = None
    rtl_filelist: Optional[Path] = None
    verification_filelist: Optional[Path] = None

    @property
    def root(self):
        return self.source_root


def load_project(path: Path, validate=True) -> Project:
    try:
        path = descriptor_path(path)
    except ProjectError as exc:
        raise ConfigError(str(exc)) from exc
    try:
        if path.name == "nsim.toml":
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        elif path.name == "nsim.yaml":
            data = yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)
        else:
            raise ConfigError("project descriptor must be named nsim.toml or nsim.yaml")
    except (ValueError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError('project: expected an object')
    schema = integer(data.get('schema_version'), 'schema_version')
    if schema == 2:
        fields(data, {'schema_version', 'name', 'inputs', 'rtl_root', 'rtl_filelist', 'verification_filelist'},
               {'schema_version', 'name', 'rtl_root', 'rtl_filelist', 'verification_filelist'}, 'project')
        if path.parent.name != '.nsim' or path.name != 'nsim.toml':
            raise ConfigError('schema 2 requires verification/.nsim/nsim.toml')
        root = path.parent.parent
        if name(data['name'], 'project name') != root.name:
            raise ConfigError('project name must match verification directory name')
        resolved = {}
        for key in ('rtl_root', 'rtl_filelist', 'verification_filelist'):
            if not isinstance(data[key], str) or not data[key] or '\0' in data[key]:
                raise ConfigError(f'{key}: expected a path string')
            resolved[key] = (root / data[key]).resolve()
        paths = [(root / p).resolve() for p in strings(data.get('inputs', []), 'inputs')]
        if validate:
            if not resolved['rtl_root'].is_dir():
                raise ConfigError(f"rtl_root is not a directory: {resolved['rtl_root']}")
            for p in [*paths, resolved['rtl_filelist'], resolved['verification_filelist']]:
                if not p.is_file():
                    raise ConfigError(f'project input is not a file: {p}')
        return Project(path, data['name'], paths, root, schema, **resolved)
    fields(data, {"schema_version", "name", "inputs", "source_root"}, {"schema_version", "name"}, "project")
    if schema != 1:
        raise ConfigError("unsupported project schema version")
    paths = [(path.parent / x).resolve() for x in strings(data.get("inputs", []), "inputs")]
    for item in paths:
        if validate and not item.is_file():
            raise ConfigError(f"project input is not a file: {item}")
    source = data.get("source_root", ".")
    if not isinstance(source, str) or not source:
        raise ConfigError("source_root must be a path string")
    source_root = (path.parent / source).resolve()
    if validate and not source_root.is_dir():
        raise ConfigError(f"source_root is not a directory: {source_root}")
    return Project(path, name(data["name"], "project name"), paths, source_root)


def discover(roots: List[Path]) -> List[Project]:
    descriptors = set()
    for root in roots:
        if not root.is_dir():
            raise ConfigError(f"search root is not a directory: {root}")
        for pattern in ("nsim.toml", "nsim.yaml"):
            for path in root.rglob(pattern):
                if not any(p in {".git", ".venv", "node_modules"} for p in path.parts):
                    descriptors.add(path.parent.resolve())
    return [load_project(p) for p in sorted(descriptors)]


def load_cases(path: Path) -> List[str]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.partition("#")[0].strip()
        if value:
            name(value, "case")
            if value not in cases:
                cases.append(value)
    if not cases:
        raise ConfigError(f"empty caselist: {path}")
    return cases


def load_submit(path: Path) -> Dict[str, Any]:
    data = read_json(path)
    fields(data, {"schema_version", "name", "adapter", "argv", "env", "plusargs", "required_checks",
                  "timeout_seconds", "settle_seconds", "fake", "vcs", "scheduler"},
           {"schema_version", "name", "adapter"}, "submit")
    if integer(data["schema_version"], "schema_version") != 1:
        raise ConfigError("unsupported submit schema version")
    name(data["name"], "submit name")
    if data["adapter"] not in ("fake", "command", "vcs"):
        raise ConfigError("supported adapters: fake, command, vcs")
    argv = strings(data.get("argv", []), "argv")
    if (data["adapter"] == "command") != bool(argv):
        raise ConfigError("only command adapter requires argv")
    checks = strings(data.get("required_checks", ["functional", "golden"]), "required_checks", True)
    if len(set(checks)) != len(checks):
        raise ConfigError("duplicate required checks")
    for check in checks:
        name(check, "check")
    plusargs = strings(data.get("plusargs", []), "plusargs")
    env = data.get("env", {})
    if not isinstance(env, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
                                      or not isinstance(v, str) or "\0" in v for k, v in env.items()):
        raise ConfigError("env must map environment variable names to strings")
    timeout = number(data.get("timeout_seconds", 60), "timeout_seconds", .01)
    settle = number(data.get("settle_seconds", 0.5), "settle_seconds")
    fake = data.get("fake", {})
    fields(fake, {"outcomes", "durations"}, set(), "fake")
    if fake and data["adapter"] != "fake":
        raise ConfigError("fake settings require the fake adapter")
    for field in ("outcomes", "durations"):
        mapping = fake.get(field, {})
        if not isinstance(mapping, dict):
            raise ConfigError(f"fake.{field} must be an object")
        for case, value in mapping.items():
            name(case, "fake case")
            if field == "durations":
                number(value, "duration")
            elif value not in ("pass", "fail", "no_compare", "missing", "malformed", "crash", "performance_fail"):
                raise ConfigError(f"unknown fake outcome: {value}")
    from nsim.eda_config import scheduler_config, vcs_config
    result = dict(data, required_checks=checks, argv=argv, plusargs=plusargs, env=env,
                  timeout_seconds=timeout, settle_seconds=settle, fake=fake,
                  scheduler=scheduler_config(data.get("scheduler", {})))
    if data["adapter"] == "vcs":
        result["vcs"] = vcs_config(data.get("vcs", {}))
        if "golden" in checks and not result["vcs"]["golden_enabled"]:
            raise ConfigError("required golden check needs explicit vcs.golden_enabled=true")
    elif "vcs" in data:
        raise ConfigError("vcs settings require the vcs adapter")
    return result
