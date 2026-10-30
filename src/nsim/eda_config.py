"""Versioned EDA and scheduler configuration validation."""

from nsim.config import ConfigError, fields, integer, name, number, strings


CM_WITH_VALUE = {'-cm', '-cm_hier', '-cm_dir', '-cm_name', '-cm_assert_hier'}


def split_coverage_args(args, label):
    """Move recognized legacy -cm groups out of generic stage arguments."""
    ordinary, coverage = [], []
    index = 0
    while index < len(args):
        token = args[index]
        if token in CM_WITH_VALUE:
            if index + 1 >= len(args) or args[index + 1].startswith('-'):
                raise ConfigError(f'{label}: {token} requires a value')
            coverage.extend((token, args[index + 1]))
            index += 2
        elif token.startswith('-cm'):
            raise ConfigError(f'{label}: unsupported coverage option {token}; move it to vcs.coverage')
        else:
            ordinary.append(token)
            index += 1
    return ordinary, coverage


def coverage_config(value):
    fields(value, {'compile_args', 'run_args'}, set(), 'vcs.coverage')
    result = {key: strings(value.get(key, []), 'vcs.coverage.' + key)
              for key in ('compile_args', 'run_args')}
    for key, args in result.items():
        for index, arg in enumerate(args):
            if arg in CM_WITH_VALUE and (index + 1 >= len(args) or args[index + 1].startswith('-')):
                raise ConfigError(f'vcs.coverage.{key}: {arg} requires a value')
    return result


def text(value, label):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ConfigError(f"{label}: expected non-empty string")
    return value


def scheduler_config(value):
    fields(value, {"kind", "queue", "resources", "licenses", "bsub", "bjobs", "bkill",
                   "poll_seconds", "deadline_seconds", "cancel_seconds", "worker_python",
                   "command_timeout_seconds", "unknown_seconds", "result_wait_seconds"}, set(), "scheduler")
    kind = value.get("kind", "lsf")
    if kind not in ("local", "lsf"):
        raise ConfigError("scheduler kind must be local or lsf")
    licenses = value.get("licenses", {})
    if not isinstance(licenses, dict):
        raise ConfigError("licenses must map resource names to positive counts")
    for key, amount in licenses.items():
        name(key, "license")
        integer(amount, "license count", 1)
    result = dict(value, kind=kind, resources=strings(value.get("resources", []), "resources"), licenses=licenses)
    if "queue" in value:
        text(value["queue"], "queue")
    for key in ("bsub", "bjobs", "bkill"):
        result[key] = text(value.get(key, key), key)
    for key, default, minimum in (("poll_seconds", 1, .01), ("deadline_seconds", 3600, .01),
                                  ("cancel_seconds", 30, .01), ('command_timeout_seconds', 30, .01),
                                  ('unknown_seconds', 30, .01), ('result_wait_seconds', 5, .01)):
        result[key] = number(value.get(key, default), key, minimum)
    if "worker_python" in value:
        text(value["worker_python"], "worker_python")
    return result


def vcs_config(value):
    fields(value, {"executable", "filelist", "top", "compile_args", "run_args", "compile_timeout_seconds",
                   "golden_enabled", "fsdb", "fsdb_plusargs", "fsdb_file", "dpi", "result_argv", "verdi",
                   "coverage"}, set(), "vcs")
    result = dict(value)
    result["executable"] = text(value.get("executable", "vcs"), "vcs.executable")
    if 'filelist' in value:
        text(value['filelist'], 'filelist')
    if "top" in value:
        name(value["top"], "top")
    for key in ("compile_args", "run_args", "result_argv"):
        result[key] = strings(value.get(key, []), key)
    recipe = coverage_config(value.get('coverage', {}))
    for key in ('compile_args', 'run_args'):
        result[key], legacy = split_coverage_args(result[key], 'vcs.' + key)
        if legacy:
            if recipe[key]:
                raise ConfigError(f'vcs.{key} contains -cm options alongside vcs.coverage.{key}')
            recipe[key] = legacy
    default_metrics = 'line+cond+fsm+tgl+branch'
    metrics = next((args[args.index('-cm') + 1] for args in recipe.values() if '-cm' in args),
                   default_metrics)
    for key in ('compile_args', 'run_args'):
        if '-cm' not in recipe[key]:
            recipe[key] = ['-cm', metrics, *recipe[key]]
    result['coverage'] = recipe
    result["verdi"] = text(value.get("verdi", "verdi"), "verdi")
    for key in ("golden_enabled",):
        if type(value.get(key, False)) is not bool:
            raise ConfigError(f"{key} must be boolean")
        result[key] = value.get(key, False)
    if 'fsdb' in value:
        if type(value['fsdb']) is not bool:
            raise ConfigError('fsdb must be boolean')
        result['fsdb'] = value['fsdb']  # Legacy submit policy; new submits omit it.
    result["fsdb_file"] = text(value.get("fsdb_file", "{wave_dir}/wave.fsdb"), "fsdb_file")
    result["fsdb_plusargs"] = strings(value.get("fsdb_plusargs", ["+fsdb", "+NSIM_FSDB={wave}"]), "fsdb_plusargs")
    result["compile_timeout_seconds"] = number(value.get("compile_timeout_seconds", 600), "compile timeout", .01)
    dpi = value.get("dpi")
    if dpi is not None:
        fields(dpi, {"makefile", "make", "cc", "cxx", "scope", "target", "args", "timeout_seconds"}, {"makefile"}, "dpi")
        text(dpi["makefile"], "dpi.makefile")
        scope = dpi.get("scope", "project")
        if scope not in ("project", "regression", "run"):
            raise ConfigError("dpi.scope must be project, regression or run")
        result["dpi"] = dict(dpi, scope=scope, make=text(dpi.get("make", "make"), "make"),
                             cc=text(dpi.get("cc", "cc"), "cc"), cxx=text(dpi.get("cxx", "c++"), "cxx"),
                             target=name(dpi.get("target", "all"), "make target"), args=strings(dpi.get("args", []), "dpi.args"),
                             timeout_seconds=number(dpi.get("timeout_seconds", 300), "dpi timeout", .01))
    return result
