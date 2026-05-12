#!/usr/bin/env python3
"""Check AI workflow governance files and review block conventions."""

from pathlib import Path
import sys


WORKFLOW_PATH = Path("docs/governance/nsim_codex_workflow.md")
REQUIRED_FILES = [
    WORKFLOW_PATH,
    Path("docs/governance/change_requests.md"),
    Path("docs/freeze_log.md"),
]
PROMPT_DIR = Path(".codex/prompts")
REVIEW_BLOCK_FILES = [
    Path("docs/plan.md"),
    Path("docs/roadmap.md"),
]

WORKFLOW_REFERENCE = "docs/governance/nsim_codex_workflow.md"
REVIEW_BLOCK_HEADINGS = [
    "## File-Level Human Review",
    "### Project-Level References",
    "### Review Metadata",
    "### Human Decisions",
    "### Human Review Notes",
    "### Approval Decision",
    "### Notes",
]


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
        return []

    text = read_text(path)
    if WORKFLOW_REFERENCE not in text:
        return [f"AGENTS.md must require reading {WORKFLOW_REFERENCE}"]

    return []


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


def main() -> int:
    errors: list[str] = []
    errors.extend(check_required_files())
    errors.extend(check_prompt_references())
    errors.extend(check_agents_reference())
    errors.extend(check_review_blocks())
    errors.extend(check_change_request_doc())

    if errors:
        for message in errors:
            error(message)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
