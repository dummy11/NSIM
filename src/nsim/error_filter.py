"""Validated, versioned exceptions for individual log diagnostics."""

import hashlib
from pathlib import Path

from nsim.config import ConfigError, fields, read_json


MAX_RULE_BYTES = 128 * 1024
MAX_RULES = 128


def load(path, content):
    if len(content) > MAX_RULE_BYTES:
        raise ConfigError(f'error_filter is larger than {MAX_RULE_BYTES} bytes: {path}')
    value = read_json(path, content)
    fields(value, {'schema_version', 'rules'}, {'schema_version', 'rules'}, 'error_filter')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ConfigError('error_filter.schema_version must be 1')
    if not isinstance(value['rules'], list) or len(value['rules']) > MAX_RULES:
        raise ConfigError(f'error_filter.rules must be an array of at most {MAX_RULES} rules')
    rules = []
    seen = set()
    for index, rule in enumerate(value['rules'], 1):
        fields(rule, {'phase', 'log_file', 'code', 'message', 'reason'},
               {'phase', 'code', 'message', 'reason'}, f'error_filter rule {index}')
        if rule['phase'] not in ('compile', 'simulate'):
            raise ConfigError(f'error_filter rule {index}: phase must be compile or simulate')
        for key in ('code', 'message', 'reason'):
            item = rule[key]
            if not isinstance(item, str) or not item.strip() or len(item) > 4096 or '\0' in item:
                raise ConfigError(f'error_filter rule {index}: {key} must be non-empty text')
        if '\n' in rule['message'] or '\r' in rule['message']:
            raise ConfigError(f'error_filter rule {index}: message must be one exact log line')
        if rule['code'] == 'UVM_SUMMARY':
            raise ConfigError('error_filter cannot waive an unmatched UVM summary count')
        if 'log_file' in rule:
            name = rule['log_file']
            part = Path(name) if isinstance(name, str) else None
            if not part or part.is_absolute() or '..' in part.parts or not part.parts or part.parts[0].startswith('.'):
                raise ConfigError(f'error_filter rule {index}: invalid log_file')
        identity = tuple(rule.get(key) for key in ('phase', 'log_file', 'code', 'message'))
        if identity in seen:
            raise ConfigError(f'error_filter rule {index}: duplicate match')
        seen.add(identity)
        rules.append(dict(rule, number=index))
    return {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest(), 'rules': rules}


def validate_logs(filter_data, settings):
    for rule in filter_data['rules']:
        if 'log_file' in rule:
            names = settings['compile_files' if rule['phase'] == 'compile' else 'files']
            if rule['log_file'] not in names:
                raise ConfigError(f"error_filter rule {rule['number']}: log_file is not scanned: {rule['log_file']}")


def match(filter_data, phase, log_file, diagnostic):
    if diagnostic['severity'] != 'error' or diagnostic.get('code') == 'UVM_SUMMARY':
        return None
    message = diagnostic['message'].split('\n', 1)[0]
    for rule in filter_data['rules']:
        if (rule['phase'] == phase and
                ('log_file' not in rule or rule['log_file'] == log_file) and
                rule['code'] == diagnostic.get('code') and rule['message'] == message):
            return rule
    return None
