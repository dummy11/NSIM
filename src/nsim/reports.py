"""Render recorded results without consulting the live scheduler."""

import copy
import html
from pathlib import Path

from nsim.checks import ACCEPTED
from nsim.config import read_json
from nsim.storage import IntegrityError, digest, verify_files


def assessed(state):
    value = copy.deepcopy(state)
    for task in value["tasks"]:
        attempts = task["attempts"]
        if attempts and attempts[-1]["verdict"] in ACCEPTED:
            try:
                if not attempts[-1]["evidence"]:
                    raise IntegrityError("accepted attempt has no evidence")
                verify_files(attempts[-1]["evidence"])
            except IntegrityError as exc:
                task["effective_verdict"] = "INCOMPLETE"
                task["evidence_error"] = str(exc)
                continue
        task["effective_verdict"] = attempts[-1]["verdict"] if attempts else task["status"].upper()
    value["passed"] = bool(value["tasks"]) and value["terminal"] and not value["report_errors"] and all(
        t["effective_verdict"] in ACCEPTED for t in value["tasks"])
    return value


def load_final(journal: Path):
    journal = journal.resolve()
    try:
        final = read_json(journal / "final.json")
        if not isinstance(final, dict) or final.get("schema_version") != 1 or final.get("terminal") is not True:
            raise IntegrityError("source has no terminal completion record")
        if set(final.get("files", {})) != {"plan.json", "state.json", "report.md", "report.html", "failed.json"}:
            raise IntegrityError("invalid final manifest file set")
        for filename, expected in final["files"].items():
            if digest(journal / filename) != expected:
                raise IntegrityError(f"journal checksum mismatch: {filename}")
        plan = read_json(journal / "plan.json")
        state = read_json(journal / "state.json")
        if plan["id"] != state["id"] or state["terminal"] is not True:
            raise IntegrityError("source is not a completed run")
        return plan, assessed(state)
    except OSError as exc:
        raise IntegrityError(f"cannot read terminal journal {journal}: {exc}") from exc
    except (KeyError, TypeError, AttributeError) as exc:
        raise IntegrityError(f"malformed terminal journal: {journal}") from exc


def markdown(state):
    state = assessed(state)
    title = "PASS" if state["passed"] else "FAIL" if state["terminal"] else "RUNNING"
    lines = [f"# NSIM {state['id']} — {title}", "", "| Case | Submit | Seed | Result | Attempts |",
             "|---|---|---|---|---|"]
    for task in state["tasks"]:
        lines.append(f"| {task['case']} | {task['submit_name']} | {task['seed']} | {task['effective_verdict']} | {len(task['attempts'])} |")
    if state["report_errors"]:
        lines.extend(["", "Report publication errors:", "", *[f"- {e}" for e in state["report_errors"]]])
    lines.extend(["", "Every retry retains its original failure in the JSON record.", ""])
    return "\n".join(lines)


def html_report(state):
    esc = html.escape
    content = markdown(state)
    rows = []
    for task in state["tasks"]:
        for attempt in task["attempts"]:
            links = " ".join(f'<a href="{esc(Path(p).as_uri(), quote=True)}">{esc(Path(p).name)}</a>'
                             for p in attempt["evidence"])
            rows.append(f"<li>{esc(task['case'])} / {esc(attempt['id'])}: {esc(attempt['verdict'])} "
                        f"{esc(attempt.get('reason', ''))} {links}</li>")
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><title>NSIM report</title>"
            "<style>body{font:16px system-ui;margin:2rem}pre{white-space:pre-wrap}li{margin:1rem 0}</style>"
            f"<h1>NSIM regression</h1><pre>{esc(content)}</pre><ul>{''.join(rows)}</ul></html>").encode()
