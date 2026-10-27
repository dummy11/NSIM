# nsim 架构

## 系统边界

`nsim` 以 Python CLI 工具形态交付，而不是服务端平台或 Web 应用。架构上有三个相互独立的领域：

1. **工具领域**：已安装的 Python 包、CLI、适配器、调度器和报告代码。不同 ASIC 工程可以使用不同版本的 `nsim`。
2. **工程领域**：ASIC 工程仓库、工程描述文件、源码树、filelist、testbench 资源、DPI 源码、caselist 和 submit JSON 文件。
3. **输出领域**：生成的 build 目录、仿真 log、FSDB wave、report 和 regression 元数据。

架构必须防止这些领域发生意外耦合。`nsim` 默认只读取工程输入、创建输出工作区，并将结果写入工程源码树之外的独立 output root，不修改 ASIC 工程源码。

## 高层组件

```text
CLI
 |
 v
Command Services
 |
 +-- Discovery Service
 +-- Config Loader
 +-- Project Model
 +-- Regression Planner
 +-- Scheduler Interface
 |     +-- Local Multiprocessing Scheduler
 |     +-- LSF Scheduler
 +-- Simulator Interface
 |     +-- Synopsys VCS Adapter
 |     +-- Verdi Adapter
 +-- Output Store
 +-- Result Checkers
 +-- Report Generator
```

## 组件职责

### CLI

解析用户命令，校验参数，选择目标工程，并委托给 command service。CLI 不应包含仿真器或调度器业务逻辑。

### Discovery Service

项目通过 `nsim bootstrap` 初始化注册到用户级索引，`nsim list` 查询全部登记。`status/update/switch` 共用从当前目录向上查找最近 `.nsim` 的发现逻辑，也支持显式项目名称或路径。新项目使用 `.nsim/nsim.toml` schema 2；旧 schema 1 的 TOML/YAML 扫描读取继续兼容。损坏的最近项目不能被跳过；新旧布局冲突必须显式报错。

### Config Loader

加载并校验：

- nsim 全局或 workspace 配置
- 工程描述文件
- 纯文本 caselist
- submit JSON 文件，并按 `nsim` 定义的 schema 强校验

schema 2 的相对路径以 verification 根目录为基准，schema 1 保留描述文件所在目录的旧语义。工具版本只有 `.nsim/.nsim_version` 一个来源；新项目默认解析最新已安装可用稳定版本并保存具体版本号。注册索引仅保存名称与配置入口，配置修改采用锁及可恢复事务。稳定安装入口与项目 bootstrap/update 不复用命令含义。

### Project Model

表示项目名称、verification 与 RTL 两套根目录、两套 filelist、显式执行输入以及工具版本。项目名来自 verification 目录名，在本机用户索引内唯一。注册不要求 RTL 版本；每次规划分别采集两套源码的 Git 身份和实际输入哈希。schema 2 的注册配置保存为计划快照，后续 update 不重绑历史回归；实际执行输入仍严格校验。

### Regression Planner

将用户请求展开为具体运行任务。例如，一个包含 20 个 case 的 caselist 和 3 个 submit JSON 文件会被展开为 60 个仿真任务，并在需要时附加编译任务。VCS compile 当前按 submit 独立规划，compile artifact 也按 submit 独立生成和追踪。

### Scheduler Interface

定义 planned task 的执行契约。调度器适配器接收任务描述，并返回状态变化、log、退出码和资源元数据。

### Local Multiprocessing Scheduler

在当前机器上以可配置并发度运行任务。它应作为第一个实现的调度器，并可配合 fake simulator 验证核心流程，因为它不依赖真实 VCS、Verdi 或 LSF。

### LSF Scheduler

将任务提交到 LSF，用于大规模 regression。队列、资源和 license 约束可以来自 submit JSON，也可以由 `nsim` 命令行覆盖。命令行约束优先级高于 submit 配置。该适配器应把 `bsub`、`bjobs` 和状态解析隔离在稳定 Python 接口之后。

### Simulator Interface

定义仿真器操作，例如 compile、simulate、waveform 配置和 debug 启动。

### Synopsys VCS Adapter

根据工程元数据和 submit JSON 构建 VCS 编译与仿真命令。它应支持：

- 编译选项
- 仿真选项
- plusargs
- FSDB dump 选项，允许 submit 和 testbench plusarg 共同配合控制
- DPI C 和 C++ 编译输入；DPI 编译使用独立 Makefile，编译结果以 `.so` 形式存在；DPI 产物默认工程共享，也可以通过 submit 控制为按 regression 或 run 隔离
- 环境变量
- timeout 和 retry 策略扩展点

DPI `.so` 的默认共享位置位于工程 cache。

### Verdi Adapter

基于已完成或进行中的 run，使用正确的 FSDB、设计数据库和工程上下文启动 Verdi。Verdi adapter 不负责仿真流程本身。Verdi debug 所需设计数据库路径由 VCS adapter 生成并记录到 task metadata。

### Output Store

创建和管理输出目录：

```text
<output-root>/
  regressions/
    <regression-id>/
      metadata.json
      config/
      build/
      logs/
      waves/
      reports/
      tasks/
```

Output root 的默认位置来自用户配置或环境变量。Output Store 负责稳定路径、元数据写入以及后续保留策略。

### Report Generator

读取任务元数据和 log，生成面向人的报告和机器可读摘要。报告应基于已记录 artifact 生成，而不是依赖实时调度器状态。

## 数据流

1. 用户运行 `nsim` CLI 命令。
2. CLI 解析配置和工程搜索根目录。
3. Discovery 识别目标 ASIC 工程。
4. Config Loader 读取工程描述文件、caselist 和 submit JSON。
5. Regression Planner 将输入展开为任务定义。
6. Output Store 创建 regression 目录并快照配置。
7. Scheduler 使用 simulator adapter 执行任务。
8. 每个任务写入 log、wave、build 输出、状态和元数据。
9. Report Generator 汇总完成后的 regression。

## 可追溯模型

每次 regression 应写入一个 `metadata.json` 记录，包含：

- regression id
- 工程名
- 工程根目录
- 工程 tag 或 commit
- 当前工程使用的 `nsim` 版本
- `nsim` 配置 hash
- caselist hash
- submit JSON hash
- 调度器适配器
- 仿真器适配器
- 命令行调用
- 环境摘要
- 任务列表和最终状态

该元数据是 rerun 和 debug 的主要审计记录。Regression metadata 统一使用一个 schema version。长期存储先保持文件形式；只有当文件元数据和索引能力被证明不足时，才考虑引入数据库。

## 任务身份、执行与结果契约

### 规划与编译身份

Planner 在执行前生成稳定的 task id、有效 seed 和初始 attempt id，明确 compile → simulate 依赖。相同 submit 的 case 可以复用同一个受追踪 compile artifact；不同 submit 仍独立编译。编译元数据记录工程身份、输入 hash、工具版本、有效参数、Golden/参考模型是否启用及其身份；不得以当前 cache 目录是否存在推断旧二进制的能力。

输入快照记录工程 commit/tag、dirty 状态、相关输入 hash、caselist、有效 submit、plusargs、工具与环境身份。源码引用必须可验证；输入已变化且无法取得原内容时，不能标为精确重跑。NSIM 不自动更新源码；调用工程构建入口时也必须禁止隐式拉取源码和清理既有回归。

保持现有输出根布局，build 按 submit/compile id 隔离，logs、waves、tasks 下按 task id/attempt id 隔离。名称由受控 id 映射，不能直接将不受校验的 case 名拼为路径。路径在配置加载时解析，改变工作目录后不丢失原含义。重跑和恢复均创建新 attempt，不覆盖旧结果；清理需确认产物不再被活动任务引用。

### 调度状态与结果判定

调度状态表示 planned、queued、running、completed、failed、cancelled 等执行进度；检查 verdict 表示验证结果，两者独立记录。具体枚举和字段在相应实现阶段定稿。

- Scheduler 显式保存 job id、执行节点、提交/启动/结束时间、退出码、启动 stderr 和失败原因；时间未知时保持未知，不从目录 mtime 伪造真实调度时间。
- 本地调度采用有上限的滚动并发窗口；一个任务结束即补充下一个。单个 DUT 失败按策略处理，不阻塞无关任务。
- 零退出码不足以通过；非零退出码、超时、取消或证据不完整也不能被日志中的成功 banner 覆盖。
- 区分验证失败、未执行所需比较、仅存活检查通过、崩溃/超时、基础设施失败和证据不完整。所需比较未发生时，即使 mismatch 为零也不通过。

### Result Checkers 与最终发布

工程提供受版本追踪的解析器，将原始日志或结构化记录转换成统一检查结果：检查器身份、schema version、是否必需、是否实际执行、verdict、失败原因和证据引用。核心不内置 ACC banner、Syscan 参数或 ACC 性能公式。功能、Golden、性能等必需检查全部有效通过，且执行及证据均完整，才允许最终通过；仅存活检查必须由策略显式选择并在报告中标识。

Report Generator 从持久化任务和检查结果生成 JSON 与面向人的 Markdown/静态 HTML。实时快照可显示排队/运行，但不得发布最终失败清单。最终结果和重跑清单引用同一已完成快照；采用单写者或有序发布，防止旧实时快照覆盖新终态。

Output Store 在同一文件系统内用唯一临时文件与原子替换发布完整文件。多文件结果以最后发布的完成 manifest 关联各文件身份/hash；任何必需文件发布失败，都不能将该批次标为完整。单文件原子替换不等价于多文件事务、断电持久性或备份。

### 重跑与恢复来源

失败重跑从最终快照选取未满足该批策略的 task，保存 source regression/task/attempt，默认使用原 seed、有效 submit、plusargs 和可验证的工程/编译身份。用户更换 seed 或输入时必须记录差异。编译产物损坏时只能从可验证输入重新构建并记录新身份。

重跑或基础设施重试不会删除原失败。自动重试必须有次数、时间预算及适用原因限制；未确认旧 job 已终止或取消时不得重叠启动副本，应保留未知状态并等待处置。

## 存储与执行环境故障契约

Storage 检查由 Output Store 提供，Scheduler 在实际执行节点启动任务前调用。检查输入、编译产物和输出路径的可见性与读写能力，采集节点、挂载/文件系统信息、可用字节、inode 和可获得的配额信息。无法查询的配额标记未知，不能报告已验证。阈值、重试预算及备用 output root 来自配置并纳入快照。

LSF 队列配置必须表达或引用所需工具环境与存储条件；编译和仿真不要求同一主机，但执行环境必须兼容、产物必须一致可见。提交机上的文件检查不能替代执行节点检查；不把 `nightly` 或 `/space8` 写死为平台默认值。

| 故障类别 | 检测与记录 | 处理与恢复条件 |
|---|---|---|
| 挂载缺失、权限不足、工具 loader 缺失 | 在执行节点检查并保存路径、节点、原始 stderr 与原因；不单凭 No such file 区分缺文件和 loader 问题 | 标记基础设施失败；隔离不兼容节点/配置，条件恢复或选择已配置兼容资源后才可新建 attempt |
| 磁盘空间、inode 或配额耗尽 | 启动前检查并识别运行中 ENOSPC/EDQUOT 等写入错误 | 暂停向受影响存储派发；保留可取得的证据，不自动删除旧回归；恢复空间后验证读写再继续 |
| NFS 延迟、短暂不可用 | 将 job 完成与产物可见分开；记录有截止时间的重读与重试 | 缺失或不完整的必需产物均允许有限等待；超预算记为证据不完整/存储故障，不能无限等待或判 PASS |
| 持续 I/O 错误、磁盘损坏 | 保存首次错误、受影响根目录和最后可信状态 | 隔离故障存储；在已配置健康根目录校验输入后创建新 attempt；原产物缺失标记丢失，不声称已经恢复 |
| 截断日志、校验失败、发布失败 | 对照完成 manifest、检查器要求与 hash 验证证据 | 禁止完整通过；保留原故障，必要时从可信输入重跑 |

存储故障还可能使故障记录本身无法写入原 output root。调度控制端应将最小事件（task/attempt、节点、路径、错误、时间）写入独立于该根目录的已配置健康事件目录；若也不可写，停止相关派发，以非零退出状态和 stderr 告知证据未持久化，不能假装记录成功。控制事件保持文件形式，不引入数据库。

默认恢复粒度为从输入重新运行一个 attempt，不承诺仿真进程断点续跑。故障范围按节点或存储根目录隔离；只有重新验证挂载、读写及容量后才能解除隔离。物理硬盘和文件系统修复、备份管理由基础设施负责；NSIM 负责检测、隔离、证据状态与受控重跑。

## Architecture Assumptions

- `nsim` 以 Python CLI 工具形态交付，而不是服务端平台或 Web 应用。
- ASIC 工程通过明确的工程描述文件被发现和识别。
- `nsim` 工具、ASIC 工程源码和仿真输出目录彼此独立。
- 不同 ASIC 工程可以使用不同版本的 `nsim`。
- 初期使用 fake simulator 和本地多进程调度验证核心流程。
- VCS、Verdi 和 LSF 通过 adapter 接入，不进入 CLI 业务逻辑。
- DPI 编译使用工程提供的独立 Makefile，产物以 `.so` 形式存在。
- Regression metadata 初期使用文件形式存储。

## Architecture Open Questions

- None

## 错误处理

错误应被归类为明确类型：

- 配置错误
- 工程发现错误
- 规划错误
- 调度提交错误
- 调度运行时错误
- 仿真器编译错误
- 仿真器运行错误
- 报告生成错误
- 存储/执行环境基础设施错误
- 证据缺失、损坏或发布不完整

每个失败任务都应保留足够上下文，使工程师能够手动复现失败命令。

## 测试策略

架构应支持在没有商业 EDA 工具的情况下测试：

- 配置解析、工程发现、任务规划和输出路径的单元测试
- 使用 fake simulator adapter 测试命令生成
- 使用 fake scheduler 测试任务状态变化
- 使用示例 ASIC 工程 fixture 的集成测试
- 面向真实 VCS、Verdi 和 LSF 安装的可选环境门控测试

### 可靠性验收场景

后续测试须使用受控 fake fixture 或故障注入，不破坏真实磁盘：

- 退出码为零但比较未发生、缺结束记录、必需指标缺失，均不能成为有效通过；功能和性能任一失败均保留。
- 同 case 的多 submit、多 seed、多 attempt 并行不覆盖；慢任务不会使其它空闲槽停顿。
- 实时与最终报告并发发布、日志延迟出现/截断、报告写入失败，不能产生错误最终快照或重跑清单。
- 模拟挂载不可见、只读路径、空间/inode/配额不足、I/O 错误，确认故障分类、派发隔离和有界等待；真实 LSF 环境再验证执行节点差异。
- 故障存储与健康存储并存，健康任务继续；源输入变化、损坏或旧 job 状态未知时拒绝盲目恢复。
- 原 seed 与输入身份重跑可追溯；备用存储恢复创建新 attempt，保留原失败和丢失证据标记。
