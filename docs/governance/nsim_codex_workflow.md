# Codex Workflow

## Purpose

This document defines the recommended Codex-driven development workflow for the `nsim` repository.

The workflow is based on staged development with explicit human review and frozen baselines.
It is designed to keep the repository:

- reviewable
- controllable
- testable
- suitable for AI-assisted iteration

This workflow must be read together with:

- `docs/plan.md`
- `docs/architecture.md`
- `docs/roadmap.md`
- `docs/freeze_log.md`
- `docs/governance/change_requests.md`
- `docs/governance/freeze_manifest.json`

When `AGENTS.md` exists, it must require AI agents to read this workflow, the freeze log, the change request log, `docs/plan.md`, `docs/roadmap.md`, and `docs/architecture.md` before making repository changes.

## Core Principle

Every phase and stage should follow this pattern:

1. AI drafts
2. Human reviews
3. AI revises
4. Human reviews again
5. Baseline is frozen
6. Only then proceed to the next phase or stage

Do not skip review.
Do not skip freeze points.
Do not let Codex silently jump across stages.

## Governance Rules

Future Codex runs must treat the following as mandatory:

- Human decisions
- Locked decisions
- Frozen decisions

If a generated change conflicts with these constraints, the change must be revised.
Codex must not silently override repository governance.

Review metadata must be internally consistent:

- `Status: Approved` requires a non-pending approval decision that states the document is approved.
- `Status: Pending` requires a pending approval decision.
- `Status: Changes Requested` must not be paired with an approved decision.

Project-level references listed in `File-Level Human Review` must point to existing repository files, except for explicit `None` entries.

## Repository Initialization

The repository should contain a `.codex/prompts/init.md` file that defines the bootstrap workflow.
All prompt files under `.codex/prompts/` must reference this workflow at the top.

## Modifying Previous Frozen Stages

If a later stage reveals that an earlier frozen stage must be modified, the agent must not directly edit the previous stage.

The required workflow is:

1. Create or update `docs/governance/change_requests.md`.
2. Identify the affected previous stage.
3. Identify whether the change touches frozen scope.
4. If frozen scope is touched, record a temporary unfreeze entry in `docs/freeze_log.md`.
5. Limit edits to the approved files only.
6. Run tests for the affected stage and all dependent stages.
7. Update affected file-level `Human Decisions` if any locked decision changes.
8. Update affected file-level `Human Decisions` or `Notes` with the rebaseline summary.
9. Add a re-freeze entry to `docs/freeze_log.md`.

The agent must never silently rewrite frozen baseline files.

## Living Documents and Freeze Enforcement

Frozen Stage 0 documents are living documents. Routine clarifications may update mutable sections, but locked sections remain protected.

`docs/freeze_log.md` records baseline and Stage/Phase freeze status only. Stage/Phase to file mapping is defined by `docs/stage0-phase-planning.md` and should not be duplicated in the freeze log.

Locked sections include:

- `Human Decisions`
- `Approval Decision`
- baseline or freeze records

Mutable sections include:

- `Notes`
- `Open Questions`
- `Review Trace`
- `Revision Log`
- clarifications that do not alter stage boundaries, architecture decisions, workflow rules, or file-level decisions

Any change to locked sections requires an approved change request. Mutable updates must leave a `Revision Log` entry. `scripts/check_freeze.py` validates the locked sections recorded in `docs/governance/freeze_manifest.json`.

Freeze operations must create and upload a git commit. The freeze commit id must be recorded in `docs/governance/freeze_manifest.json` under the corresponding Stage/Phase snapshot.

Allowed categories:

- Patch: typo, comments, minor docs, non-behavioral fixes.
- Change Request: CLI, config, schema, API, workflow, test behavior changes.
- Rebaseline: packaging model, architecture, repository structure, core workflow changes.
