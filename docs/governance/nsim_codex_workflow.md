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

Allowed categories:

- Patch: typo, comments, minor docs, non-behavioral fixes.
- Change Request: CLI, config, schema, API, workflow, test behavior changes.
- Rebaseline: packaging model, architecture, repository structure, core workflow changes.
