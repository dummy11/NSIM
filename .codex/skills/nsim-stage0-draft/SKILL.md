---
name: nsim-stage0-draft
description: Draft any Stage 0 planning or governance phase document in the nsim repository. Use when creating or expanding a Stage 0 document by first resolving the phase-to-file mapping from docs/stage0-phase-planning.md. Do not generate implementation code.
---

# nsim Stage 0 Draft

Use this skill to draft Stage 0 documents. It is phase-agnostic: always resolve the target file from `docs/stage0-phase-planning.md` or the user request.

## Required Reading

- `docs/stage0-phase-planning.md`
- `docs/governance/nsim_codex_workflow.md`
- `docs/governance/change_requests.md`
- Target phase document, if it exists
- `AGENTS.md`, if it exists

## Rules

- Do not generate implementation code during Stage 0.
- Do not edit frozen files unless an approved change request covers the exact scope.
- Keep drafts scoped to the target Phase file and directly affected governance files.
- New reviewable documents must include `## File-Level Human Review`.

## File-Level Review Template

```markdown
## File-Level Human Review

### Project-Level References

- None

### Review Metadata

- Status: Pending
- Reviewer: Human
- Decision Date: YYYY-MM-DD

### Human Decisions

- None

### Human Review Notes

- None

### Approval Decision

Pending.

### Notes

- None
```

## Workflow

1. Identify current Stage and requested Phase.
2. Resolve the target document from `docs/stage0-phase-planning.md`.
3. Draft only the target document or approved supporting governance content.
4. Preserve existing file-level review content.
5. Run `python3 scripts/check_ai_workflow.py` if governance or review blocks changed.
6. Summarize the drafted file and what needs human review.
