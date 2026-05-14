# AGENTS.md

本文定义 AI agent 在 `nsim` 仓库中工作的强制上下文和执行边界。

## Mandatory Reading

任何 AI agent 在修改仓库前，必须先读取：

- `docs/governance/nsim_codex_workflow.md`
- `docs/freeze_log.md`
- `docs/governance/change_requests.md`
- `docs/plan.md`
- `docs/roadmap.md`
- `docs/architecture.md`

执行具体 stage 或 phase 时，还必须读取：

- `docs/stage0-phase-planning.md`
- 当前目标 stage 或 phase 对应的文档
- 任何被修改文件中的 `File-Level Human Review` 区块

## Governance Rules

- Treat `Human Decisions` as mandatory.
- Treat `Human Review Notes` as review feedback to address or explicitly preserve.
- Do not modify frozen files unless an approved change request covers the exact scope.
- Do not silently change historical decisions.
- Do not generate implementation code during Stage 0.
- Keep changes limited to the active stage or phase.

## Frozen Scope Handling

如果任务涉及已冻结文件，AI agent 必须：

1. 读取 `docs/freeze_log.md` 确认冻结状态。
2. 读取 `docs/governance/change_requests.md` 确认是否已有 approved change request。
3. 如果没有 approved change request，不得修改冻结文件。
4. 如果存在 approved change request，只能修改 `Approved Scope` 中列出的文件和内容。
5. 修改后运行相关检查，并更新冻结记录或 change request 状态。

## Stage 0 Boundary

Stage 0 只允许生成和修订治理、规划、架构和 review/freeze 文档。

Stage 0 不生成实现代码，不创建 Python package skeleton，不引入 CLI 实现，不接入真实 VCS、Verdi 或 LSF。

## Assumptions

- AI agents may be invoked in different stages with incomplete conversation context.
- Repository governance files are the source of truth for stage, phase, freeze, and change request handling.
- Frozen files may only be changed through approved change requests.

## Open Questions

- None

## Lifecycle

Status: Living
Baseline: Stage 0 v1
Frozen Sections:
- Human Decisions
- Approval Decision

Mutable Sections:
- Notes
- Open Questions
- Review Trace
- Revision Log
- Clarifications that do not alter mandatory reading, frozen-scope handling, or Stage 0 boundaries

## Review Trace

### Review 2026-05-13

Reviewer: Human
Status: Approved

Findings:
- AGENTS.md must require AI agents to read governance, plan, roadmap, and architecture baselines before modifying the repository.

Required Changes:
- None

Resolution:
- Approved as Stage 0 Phase 4 baseline.

## Revision Log

- 2026-05-14: Rebaseline governance metadata to support living-document updates and machine-checked frozen sections.

## File-Level Human Review

### Project-Level References

- `docs/governance/nsim_codex_workflow.md`
- `docs/freeze_log.md`
- `docs/governance/change_requests.md`
- `docs/plan.md`
- `docs/roadmap.md`
- `docs/architecture.md`

### Review Metadata

- Status: Approved
- Reviewer: Human
- Decision Date: 2026-05-13

### Human Decisions

- AI agents must read `docs/governance/nsim_codex_workflow.md`, `docs/freeze_log.md`, `docs/governance/change_requests.md`, `docs/plan.md`, `docs/roadmap.md`, and `docs/architecture.md` before modifying the repository.
- AI agents must read `docs/stage0-phase-planning.md`, the target stage or phase document, and affected file-level `File-Level Human Review` blocks before executing a specific stage or phase.
- AI agents must treat `Human Decisions` as mandatory.
- Frozen files may only be changed through approved change requests that cover the exact scope.
- Stage 0 must not generate implementation code.

### Human Review Notes

- None

### Approval Decision

This AGENTS.md is approved as the Stage 0 Phase 4 baseline.

### Notes

- None
