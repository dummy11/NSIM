#!/usr/bin/env python3
"""Check AI workflow governance files and review block conventions."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


WORKFLOW_PATH = Path("docs/governance/nsim_codex_workflow.md")
REQUIRED_FILES = [
    Path("AGENTS.md"),
    WORKFLOW_PATH,
    Path("docs/governance/change_requests.md"),
    Path("docs/governance/freeze_manifest.json"),
    Path("docs/freeze_log.md"),
    Path("scripts/check_freeze.py"),
]
PROMPT_DIR = Path(".codex/prompts")
REVIEW_BLOCK_FILES = [
    Path("AGENTS.md"),
    Path("docs/plan.md"),
    Path("docs/roadmap.md"),
    Path("docs/architecture.md"),
    Path("docs/stage0-phase-planning.md"),
    Path("docs/freeze_log.md"),
]

WORKFLOW_REFERENCE = "docs/governance/nsim_codex_workflow.md"
AGENTS_REQUIRED_REFERENCES = [
    WORKFLOW_REFERENCE,
    "docs/freeze_log.md",
    "docs/governance/change_requests.md",
    "docs/plan.md",
    "docs/roadmap.md",
    "docs/architecture.md",
]
REVIEW_BLOCK_HEADINGS = [
    "## Lifecycle",
    "## Review Trace",
    "## Revision Log",
    "## File-Level Human Review",
    "### Project-Level References",
    "### Review Metadata",
    "### Human Decisions",
    "### Human Review Notes",
    "### Approval Decision",
    "### Notes",
]


def markdown_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        return ""

    level = len(heading) - len(heading.lstrip("#"))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.startswith("#"):
            continue
        current_level = len(line) - len(line.lstrip("#"))
        if current_level <= level:
            end = index
            break

    return "\n".join(lines[start:end])


def extract_status(review_metadata: str) -> str | None:
    match = re.search(r"^- Status:\s*(.+?)\s*$", review_metadata, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def extract_referenced_paths(project_references: str) -> list[str]:
    paths: list[str] = []
    for line in project_references.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        if stripped in {"- None", "- none"}:
            continue
        paths.extend(re.findall(r"`([^`]+)`", stripped))
    return paths


def error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_required_files() -> list[str]:
    errors: list[str] = []
    for path in REQUIRED_FILES:
        if not path.is_file():
            errors.append(f"missing required governance file: {path}")
    return errors


def check_prompt_references() -> list[str]:
    errors: list[str] = []
    if not PROMPT_DIR.exists():
        return errors

    for path in sorted(PROMPT_DIR.glob("*.md")):
        text = read_text(path)
        first_lines = "\n".join(text.splitlines()[:5])
        if WORKFLOW_REFERENCE not in first_lines:
            errors.append(f"prompt missing top workflow reference: {path}")

    return errors


def check_agents_reference() -> list[str]:
    path = Path("AGENTS.md")
    if not path.exists():
        return ["missing required agent instruction file: AGENTS.md"]

    text = read_text(path)
    return [
        f"AGENTS.md must require reading {reference}"
        for reference in AGENTS_REQUIRED_REFERENCES
        if reference not in text
    ]


def check_review_blocks() -> list[str]:
    errors: list[str] = []
    for path in REVIEW_BLOCK_FILES:
        if not path.is_file():
            errors.append(f"missing review-block document: {path}")
            continue

        text = read_text(path)
        for heading in REVIEW_BLOCK_HEADINGS:
            if heading not in text:
                errors.append(f"{path} missing review heading: {heading}")

        project_references = markdown_section(text, "### Project-Level References")
        for reference in extract_referenced_paths(project_references):
            if not Path(reference).exists():
                errors.append(f"{path} project-level reference does not exist: {reference}")

        review_metadata = markdown_section(text, "### Review Metadata")
        approval_decision = markdown_section(text, "### Approval Decision").lower()
        status = extract_status(review_metadata)
        if status is None:
            errors.append(f"{path} missing review metadata status")
            continue

        normalized_status = status.lower()
        decision_is_approved = "approved" in approval_decision
        decision_is_pending = "pending" in approval_decision
        if normalized_status == "approved" and not decision_is_approved:
            errors.append(f"{path} has Status: Approved but approval decision is not approved")
        if normalized_status == "approved" and decision_is_pending:
            errors.append(f"{path} has Status: Approved but approval decision is pending")
        if normalized_status == "pending" and not decision_is_pending:
            errors.append(f"{path} has Status: Pending but approval decision is not pending")
        if normalized_status == "changes requested" and decision_is_approved:
            errors.append(f"{path} has Changes Requested status but an approved decision")

    return errors


def check_change_request_doc() -> list[str]:
    path = Path("docs/governance/change_requests.md")
    if not path.is_file():
        return [f"missing change request document: {path}"]

    text = read_text(path)
    required_terms = [
        "## Change Categories",
        "### Patch",
        "### Change Request",
        "### Rebaseline",
        "## Approval Rules",
        "## Active Change Requests",
        "## Closed Change Requests",
    ]
    return [f"{path} missing required section: {term}" for term in required_terms if term not in text]


def check_freeze_manifest() -> list[str]:
    result = subprocess.run(
        [sys.executable, "scripts/check_freeze.py"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0:
        return []

    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    if not output:
        output = "freeze manifest check failed"
    return [output]


def main() -> int:
    errors: list[str] = []
    errors.extend(check_required_files())
    errors.extend(check_prompt_references())
    errors.extend(check_agents_reference())
    errors.extend(check_review_blocks())
    errors.extend(check_change_request_doc())
    errors.extend(check_freeze_manifest())

    if errors:
        for message in errors:
            error(message)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
