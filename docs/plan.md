# NSIM 开发规划

## 目标

`nsim` 是一个 ASIC 仿真回归平台。它负责协调多个 ASIC 工程的仿真活动，同时保持三个领域彼此独立：

- `nsim` 工具及其有版本管理的 Python 包
- ASIC 工程仓库及其本地元数据
- 生成的仿真输出，例如 build、log、wave 和 report

当前候选实现已具备项目注册、版本选择、submit 继承、编译/仿真分离、local/LSF 调度、日志分析和终端进度。原生 VCS、Verdi、DPI、FSDB 集成仍停留在命令预览；工程可以通过 command 包装器执行实际编译与仿真。下文区分已确定接口与后续目标，验证范围见 [实现状态](implementation_status.md)，后续任务见 [待办](backlog.md)。

## 产品范围

### 产品目标范围（包含后续工作）

- 使用 `pyproject.toml` 管理 Python 包
- CLI 命令名为 `nsim`
- 支持 `nsim --version`
- 自动发现 ASIC 工程
- 分离工具安装、工程配置和输出目录
- 有版本可追溯的 regression 记录
- 使用 caselist 组织 testcase
- 使用 JSON 文件组织 submit 配置
- 单个 case 可以指定一个 submit 运行
- 一次 regression 可以指定多个 submit
- 管理 build、log、wave 和 report 目录
- 集成 Synopsys VCS 编译与仿真
- 支持 Verdi 波形调试启动
- 支持 FSDB 波形 dump 配置
- 支持 C 和 C++ DPI 编译
- 本地多进程调度
- 面向大规模回归的 LSF 调度适配器
- log 捕获、索引和保留策略扩展点

### 初始规划基线范围外

- 立即实现完整仿真引擎
- 在文件元数据证明不足之前引入数据库
- 直接管理 license server
- 替代工程已有的 Makefile、filelist 或仿真脚本
- 创建 Web UI

## 假设（Assumptions）

- `nsim` 初期以 Python CLI 工具形态交付，而不是服务端平台或 Web 应用。
- ASIC 工程可以通过一个明确的工程描述文件被发现和识别。
- ASIC 工程自身继续维护源码、filelist、testbench、DPI 源码和已有脚本。
- 仿真输出默认可以写入工程目录之外的独立 output root。
- submit JSON 是仿真选项矩阵的主要组织方式。
- caselist 是 testcase 集合的主要组织方式。
- 初期可用 fake simulator 和本地多进程调度验证核心流程，不要求立即接入真实 VCS、Verdi 或 LSF。
- 真实 EDA 工具相关测试需要通过环境门控运行，普通 CI 不假设存在 VCS、Verdi 或 LSF。
- regression 可追溯性优先依赖文件快照、hash 和机器可读 metadata，而不是人工命名约定。

## 已确定的接口

| 领域 | 当前约定 |
| --- | --- |
| 工程配置 | 新项目使用 `.nsim/nsim.toml` schema 2；兼容读取根目录 schema 1 TOML/YAML。版本单独保存在 `.nsim/.nsim_version`。 |
| Caselist | 纯文本，每行一个 case，支持注释、多清单合并及来源追踪。 |
| Submit | 严格 JSON schema 1；支持一个父模板、一层继承，合并后校验。扩展命令通过 argv、env 和 hooks 配置。 |
| 输入身份 | 保存 verification/RTL 各自 Git 身份，以及声明输入、配置和工具的内容哈希；Git tag 不替代文件校验。 |
| 输出根 | 命令行覆盖项目默认值，再回退兼容变量 `NSIM_OUTPUT_ROOT`；输出根须预先存在。 |
| 编译复用 | 同一 submit/输出根在一次 run/regr 内共享编译；sim 显式复用兼容产物，不自动补编译。不同 submit 独立编译。 |
| FSDB / DPI | FSDB 由配置与 plusargs 配合；DPI 提供 project/regression/run 范围的命令预览，实际工具验收待完成。 |
| 调度 | 默认 LSF，可切 local；队列、资源与 license 在 submit 中配置，CLI 可覆盖。每次调用共用一个并发窗口。 |
| 记录与报告 | 文件化 plan、attempt records、state、final；回归另有终端 Dashboard、有界 live.json 和最终静态报告。 |

后续需要真实环境确定原生 EDA 的执行细节、共享文件系统保障及长期归档策略；不将这些待验收内容作为现有能力承诺。

## 核心概念

### nsim 安装

安装后的 Python 包提供 CLI、核心编排代码、调度器适配器、仿真器适配器和报告生成能力。

### ASIC 工程

ASIC 工程是一个独立版本管理的仓库或目录树，包含源文件和 `nsim` 工程描述文件。`nsim` 必须能在不把工程复制进 `nsim` 仓库的情况下发现并识别工程。

### 输出根目录

仿真输出默认位于 `nsim` 包和 ASIC 源码树之外。输出按 submit 分组，每个工作区内编译位于 com_dir，仿真位于 sim_dir/case_name_seed；相同 case/seed 串行覆盖旧产物。批次状态与汇总放在 regr_dir。

### Regression 记录

一次 regression 需要通过以下信息追溯：

- ASIC 工程身份和 tag
- `nsim` 版本
- `nsim` 配置快照
- 使用的 submit 定义
- caselist 快照
- 调度模式
- 开始和结束时间
- 结果摘要

## 仓库结构

NSIM 开发仓库包含 `pyproject.toml`、`src/nsim/`、`tests/`、`examples/`、`scripts/`、`docs/` 和 `user_guide.md`。安装包只交付 `nsim` Python 包及命令入口；工程源码与运行产物存放在独立位置。实际结构以仓库文件为准。

## 配置模型

当前使用显式文件与命令行参数组织配置：

- 工程描述文件：标识 verification/RTL、输入清单和默认输出根
- caselist：按行列出 testcase 名称
- submit JSON 及公共模板：定义编译、仿真、波形、DPI、hooks 和调度选项
- 命令行：选择执行阶段、工作区、并发、一个输出根及调度设置；再次执行由显式 rerun/recover 发起

配置原文和有效配置保存到每次计划，recover 校验历史输入与运行时；rerun 对当前输入重新规划，在独立目录强制编译并固定失败 case/seed。快照用于追溯，不能在原始依赖变化或丢失后自动重建它们；精确重跑仍要求原输入可验证。保留策略和全局配置层未实现。

## CLI 基线

当前包提供以下入口。路径、队列和 submit 需要按实际工程替换；安装与版本目录命令见 [使用指南](../user_guide.md)。

```bash
nsim --version
nsim bootstrap --name verification --rtl-root <rtl> --rtl-filelist <rtl.f>
nsim list
nsim status
nsim update --rtl-root <rtl> --rtl-filelist <rtl.f>
nsim switch <version>
nsim project list <search-root>
nsim project inspect <project-path>
nsim com --project <project> --submit <submit.json> [-mode rtl] [-dir <workspace>] [-com-dir <path>]
nsim sim --project <project> --case <name> --submit <submit.json>
nsim run --project <project> --case <name> --submit <submit.json>
nsim regr --project <project> -l <file> <other-file> -sub <submit> <other-submit> -seed 42 43
nsim regr --project <project> -l <file> -sub <submit> -repeat 10
nsim report <record-dir>
nsim rerun <record-dir>
nsim recover <record-dir> --output-root <healthy-output-root>
nsim storage check <output-root>
```

`report/rerun/recover` 接收记录目录路径，不是裸 ID。`case list`、`verdi open` 尚未作为 CLI 子命令提供；Verdi 命令目前包含在 EDA dry-run 输出中。执行参数见 [执行阶段](execution_stages.md)。

## 回归可靠性与恢复要求

以下是回归可靠性与恢复的产品要求。

- 测试通过必须有完整的结束证据和所需检查结果。仅正常退出、零 mismatch、未执行比较或日志缺失均不能替代有效检查；允许工程显式选择仅存活检查，但报告必须与功能通过区分。
- 在任务规划时确定 seed，并保存 case、submit、plusargs、工程版本、工具版本、编译身份与配置快照。相同 seed 是复现条件之一，不单独保证结果完全一致。
- regression 和 attempt 拥有独立身份；同一 submit 的 case/seed 使用固定输出路径，失败重跑覆盖旧产物并记录来源关联。批次记录保留，单跑记录随目录更新。
- 任务按并发上限持续补位；实时进度与最终报告分离。只有完整的最终快照可以作为通过判定和失败重跑输入。
- 允许工程提供结果解析与检查扩展，将功能、参考模型和性能检查分别记录，再按已快照的策略汇总；ACC 特定日志和性能公式不写入通用核心。
- 回归读取确定的工程输入，编译和运行不得隐式 git pull。com/run/regr 重建选中的编译目录，sim/run/regr 在锁保护下覆盖选中的 case/seed；批次历史记录保留。面向历史输出的批量清理仍是待实现的独立功能。
- 检测实际执行节点的路径可见性、输出可写性、可用空间、inode 及可获取的配额信息；启动前检查不能代替运行时 I/O 错误处理。
- 挂载缺失、空间不足、配额不足、持续 I/O 错误应记录原始操作、错误码和路径，与 DUT 验证失败分开。NSIM 不根据单个 case 的错误推断磁盘故障或自动避开目录；后续 case 各自执行。
- 产物可见性延迟采用有上限的等待。需要换根或重新执行时，由用户显式 rerun/recover；无法验证输入或 LSF 旧 job 状态时拒绝盲目恢复。
- 证据缺失或损坏时标记不完整，不能据此宣布通过。原子写入防止半份报告；硬盘损坏后的数据恢复依赖底层备份与冗余，nsim 不负责修盘、修文件系统或提供存储备份服务。

### 工程经验来源与适用边界

参考工程为 `npu8860_ai/acc`，检查对象为 `sim/regress/Makefile`、`collect_results.py`、`collect_performance.py` 及其测试，故障历史见该工程 `docs/handoff.md` 与 `docs/handoff_archive.md`。这些是外部工程定位信息，不是 nsim 的运行依赖或公开测试资产。

已有脚本体现了显式 seed、逐 case 目录、失败清单、独立重跑、滚动并发、结果检查和原子报告写入。历史记录描述了 `/space8` 在部分 LSF 节点不可见及 VCS runtime 环境差异，通过选择兼容队列解决启动故障；并使用有限重读处理 NFS 日志可见性延迟。未发现物理硬盘损坏后自动恢复的实现。

NSIM 将该工程作为首个真实接入候选；通用 CI 使用自建的最小 fake fixture，真实接入在具备工具与 farm 的环境中验证。静态 HTML/Markdown 报告属于文件产物，不引入 Web 服务。队列名、挂载路径、工具环境、存储阈值和恢复目标必须可配置，不能固化参考工程的环境值。

## 开发原则

- 将仿真器相关逻辑封装在适配器边界之后。
- 将工程发现与执行流程分离。
- 优先依赖机器可读的运行元数据，而不是解析人工 log。
- 使用 local 和进程级 LSF 测试服务验证共用流程，再进行真实工具与 farm 验收。
- 将每次 regression 视为可审计 artifact。
