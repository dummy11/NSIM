"""Logical cases, explicit runtime mappings and deterministic parameter overlays."""

from copy import deepcopy
from pathlib import Path
import re
import shlex
import string

import yaml

from nsim.config import ConfigError, StrictLoader, fields, integer, name, strings


BUILTINS = {
    'uvm_test': {'argv': ['+UVM_TESTNAME={value}']},
    'seq': {'argv': ['+SEQ_NAME={value}']},
    'c_test': {'argv': ['+C_TEST={value}']},
}
RESERVED = {'case', 'plusargs', 'seed', 'result', 'sequence'}
MANAGED = {'+ntb_random_seed', '+NSIM_RESULT'}
RUNTIME = {'case', 'seed', 'result', 'project', 'wave_dir', 'build_dir', 'wave', 'log', 'dpi_library'}


def field_name(key):
    if key == 'sequence':
        raise ConfigError('use seq, not sequence, for the sequence field')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or key in RESERVED:
        raise ConfigError(f'invalid case field: {key}')
    return key


def template(value, allowed):
    try:
        for _, key, spec, conv in string.Formatter().parse(value):
            if key == 'file' and spec and not conv:
                name(spec, 'file reference')
                continue  # Resolved against the selected submit before case expansion.
            if key is not None and (key not in allowed or spec or conv):
                raise ConfigError(f'invalid case placeholder: {value}')
    except ValueError as exc:
        raise ConfigError(f'invalid case template: {value}') from exc
    return value


def layer(value, label, mappings=None):
    if not isinstance(value, dict):
        raise ConfigError(f'{label}: expected an object')
    result = {}
    for key, item in value.items():
        if key == 'plusargs':
            result[key] = strings(item, f'{label}.plusargs')
            for arg in result[key]:
                if not arg.startswith('+'):
                    raise ConfigError(f'{label}: plusargs must start with +: {arg}')
                template(arg, {'case'})
            continue
        field_name(key)
        if mappings is not None and key not in mappings:
            raise ConfigError(f'{label}: unknown case field {key}; declare it in case.yaml mappings')
        if item is not None:
            if not isinstance(item, str) or not item or '\0' in item:
                raise ConfigError(f'{label}.{key}: expected a non-empty string or null')
            template(item, {'case'})
        result[key] = item
    return result


def assignments(tokens, label):
    result, plusargs = {}, []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.startswith('+'):
            plusargs.append(token)
            continue
        if '=' not in token:
            if index >= len(tokens) or not tokens[index].startswith('='):
                raise ConfigError(f'{label}: expected field=value or +KEY=VALUE, got {token}')
            token += tokens[index]
            index += 1
        key, value = token.split('=', 1)
        if not value and index < len(tokens):
            value = tokens[index]
            index += 1
        field_name(key)
        if key in result:
            raise ConfigError(f'{label}: duplicate case field {key}')
        if not value:
            raise ConfigError(f'{label}: missing value for {key}')
        result[key] = None if value == 'null' else value
    if plusargs:
        result['plusargs'] = plusargs
    return result


def read_bytes(path, sources):
    key = str(path)
    if key not in sources:
        try:
            sources[key] = path.read_bytes()
        except OSError as exc:
            raise ConfigError(f'cannot read case configuration {path}: {exc}') from exc
    return sources[key]


def read_config(path, sources):
    try:
        data = yaml.load(read_bytes(path, sources).decode('utf-8'), Loader=StrictLoader)
    except (yaml.YAMLError, UnicodeError, TypeError) as exc:
        raise ConfigError(f'{path}: {exc}') from exc
    fields(data, {'schema_version', 'mappings', 'cases'}, {'schema_version'}, str(path))
    if integer(data['schema_version'], 'case schema_version') != 1:
        raise ConfigError(f'{path}: unsupported case schema version')
    mappings = deepcopy(BUILTINS)
    custom = data.get('mappings', {})
    if not isinstance(custom, dict):
        raise ConfigError(f'{path}: mappings must be an object')
    for key, mapping in custom.items():
        field_name(key)
        fields(mapping, {'argv'}, {'argv'}, f'{path}: mapping {key}')
        args = strings(mapping['argv'], f'mapping {key}.argv', nonempty=True)
        for arg in args:
            template(arg, {'value', 'case'})
        # Option names must be static so aliases and null have unambiguous keys.
        for option in argument_groups(args):
            if option.startswith('@') or '{' in option or '}' in option:
                raise ConfigError(f'{path}: mapping {key} requires static option names')
        mappings[key] = {'argv': args}
    cases = data.get('cases', {})
    if not isinstance(cases, dict):
        raise ConfigError(f'{path}: cases must be an object')
    for case, config in cases.items():
        name(case, 'logical case name')
        layer(config, f'{path}: {case}', mappings)
    return mappings, cases


def load_caselist(path, sources=None):
    path = Path(path).resolve()
    sources = {} if sources is None else sources
    try:
        content = read_bytes(path, sources).decode('utf-8')
    except UnicodeError as exc:
        raise ConfigError(f'{path}: {exc}') from exc
    defaults, config_path, rows = None, None, []
    for number, line in enumerate(content.splitlines(), 1):
        label = f'{path}:{number}'
        try:
            tokens = shlex.split(line, comments=True, posix=True)
        except ValueError as exc:
            raise ConfigError(f'{label}: {exc}') from exc
        if not tokens:
            continue
        if tokens[0].startswith('@'):
            if rows:
                raise ConfigError(f'{label}: directives must precede cases')
            if tokens[0] == '@defaults' and defaults is None:
                defaults = assignments(tokens[1:], label)
            elif tokens[0] == '@config' and config_path is None and len(tokens) == 2:
                config_path = (path.parent / tokens[1]).resolve()
            else:
                raise ConfigError(f'{label}: invalid or repeated directive {tokens[0]}')
            continue
        case = name(tokens[0], 'logical case name')
        rows.append((case, assignments(tokens[1:], label), label))
    if not rows:
        raise ConfigError(f'empty caselist: {path}')
    mappings, overrides = read_config(config_path, sources) if config_path else (deepcopy(BUILTINS), {})
    default_layer = layer(defaults or {}, f'{path}: @defaults', mappings)
    legacy = defaults is None and config_path is None and all(not row[1] for row in rows)
    return [dict(case=case, defaults=default_layer, inline=layer(values, label, mappings),
                 override=overrides.get(case, {}), mappings=mappings, legacy=legacy, source=label,
                 config=str(config_path) if config_path else None)
            for case, values, label in rows]


def simple_case(case):
    return dict(case=case, defaults={}, inline={}, override={}, mappings=deepcopy(BUILTINS),
                legacy=True, source='--case', config=None)


def is_option(arg):
    return arg.startswith('+') or re.match(r'--?[A-Za-z_]', arg) is not None


def argument_groups(argv):
    """Group single-valued plusargs/options; keep positional argv tokens in order."""
    result = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        index += 1
        if is_option(token):
            key = token.split('=', 1)[0]
            if any(char.isspace() or char in '{}' for char in key) or key == '+':
                raise ConfigError(f'invalid option name: {key}')
            group = [token]
            if token.startswith('-') and '=' not in token:
                while index < len(argv) and not is_option(argv[index]):
                    group.append(argv[index])
                    index += 1
        else:
            key, group = f'@{index}', [token]
        if key in result and result[key] != group:
            raise ConfigError(f'conflicting values for {key} in the same layer')
        result[key] = group
    return result


def escaped(value):
    # Caselist data is expanded once at planning; later runtime formatting must
    # never interpret braces introduced by values or literal escaped braces.
    return value.replace('{', '{{').replace('}', '}}')


def bind_case(value, case):
    return ''.join(escaped(literal) + (escaped(case) if key == 'case' else '{' + key + '}' if key else '')
                   for literal, key, _, _ in string.Formatter().parse(value))


def resolve_case(record, submit, plusargs=()):
    case, mappings = record['case'], record['mappings']
    if submit['adapter'] == 'vcs':
        base_args = submit['vcs']['run_args'] + submit['plusargs']
        if submit['vcs'].get('fsdb'):
            base_args += submit['vcs']['fsdb_plusargs']
    elif submit['adapter'] == 'command':
        base_args = submit['argv'][1:] + submit['plusargs']
    else:
        base_args = submit['plusargs']
    for arg in base_args:
        template(arg, RUNTIME if submit['adapter'] == 'vcs' else RUNTIME - {'wave', 'log', 'dpi_library'})
    base = argument_groups([bind_case(arg, case) for arg in base_args])
    default = layer(submit.get('case_defaults', {}), 'submit.case_defaults', mappings)
    values, origins = {}, {}
    # Preserve old VCS name-only lists without injecting UVM into explicit new
    # syntax or a submit that already declares an entry mechanism.
    entry_keys = {key for field in BUILTINS for key in argument_groups(mappings[field]['argv'])}
    if record['legacy'] and submit['adapter'] == 'vcs' and not default and not entry_keys.intersection(base):
        values['+UVM_TESTNAME'] = [escaped('+UVM_TESTNAME=' + case)]
        origins['+UVM_TESTNAME'] = 'legacy'

    def expand(config):
        output = {}
        def add(key, group):
            if key in output and output[key] != group:
                raise ConfigError(f'conflicting values for {key} in the same layer')
            output[key] = group
        for field, value in config.items():
            if field == 'plusargs':
                continue
            expanded = value.format(case=case) if value is not None else ''
            for key, group in argument_groups(mappings[field]['argv']).items():
                args = [escaped(arg.format(value=expanded, case=case)) for arg in group]
                add(key, args if value is not None else None)
        raw = [escaped(arg.format(case=case)) for arg in config.get('plusargs', [])]
        for key, group in argument_groups(raw).items():
            add(key, group)
        return output

    try:
        submit_layer = dict(base)
        for key, group in expand(default).items():
            if key in submit_layer and submit_layer[key] != group:
                raise ConfigError(f'conflicting values for {key} in submit')
            submit_layer[key] = group
        layers = [('submit', submit_layer), ('defaults', expand(record['defaults'])),
                  ('inline', expand(record['inline'])), ('case.yaml', expand(record['override'])),
                  ('cli', expand(layer({'plusargs': list(plusargs)}, 'CLI plusargs', mappings)))]
        for source, params in layers:
            for key, group in params.items():
                if key in MANAGED and not (submit['adapter'] == 'command' and source == 'submit'):
                    raise ConfigError(f'{key} is managed by NSIM; configure seed/result through NSIM')
                if group is None:
                    values.pop(key, None)
                    origins.pop(key, None)
                else:
                    values[key] = group
                    origins[key] = source
    except ConfigError as exc:
        raise ConfigError(f"{record['source']} ({case}): {exc}") from exc
    return {'argv': [arg for group in values.values() for arg in group], 'origins': origins}
