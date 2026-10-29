"""Sequential batches of ordinary regressions, with controller-owned summaries."""

import argparse
from datetime import datetime
import hashlib
import html
import os
from pathlib import Path
import re
import shlex
import sys
import time
from urllib.parse import quote
import uuid

from nsim.config import ConfigError, read_json
from nsim.layout import make_directory
from nsim.planning import check_identity
from nsim.storage import IntegrityError, atomic_write, digest, preflight, verify_files, write_json


class RowParser(argparse.ArgumentParser):
    def error(self, message):
        raise ConfigError(message)

    def exit(self, status=0, message=None):
        raise ConfigError(message or 'help/version is not an executable regression row')


def validate_outer(argv):
    """Reject even explicitly supplied default-valued execution options outside rows."""
    parser = RowParser(add_help=False, allow_abbrev=False)
    from nsim.cli_aliases import add_argument
    add_argument(parser, '-o', '--output-root', action='append')
    command = parser.add_subparsers(dest='command', required=True).add_parser('regr', add_help=False)
    command.add_argument('-rl', '--regr-list', required=True)
    add_argument(command, '--project')
    add_argument(command, '-o', '--output-root', action='append')
    add_argument(command, '--dry-run', action='store_true')
    try:
        parser.parse_args(argv)
    except ConfigError as exc:
        raise ConfigError(f'batch options: put single-regression options inside the list; {exc}') from exc


def new_batch_id(path):
    """Add local creation time and a bounded list label without shortening UUIDs."""
    stamp = datetime.now().strftime('%Y%m%dT%H%M%S')
    label = re.sub(r'[^A-Za-z0-9_.-]+', '-', path.stem).strip('._-')[:48] or 'batch'
    return f'{label}_{stamp}_{uuid.uuid4().hex}'


def prepare_batch(outer):
    from nsim.cli import build_parser, prepare_execution
    from nsim.service import validate_roots
    path = outer.regr_list.resolve()
    content = path.read_bytes()
    source = content.decode('utf-8')
    source_hash = hashlib.sha256(content).hexdigest()
    batch_id = new_batch_id(path)
    entries = []
    parser = build_parser(RowParser)
    for line, raw in enumerate(source.splitlines(), 1):
        try:
            tokens = shlex.split(raw, comments=True, posix=True)
            if not tokens:
                continue
            args = parser.parse_args(['regr', *tokens])
            if args.regr_list:
                raise ConfigError('nested regression lists are not supported')
            args.project = args.project if args.project is not None else outer.project
            args.output_root = args.output_root or outer.output_root or outer.global_output_root
            args.dry_run = args.dry_run or outer.dry_run
            plan, roots, settings = prepare_execution(args)
            plan['input_hashes'][str(path)] = source_hash
            plan['snapshots'][str(path)] = source
            plan['batch'] = {'id': batch_id, 'list': str(path), 'line': line, 'index': len(entries) + 1,
                             'argv': tokens, 'cwd': str(Path.cwd())}
            entries.append(dict(index=len(entries) + 1, line=line, argv=tokens, plan=plan,
                                roots=[str(p) for p in roots], settings=settings))
        except (ConfigError, OSError, ValueError) as exc:
            raise ConfigError(f'{path}:{line}: {exc}') from exc
    if not entries:
        raise ConfigError(f'empty regression list: {path}')
    if digest(path) != source_hash:
        raise ConfigError('regression list changed during planning')
    roots = outer.output_root or outer.global_output_root or entries[0]['roots']
    for entry in entries:
        validate_roots(entry['plan'], roots)
    batch = dict(schema_version=1, kind='batch', id=batch_id, cwd=str(Path.cwd()),
                 list=str(path), list_sha256=source_hash, snapshot=source,
                 output_roots=[str(Path(p).resolve()) for p in roots], entries=entries)
    return batch, entries


def counts(state):
    buckets = ('total', 'completed', 'passed', 'failed', 'running', 'pending', 'unknown')
    state['totals'] = {key: sum(row['totals'][key] for row in state['regressions']) for key in buckets}
    state['regression_counts'] = {key: sum(row['status'] == key for row in state['regressions'])
                                  for key in ('planned', 'running', 'passed', 'failed', 'preview',
                                              'unresolved', 'error', 'interrupted', 'not_started')}
    state['passed'] = (state['terminal'] and not state['report_errors'] and not state['scheduler_errors']
                       and all(r['status'] == 'passed' for r in state['regressions']))
    state['task_count'] = state['totals']['total']


def final_totals(child):
    from nsim.dashboard import view
    return view(child, Path.cwd(), 0, 'complete', '')['totals']


def batch_markdown(state):
    lines = [f"# NSIM batch {state['id']}", '',
             f"State: {state['lifecycle']}; passed: {state['passed']}; tasks: {state['task_count']}", '',
             '| Line | Regression | Status | Passed | Failed | Total | Report |',
             '| --- | --- | --- | --- | --- | --- | --- |']
    for row in state['regressions']:
        total = row['totals']
        target = (Path(row['record_dir']) / 'report.html').as_uri() if row.get('record_dir') else ''
        lines.append(f"| {row['line']} | {row['id']} | {row['status']} | {total['passed']} | "
                     f"{total['failed']} | {total['total']} | {target} |")
    return '\n'.join(lines) + '\n'


def batch_html(state, journal):
    def esc(value):
        return html.escape(str(value), quote=True)
    rows = []
    for row in state['regressions']:
        links = []
        if row.get('record_dir'):
            for name, label in (('dashboard.html', 'Dashboard'), ('report.html', '报告'),
                                ('live.json', '实时状态'), ('plan.json', '计划'), ('commands.json', '命令预览')):
                if not (Path(row['record_dir']) / name).is_file():
                    continue
                url = quote(os.path.relpath(Path(row['record_dir']) / name, journal), safe='/')
                links.append(f'<a href="{esc(url)}">{label}</a>')
        total = row['totals']
        rows.append('<tr>' + ''.join(f'<td>{esc(value)}</td>' for value in
                    (row['line'], row['id'], row['status'], total['passed'], total['failed'],
                     total['running'], total['pending'], total['unknown'], total['total'])) +
                    f'<td>{" · ".join(links)}</td><td><code>{esc(shlex.join(row["argv"]))}</code></td></tr>')
    return ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>NSIM 批量回归</title>'
            '<style>body{font:15px system-ui;margin:32px;color:#17212e}table{border-collapse:collapse;width:100%}'
            'td,th{border-bottom:1px solid #ddd;padding:10px;text-align:left}code{white-space:pre-wrap}'
            'a{color:#1260a0}p{overflow-wrap:anywhere}</style>'
            f'<h1>批量回归 {esc(state["id"])}</h1><p>状态：{esc(state["lifecycle"])} · '
            f'回归数：{state.get("regression_count", len(state["regressions"]))} · 仿真任务数：{state["task_count"]} · '
            f'通过：{state["totals"]["passed"]} · 失败：{state["totals"]["failed"]}</p>'
            '<p>此页面由控制器在状态变化时保存；运行期间重新打开或刷新可读取最新快照。</p>'
            f'<p>{esc(state.get("message", ""))}</p><table><thead><tr>' +
            ''.join(f'<th>{title}</th>' for title in ('行', '回归 ID', '状态', '通过', '失败', '运行',
                                                    '待执行', '未知', '总数', '详情', '参数')) +
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></html>').encode()


def run_batch(batch, entries):
    from nsim.service import run
    # All plans are prepared before the first submission, and rechecked here.
    for entry in entries:
        check_identity(entry['plan'])
    roots = [Path(p) for p in batch['output_roots']]
    if len(roots) != 1:
        raise ConfigError('select exactly one output root for a regression batch')
    selected = roots[0]
    preflight(selected, entries[0]['settings']['min_free_bytes'], entries[0]['settings']['min_free_inodes'])
    base = selected / 'regr_batch_dir' / batch['id']
    make_directory(base.parent, selected)
    base.mkdir(exist_ok=False)
    journal = base / 'report'
    journal.mkdir()
    write_json(journal / 'plan.json', batch)
    state = dict(schema_version=1, kind='batch', phase='batch', id=batch['id'], terminal=False,
                 lifecycle='running', started_at=time.time(), ended_at=None, revision=0,
                 dry_run=all(e['settings']['dry_run'] for e in entries), report_errors=[],
                 scheduler_errors=[], regressions=[])
    for entry in entries:
        total = len(entry['plan']['tasks'])
        state['regressions'].append(dict(index=entry['index'], line=entry['line'], argv=entry['argv'],
            id=entry['plan']['id'], status='planned', record_dir=None,
            totals=dict(total=total, completed=0, passed=0, failed=0, running=0, pending=total, unknown=0)))

    display_failed = False
    def display(value):
        nonlocal display_failed
        if display_failed:
            return
        try:
            write_json(journal / 'live.json', value)
            atomic_write(journal / 'dashboard.html', batch_html(value, journal))
        except OSError as exc:
            display_failed = True
            print(f'nsim: batch dashboard publication failed: {exc}', file=sys.stderr)

    def publish(message=''):
        counts(state)
        state['revision'] += 1
        state['published_at'] = time.time()
        if message:
            state['message'] = message
        write_json(journal / 'state.json', state)
        display(state)

    current = None
    try:
        publish()
        for entry, current in zip(entries, state['regressions']):
            current['status'] = 'running'
            publish()
            print(f"NSIM 批次 {batch['id']} | 回归 {entry['index']}/{len(entries)} | "
                  f"清单第 {entry['line']} 行 | {current['id']}", file=sys.stderr, flush=True)

            def progress(record, snapshot):
                if snapshot['run_id'] != current['id']:
                    raise IntegrityError('batch progress belongs to another regression')
                # Update only this row's contribution. No full batch state or
                # all-row HTML rewrite on every case/scheduler event.
                for key, value in snapshot['totals'].items():
                    state['totals'][key] += value - current['totals'][key]
                current.update(record_dir=str(record), totals=dict(snapshot['totals']),
                               lifecycle=snapshot['lifecycle'])
                state['revision'] += 1
                state['published_at'] = time.time()
                brief = {key: state[key] for key in ('kind', 'id', 'lifecycle', 'terminal', 'passed',
                         'revision', 'published_at', 'totals', 'regression_counts', 'task_count')}
                brief.update(schema_version=2, regression_count=len(state['regressions']),
                             regressions=[dict(current)], partial=True)
                display(brief)

            record, child = run(entry['plan'], entry['roots'], **entry['settings'], on_progress=progress)
            current['record_dir'] = str(record)
            if child.get('dry_run'):
                current['status'] = 'preview'
                current['preview_files'] = {name: digest(record / name) for name in
                                            ('plan.json', 'commands.json', 'preflight.json')}
            elif not child.get('terminal') or child.get('scheduler_errors'):
                current['status'] = 'unresolved'
                state['scheduler_errors'] = child.get('scheduler_errors') or ['child regression is not terminal']
                state['lifecycle'] = 'unresolved'
                current['totals'] = final_totals(child)
                for row in state['regressions'][entry['index']:]:
                    row['status'] = 'not_started'
                publish('后续回归未启动：当前回归存在未确认的作业状态。')
                return journal, state
            else:
                current['status'] = 'passed' if child['passed'] else 'failed'
                current['totals'] = final_totals(child)
                current['final_sha256'] = digest(record / 'final.json')
                state['report_errors'].extend(child['report_errors'])
            publish()
            print(f"NSIM 批次进度 {entry['index']}/{len(entries)} | {current['status']} | "
                  f"通过 {state['totals']['passed']} / {state['task_count']}", file=sys.stderr, flush=True)
        state['lifecycle'] = 'finalizing'
        publish()
        state.update(terminal=True, lifecycle='preview' if state['dry_run'] else 'complete', ended_at=time.time())
        counts(state)
        state['revision'] += 1
        state['published_at'] = time.time()
        write_json(journal / 'state.json', state)
        atomic_write(journal / 'report.md', batch_markdown(state).encode())
        atomic_write(journal / 'report.html', batch_html(state, journal))
        files = ('plan.json', 'state.json', 'report.md', 'report.html')
        write_json(journal / 'final.json', dict(schema_version=1, kind='batch', terminal=True,
                                               files={name: digest(journal / name) for name in files}))
        # Display success only after the completion manifest is durable. Never
        # rewrite the now-hashed state.json while publishing the final view.
        display(state)
        return journal, state
    except BaseException as exc:
        state.update(terminal=False, lifecycle='interrupted' if isinstance(exc, KeyboardInterrupt) else 'error')
        if current is not None and current['status'] == 'running':
            current['status'] = state['lifecycle']
        for row in state['regressions']:
            if row['status'] == 'planned':
                row['status'] = 'not_started'
        try:
            for filename in ('final.json', 'report.html', 'report.md'):
                (journal / filename).unlink(missing_ok=True)
            publish(str(exc) or '控制器已中断；未发布最终完成记录。')
        except OSError:
            pass
        raise


def load_batch_final(journal):
    from nsim.reports import load_final
    final = read_json(journal / 'final.json')
    if (final.get('kind') != 'batch' or final.get('terminal') is not True or
            set(final.get('files', {})) != {'plan.json', 'state.json', 'report.md', 'report.html'}):
        raise IntegrityError('batch has no valid terminal completion record')
    verify_files({str(journal / name): value for name, value in final['files'].items()})
    plan, state = read_json(journal / 'plan.json'), read_json(journal / 'state.json')
    if plan['id'] != state['id'] or not state['terminal'] or len(plan['entries']) != len(state['regressions']):
        raise IntegrityError('batch plan and state disagree')
    for entry, row in zip(plan['entries'], state['regressions']):
        if entry['plan']['id'] != row['id'] or entry['line'] != row['line']:
            raise IntegrityError('batch regression membership mismatch')
        record = Path(row['record_dir'])
        if row['status'] == 'preview':
            verify_files({str(record / name): value for name, value in row['preview_files'].items()})
            continue
        if digest(record / 'final.json') != row['final_sha256']:
            raise IntegrityError('child regression completion changed')
        child_plan, child = load_final(record)
        if child_plan['id'] != row['id'] or child_plan.get('batch', {}).get('id') != plan['id']:
            raise IntegrityError('child regression identity mismatch')
        row['status'] = 'passed' if child['passed'] else 'failed'
        row['totals'] = final_totals(child)
    counts(state)
    return plan, state
