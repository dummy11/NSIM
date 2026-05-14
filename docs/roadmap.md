# nsim 路线图

## Stage 0：治理与规划

目标：在实现前建立可 review 的基线。

交付物：

- `docs/plan.md`
- `docs/architecture.md`
- `docs/roadmap.md`
- `docs/stage0-phase-planning.md`
- `docs/freeze_log.md`
- `AGENTS.md`

退出标准：

- 项目范围已 review
- 文件级 `Human Decisions` 已同步到其他规划文档
- 架构边界已接受
- 已选择第一个实现里程碑
- Stage 0 各个子阶段由 `docs/stage0-phase-planning.md` 描述
- 基线已冻结

## Stage 1：Python 包与 CLI 骨架

目标：让 `nsim` 在不集成仿真器的情况下可安装、可执行。

交付物：

- `pyproject.toml`
- `src/nsim/` 包结构
- 名为 `nsim` 的 CLI entry point
- `nsim --version`
- 基础命令结构
- 初始单元测试配置
- 支持记录当前工程使用的 `nsim` 版本

退出标准：

- package 可以 editable install
- `nsim --version` 可运行
- 测试可以在本地运行
- 不生成真实 VCS、Verdi 或 LSF 依赖

## Stage 2：工程发现与配置

目标：识别 ASIC 工程并校验用户输入。

交付物：

- `nsim.yaml` 和 `nsim.toml` 工程描述文件 schema
- 工程发现服务
- 纯文本 caselist parser
- 强 schema 校验的 submit JSON parser
- 配置校验错误
- 示例工程 fixture
- 工程级 `nsim` 版本约束或选择

退出标准：

- `nsim project list` 能发现示例工程
- 无效描述文件能给出清晰错误
- caselist 和 submit JSON 能被加载为类型化模型
- 不同工程可以表达不同的 `nsim` 版本需求

## Stage 3：Regression 规划与 Output Store

目标：将工程输入转换为可追溯的 regression artifact。

交付物：

- regression id 生成
- 输出目录创建
- 配置快照
- 基于 case 和 submit 的任务展开
- regression 元数据写入
- report 骨架

退出标准：

- dry-run regression 生成完整元数据记录
- build、log、wave 和 report 目录被确定性创建
- 配置快照被保存到每次 regression

## Stage 4：使用 Fake Simulator 的本地执行

目标：在不依赖商业工具的情况下验证调度和报告行为。

交付物：

- scheduler interface
- local multiprocessing scheduler
- fake simulator adapter
- 任务状态跟踪
- log 捕获
- pass/fail report 生成

退出标准：

- 本地 regression 能并发运行多个 fake case
- 失败会被记录，且除非配置要求，否则不阻塞无关任务
- summary report 与任务元数据一致

## Stage 5：Synopsys VCS 集成

目标：通过 adapter 边界编译并运行真实 VCS 仿真。

交付物：

- VCS command builder
- 按 submit 独立规划的 compile task 支持
- simulation task 支持
- plusarg 和选项处理
- submit 与 testbench plusarg 协同控制的 FSDB dump 选项处理
- compile 和 run log 捕获

退出标准：

- 小型示例或用户提供工程可以通过 VCS compile 和 simulate
- 生成命令可从元数据复现
- compile 和 simulation 失败被清晰分类

## Stage 6：DPI 与 Verdi 支持

目标：支持常见 debug 和混合语言仿真流程。

交付物：

- C 和 C++ DPI 输入建模
- DPI compile command 集成，使用独立 Makefile 产出 `.so`
- DPI 产物默认工程共享，并允许通过 submit 改为 regression/run 隔离
- Verdi launch command builder
- wave 路径发现
- debug 元数据

退出标准：

- DPI library 可以通过独立 Makefile 构建为 `.so`
- 已完成 run 可以带正确 FSDB 上下文打开 Verdi

## Stage 7：LSF 调度

目标：将 regression 扩展到 farm 执行。

交付物：

- LSF scheduler adapter
- submit JSON 和 `nsim` 命令行中的 queue、resource 和 license 约束
- job 提交、轮询和取消
- farm log 捕获
- 调度器特有元数据

退出标准：

- regression 任务可以提交到 LSF
- job 状态反映在 nsim 元数据中
- 本地和 LSF 调度共享同一套报告路径

## Stage 8：Regression 运维能力

目标：让 regression 适合工程师重复日常使用。

交付物：

- rerun failed tasks
- report comparison
- 保留策略扩展点
- log 索引改进
- 稳定 regression archive 格式

退出标准：

- 工程师可以检查、重跑和比较 regression，而不需要手动翻目录
- regression artifact 在工具和工程版本变化后仍可追溯

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
- Stage detail clarifications that do not alter approved stage boundaries

## Review Trace

### Review 2026-05-12

Reviewer: Human
Status: Approved

Findings:
- Top-level roadmap stages are approved as Stage units.
- Stage 0 subphases are delegated to `docs/stage0-phase-planning.md`.

Required Changes:
- None

Resolution:
- Approved as Stage 0 Phase 2 baseline.

## Revision Log

- 2026-05-14: Rebaseline governance metadata to support living-document updates and machine-checked frozen sections.

## File-Level Human Review

### Project-Level References

- None

### Review Metadata

- Status: Approved
- Reviewer: Human
- Decision Date: 2026-05-11

### Human Decisions

- 将各个顶层 phase 改成各个 stage。
- Stage 0 各个子阶段用一个单独文件描述，例如 `docs/stage0-phase-planning.md`。

### Human Review Notes

- None

### Approval Decision

approved.

### Notes

- None
