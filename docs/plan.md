# nsim 项目规划

## 目标

`nsim` 是一个 ASIC 仿真回归平台。它负责协调多个 ASIC 工程的仿真活动，同时保持三个领域彼此独立：

- `nsim` 工具及其有版本管理的 Python 包
- ASIC 工程仓库及其本地元数据
- 生成的仿真输出，例如 build、log、wave 和 report

首个可运行版本的开发目标是建立一个小而可执行的基础系统：能够发现工程、识别版本、加载 case 和 submit 定义，并运行一次可追溯的本地回归。VCS、Verdi、DPI、FSDB、多进程和 LSF 支持在工程模型稳定后按功能逐步加入。

## 产品范围

### 范围内

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

## 开放问题（Open Questions）

- 工程描述文件的文件名和格式是否固定为 `nsim.yaml`、`nsim.toml`，还是允许多种格式？
- caselist 采用纯文本、YAML、TOML、JSON，还是兼容已有工程格式？
- submit JSON 的 schema 由 `nsim` 强约束，还是允许工程扩展自定义字段？
- ASIC 工程 tag 如何定义：优先使用 git tag、commit SHA，还是允许用户传入工程自定义版本号？
- output root 的默认位置应来自用户配置、环境变量，还是当前工作目录下的默认目录？
- VCS compile 是否按 submit 独立执行，还是支持多个 simulation submit 共享同一个 compile artifact？
- FSDB dump 由 submit JSON 直接控制，还是由 testcase、testbench 或运行时 plusarg 共同决定？
- DPI 编译产物是否按工程共享，还是按 regression/run 隔离生成？
- LSF 队列、资源和 license 约束应建模在 submit JSON 中，还是独立为 scheduler profile？
- regression metadata 的长期存储先保持文件形式，还是需要预留后续数据库索引接口？





## 核心概念

### nsim 安装

安装后的 Python 包提供 CLI、核心编排代码、调度器适配器、仿真器适配器和报告生成能力。

### ASIC 工程

ASIC 工程是一个独立版本管理的仓库或目录树，包含源文件和 `nsim` 工程描述文件。`nsim` 必须能在不把工程复制进 `nsim` 仓库的情况下发现并识别工程。

### 输出根目录

仿真输出默认位于 `nsim` 包和 ASIC 源码树之外。每次运行都有独立输出目录，包含 build 产物、log、wave、report 和机器可读元数据。

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

平台应优先使用显式的文件化配置：

- 工程描述文件：标识 ASIC 工程及其支持的仿真根目录
- caselist：列出 testcase 及每个 case 的属性
- submit JSON：定义编译、仿真、波形、DPI 和调度选项
- nsim 配置：定义输出根目录、调度默认值、保留策略和工具路径等默认项

配置应被复制或渲染到每次 regression 输出中，使结果在工程文件变化后仍可复现。

## CLI 基线

CLI 后续应支持：

```bash
nsim --version
nsim bootstrap --name verification --rtl-root <rtl> --rtl-filelist <rtl.f>
nsim list
nsim status
nsim update --rtl-root <rtl> --rtl-filelist <rtl.f>
nsim switch <version>
nsim case list --project <project> --caselist <file>
nsim run case --project <project> --case <name> --submit <submit.json>
nsim run regression --project <project> --caselist <file> --submit <submit.json> --submit <other.json>
nsim report show <regression-id>
nsim verdi open <run-id>
```

具体命令名称可以在 CLI 设计阶段调整，但接口必须保持显式且便于脚本调用。

## 回归可靠性与恢复要求

以下是回归可靠性与恢复的产品要求。

- 测试通过必须有完整的结束证据和所需检查结果。仅正常退出、零 mismatch、未执行比较或日志缺失均不能替代有效检查；允许工程显式选择仅存活检查，但报告必须与功能通过区分。
- 在任务规划时确定 seed，并保存 case、submit、plusargs、工程版本、工具版本、编译身份与配置快照。相同 seed 是复现条件之一，不单独保证结果完全一致。
- 每次 regression、每组 submit、每个 case/seed 和每次 attempt 拥有独立身份与输出路径；失败重跑保留原结果，并记录来源关联。
- 任务按并发上限持续补位；实时进度与最终报告分离。只有完整的最终快照可以作为通过判定和失败重跑输入。
- 允许工程提供结果解析与检查扩展，将功能、参考模型和性能检查分别记录，再按已快照的策略汇总；ACC 特定日志和性能公式不写入通用核心。
- 回归读取确定的工程输入，编译和运行不得隐式 git pull 或清理旧回归。清理单独执行，并保护正在使用的编译产物和运行证据。
- 检测实际执行节点的路径可见性、输出可写性、可用空间、inode 及可获取的配额信息；启动前检查不能代替运行时 I/O 错误处理。
- 挂载缺失、空间不足、配额不足、持续 I/O 错误应记录为基础设施失败，与 DUT 验证失败分开。受影响节点或存储停止接收新任务，健康资源上的无关任务可继续。
- 瞬态存储故障与产物可见性延迟采用有上限的等待或重试。持续故障只允许在配置好的健康存储上，从可信输入创建新 attempt；无法验证输入时停止恢复。
- 证据缺失或损坏时标记不完整，不能据此宣布通过。原子写入防止半份报告；硬盘损坏后的数据恢复依赖底层备份与冗余，NSIM 不负责修盘、修文件系统或提供存储备份服务。

### 工程经验来源与适用边界

参考工程为 `npu8860_ai/acc`，检查对象为 `sim/regress/Makefile`、`collect_results.py`、`collect_performance.py` 及其测试，故障历史见该工程 `docs/handoff.md` 与 `docs/handoff_archive.md`。这些是外部工程定位信息，不是 NSIM 的运行依赖或公开测试资产。

已有脚本体现了显式 seed、逐 case 目录、失败清单、独立重跑、滚动并发、结果检查和原子报告写入。历史记录描述了 `/space8` 在部分 LSF 节点不可见及 VCS runtime 环境差异，通过选择兼容队列解决启动故障；并使用有限重读处理 NFS 日志可见性延迟。未发现物理硬盘损坏后自动恢复的实现。

NSIM 将该工程作为首个真实接入候选；通用 CI 使用自建的最小 fake fixture，真实接入在具备工具与 farm 的环境中验证。静态 HTML/Markdown 报告属于文件产物，不引入 Web 服务。队列名、挂载路径、工具环境、存储阈值和恢复目标必须可配置，不能固化参考工程的环境值。

## 开发原则

- 将仿真器相关逻辑封装在适配器边界之后。
- 将工程发现与执行流程分离。
- 优先依赖机器可读的运行元数据，而不是解析人工 log。
- 先实现本地执行，再加入 LSF 扩展。
- 将每次 regression 视为可审计 artifact。
