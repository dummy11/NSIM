"""Bounded streaming VCS/UVM diagnostics, shared by local and farm workers."""

import hashlib
from pathlib import Path
import re
import socket
import os
import stat
import time

from nsim.config import ConfigError, fields, integer, number, strings

SEVERITIES = ('warning', 'error', 'fatal')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
SUMMARY = re.compile(r'^\s*UVM_(WARNING|ERROR|FATAL)\s*:\s*(\d+)\s*$')
UVM = re.compile(r'^\s*UVM_(WARNING|ERROR|FATAL)(?:\([^)]*\))?\s+(.*?)\s*@\s*([^:]+):\s*(.*?)\s*\[([^]]*)\]\s*(.*)$')
VCS = re.compile(r'^\s*(Warning|Error|Fatal)-\[([^]]+)\]\s*(.*)$', re.I)
RUNTIME = re.compile(r'^\s*(Warning|Error|Fatal):\s*(.+)$', re.I)
SOURCE_SUFFIX = r'(?:svh?|vh?|vhd|vhdl|c|cpp|h)'
SOURCE_PATH = rf'(?P<path>[^,\r\n:()]+?\.{SOURCE_SUFFIX})'
SIMPLE_SOURCE_PATH = rf'(?P<path>[^,\s:()]+?\.{SOURCE_SUFFIX})'
SOURCE_LINE = r'(?:\((?P<paren>\d+)\)|\s*[:,]\s*(?P<number>\d+))(?=$|[\s,:;)\]])'
QUOTED_LOCATION = re.compile(
    rf'(?P<quote>["\'])(?P<path>[^"\'\r\n]+?\.{SOURCE_SUFFIX})(?P=quote)\s*'
    r'(?:,\s*(?P<number>\d+)|:\s*(?P<colon>\d+)|\(\s*(?P<paren>\d+)\s*\))'
    r'(?=$|[\s,:;)\]])', re.I)
AT_LOCATION = re.compile(r'\bat\s+' + SOURCE_PATH + SOURCE_LINE, re.I)
SIMPLE_MARKED_LOCATION = re.compile(
    r'\b(?:in|from)\s+' + SIMPLE_SOURCE_PATH + SOURCE_LINE, re.I)
BARE_LOCATION = re.compile(r'(?:^|[:,;(\[])\s*' + SIMPLE_SOURCE_PATH + SOURCE_LINE, re.I)


def config(value):
    fields(value, {'files', 'compile_files', 'max_diagnostics', 'max_scan_bytes', 'settle_seconds', 'error_filter'}, set(), 'log_analysis')
    result = dict(files=strings(value.get('files', ['run.log']), 'log_analysis.files', True),
                  compile_files=strings(value.get('compile_files', ['compile.log']), 'compile_files', True),
                  max_diagnostics=integer(value.get('max_diagnostics', 100), 'max_diagnostics', 1, 10000),
                  max_scan_bytes=integer(value.get('max_scan_bytes', 1024 ** 3), 'max_scan_bytes', 1),
                  settle_seconds=number(value.get('settle_seconds', .5), 'log_analysis.settle_seconds'),
                  error_filter=value.get('error_filter'))
    for key in ('files', 'compile_files'):
        result[key] = list(dict.fromkeys(result[key]))
        for item in result[key]:
            path = Path(item)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0].startswith('.'):
                raise ConfigError(f'{key}: log must be a relative path inside the stage directory: {item}')
            if path.name in {'log_analysis.json', 'analysis.json', 'run.json', 'hooks.json'}:
                raise ConfigError(f'{key}: analysis output cannot be its own input')
    if result['error_filter'] is not None:
        from nsim.error_filter import validate_logs
        if not isinstance(result['error_filter'], dict) or not {'path', 'sha256', 'rules'} <= result['error_filter'].keys():
            raise ConfigError('error_filter must name a valid rule file in submit')
        validate_logs(result['error_filter'], result)
    return result


def location(text):
    # Unquoted paths containing spaces need a clear left boundary. Never fall
    # back to the last whitespace-delimited suffix, which invents a file path.
    for pattern in (QUOTED_LOCATION, AT_LOCATION, SIMPLE_MARKED_LOCATION, BARE_LOCATION):
        match = pattern.search(text)
        if not match:
            continue
        path = match.group('path').strip()
        if pattern is BARE_LOCATION and re.match(r'(?i)^(?:error|fatal|warning|in|from|at)\b', path):
            continue
        line = next((match.group(key) for key in ('number', 'colon', 'paren')
                     if key in match.re.groupindex and match.group(key)), None)
        if path and line:
            return {'source_file': path, 'source_line': int(line)}
    return {}


def snapshot(work, settings, phase):
    result = {}
    for name in settings['compile_files' if phase == 'compile' else 'files']:
        path = Path(work) / name
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ConfigError(f'refusing symlink log: {path}')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError(f'log is not a regular file: {path}')
        result[str(path)] = (info.st_size, info.st_mtime_ns, info.st_ino)
    return result


def wait_stable(work, settings, phase, cancel):
    deadline = time.monotonic() + settings['settle_seconds']
    previous = None
    while True:
        try:
            current = snapshot(work, settings, phase)
            if current == previous or settings['settle_seconds'] == 0:
                return current, None
            previous = current
        except (OSError, ValueError) as exc:
            previous = None
            if time.monotonic() >= deadline:
                return None, str(exc)
        if cancel.is_set() or time.monotonic() >= deadline:
            return None, 'logs did not become stable before the deadline or were cancelled'
        cancel.wait(min(.05, max(0, deadline - time.monotonic())))


def validate_custom(value, limit):
    fields(value, {'schema_version', 'complete', 'counts', 'diagnostics'},
           {'schema_version', 'complete', 'counts', 'diagnostics'}, 'custom log analysis')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or type(value['complete']) is not bool:
        raise ConfigError('invalid custom log analysis version or complete flag')
    fields(value['counts'], SEVERITIES, SEVERITIES, 'analysis counts')
    for key in SEVERITIES:
        integer(value['counts'][key], 'analysis count')
    if not isinstance(value['diagnostics'], list) or len(value['diagnostics']) > limit:
        raise ConfigError('custom diagnostics must be a bounded array')
    observed = dict.fromkeys(SEVERITIES, 0)
    for item in value['diagnostics']:
        fields(item, {'severity', 'message', 'code', 'source_file', 'source_line', 'log_file', 'log_line'},
               {'severity', 'message'}, 'diagnostic')
        if item['severity'] not in SEVERITIES:
            raise ConfigError('invalid diagnostic severity')
        observed[item['severity']] += 1
        for key in ('message', 'code', 'source_file', 'log_file'):
            if key in item and (not isinstance(item[key], str) or len(item[key]) > 4096):
                raise ConfigError(f'invalid diagnostic {key}')
        for key in ('source_line', 'log_line'):
            if key in item:
                integer(item[key], key, 1)
    if any(observed[key] > value['counts'][key] for key in SEVERITIES):
        raise ConfigError('diagnostic counts are inconsistent')
    return value


def empty_report(phase, task, custom=False):
    return {'schema_version': 1, 'phase': phase, 'case': task['case'] if phase == 'simulate' else None,
              'seed': task['seed'] if phase == 'simulate' else None, 'complete': True,
              'analyzer': 'custom' if custom else 'vcs_uvm', 'host': socket.gethostname(), 'pid': os.getpid(),
              'counts': dict.fromkeys(SEVERITIES, 0), 'diagnostics': [], 'files': [], 'issues': [],
              'diagnostics_truncated': 0, 'first_failure': None,
              'blocking_counts': {'error': 0, 'fatal': 0}, 'waived_counts': {'error': 0, 'fatal': 0},
              'first_blocking': None, 'waivers': [], 'waivers_truncated': 0}


def scan(work, settings, phase, task, cancel=None, custom=None):
    """Scan at most max_scan_bytes with bounded line/context storage; hash in the same pass."""
    work = Path(work)
    result = empty_report(phase, task, custom is not None)
    filter_data = settings.get('error_filter')
    result['error_filter_sha256'] = filter_data['sha256'] if filter_data else None
    remaining = settings['max_scan_bytes']
    evidence = {}

    def emit(item, occurrences=1):
        if item['severity'] in ('error', 'fatal') and result['first_failure'] is None:
            result['first_failure'] = item
        if item['severity'] in ('error', 'fatal'):
            from nsim.error_filter import match
            try:
                source = Path(item['log_file'])
                log_file = str(source.relative_to(work)) if source.is_absolute() else str(source)
            except (KeyError, ValueError):
                log_file = None
            rule = match(filter_data, phase, log_file, item) if filter_data and occurrences == 1 else None
            if rule:
                result['waived_counts'][item['severity']] += 1
                if len(result['waivers']) < 8:
                    result['waivers'].append(dict(rule=rule['number'], code=item['code'],
                                                  log_file=log_file, log_line=item.get('log_line'),
                                                  reason=rule['reason']))
                else:
                    result['waivers_truncated'] += 1
            else:
                result['blocking_counts'][item['severity']] += occurrences
                if result['first_blocking'] is None:
                    result['first_blocking'] = item
        if len(result['diagnostics']) < settings['max_diagnostics']:
            result['diagnostics'].append(item)
        else:
            result['diagnostics_truncated'] += 1

    for name in settings['compile_files' if phase == 'compile' else 'files']:
        path = work / name
        entry = {'path': str(path), 'bytes': 0, 'lines': 0, 'complete': False}
        result['files'].append(entry)
        before = None
        try:
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ConfigError(f'refusing symlink log: {path}')
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise ConfigError(f'log is not a regular file: {path}')
            hashed = hashlib.sha256()
            counts = dict.fromkeys(SEVERITIES, 0)
            uvm_counts = dict.fromkeys(SEVERITIES, 0)
            summaries = dict.fromkeys(SEVERITIES, 0)
            summary_lines = {}
            pending = None
            context_lines = 0
            with path.open('rb') as stream:
                while remaining > 0 and not (cancel and cancel.is_set()):
                    raw = stream.readline(min(65536, remaining))
                    if not raw:
                        break
                    hashed.update(raw)
                    remaining -= len(raw)
                    entry['bytes'] += len(raw)
                    entry['lines'] += 1
                    # Drain an oversized physical line without allocating its entire body.
                    head = raw
                    while raw and not raw.endswith(b'\n') and remaining > 0 and not (cancel and cancel.is_set()):
                        raw = stream.readline(min(65536, remaining))
                        hashed.update(raw)
                        remaining -= len(raw)
                        entry['bytes'] += len(raw)
                    if custom is not None:
                        continue
                    line = ANSI.sub('', head.decode('utf-8', errors='replace')).rstrip('\r\n')
                    summary = SUMMARY.match(line)
                    uvm = UVM.match(line)
                    vcs = VCS.match(line)
                    runtime = RUNTIME.match(line)
                    if summary or uvm or vcs or runtime or line.lstrip().startswith('UVM_INFO') or not line.strip():
                        if pending:
                            emit(pending)
                            pending = None
                    if summary:
                        severity = summary[1].lower()
                        summaries[severity] = max(summaries[severity], int(summary[2]))
                        summary_lines[severity] = entry['lines']
                    elif uvm or vcs or runtime:
                        matched = uvm or vcs or runtime
                        severity = matched[1].lower()
                        if uvm:
                            uvm_counts[severity] += 1
                            message = uvm[6]
                            details = dict(code=uvm[5], simulation_time=uvm[3], component=uvm[4], **location(uvm[2]))
                        else:
                            counts[severity] += 1
                            message = vcs[3] if vcs else runtime[2]
                            details = dict(code=vcs[2] if vcs else 'runtime', **location(message))
                        pending = dict(severity=severity, message=message[:4096], log_file=str(path),
                                       log_line=entry['lines'], **details)
                        context_lines = 0
                    elif pending and context_lines < 6:
                        # VCS commonly prints the source location on the following line.
                        pending.update({k: v for k, v in location(line).items() if k not in pending})
                        pending['message'] = (pending['message'] + '\n' + line.strip())[:4096]
                        context_lines += 1
                if pending:
                    emit(pending)
                after = path.stat()
                for severity in SEVERITIES:
                    result['counts'][severity] += counts[severity] + max(uvm_counts[severity], summaries[severity])
                    if summaries[severity] > uvm_counts[severity]:
                        emit(dict(severity=severity, code='UVM_SUMMARY', log_file=str(path),
                                  log_line=summary_lines[severity], message=f'UVM {severity} count {summaries[severity]}; individual messages not all present'),
                             summaries[severity] - uvm_counts[severity])
                entry['complete'] = (entry['bytes'] == before.st_size and
                                     (before.st_size, before.st_mtime_ns, before.st_ino) ==
                                     (after.st_size, after.st_mtime_ns, after.st_ino))
                if not entry['complete']:
                    raise ConfigError(f'log scan incomplete, interrupted, over budget or log changed: {path}')
                entry['sha256'] = hashed.hexdigest()
                evidence[str(path)] = entry['sha256']
        except (OSError, ValueError) as exc:
            result['complete'] = False
            result['issues'].append(str(exc))
    if custom is not None:
        result['complete'] = result['complete'] and custom['complete']
        result['counts'] = custom['counts']
        for item in custom['diagnostics']:
            emit(item)
        for severity in ('error', 'fatal'):
            undisclosed = custom['counts'][severity] - result['blocking_counts'][severity] - result['waived_counts'][severity]
            result['blocking_counts'][severity] += max(0, undisclosed)
            if undisclosed > 0 and result['first_blocking'] is None:
                result['first_blocking'] = dict(severity=severity, message='custom analysis did not include every diagnostic')
    result['failure_reason'] = (result['first_blocking']['message'] if result['first_blocking'] else
                                next(iter(result['issues']), 'log analysis incomplete' if not result['complete'] else
                                     'all log errors waived' if result['waived_counts']['error'] else 'no log errors'))
    return result, evidence


def short_reason(value):
    """Keep one bounded line; context and repeated messages belong in logs."""
    if value is None:
        return None
    line = ' '.join(str(value).strip().split('\n', 1)[0].split())
    return line if len(line) <= 512 else line[:509] + '...'


def first_location(diagnostic):
    return {key: value for key, value in (diagnostic or {}).items()
            if key in ('source_file', 'source_line', 'log_file', 'log_line')} or None


def brief(report, path):
    return dict(complete=report['complete'], counts=report['counts'],
                first_failure=first_location(report['first_failure']),
                first_blocking=first_location(report.get('first_blocking')),
                failure_reason=short_reason(report['failure_reason']),
                blocking_counts=report.get('blocking_counts', {'error': report['counts']['error'], 'fatal': report['counts']['fatal']}),
                waived_counts=report.get('waived_counts', {'error': 0, 'fatal': 0}),
                waivers=report.get('waivers', []), waivers_truncated=report.get('waivers_truncated', 0),
                error_filter_sha256=report.get('error_filter_sha256'),
                analyzer=report['analyzer'], path=str(path))


def case_summary(task, attempt):
    """Public case result, independent of diagnostic count and log volume."""
    reports = {'compile': attempt.get('compile_log_analysis'), 'simulation': attempt.get('log_analysis')}
    first_error = None
    first_blocking = None
    for phase, report in reports.items():
        if report and report['counts']['error'] + report['counts']['fatal']:
            location = first_location(report.get('first_failure'))
            if location:
                first_error = dict(phase=phase, **location)
            break
    for phase, report in reports.items():
        if report and report.get('first_blocking'):
            location = first_location(report['first_blocking'])
            if location:
                first_blocking = dict(phase=phase, **location)
            break
    return dict(schema_version=3, case=task['case'], seed=task['seed'],
                verdict=attempt['verdict'], reason=short_reason(attempt.get('reason')),
                first_error=first_error, first_blocking=first_blocking,
                result_verdict=attempt.get('result_verdict'), result_reason=short_reason(attempt.get('result_reason')),
                counts={phase: report['counts'] if report else None for phase, report in reports.items()},
                blocking_counts={phase: report.get('blocking_counts') if report else None for phase, report in reports.items()},
                waived_counts={phase: report.get('waived_counts') if report else None for phase, report in reports.items()},
                waivers={phase: report.get('waivers', []) if report else [] for phase, report in reports.items()},
                error_filter_sha256={phase: report.get('error_filter_sha256') if report else None for phase, report in reports.items()},
                scan_complete={phase: report['complete'] if report else None for phase, report in reports.items()})
