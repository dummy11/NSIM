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
- 解析并校验 seed、必需结果检查、并发上限、存储阈值、等待/重试预算和备用输出配置；后续阶段负责执行这些策略
- 空 caselist、非法任务名称/路径和无效参数给出明确错误；保留纯文本 caselist 与强 schema submit 基线

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
- 在规划时保存有效 seed、plusargs、输入身份、dirty 状态、编译身份及 task/attempt 关联
- 同 case 在不同 submit、seed、attempt 下的路径不冲突；所有路径在切换工作目录前完成解析
- Output Store 建立原子文件发布、完成 manifest 与独立故障事件目录契约；注入写入失败时不产生完整快照
- 编译和运行不隐式更新源码或清理旧回归；活动产物具备清理保护所需的引用记录

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
- 滚动并发在任意任务结束后补位，显式记录状态、退出码、时间和启动错误
- Result Checkers 用 fake 日志/指标验证有效通过、仅存活、未比较、崩溃、证据不完整与基础设施失败的区分；任一必需检查失败均不能通过
- 实时报告不发布最终失败清单，旧快照不能覆盖最终结果
- 以故障注入验证本地存储检查、空间/inode/配额不足、只读/I/O 错误、延迟或缺失日志、最终发布失败与有界等待
- 受影响存储停止新任务，健康根目录上的任务可继续；独立事件目录失败时明确报告未持久化
- Stage 4 验证检测、隔离与状态契约；用户级失败重跑与备用存储恢复入口留在 Stage 8

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
- 将有效 seed 显式传给 VCS，并从 compile artifact 元数据读取参数、工具及参考模型能力，禁止依据当前 cache 内容推断
- 接入工程提供的结果解析器，验证零退出码/零 mismatch 但未比较不能假通过；采集独立 launch stderr
- 以 npu8860_ai 的 regress 行为作为首个真实接入候选，按所需依赖逐步验证；涉及参考模型与 farm 的全流程待相应阶段完成
- 真实 EDA 运行保持环境门控；普通 CI 不依赖外部工程仓库、私有路径或商业工具

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
- 参考模型、DPI 产物及 debug 数据库身份与 compile artifact 关联；共享 cache 缺失或失效时给出明确诊断
- 工程特定参考模型构建方式通过工程适配保持隔离，不将 ACC Syscan 配置写入通用 DPI 流程

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
- 启动前在实际执行节点验证输入/编译产物可见、输出可写及工具环境兼容，不能只检查提交机
- 队列、挂载条件和工具环境可配置；记录 job id、host、退出码与时间，不依赖目录 mtime 伪造调度状态
- 验证提交机可见而执行节点不可见、loader 缺失、NFS 延迟与持续存储失败；分别记录环境原因与 DUT verdict
- 基础设施故障可隔离节点或存储根目录；旧 job 未确认终止时不重复派发同一任务

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
- 从最终快照生成失败任务集合，默认保留原 seed、有效配置、输入/编译身份，重跑输出为新 attempt 并记录来源
- 功能、参考模型和性能检查分别保留且组合判定；报告比较可区分原失败与重跑结果
- 实现有次数/时间上限的基础设施重试和已配置备用存储恢复；故障注入验证空间耗尽、持续 I/O 错误与证据丢失
- 健康存储恢复只使用可验证输入；输入不可得或旧 job 状态未知时停止，不承诺进程断点续跑或丢失产物恢复
- 清理为独立操作，保护活动产物及恢复所需证据；验证失败 attempt 不被重跑覆盖
- 对照首个真实工程完成环境门控的回归、结果检查和重跑验收，备份/修盘仍由基础设施负责

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

### Review 2026-09-20 CR-20260920-001

Reviewer: Human
Status: Approved

Findings:
- 用户已审阅回归经验和存储故障建议，并授权「行，都修改一下」。

Required Changes:
- 将已讨论要求纳入本文件，并与产品、架构和路线图同步。

Resolution:
- 按 CR-20260920-001 批准范围修订；本记录是范围授权落实，不声明后续功能已实现或真实环境已验证。

## Revision Log

- 2026-09-20: CR-20260920-001，补充回归可靠性与存储故障检测、隔离和恢复契约；保留原历史决策与阶段主目标。

- 2026-05-14: Rebaseline governance metadata to support living-document updates and machine-checked frozen sections.

## File-Level Human Review

### Project-Level References

- None

### Review Metadata

- Status: Approved
- Reviewer: Human
- Decision Date: 2026-09-20

### Human Decisions

- 将各个顶层 phase 改成各个 stage。
- Stage 0 各个子阶段用一个单独文件描述，例如 `docs/stage0-phase-planning.md`。

- CR-20260920-001 的回归可靠性和存储恢复要求分配到现有 Stage 2-8；Stage 0 仍只修订文档，Stage 1 仍为包与 CLI 骨架。
- Stage 3 建立追溯与输出契约，Stage 4 通过 fake 故障注入验证执行和存储检测/隔离，Stage 7 验证 farm 环境差异，Stage 8 实现重跑与备用存储恢复。

### Human Review Notes

- None

### Approval Decision

CR-20260920-001 scope approved by Human on 2026-09-20; applied to the requirements in this document. Historical baseline approval follows.

approved.

### Notes

- CR-20260920-001：已落实用户授权的回归与存储可靠性修订；新增要求按路线图分阶段实现。
