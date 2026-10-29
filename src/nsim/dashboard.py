"""Event-driven terminal progress and self-contained, paginated HTML reports."""

from collections import Counter, OrderedDict
import html
import json
import os
from pathlib import Path
import sys
import time
import unicodedata
from urllib.parse import quote

from nsim.checks import ACCEPTED
from nsim.storage import atomic_write, write_json


def javascript_value(value):
    # Data is never HTML or source code, including messages containing </script>.
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def progress_page(data, has_report=False):
    """Bounded controller-written browser view; never scan tasks or poll files."""
    def esc(value):
        return html.escape(str(value), quote=True)
    totals = data['totals']
    rows = []
    for row in data['recent_cases']:
        cells = (row.get(key, '') for key in ('case', 'submit', 'seed', 'verdict', 'reason'))
        rows.append('<tr>' + ''.join(f'<td>{esc(value)}</td>' for value in cells) + '</tr>')
    jobs = ''.join('<li>' + esc(f"{job.get('job_id') or '未确认'} / {job.get('phase')} / {job.get('status')}") + '</li>'
                   for job in data['recent_jobs'])
    report = '<a href="report.html">完整报告与全部任务</a> · ' if has_report else ''
    summary = ' · '.join(f'{label} {totals[key]}' for key, label in
                        (('total', '总数'), ('passed', '通过'), ('failed', '未通过'),
                         ('running', '运行'), ('pending', '待执行'), ('unknown', '未确认')))
    return ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>NSIM 回归进度</title>'
            '<style>body{font:15px system-ui;color:#17212e;margin:32px}table{border-collapse:collapse;width:100%}'
            'th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}p,td{overflow-wrap:anywhere}</style>'
            f'<h1>回归 {esc(data["run_id"])}</h1><p>{esc(PHASES.get(data["lifecycle"], data["lifecycle"]))}</p>'
            f'<p>{esc(summary)}</p><p>{esc(data.get("message", ""))}</p>'
            f'<p>{report}<a href="plan.json">计划</a> · <a href="live.json">状态数据</a></p>'
            '<p>控制器在事件发生时保存快照；运行期间刷新页面可查看最新状态。</p>'
            '<h2>最近用例</h2><table><tr><th>Case</th><th>Submit</th><th>Seed</th><th>结果</th><th>原因</th></tr>'
            + ''.join(rows) + '</table><h2>最近 LSF 作业</h2><ul>' + jobs + '</ul></html>').encode()


PAGE_SIZE = 100
RECENT_CASES = 8
RECENT_JOBS = 4
PHASES = dict(running='运行中', finalizing='正在发布最终报告', complete='运行结束',
              interrupted='控制器已中断', error='控制器发生错误', unresolved='存在状态未确认的作业')


def task_row(task, journal):
    attempt = task['attempts'][-1] if task['attempts'] else {}
    verdict = ('INCOMPLETE' if task.get('evidence_error') else
               attempt.get('verdict', task['status'].upper()))
    report = attempt.get('log_analysis') or attempt.get('compile_log_analysis') or {}
    first = report.get('first_blocking') or report.get('first_failure') or {}
    location = first.get('source_file') or first.get('log_file') or ''
    line = first.get('source_line') if first.get('source_file') else first.get('log_line')
    if location and line:
        location += ':' + str(line)
    analysis = attempt.get('record_path') or next((p for p in attempt.get('evidence', {}) if Path(p).name == 'analysis.json'), None)
    return dict(id=task['id'], case=task['case'], submit=task['submit_name'], seed=task['seed'],
                verdict=verdict, errors=report.get('counts', {}).get('error', 0),
                fatals=report.get('counts', {}).get('fatal', 0),
                waived_errors=report.get('waived_counts', {}).get('error', 0), location=location[:1024],
                reason=(attempt.get('reason') or task.get('pending_reason') or '')[:2048],
                analysis=quote(os.path.relpath(analysis, journal), safe='/') if analysis else None)


def bucket(task, row):
    attempt = task['attempts'][-1] if task['attempts'] else {}
    if attempt.get('terminal') is False:
        return 'unknown'
    if attempt:
        return 'passed' if row['verdict'] in ACCEPTED else 'failed'
    return 'pending' if task['status'] == 'planned' else 'running'


def view(state, journal, revision, lifecycle, message):
    # Full rows are embedded only in the final, self-contained HTML report.
    rows = [task_row(task, journal) for task in state['tasks']]
    totals = dict(total=len(rows), completed=0, passed=0, failed=0, running=0, pending=0, unknown=0)
    for task, row in zip(state['tasks'], rows):
        totals[bucket(task, row)] += 1
    totals['completed'] = totals['passed'] + totals['failed']
    return dict(schema_version=2, run_id=state['id'], revision=revision, lifecycle=lifecycle,
                published_at=time.time(), terminal=state['terminal'], passed=state['passed'], totals=totals,
                rows=rows, jobs=list(state.get('scheduler_jobs', {}).values()), message=message,
                log_summary=state.get('log_summary', {}), page_size=PAGE_SIZE)


# Report navigation only: no timer, filesystem watcher, network or live transport.
CLIENT = r'''
(() => {
  "use strict";
  const initial = NSIM_INITIAL;
  const selected = {cases: 0, jobs: 0};
  const byId = id => document.getElementById(id);
  const phases = {running: "运行中", finalizing: "正在发布最终报告", complete: "运行结束",
                  interrupted: "控制器已中断", error: "控制器发生错误", unresolved: "存在状态未确认的作业"};
  function textCell(row, value) {
    const cell = document.createElement("td");
    cell.textContent = String(value ?? "");
    row.appendChild(cell);
    return cell;
  }
  function pageCount(data, kind) {
    return Math.ceil((kind === 'cases' ? data.rows : data.jobs).length / data.page_size);
  }
  function renderRows(kind, items, data, index) {
    const rows = document.createDocumentFragment();
    for (const item of items) {
      const row = document.createElement("tr");
      if (kind === 'cases') {
        row.dataset.taskId = item.id;
        for (const value of [item.case, item.submit, item.seed, item.verdict, item.errors, item.fatals, item.waived_errors, item.location, item.reason]) textCell(row, value);
        const cell = textCell(row, "");
        if (item.analysis) {
          const link = document.createElement("a");
          link.textContent = "分析结果";
          link.setAttribute("href", item.analysis);
          cell.appendChild(link);
        }
        if (item.attempts) {
          const details = document.createElement('details');
          const summary = document.createElement('summary');
          summary.textContent = '执行记录与证据';
          details.appendChild(summary);
          for (const attempt of item.attempts) {
            const record = document.createElement('p');
            record.textContent = `${attempt.id}: ${attempt.verdict} ${attempt.reason} `;
            for (const evidence of attempt.evidence) {
              const link = document.createElement('a');
              link.textContent = evidence.name + ' ';
              link.setAttribute('href', evidence.url);
              record.appendChild(link);
            }
            details.appendChild(record);
          }
          cell.appendChild(details);
        }
      } else {
        for (const value of [item.job_id || "未确认", item.phase, item.status, item.terminal ? "是" : "否"]) textCell(row, value);
      }
      rows.appendChild(row);
    }
    byId(kind).replaceChildren(rows);
    const count = pageCount(data, kind);
    byId(kind + '-page').value = String(index + 1);
    byId(kind + '-page').max = String(Math.max(1, count));
    byId(kind + '-pages').textContent = `/ ${Math.max(1, count)} 页，每页 ${data.page_size} 条`;
    byId(kind + '-prev').disabled = index === 0;
    byId(kind + '-next').disabled = index + 1 >= count;
  }
  function renderSummary(data) {
    byId("run-id").textContent = data.run_id;
    byId("phase").textContent = phases[data.lifecycle] || data.lifecycle;
    byId("verdict").textContent = data.lifecycle === "complete" ? (data.passed ? "PASS" : "FAIL") : "尚未最终确认";
    byId("updated").textContent = new Date(data.published_at * 1000).toLocaleString();
    byId("revision").textContent = String(data.revision);
    byId("message").textContent = data.message || "";
    for (const key of ["total", "completed", "passed", "failed", "running", "pending", "unknown"]) byId("count-" + key).textContent = String(data.totals[key]);
    byId("log-summary").textContent = JSON.stringify(data.log_summary);
  }
  for (const kind of ['cases', 'jobs']) {
    function select(index) {
      const count = pageCount(initial, kind);
      selected[kind] = Math.max(0, Math.min(Math.max(0, count - 1), Math.trunc(Number(index)) || 0));
      const all = kind === 'cases' ? initial.rows : initial.jobs;
      const start = selected[kind] * initial.page_size;
      renderRows(kind, all.slice(start, start + initial.page_size), initial, selected[kind]);
    }
    byId(kind + '-prev').onclick = () => select(selected[kind] - 1);
    byId(kind + '-next').onclick = () => select(selected[kind] + 1);
    byId(kind + '-page').onchange = event => select(Number(event.target.value) - 1);
    select(0);
  }
  renderSummary(initial);
})();
'''


def page(data):
    def controls(kind):
        return (f'<p><button id="{kind}-prev">上一页</button> '
                f'<input id="{kind}-page" type="number" min="1" value="1" aria-label="页码" style="width:80px"> '
                f'<span id="{kind}-pages"></span> <button id="{kind}-next">下一页</button></p>')
    cards = ''.join(f'<div><span>{label}</span><strong id="count-{key}">—</strong></div>' for key, label in
                    [('total', '总数'), ('completed', '已完成'), ('passed', '通过'), ('failed', '未通过'),
                     ('running', '运行中'), ('pending', '待执行'), ('unknown', '状态未确认')])
    return ('''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>NSIM 回归报告</title>
<style>
body{font:14px system-ui,sans-serif;background:#f5f7fa;color:#1d2939;margin:0;padding:28px}
main{max-width:1500px;margin:auto}h1{margin:0 0 10px;font-size:26px}h2{font-size:18px}
header,section{background:white;border:1px solid #e4e7ec;border-radius:10px;padding:20px;margin-bottom:16px}
.meta{color:#667085;line-height:1.8;overflow-wrap:anywhere}.cards{display:grid;grid-template-columns:repeat(7,minmax(75px,1fr));gap:12px;margin-top:20px}
.cards span{display:block;color:#667085}.cards strong{display:block;font-size:26px;margin-top:8px}
.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;text-align:left}th,td{padding:10px 12px;border-bottom:1px solid #eaecf0;vertical-align:top}
th{white-space:nowrap;color:#475467;font-weight:600}td{max-width:440px;overflow-wrap:anywhere}a{color:#175cd3}#connection{color:#344054}
#phase{font-weight:600}#message{color:#b54708;white-space:pre-wrap}#log-summary{overflow-wrap:anywhere}
@media(max-width:700px){body{padding:12px}.cards{grid-template-columns:repeat(3,1fr)}}
</style></head><body><main><header>
<h1>NSIM 回归报告</h1><div><span id="phase">正在加载</span> · <span id="verdict"></span></div>
<div class="meta">运行 ID：<span id="run-id"></span><br>最近发布：<span id="updated"></span> · 版本 <span id="revision"></span></div>
<p id="connection" role="status" aria-live="polite">静态报告 · 实时 Dashboard 显示在运行终端</p><p id="message"></p><div class="cards">''' + cards + '''</div>
</header><section><p id="page-error" role="status"></p><h2>Case 结果</h2>''' + controls('cases') + '''<div class="table-wrap"><table><thead><tr>
<th>Case</th><th>Submit</th><th>Seed</th><th>状态 / 判定</th><th>Error</th><th>Fatal</th><th>豁免 Error</th><th>错误位置</th><th>原因</th><th>结果</th>
</tr></thead><tbody id="cases"></tbody></table></div></section>
<section><h2>LSF 作业</h2>''' + controls('jobs') + '''<div class="table-wrap"><table><thead><tr><th>Job ID</th><th>阶段</th><th>状态</th><th>已确认终态</th></tr></thead><tbody id="jobs"></tbody></table></div></section>
<section><h2>日志汇总</h2><p id="log-summary"></p><p class="meta">运行中结果来自已保存的 case 分析。最终报告发布后才确认整批结果。</p>
<a href="report.html">最终报告</a> · <a href="live.json">实时 JSON</a></section>
<noscript>此报告需要 JavaScript 分页查看。也可查看同目录的 report.md 或 state.json。</noscript>
</main><script>const NSIM_INITIAL = ''' + javascript_value(data) + ';\n' + CLIENT + '</script></body></html>').encode('utf-8')


def terminal_text(value, width=None):
    """Strip control/escape/bidi characters and clip by terminal display columns."""
    text = ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in str(value))
    if width is None:
        return text
    used, result = 0, []
    for char in text:
        size = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in 'WF' else 1
        if used + size > width:
            break
        result.append(char)
        used += size
    return ''.join(result)


class TerminalDisplay:
    """Render only on controller events, on stderr; redirected output is plain text."""

    def __init__(self, stream=None):
        self.stream = sys.stderr if stream is None else stream
        self.interactive = self.stream.isatty() and os.environ.get('TERM') != 'dumb'
        self.lines = 0
        self.closed = False

    @staticmethod
    def case_line(row):
        return (f"{row['case']}  {row['submit']}  seed={row['seed']}  {row['verdict']}  "
                f"E/F={row['errors']}/{row['fatals']}  waived={row.get('waived_errors', 0)}  {row['location']}  {row['reason']}")

    def render(self, data, cases=(), jobs=()):
        if self.closed:
            return
        totals = data['totals']
        phase = PHASES[data['lifecycle']]
        verdict = ('PASS' if data['passed'] else 'FAIL') if data['lifecycle'] == 'complete' else '尚未最终确认'
        summary = (f"完成 {totals['completed']}/{totals['total']} | PASS {totals['passed']} | 未通过 {totals['failed']} | "
                   f"运行 {totals['running']} | 待执行 {totals['pending']} | 未确认 {totals['unknown']}")
        header = f"NSIM 回归 {data['run_id']} | {phase} | {verdict}"
        if self.interactive:
            try:
                width, height = os.get_terminal_size(self.stream.fileno())
            except (OSError, ValueError, AttributeError):
                width, height = 120, 24
            # A newly allocated PTY can report (0, 0) until its first resize.
            width, height = width or 120, height or 24
            lines = [header, summary]
            if data['job_counts']:
                lines.append('LSF: ' + '  '.join(f'{k}={v}' for k, v in sorted(data['job_counts'].items())))
            if data['message']:
                lines.append(data['message'])
            lines.append('最近 Case: case / submit / seed / 判定 / Error/Fatal / 位置与原因')
            lines.extend(self.case_line(row) for row in reversed(data['recent_cases']))
            lines.extend(f"LSF {job.get('job_id') or '未确认'} {job['phase']} {job['status']}"
                         for job in reversed(data['recent_jobs']))
            lines = [terminal_text(line, max(1, width - 1)) for line in lines[:max(1, height - 1)]]
            prefix = f'\x1b[{self.lines}A\r\x1b[J' if self.lines else ''
            text = prefix + '\n'.join(lines) + '\n'
            self.lines = len(lines)
        else:
            lines = [header, summary]
            lines.extend(self.case_line(row) for row in cases if row['verdict'] not in ('PLANNED', 'RUNNING'))
            lines.extend(f"LSF {job.get('job_id') or '未确认'} {job['phase']} {job['status']}" for job in jobs)
            if data['message']:
                lines.append(data['message'])
            text = '\n'.join(terminal_text(line) for line in lines) + '\n'
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError, UnicodeError):
            # Losing a terminal must not turn a completed simulation into a failure.
            self.closed = True

    def close(self):
        self.closed = True


class Dashboard:
    """Controller-owned incremental counters; no timer, polling or extra process."""

    def __init__(self, journal, stream=None):
        self.journal = Path(journal)
        self.revision = 0
        self.display = TerminalDisplay(stream)
        self.initialized = False
        self.lifecycle = 'running'
        self.rows = []
        self.jobs = []
        self.task_indices = {}
        self.job_indices = {}
        self.contributions = {}
        self.log_refs = {}
        self.totals = dict(total=0, completed=0, passed=0, failed=0, running=0, pending=0, unknown=0)
        self.log_summary = dict(simulation=dict(warning=0, error=0, fatal=0),
                                compile=dict(warning=0, error=0, fatal=0), incomplete_analyses=0)
        self.changed_cases = {}
        self.changed_jobs = {}
        self.recent_cases = OrderedDict()
        self.recent_jobs = OrderedDict()
        self.job_counts = Counter()

    def _log(self, phase, report, change):
        key = (phase, report['path'])
        if key not in self.log_refs:
            self.log_refs[key] = [0, report['counts'], not report['complete']]
        entry = self.log_refs[key]
        previous = entry[0]
        entry[0] += change
        delta = int(entry[0] > 0) - int(previous > 0)
        for severity, count in entry[1].items():
            self.log_summary[phase][severity] += delta * count
        self.log_summary['incomplete_analyses'] += delta * entry[2]
        if not entry[0]:
            del self.log_refs[key]

    def update(self, state, tasks=(), jobs=()):
        if not self.initialized:
            self.initialized = True
            self.task_indices = {task['id']: i for i, task in enumerate(state['tasks'])}
            self.rows = [None] * len(self.task_indices)
            self.totals['total'] = len(self.rows)
            tasks, jobs = state['tasks'], state.get('scheduler_jobs', {}).values()
        for task in tasks:
            index = self.task_indices[task['id']]
            old = self.contributions.get(index)
            if old:
                self.totals[old[0]] -= 1
                for phase, report in old[1]:
                    self._log(phase, report, -1)
            row = task_row(task, self.journal)
            category = bucket(task, row)
            self.totals[category] += 1
            attempt = task['attempts'][-1] if task['attempts'] else {}
            reports = [(phase, attempt[key]) for key, phase in
                       (('log_analysis', 'simulation'), ('compile_log_analysis', 'compile')) if attempt.get(key)]
            for phase, report in reports:
                self._log(phase, report, 1)
            self.contributions[index] = category, reports
            if self.rows[index] != row:
                self.rows[index] = row
                self.changed_cases[task['id']] = row
                self._recent(self.recent_cases, task['id'], row, RECENT_CASES)
        self.totals['completed'] = self.totals['passed'] + self.totals['failed']
        for job in jobs:
            token = job['token']
            if token not in self.job_indices:
                self.job_indices[token] = len(self.jobs)
                self.jobs.append(None)
            index = self.job_indices[token]
            if self.jobs[index] != job:
                if self.jobs[index]:
                    self.job_counts[self.jobs[index]['status']] -= 1
                self.jobs[index] = dict(job)
                self.job_counts[job['status']] += 1
                compact = {key: job.get(key) for key in ('job_id', 'phase', 'status', 'terminal')}
                self.changed_jobs[token] = compact
                self._recent(self.recent_jobs, token, compact, RECENT_JOBS)

    @staticmethod
    def _recent(items, key, value, limit):
        items.pop(key, None)
        items[key] = value
        if len(items) > limit:
            items.popitem(last=False)

    def publish(self, state, lifecycle='running', message=''):
        self.update(state)
        # Reconcile once at shutdown, including final evidence validation and
        # tasks cancelled without dispatch. Ordinary events touch only changes.
        if lifecycle != 'running' and self.lifecycle == 'running':
            self.update(state, state['tasks'], state.get('scheduler_jobs', {}).values())
        if self.revision and lifecycle == self.lifecycle and not self.changed_cases and not self.changed_jobs and not message:
            return False
        revision = self.revision + 1
        data = dict(schema_version=3, run_id=state['id'], revision=revision, lifecycle=lifecycle,
                    published_at=time.time(), terminal=state['terminal'],
                    passed=lifecycle == 'complete' and bool(self.rows) and state['terminal']
                           and not state['report_errors'] and self.totals['passed'] == self.totals['total'],
                    totals=dict(self.totals), log_summary=self.log_summary, message=message,
                    recent_cases=list(self.recent_cases.values()), recent_jobs=list(self.recent_jobs.values()),
                    job_counts={key: value for key, value in self.job_counts.items() if value}, job_count=len(self.jobs))
        # Persist first: the terminal never displays an update whose journal
        # summary failed to save. Full attempts remain in each case's run.json.
        write_json(self.journal / 'live.json', data)
        atomic_write(self.journal / 'dashboard.html', progress_page(
            data, lifecycle == 'complete' and (self.journal / 'report.html').is_file()))
        self.snapshot = data
        self.revision, self.lifecycle = revision, lifecycle
        self.display.render(data, self.changed_cases.values(), self.changed_jobs.values())
        self.changed_cases.clear()
        self.changed_jobs.clear()
        return True

    def close(self):
        self.display.close()
