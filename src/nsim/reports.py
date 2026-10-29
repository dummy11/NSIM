"""Render recorded results without consulting the live scheduler."""

import copy
import hashlib
import json
from pathlib import Path

from nsim.checks import ACCEPTED
from nsim.case_records import read_record, reference_matches, unpack_single
from nsim.config import read_json
from nsim.layout import is_regression
from nsim.storage import IntegrityError, digest, verify_files


def assessed(state, verify=True, verified=None):
    value = copy.deepcopy(state)
    verified = dict(verified or {})
    for task in value["tasks"]:
        attempts = task["attempts"]
        if task.get('record_error'):
            task.update(effective_verdict='INCOMPLETE', evidence_error=task['record_error'])
            continue
        if not verify and task.get('evidence_error'):
            task['effective_verdict'] = 'INCOMPLETE'
            continue
        if verify and attempts and attempts[-1]["verdict"] in ACCEPTED:
            try:
                if not attempts[-1]["evidence"]:
                    raise IntegrityError("accepted attempt has no evidence")
                for path, expected in attempts[-1]['evidence'].items():
                    key = (path, expected)
                    if key not in verified:
                        try:
                            verify_files({path: expected})
                            verified[key] = None
                        except IntegrityError as exc:
                            verified[key] = str(exc)
                    if verified[key]:
                        raise IntegrityError(verified[key])
            except IntegrityError as exc:
                task["effective_verdict"] = "INCOMPLETE"
                task["evidence_error"] = str(exc)
                continue
        if verify:
            task.pop('evidence_error', None)
        task["effective_verdict"] = attempts[-1]["verdict"] if attempts else task["status"].upper()
    value["passed"] = bool(value["tasks"]) and value["terminal"] and not value["report_errors"] and all(
        t["effective_verdict"] in ACCEPTED for t in value["tasks"])
    summaries = {'simulation': dict(warning=0, error=0, fatal=0), 'compile': dict(warning=0, error=0, fatal=0),
                 'incomplete_analyses': 0}
    seen = set()
    for task in value['tasks']:
        if not task['attempts']:
            continue
        for key, phase in (('log_analysis', 'simulation'), ('compile_log_analysis', 'compile')):
            report = task['attempts'][-1].get(key)
            if not report or report['path'] in seen:
                continue
            seen.add(report['path'])
            for severity, count in report['counts'].items():
                summaries[phase][severity] += count
            summaries['incomplete_analyses'] += not report['complete']
    value['log_summary'] = summaries
    return value


def load_final(journal: Path):
    journal = journal.resolve()
    if (journal / 'report' / 'plan.json').is_file():
        journal = journal / 'report'
    try:
        source, reference = journal, None
        reference_hash = None
        if not (journal / 'plan.json').exists() and (journal / 'run.json').is_file():
            reference, reference_hash = read_record(journal / 'run.json')
            if reference.get('schema_version') == 2 and 'plan' in reference:
                plan, state = unpack_single(reference, journal / 'run.json', reference_hash)
                return plan, assessed(state, verified={(str(journal / 'run.json'), reference_hash): None})
            journal = Path(reference['record_dir']).resolve()
            if not (journal / 'plan.json').exists() and journal != source:
                # Failover outputs can refer to a consolidated single-run record.
                value, saved_hash = read_record(journal / 'run.json')
                plan, state = unpack_single(value, journal / 'run.json', saved_hash)
                selected = [t for t in state['tasks'] if t['id'] == reference['task_id']]
                bindings = [a for a in selected[0]['attempts'] if str(source / 'run.json') in a['evidence']] if len(selected) == 1 else []
                if not bindings or not reference_matches(reference, reference_hash,
                        bindings[-1]['evidence'][str(source / 'run.json')], selected[0], bindings[-1]):
                    raise IntegrityError('simulation reference does not match recorded evidence')
                return plan, assessed(state, verified={(str(source / 'run.json'), reference_hash): None,
                                                       (str(journal / 'run.json'), saved_hash): None})
        final = read_json(journal / "final.json")
        if isinstance(final, dict) and final.get('kind') == 'batch':
            from nsim.batch import load_batch_final
            return load_batch_final(journal)
        if not isinstance(final, dict) or final.get("schema_version") not in (1, 2) or final.get("terminal") is not True:
            raise IntegrityError("source has no terminal completion record")
        base = {'plan.json', 'state.json'}
        full = base | {'report.md', 'report.html', 'failed.json'}
        filenames = set(final.get('files', {}))
        if filenames != full and not (final['schema_version'] == 2 and filenames == base):
            raise IntegrityError("invalid final manifest file set")
        records = {}
        for filename, expected in final["files"].items():
            if filename in base:
                data = (journal / filename).read_bytes()
                actual = hashlib.sha256(data).hexdigest()
                records[filename] = data
            else:
                actual = digest(journal / filename)
            if actual != expected:
                raise IntegrityError(f"journal checksum mismatch: {filename}")
        plan = json.loads(records['plan.json'])
        state = json.loads(records['state.json'])
        if plan["id"] != state["id"] or state["terminal"] is not True:
            raise IntegrityError("source is not a completed run")
        if final['schema_version'] == 2:
            expected = full if is_regression(plan) and not state['report_errors'] else base
            if filenames != expected:
                raise IntegrityError('completion manifest does not match run kind or report status')
        if reference is not None:
            selected = [t for t in state['tasks'] if t['id'] == reference['task_id']]
            if len(selected) != 1:
                raise IntegrityError('simulation reference is absent from regression')
            evidence_path = str(source / 'run.json')
            bindings = [a for a in selected[0]['attempts'] if evidence_path in a['evidence']]
            if not bindings or not reference_matches(reference, reference_hash,
                    bindings[-1]['evidence'][evidence_path], selected[0], bindings[-1]):
                raise IntegrityError('simulation reference does not match recorded evidence')
            plan = copy.deepcopy(plan)
            plan['tasks'] = [t for t in plan['tasks'] if t['id'] == reference['task_id']]
            plan['kind'] = 'case'
            state = dict(state, tasks=selected)
        verified = {(str(source / 'run.json'), reference_hash): None} if reference is not None else None
        return plan, assessed(state, verified=verified)
    except OSError as exc:
        raise IntegrityError(f"cannot read terminal journal {journal}: {exc}") from exc
    except (KeyError, TypeError, AttributeError) as exc:
        raise IntegrityError(f"malformed terminal journal: {journal}") from exc


def markdown(state, verify=True):
    if state.get('kind') == 'batch':
        from nsim.batch import batch_markdown
        return batch_markdown(state)
    state = assessed(state, verify)
    title = "PASS" if state["passed"] else "FAIL" if state["terminal"] else "RUNNING"
    lines = [f"# NSIM {state['id']} — {title}", "", "| Case | Submit | Seed | Result | Attempts |",
             "|---|---|---|---|---|"]
    for task in state["tasks"]:
        lines.append(f"| {task['case']} | {task['submit_name']} | {task['seed']} | {task['effective_verdict']} | {len(task['attempts'])} |")
    lines.extend(['', '| Case / seed | Log errors / fatals | Waived errors | First location | Failure reason |', '|---|---|---|---|---|'])
    def cell(value):
        return str(value or '').replace('|', '\\|').replace('\n', ' ')[:2048]
    for task in state['tasks']:
        if not task['attempts']:
            continue
        attempt = task['attempts'][-1]
        report = attempt.get('log_analysis') or attempt.get('compile_log_analysis')
        first = (report or {}).get('first_blocking') or (report or {}).get('first_failure') or {}
        where = (str(first.get('source_file', '')) + ':' + str(first.get('source_line', ''))).strip(':')
        if not where and first.get('log_file'):
            where = first['log_file'] + ':' + str(first.get('log_line', ''))
        counts = report['counts'] if report else {}
        waived = (report or {}).get('waived_counts', {}).get('error', 0)
        lines.append(f"| {task['case']} / {task['seed']} | {counts.get('error', 0)} / {counts.get('fatal', 0)} | {waived} | {cell(where)} | {cell(attempt.get('reason'))} |")
    lines.extend(['', 'Log totals (shared compilation counted once): ' + str(state['log_summary'])])
    if state["report_errors"]:
        lines.extend(["", "Report publication errors:", "", *[f"- {e}" for e in state["report_errors"]]])
    if state.get('scheduler_jobs'):
        lines.extend(['', '| LSF job | Phase | Status | Terminal |', '|---|---|---|---|'])
        for job in state['scheduler_jobs'].values():
            lines.append(f"| {job['job_id'] or 'unconfirmed'} | {job['phase']} | {job['status']} | {job['terminal']} |")
    if state.get('scheduler_errors'):
        lines.extend(['', 'Unresolved scheduler jobs:', *[f'- {error}' for error in state['scheduler_errors']]])
    lines.extend(["", "Every retry retains its original failure in the JSON record.", ""])
    return "\n".join(lines)


def html_report(state, verify=True):
    from nsim.dashboard import page, view
    state = assessed(state, verify)
    messages = [str(item) for item in state['report_errors'] + state.get('scheduler_errors', [])]
    data = view(state, Path.cwd(), 1, 'complete' if state['terminal'] else 'running', '\n'.join(messages))
    for task, row in zip(state['tasks'], data['rows']):
        attempt = task['attempts'][-1] if task['attempts'] else {}
        analysis = attempt.get('record_path') or next((p for p in attempt.get('evidence', {}) if Path(p).name == 'analysis.json'), None)
        row['analysis'] = Path(analysis).as_uri() if analysis else None
        row['attempts'] = [dict(id=a['id'], verdict=a['verdict'], reason=a.get('reason', ''),
                                evidence=[dict(name=Path(p).name, url=Path(p).as_uri()) for p in a.get('evidence', {})])
                           for a in task['attempts']]
    # A self-contained final snapshot; only the selected page creates DOM rows.
    return page(data)
