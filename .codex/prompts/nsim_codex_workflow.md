codex Workflow

## Purpose

This document defines the recommended Codex-driven development workflow for the nsim repository.

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
- `docs/decisions.md`
- `docs/freeze_log.md`
- `AGENTS.md`

---

## Core principle

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

---

## Governance rules

Future Codex runs must treat the following as mandatory:

- Human decisions
- Locked decisions
- Frozen decisions

If a generated change conflicts with these constraints, the change must be revised.
Codex must not silently override repository governance.

---

## Repository initialization

The repository should contain a `.codex/prompts/init.md` file that defines the bootstrap workflow.

