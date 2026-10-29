"""Read saved results and optionally rebuild a derived HTML view.

The completion manifest and execution records are never rewritten here.
"""

import json
from pathlib import Path

from nsim.config import ConfigError
from nsim.reports import html_report, load_final, markdown
from nsim.storage import IntegrityError, atomic_write


def _record_directory(source):
    directory = Path(source).resolve()
    if (directory / 'report' / 'plan.json').is_file():
        return directory / 'report'
    return directory


def _issues(state):
    issues = [str(item) for item in state.get('report_errors', [])]
    if state.get('kind') == 'batch':
        issues.extend(str(row['evidence_error']) for row in state['regressions']
                      if row.get('evidence_error'))
        issues.extend(str(row['report_error']) for row in state['regressions']
                      if row.get('report_error'))
    else:
        issues.extend(str(task['evidence_error']) for task in state['tasks']
                      if task.get('evidence_error'))
    return issues


def report(source, rebuild=False, as_json=False):
    """Return CLI output and exit code without executing or replaying work."""
    source = Path(source)
    original_issue = None
    try:
        plan, state = load_final(source)
    except (IntegrityError, ConfigError) as exc:
        original_issue = str(exc)
        try:
            plan, state = load_final(source, allow_broken_views=True)
        except (IntegrityError, ConfigError):
            plan, state = None, None

    issues = ([original_issue] if original_issue else []) + (_issues(state) if state else [])
    case = plan is not None and plan.get('kind') == 'case'
    directory = _record_directory(source)
    report_path = directory / 'report.html'
    rebuilt_path = None
    if rebuild and state is not None and not case:
        notice = '此页面由现存记录重建；原完成记录未修改，重建页面不属于原完成校验。'
        if issues:
            notice += ' 原报告或证据不完整：' + '; '.join(issues[:3])
        if state.get('kind') == 'batch':
            from nsim.batch import batch_html
            content = batch_html(dict(state, message=notice), directory)
        else:
            content = html_report(state, notice=notice, report_filename='report.rebuilt.html')
        rebuilt_path = directory / 'report.rebuilt.html'
        atomic_write(rebuilt_path, content)

    complete = state is not None and not issues
    if as_json:
        result = dict(state) if state is not None else {}
        result.update(report_complete=complete, issues=issues)
        if not case:
            result['report_path'] = str(report_path) if complete else None
        if rebuilt_path is not None:
            result['rebuilt_report_path'] = str(rebuilt_path)
        output = json.dumps(result, indent=2, ensure_ascii=False)
    elif state is None:
        output = f'报告不完整：{original_issue or "缺少有效的完成记录"}'
    elif case:
        output = markdown(state)
    elif complete:
        label = '回归批次' if state.get('kind') == 'batch' else '回归'
        verdict = 'PREVIEW' if state.get('dry_run') else 'PASS' if state['passed'] else 'FAIL'
        output = (f'{label}结果：{verdict}\n最终报告：{report_path}\n'
                  f'浏览器打开：{report_path.as_uri()}')
    else:
        output = '报告不完整：' + '; '.join(issues[:3])
        if len(issues) > 3:
            output += f'（另有 {len(issues) - 3} 项）'
    if rebuilt_path is not None and not as_json:
        output += (f'\n已重建展示报告：{rebuilt_path}\n'
                   '原完成记录未修改；重建文件不属于原完成校验。')
    if not complete:
        return output, 2
    return output, 0 if state['passed'] else 1
