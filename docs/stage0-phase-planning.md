# Stage 0 Phase Planning

## 目标

Stage 0 的目标是在实现代码之前完成治理与规划基线：确认各文档的 `File-Level Human Review` 区块，将所有规划文档彼此对齐，并准备进入 Stage 1 的冻结条件。

Stage 0 不生成任何实现代码。

## Phase 编号规则

- Phase 使用整数编号。
- 每一个 Stage 0 文档对应一个 Phase。
- 每个 Phase 的完成标准是该文档已 review、已与强制决策对齐，并可纳入 Stage 0 baseline。

## Phase 1：`docs/plan.md`

定位：

- Stage 0 的产品规划来源。
- 本文档保留文件级 `Human Decisions`。

任务：

- Review 项目目标、产品范围、假设、开放问题、核心概念、配置模型和 CLI 基线。
- 确认本文件的 `Human Decisions` 已反映到规划内容。
- 确认初始规划基线不生成实现代码。

退出标准：

- `docs/plan.md` 已被人工 review。
- 文件级 `Human Decisions` 已被确认作为后续文档同步依据。

## Phase 2：`docs/roadmap.md`

定位：

- Stage 级路线图来源。
- 本文档保留文件级 `Human Decisions`。

任务：

- Review 顶层 Stage 划分。
- 将顶层阶段命名统一为 Stage。
- 确认 Stage 0 的子阶段由单独文件描述。
- 确认 Stage 1 之前不引入真实 VCS、Verdi 或 LSF 依赖。

退出标准：

- `docs/roadmap.md` 已被人工 review。
- 文件级 `Human Decisions` 已被同步到其他文档。
- 顶层路线图使用 Stage，而不是顶层 Phase。

## Phase 3：`docs/architecture.md`

定位：

- Stage 0 的系统架构说明。

任务：

- 与文件级 `Human Decisions` 对齐。
- 明确 `nsim` 是 Python CLI 工具，不是服务端平台或 Web 应用。
- 明确 ASIC 工程、`nsim` 工具和输出目录三者独立。
- 明确 fake simulator、本地多进程、真实 EDA 工具环境门控、文件化 metadata 等架构约束。

退出标准：

- `docs/architecture.md` 不包含与 mandatory decisions 冲突的说法。
- 架构边界可以指导 Stage 1 之后的实现。

## Phase 4：`AGENTS.md`

定位：

- AI agent 的仓库入口规则和强制上下文来源。
- 本文档规定 agent 修改仓库前必须读取的治理、规划、路线图和架构基线。

任务：

- 生成 `AGENTS.md`。
- 强制要求读取 `docs/governance/nsim_codex_workflow.md`、`docs/freeze_log.md` 和 `docs/governance/change_requests.md`。
- 强制要求读取 `docs/plan.md`、`docs/roadmap.md` 和 `docs/architecture.md`。
- 明确执行具体 stage 或 phase 时必须读取目标文档和被修改文件的 `File-Level Human Review` 区块。
- 明确修改 frozen scope 前必须走 approved change request。
- 完成 draft、review、revise 和 freeze。

退出标准：

- `AGENTS.md` 已生成。
- `AGENTS.md` 已被人工 review。
- `AGENTS.md` 的强制读取规则与 Stage 0 已冻结基线一致。
- `AGENTS.md` 已冻结后，才能进入后续 Stage 0 phase。

## Phase 5：`docs/stage0-phase-planning.md`

定位：

- Stage 0 的 Phase 索引和执行说明。

任务：

- 使用整数 Phase 编号。
- 确认每一个 Stage 0 文档对应一个 Phase。
- 确认 `AGENTS.md` 已作为独立 Phase 纳入 Stage 0。
- 说明每个文档在 Stage 0 baseline 中的作用。

退出标准：

- 本文件已与文件级 `Human Decisions` 规则对齐。
- Stage 0 的文档级 Phase 划分清晰。

## Phase 6：`docs/freeze_log.md`

定位：

- Stage 0 baseline 的冻结记录。

任务：

- 记录 Stage 0 baseline 的 review 对象。
- 记录冻结前检查项。
- 在人工确认后填写冻结日期、提交 ID 和 reviewer。

退出标准：

- `docs/freeze_log.md` 已存在。
- Stage 0 baseline 可以由人工 review 后冻结。

## File-Level Human Review

### Project-Level References

- `docs/plan.md`
- `docs/roadmap.md`
- `docs/architecture.md`
- `AGENTS.md`

### Review Metadata

- Status: Approved
- Reviewer: Human
- Decision Date: 2026-05-13

### Human Decisions

- Phase 使用整数编号。
- 每一个 Stage 0 文档对应一个 Phase。
- `AGENTS.md` 是 Stage 0 Phase 4。
- `docs/stage0-phase-planning.md` 是 Stage 0 Phase 5。
- `docs/freeze_log.md` 是 Stage 0 Phase 6。
- Stage 0 不生成实现代码。

### Human Review Notes

- None

### Approval Decision

This stage0 phase planning is approved as the Stage 0 Phase 5 baseline.

### Notes

- None
