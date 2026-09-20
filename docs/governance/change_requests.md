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

## CR-20260920-001：吸收工程回归经验与存储故障处理

Status: Re-frozen

Request Date: 2026-09-20

Requester: Human

Category: Rebaseline

Approval Basis:

- 用户在审阅本会话中 regress 借鉴分析与存储故障处理建议后，明确授权「行，都修改一下」。本请求据此批准文档修订范围，不代表相关功能已经实现或经过真实 EDA 验证。

Current Stage:

- Stage: Stage 0
- Phase: Post-baseline planning revision

Affected Previous Stage:

- Stage: Stage 0
- Phase: Phase 1-3, Phase 6

Affected Files:

- `docs/plan.md`
- `docs/architecture.md`
- `docs/roadmap.md`
- `docs/freeze_log.md`
- `docs/governance/change_requests.md`
- `docs/governance/freeze_manifest.json`

Frozen Status:

- Frozen: Yes
- Freeze Reference: `docs/freeze_log.md#stage-0-baseline`

Approved Scope:

- `docs/plan.md`: 增补防假通过、seed 与输入追溯、隔离输出、失败重跑、可扩展结果检查、存储故障检测与恢复的产品要求及文件级决策。
- `docs/architecture.md`: 增补任务与 attempt 身份、状态和 verdict 分离、编译元数据、结果检查器、并发调度、最终报告发布、执行节点存储检查与故障恢复契约及文件级决策。
- `docs/roadmap.md`: 将上述要求及可验证退出标准分配到现有 Stage 2-8，保留 Stage 0/1 边界及各 Stage 主目标。
- `docs/freeze_log.md`: 追加本 CR 的临时解冻和再冻结记录，保留历史记录。
- `docs/governance/change_requests.md`: 记录授权、实施、检查和关闭结果。
- `docs/governance/freeze_manifest.json`: 更新受影响文档的哈希与相应 Phase 的再冻结提交记录，保留原提交来源。

Reason:

- 将 npu8860_ai regress 的实际经验转化为通用平台需求，避免假通过、证据覆盖、执行节点挂载差异和日志可见性延迟造成误判。
- 将物理存储故障后的隔离与重跑纳入规划；不将现有脚本描述成已经具备磁盘修复或备份恢复能力。

Required Updates:

- 受影响文档的 Review Trace、Revision Log、Human Decisions / Notes。
- 冻结记录、冻结 manifest 与本 CR 状态。

Tests / Checks:

- `python3 scripts/check_freeze.py`、`python3 scripts/check_ai_workflow.py`、`git diff --check`；在最终 manifest 更新后复验。

Implementation Summary:

- Unfrozen: 仅本 Approved Scope。
- Changed: 产品要求、架构契约、Stage 2-8 验收标准与文件级 review/decision 记录已同步。
- Re-frozen: Stage 0 Phase 1-3 修订与 Phase 6 追加记录纳入当前 manifest；提交来源由 snapshots 记录。
- Tests run: 冻结检查、工作流检查与 diff 检查通过；仅文档检查，无真实 EDA 或磁盘故障运行。
- Remaining risks: Stage 0 仅定义契约，不运行真实 VCS / LSF 或执行破坏性磁盘故障测试。

## CR-20260514-003：Record freeze git commit ids in manifest

Status: Re-frozen

Request Date: 2026-05-14

Requester: Human

Current Stage:

- Stage: Stage 0
- Phase: Post-baseline governance improvement

Affected Previous Stage:

- Stage: Stage 0
- Phase: Phase 1-6

Affected Files:

- `.codex/skills/nsim-stage0-freeze/SKILL.md`
- `docs/governance/freeze_manifest.json`
- `docs/governance/nsim_codex_workflow.md`
- `docs/governance/change_requests.md`
- `scripts/check_freeze.py`

Frozen Status:

- Frozen: Yes
- Freeze Reference: `docs/freeze_log.md#stage-0-baseline`

Approved Scope:

- Require the freeze skill to commit and push freeze updates.
- Record Stage/Phase freeze git commit ids in `docs/governance/freeze_manifest.json`.
- Validate that recorded freeze commit ids are valid local git commits.

Reason:

- Freeze should be tied to uploaded git history, not only document text.
- The freeze manifest should connect Stage/Phase snapshots to git commit ids.

Required Updates:

- `.codex/skills/nsim-stage0-freeze/SKILL.md`
- `docs/governance/freeze_manifest.json`
- `docs/governance/nsim_codex_workflow.md`
- `docs/governance/change_requests.md`
- `scripts/check_freeze.py`

Tests / Checks:

- `python3 scripts/check_freeze.py` passed.
- `python3 scripts/check_ai_workflow.py` passed.

Implementation Summary:

- Unfrozen: Stage 0 governance process for this approved rebaseline scope.
- Changed: Freeze skill now requires commit, push, and manifest commit-id recording; manifest includes Stage 0 Phase 1-6 freeze commit ids.
- Re-frozen: Stage 0 manifest now records commit-backed snapshots.
- Tests run: `python3 scripts/check_freeze.py`; `python3 scripts/check_ai_workflow.py`.
- Remaining risks: Manifest records the commit containing the frozen content; a follow-up manifest update may necessarily be a separate governance commit because git commit ids are content-addressed.

## CR-20260514-002：Simplify freeze log and strengthen review metadata checks

Status: Re-frozen

Request Date: 2026-05-14

Requester: Human

Current Stage:

- Stage: Stage 0
- Phase: Post-baseline governance improvement

Affected Previous Stage:

- Stage: Stage 0
- Phase: Phase 1-6

Affected Files:

- `docs/freeze_log.md`
- `docs/governance/nsim_codex_workflow.md`
- `scripts/check_ai_workflow.py`
- `docs/governance/freeze_manifest.json`
- `docs/governance/change_requests.md`

Frozen Status:

- Frozen: Yes
- Freeze Reference: `docs/freeze_log.md#stage-0-baseline`

Approved Scope:

- Simplify `docs/freeze_log.md` so it records Stage/Phase identifiers rather than repeating phase file mappings.
- Treat `docs/stage0-phase-planning.md` as the source of truth for Stage/Phase to file mapping.
- Add checks that review metadata status is consistent with approval decisions.
- Add checks that project-level reference file paths exist.
- Regenerate `docs/governance/freeze_manifest.json` after the approved governance update.

Reason:

- Stage/Phase file mappings should have one source of truth.
- Review metadata and approval decisions should be machine-checkable.
- Project-level references should not drift into broken paths.

Required Updates:

- `docs/freeze_log.md`
- `docs/governance/nsim_codex_workflow.md`
- `scripts/check_ai_workflow.py`
- `docs/governance/freeze_manifest.json`
- `docs/governance/change_requests.md`

Tests / Checks:

- `python3 scripts/check_freeze.py` passed.
- `python3 scripts/check_ai_workflow.py` passed.

Implementation Summary:

- Unfrozen: Stage 0 governance process for this approved rebaseline scope.
- Changed: Simplified freeze log, added metadata/approval consistency checks, added project-level reference existence checks, and regenerated the freeze manifest.
- Re-frozen: Stage 0 locked sections are protected by the updated `docs/governance/freeze_manifest.json`.
- Tests run: `python3 scripts/check_freeze.py`; `python3 scripts/check_ai_workflow.py`.
- Remaining risks: Existing Stage 0 freeze history remains append-only; `docs/stage0-phase-planning.md` is the source of truth for file mapping.

## CR-20260514-001：Stage 0 living-document governance rebaseline

Status: Re-frozen

Request Date: 2026-05-14

Requester: Human

Current Stage:

- Stage: Stage 0
- Phase: Post-baseline governance improvement

Affected Previous Stage:

- Stage: Stage 0
- Phase: Phase 1-6

Affected Files:

- `AGENTS.md`
- `docs/plan.md`
- `docs/roadmap.md`
- `docs/architecture.md`
- `docs/stage0-phase-planning.md`
- `docs/freeze_log.md`
- `docs/governance/change_requests.md`
- `docs/governance/nsim_codex_workflow.md`
- `docs/governance/freeze_manifest.json`
- `scripts/check_ai_workflow.py`
- `scripts/check_freeze.py`

Frozen Status:

- Frozen: Yes
- Freeze Reference: `docs/freeze_log.md#stage-0-baseline`

Approved Scope:

- Add lifecycle, review trace, and revision log sections to Stage 0 baseline documents.
- Add a freeze manifest for Stage 0 locked sections.
- Add machine checks to detect frozen locked-section changes without approved CR coverage.
- Update governance workflow and checks to require the new freeze enforcement files.

Reason:

- Stage 0 documents may need frequent living updates after baseline freeze.
- Review and revise actions need durable traceability inside repository files.
- Freeze enforcement should be machine-checkable instead of relying only on AI discipline.

Required Updates:

- `docs/freeze_log.md`
- affected file-level `Human Decisions` or `Notes`
- `docs/governance/change_requests.md`

Tests / Checks:

- `python3 scripts/check_freeze.py` passed.
- `python3 scripts/check_ai_workflow.py` passed.

Implementation Summary:

- Unfrozen: Stage 0 governance process for this approved rebaseline scope.
- Changed: Added living-document lifecycle, review trace, revision log, freeze manifest, and freeze checks.
- Re-frozen: Stage 0 locked sections are protected by `docs/governance/freeze_manifest.json`.
- Tests run: `python3 scripts/check_freeze.py`; `python3 scripts/check_ai_workflow.py`.
- Remaining risks: The first manifest captures hashes after this approved rebaseline; older commits remain authoritative for the original Stage 0 freeze history.
