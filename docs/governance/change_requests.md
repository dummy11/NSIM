# Change Requests

本文记录对已冻结 Stage 或 Phase 的变更请求。任何影响已冻结范围的修改，都必须先在本文登记，并在请求被批准后才能修改对应文件。

## Required Reading

处理 change request 前必须读取：

- `AGENTS.md`，如果该文件已存在
- `docs/governance/nsim_codex_workflow.md`
- `docs/freeze_log.md`
- `docs/governance/change_requests.md`

说明：当前仓库使用 `docs/freeze_log.md` 作为冻结记录文件；人工说明统一保留在各文档的 `File-Level Human Review` 区块中。

## Workflow

1. Identify the current stage.
2. Identify the affected previous stage.
3. Check whether the affected files are frozen.
4. If frozen, create or update a Change Request entry.
5. Do not modify frozen files unless the Change Request is marked approved.
6. Limit edits only to the approved scope.
7. Run tests for the affected stage and all dependent stages.
8. Update:
   - `docs/freeze_log.md`
   - affected file-level `Human Decisions` or `Notes`
   - `docs/governance/change_requests.md`
9. Summarize:
   - what was unfrozen
   - what was changed
   - what was re-frozen
   - what tests were run
   - what risks remain

Do not rewrite unrelated architecture.
Do not silently change historical decisions.

## Change Categories

### Patch

Patch 用于低风险、非行为变更，例如：

- typo
- Markdown 格式修正
- 注释、措辞、链接修正
- 不改变 Stage/Phase 边界、架构、CLI、schema、测试行为或项目级决策的小修补

规则：

- Patch 可以直接修改。
- 如果 patch 触及已冻结文件，必须在 `docs/freeze_log.md` 记录 patch 摘要。
- Patch 不需要完整 Change Request approval。
- Patch 不得修改已冻结的文件级 human decisions。

### Change Request

Change Request 用于会影响局部行为或阶段边界的变更，例如：

- CLI 命令设计
- 配置格式或 schema
- API 或文件布局
- 测试行为
- workflow 执行规则
- 已冻结文件中的非平凡内容变更

规则：

- 必须先创建 CR entry。
- CR 状态为 `Approved` 后，才能修改 frozen scope。
- 修改必须限制在 `Approved Scope`。
- 修改后必须记录 tests/checks 和 implementation summary。

### Rebaseline

Rebaseline 用于影响项目整体基线的变更，例如：

- packaging model
- repository structure
- core architecture
- Stage/Phase 模型
- 已冻结的文件级 human decision
- freeze/review 流程本身

规则：

- 必须由 Human approval。
- 必须更新受影响文档的文件级 `Human Decisions` / `Notes`。
- 必须更新 `docs/freeze_log.md`。
- 必须在完成后 re-freeze 受影响范围。

## Status Values

- `Proposed`: request has been recorded but is not approved.
- `Approved`: request is approved for the listed scope only.
- `Implemented`: approved changes have been made.
- `Re-frozen`: affected files have been re-frozen.
- `Rejected`: request was reviewed and declined.

## Approval Rules

- `Proposed` 只能登记，不允许修改 frozen files。
- `Approved` 允许修改 `Approved Scope` 中列出的文件。
- `Implemented` 表示修改已完成但尚未 re-freeze。
- `Re-frozen` 表示修改已完成、检查已运行、冻结记录已更新。
- `Rejected` 表示不执行该请求。

如果 CR 涉及多个文件，每个文件都必须出现在 `Affected Files` 和 `Approved Scope` 中。

## Change Request Template

```markdown
## CR-YYYYMMDD-NNN：<title>

Status: Proposed

Request Date: YYYY-MM-DD

Requester: Human

Current Stage:

- Stage:
- Phase:

Affected Previous Stage:

- Stage:
- Phase:

Affected Files:

- `path/to/file.md`

Frozen Status:

- Frozen: Yes | No
- Freeze Reference: `docs/freeze_log.md#...`

Approved Scope:

- Pending approval.

Reason:

- ...

Required Updates:

- `docs/freeze_log.md`
- affected file-level `Human Decisions` or `Notes`
- `docs/governance/change_requests.md`

Tests / Checks:

- ...

Implementation Summary:

- Unfrozen:
- Changed:
- Re-frozen:
- Tests run:
- Remaining risks:
```

## Active Change Requests

- None

## Closed Change Requests

- None
