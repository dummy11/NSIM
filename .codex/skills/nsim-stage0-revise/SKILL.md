---
name: nsim-stage0-revise
description: Revise any Stage 0 phase document in the nsim repository after human review. Use when incorporating Human Decisions, Human Review Notes, or approved change requests. Resolve target files from docs/stage0-phase-planning.md.
---

# nsim Stage 0 Revise

Use this skill to revise Stage 0 documents after review. It is phase-agnostic and must resolve the target file from the phase plan.

## Required Reading

- `docs/stage0-phase-planning.md`
- `docs/governance/nsim_codex_workflow.md`
- `docs/governance/change_requests.md`
- `docs/freeze_log.md`
- Target phase document
- `AGENTS.md`, if it exists

## Rules

- Treat `Human Decisions` as mandatory.
- Treat `Human Review Notes` as review feedback to address or preserve.
- Keep `Notes` for process remarks and next actions.
- Do not edit frozen files unless an approved change request covers the exact scope.
- Do not rewrite unrelated architecture.
- Do not generate implementation code during Stage 0.

## Workflow

1. Identify current Stage and Phase.
2. Resolve target and dependent documents from `docs/stage0-phase-planning.md`.
3. Check `docs/freeze_log.md` for frozen status.
4. If frozen, verify an approved CR exists before editing.
5. Apply only scoped edits.
6. Update file-level review metadata when review status changes.
7. Run `python3 scripts/check_ai_workflow.py`.
8. Summarize changed files, addressed review notes, checks run, and remaining risk.
