---
name: nsim-stage0-freeze
description: Freeze or re-freeze any approved Stage 0 phase document in the nsim repository. Use by resolving the target phase file from docs/stage0-phase-planning.md and updating docs/freeze_log.md. Do not freeze unapproved documents.
---

# nsim Stage 0 Freeze

Use this skill to freeze Stage 0 phase documents. It is phase-agnostic: resolve the target file from `docs/stage0-phase-planning.md`.

## Required Reading

- `docs/stage0-phase-planning.md`
- `docs/governance/nsim_codex_workflow.md`
- `docs/governance/change_requests.md`
- `docs/freeze_log.md`
- Target phase document
- `AGENTS.md`, if it exists

## Preconditions

- Target document has `Review Metadata` with `Status: Approved`.
- Target document has an explicit `Approval Decision`.
- File-level `Human Decisions` are reflected in dependent docs when applicable.
- `python3 scripts/check_ai_workflow.py` passes.
- If re-freezing a previously frozen file, an approved CR exists.

## Workflow

1. Identify Stage, Phase, and target document.
2. Confirm approval status in `File-Level Human Review`.
3. Confirm frozen/change-request status.
4. Update `docs/freeze_log.md` with a freeze or re-freeze entry.
5. Run `python3 scripts/check_ai_workflow.py`.
6. Report what was frozen, checks run, and remaining risk.

## Freeze Entry Template

```markdown
## Stage 0 Phase N Freeze

状态：已冻结
文档：`docs/<file>.md`
日期：YYYY-MM-DD
Reviewer：Human
说明：文件级 `Human Decisions` 已作为后续文档同步依据。
```
