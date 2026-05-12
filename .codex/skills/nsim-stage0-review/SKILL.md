---
name: nsim-stage0-review
description: Review any Stage 0 phase document in the nsim repository for governance compliance, consistency, and freeze readiness. Use by resolving the phase-to-file mapping from docs/stage0-phase-planning.md. Do not edit unless explicitly asked.
---

# nsim Stage 0 Review

Use this skill to review Stage 0 documents. It is phase-agnostic and works for any Phase listed in `docs/stage0-phase-planning.md`.

## Required Reading

- `docs/stage0-phase-planning.md`
- `docs/governance/nsim_codex_workflow.md`
- `docs/governance/change_requests.md`
- `docs/freeze_log.md`
- Target phase document
- `AGENTS.md`, if it exists

## Review Checklist

- The target document matches its Phase purpose.
- The document has a complete `File-Level Human Review` block.
- `Review Metadata` status matches `Approval Decision`.
- `Human Decisions` are clear and mandatory.
- `Human Review Notes` are feedback, questions, or observations, not hidden decisions.
- `Notes` contain process remarks or next actions.
- Frozen files were not changed without an approved change request.
- No implementation code was introduced in Stage 0.

## Output

Report:

```text
Status: Ready | Needs changes | Blocked

Findings:
- ...

Required fixes:
- ...

Freeze readiness:
- ...
```
