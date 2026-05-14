#!/usr/bin/env python3
"""Validate frozen Stage 0 locked sections against the freeze manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


MANIFEST_PATH = Path("docs/governance/freeze_manifest.json")
CHANGE_REQUESTS_PATH = Path("docs/governance/change_requests.md")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def heading_title(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None

    marker, _, title = stripped.partition(" ")
    if marker and set(marker) == {"#"} and title:
        return title.strip()

    return None


def markdown_section(text: str, section_name: str) -> str | None:
    lines = text.splitlines()
    start: int | None = None
    start_level: int | None = None

    for index, line in enumerate(lines):
        title = heading_title(line)
        if title != section_name:
            continue

        start = index
        start_level = len(line) - len(line.lstrip("#"))
        break

    if start is None or start_level is None:
        return None

    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        title = heading_title(line)
        if title is None:
            continue
        level = len(line) - len(line.lstrip("#"))
        if level <= start_level:
            end = index
            break

    return "\n".join(lines[start:end]).strip() + "\n"


def approved_cr_covers(path: str) -> bool:
    if not CHANGE_REQUESTS_PATH.is_file():
        return False

    text = read_text(CHANGE_REQUESTS_PATH)
    active_marker = "## Active Change Requests"
    closed_marker = "## Closed Change Requests"
    if active_marker not in text:
        return False

    active_text = text.split(active_marker, 1)[1]
    if closed_marker in active_text:
        active_text = active_text.split(closed_marker, 1)[0]

    approved_terms = ("Status: Approved", "Status: Implemented")
    return path in active_text and any(term in active_text for term in approved_terms)


def validate_manifest() -> list[str]:
    if not MANIFEST_PATH.is_file():
        return [f"missing freeze manifest: {MANIFEST_PATH}"]

    manifest = json.loads(read_text(MANIFEST_PATH))
    errors: list[str] = []

    for snapshot in manifest.get("snapshots", []):
        commit = snapshot.get("freeze_commit", "")
        label = f"{snapshot.get('stage', '<unknown stage>')} {snapshot.get('phase', '<unknown phase>')}"
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            errors.append(f"{label} has invalid freeze_commit: {commit}")
            continue

        result = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            errors.append(f"{label} freeze_commit is not a local git commit: {commit}")

    for item in manifest.get("files", []):
        path_text = item["path"]
        path = Path(path_text)
        if not path.is_file():
            errors.append(f"manifest file is missing: {path_text}")
            continue

        text = read_text(path)
        current_file_hash = sha256_text(text)
        baseline_file_hash = item.get("sha256")
        if baseline_file_hash and current_file_hash == baseline_file_hash:
            continue

        section_errors: list[str] = []
        for section in item.get("locked_sections", []):
            section_name = section["name"]
            current_section = markdown_section(text, section_name)
            if current_section is None:
                section_errors.append(f"{path_text} missing locked section: {section_name}")
                continue

            current_section_hash = sha256_text(current_section)
            if current_section_hash != section["sha256"]:
                section_errors.append(f"{path_text} locked section changed: {section_name}")

        if section_errors:
            if approved_cr_covers(path_text):
                continue
            errors.extend(section_errors)
            continue

        if "## Revision Log" not in text:
            errors.append(f"{path_text} changed outside locked sections but has no Revision Log")

    return errors


def main() -> int:
    errors = validate_manifest()
    if errors:
        for message in errors:
            print(f"ERROR: {message}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
