# NSIM 开发文档：nsim 实现架构

本页描述 2026-10-28 工作区的组件与执行契约。功能验证记录见 [实现状态](implementation_status.md)，接口示例见 [使用指南](../user_guide.md)。原生 VCS/DPI/Verdi 当前仅提供命令预览；实际执行由 fake 或工程 command 包装器承担。

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
CLI / Project Management / Planner
 |
 +-- Project discovery and transactional registration
 +-- Config and submit inheritance -> immutable plan
 +-- Execution controller
 |     +-- Compile products and shared/exclusive locks
 |     +-- Local subprocesses / LSF worker
 |     +-- Ordered hooks -> log analysis -> result checks
 +-- Output layout, ownership and storage checks
 +-- Terminal Dashboard / final reports / evidence validation
 +-- VCS / DPI / Verdi command previews
```

| 模块 | 主要职责 |
| --- | --- |
| `cli.py`、`projects.py`、`project_paths.py` | 命令解析、项目发现、登记与事务更新 |
| `config.py`、`submit_config.py`、`submit_templates.py` | 配置校验、父子 submit 合并、新项目模板与随项目语法说明 |
| `caselist.py`、`caselist_templates.py` | 逻辑用例解析、运行参数映射与分层覆盖、bootstrap 用例示例 |
| `catalog.py` | submit/caselist/regrlist 的 TOML 目录与别名映射、消歧、查询及创建位置配置 |
| `planning.py`、`eda.py`、`eda_config.py` | 任务展开、输入/运行时快照、编译身份、EDA 命令预览 |
| `service.py`、`runner.py`、`compilation.py` | 并发派发、attempt、编译复用与重跑 |
| `lsf.py`、`lsf_worker.py` | 调度客户端调用、终态确认、执行节点校验与运行 |
| `hooks.py`、`log_analysis.py`、`checks.py` | 阶段 hooks、日志扫描、结构化结果判定 |
| `layout.py`、`ownership.py`、`storage.py`、`runtime.py` | 输出路径、归属/锁、存储证据、内置进程运行时隔离 |
| `dashboard.py`、`reports.py` | 增量进度、最终报告、终态证据读取 |

## 组件职责

Caselist 的第一列始终是逻辑用例名。解析器将清单默认值、行内字段/plusargs、可选 YAML
映射与用例覆盖规范化，再按每个 submit 解析有效运行参数。默认语义字段为 uvm_test、seq、
c_test，支持工程扩展；原始 plusargs 可直接参与覆盖。任务身份仍为 submit + case + seed。
跨清单同名且有效参数相同的用例去重，存在冲突时在规划阶段拒绝。
计划保存 case_definitions、各 task 的 case_config、清单及 YAML 快照/哈希；运行与预览共用
已经合并的参数，运行参数不进入仿真器编译身份。详见 [Caselist 语法](caselist.md)。

### CLI

解析用户命令，校验参数，选择目标工程，并委托给 command service。CLI 不应包含仿真器或调度器业务逻辑。

### Discovery Service

项目通过 `nsim bootstrap` 初始化注册到用户级索引，`nsim list` 查询全部登记。`status/update/switch` 共用从当前目录向上查找最近 `.nsim` 的发现逻辑，也支持显式项目名称或路径。新项目使用 `.nsim/nsim.toml` schema 2；旧 schema 1 的 TOML/YAML 扫描读取继续兼容。损坏的最近项目不能被跳过；新旧布局冲突必须显式报错。

### Config Loader

加载并校验：

- 工程描述文件
- 纯文本 caselist
- submit JSON 及其显式父模板，合并后按 `nsim` 定义的 schema 强校验
- 命令行执行选项和调度覆盖值

`extends` 支持一个父文件、一层继承；对象递归合并，数组和每个 hook 阶段整体替换。相对可执行路径绑定声明文件目录，父子原文和哈希进入计划。新项目模板及完整字段见 [Submit 语法](submit.md)。当前没有另一个通用全局/workspace 配置文件层。

schema 2 的相对路径以 verification 根目录为基准，schema 1 保留描述文件所在目录的旧语义。工具版本只有 `.nsim/.nsim_version` 一个来源；新项目默认解析最新已安装可用稳定版本并保存具体版本号。注册索引仅保存名称与配置入口，配置修改采用锁及可恢复事务。稳定安装入口与项目 bootstrap/update 不复用命令含义。

### Project Model

表示项目名称、verification 与 RTL 两套根目录、两套 filelist、显式执行输入以及工具版本。项目名来自 verification 目录名，在本机用户索引内唯一。注册不要求 RTL 版本；每次规划分别采集两套源码的 Git 身份和实际输入哈希。schema 2 的注册配置保存为计划快照，后续 update 不重绑历史回归；实际执行输入仍严格校验。

### Regression Planner

将合并后的 caselist、submit 和 seed 展开为任务。例如，20 个 case × 3 个 submit × 2 个 seed 得到 120 个唯一仿真任务；重复组合只派发一次，保留清单来源。`-repeat` 在规划时生成一组不同 seed。编译按 submit 独立管理，同一 submit/输出根在一次 run/regr 内只编译一次；sim 读取已有兼容产物。原生 VCS 的同类命令目前只规划，不实际执行。

### Scheduler Interface

定义 planned task 的执行契约。调度器适配器接收任务描述，并返回状态变化、log、退出码和资源元数据。

### Local Multiprocessing Scheduler

线程负责协调与等待，实际编译/仿真在独立子进程中执行，按 `-jobs` 滚动补位。阶段执行器负责进程组、超时、取消、日志、hooks 和分析；fake 与 command 共用这些路径。本地锁随子进程继承，避免控制器异常退出后覆盖仍被使用的目录。

### LSF Scheduler

将任务提交到 LSF，用于大规模 regression。队列、资源和 license 约束可以来自 submit JSON，也可以由 `nsim` 命令行覆盖。命令行约束优先级高于 submit 配置。当前 `lsf.py` 负责真实 `bsub`、`bjobs`、`bkill` 调用与终态确认，`lsf_worker.py` 在执行节点校验并运行命令。默认调度器为 LSF；local 可显式选择。所有 submit 共用本次调用的未结束作业窗口，完成回调唤醒控制器补交。排队与暂停计入额度，未知状态不释放占用、不重投；持久目录标记保护控制器退出后仍可能运行的远程任务。详见 [LSF 执行](lsf_execution.md)。

### Simulator Interface

定义仿真器操作，例如 compile、simulate、waveform 配置和 debug 启动。

### Synopsys VCS Adapter

根据工程元数据和 submit JSON 构建 VCS 编译与仿真命令，当前只支持 dry-run。预览覆盖：

- 编译选项
- 仿真选项
- plusargs
- FSDB dump 选项，允许 submit 和 testbench plusarg 共同配合控制
- DPI C 和 C++ 编译输入；DPI 编译使用独立 Makefile，编译结果以 `.so` 形式存在；DPI 产物默认工程共享，也可以通过 submit 控制为按 regression 或 run 隔离
- 环境变量
- 编译、仿真和 DPI 超时配置

DPI 的 project scope 预览路径为工程根下的 `.nsim-cache/dpi`；regression/run scope 使用输出目录内的路径。dry-run 不创建这些编译产物，实际 `.so` 构建、产物生命周期及输出隔离仍待实现与验收，见 [EDA dry-run](eda_dry_run.md)。

### Verdi Adapter

当前随 EDA dry-run 生成含设计数据库及 FSDB 路径的 Verdi 命令，不启动查看器，也没有 `verdi open` 子命令。实际调试入口需要后续接入真实工具和产物验证。

### Output Store

通过 com/sim/run/regr 分离或组合编译与仿真；-mode 选择模式，-dir 选择工作区，-com-dir/-sim-dir 指定产物路径。接口契约见 [执行阶段](execution_stages.md)。创建和管理输出目录：

```text
output/
  submit_name/workspace/                  # 单跑
    com_dir/
    sim_dir/<case>_<seed>/
  regr_dir/<regression-id>/               # 一次回归的完整产物
    report/                              # plan/state/live/report/final
    submit_name/workspace/
      com_dir/
      sim_dir/<case>_<seed>/
  regr_batch_dir/<batch-id>/report/       # regression list 的成员关系与汇总
```

Output root 通过 bootstrap/update 保存为项目默认值，run 参数可以覆盖。单跑保留固定目录与覆盖规则；
回归及其失败重跑使用新的独立目录，汇总文件放在 report/。同一次回归内部共享编译，不与单跑或另一回归
共享可写编译目录。持有目录锁期间才允许准备产物，dry-run 只保存预览，不清理编译或仿真结果。

### Report Generator

`hooks.py` 统一 local/LSF 阶段执行与有序 hooks；`log_analysis.py` 在 post_sim 的最后执行标准 VCS/UVM 扫描（或使用用户的替代 hook）。case 的 `run.json.analysis`（内嵌 schema 3） 只保留结论、单行原因、首错位置、分阶段计数和扫描完整性，详细诊断留在原始日志；共享编译单独保存分析。报告读取已保存摘要，生成面向人的报告和机器可读汇总。实时刷新不重新读取全部日志；终态和显式查询校验证据。详见 [Hooks 与日志分析](hooks_and_log_analysis.md)。

## 数据流

1. 用户运行 `nsim` CLI 命令。
2. CLI 解析配置和工程搜索根目录。
3. Discovery 识别目标 ASIC 工程。
4. Config Loader 读取工程描述文件、caselist 和 submit JSON。
5. Regression Planner 将输入展开为任务定义。
6. Output Store 检查存储与目录归属、取得锁，并保存单跑或批次计划。
7. 控制器按阶段编译或验证已有编译，通过 local/LSF 执行仿真及 hooks。
8. 每个任务保存日志分析、结构化检查和 attempt 记录，完成事件更新终端 Dashboard。
9. 全部任务取得终态后发布回归 state、报告和 final；独立单跑原子发布 run.json 的完成状态。状态未知时停止自动派发与终态发布。

## 可追溯模型

当前按 case 与回归范围保存记录，不生成独立 `metadata.json`：

| 文件 | 记录内容 |
| --- | --- |
| `run.json` | schema 2；每个 case 的身份、实际编译关联、完整 attempt 历史、分析摘要和校验；单跑内嵌 plan 与 state，回归 case 引用公共 record_dir |
| `plan.json` | 回归/仅编译/预览的公共计划；单跑的对应内容在 run.json.plan |
| `build.json` | 编译实例、配置/输入身份、命令、产物哈希、阶段与调度证据 |
| `state.json` | 回归结束或异常停止时保存完整状态；运行中不按 case 全量重写 |
| `live.json` | 有界的实时计数与近期变更；不包含全部任务与证据 |
| `final.json` | 回归/仅编译最后发布的完成 manifest；单跑完成状态与分区校验内嵌 run.json |

新仿真记录不再生成独立的 analysis.json、attempt.json 或 records/ 下的 attempt 副本。result.json、hooks.json 与 LSF 协议文件仍承担工具交换/执行证据职责。仅编译命令保留原记录布局。读取兼容旧版分离记录；启动器可从单跑 run.json.plan 选择历史工具版本。

记录各自带 schema/version 信息，不应假设所有文件使用同一个版本。rerun/recover 从校验通过的终态读取来源，长期存储仍保持文件形式。

## 任务身份、执行与结果契约

### 规划与编译身份

Planner 在执行前生成稳定的 task id、有效 seed 和编译身份，执行器在实际派发时生成新的 attempt id。相同 submit 的 case 可以复用同一个受追踪 compile artifact；不同 submit 仍独立编译。编译元数据记录输入 hash、有效编译配置与产物身份，build id 还绑定项目路径和工具代码。Golden/参考模型是否实际执行由结果协议确认，不能通过存在编译目录或配置开关推断。

输入快照记录工程 commit/tag、dirty 状态、相关输入 hash、caselist、有效 submit、plusargs、工具与环境身份。源码引用必须可验证；输入已变化且无法取得原内容时，不能标为精确重跑。nsim 不自动更新源码；调用工程构建入口时也必须禁止隐式拉取源码和清理既有回归。

顶层 submit_name 工作区用于单跑；回归的 submit 工作区放在 regr_dir/<regression-id>/ 内。
回归计划与汇总统一放在 report/，失败重跑生成新回归 ID 并关联来源。目录选项不得逃出所属回归 submit，
历史结果不覆盖。Report 按计划内任务和证据哈希校验，批次只汇总 batch plan 登记的回归，不扫描历史目录。

### 调度状态与结果判定

调度状态表示作业执行进度，检查 verdict 表示验证结果，两者独立记录。LSF 原始状态与未知作业处理见 [LSF 执行](lsf_execution.md)，PASS、NO_COMPARE、INCOMPLETE 等判定及优先级见 [结果协议](local_execution.md#result)。

- Scheduler 显式保存 job id、执行节点、提交/启动/结束时间、退出码、启动 stderr 和失败原因；时间未知时保持未知，不从目录 mtime 伪造真实调度时间。
- 本地调度采用有上限的滚动并发窗口；一个任务结束即补充下一个。单个 DUT 失败按策略处理，不阻塞无关任务。
- 零退出码不足以通过；非零退出码、超时、取消或证据不完整也不能被日志中的成功 banner 覆盖。
- 区分验证失败、未执行所需比较、仅存活检查通过、崩溃/超时、基础设施失败和证据不完整。所需比较未发生时，即使 mismatch 为零也不通过。

### Result Checkers 与最终发布

工程提供受版本追踪的解析器，将原始日志或结构化记录转换成统一检查结果：检查器身份、schema version、是否必需、是否实际执行、verdict、失败原因和证据引用。核心不内置 ACC banner、Syscan 参数或 ACC 性能公式。功能、Golden、性能等必需检查全部有效通过，且执行及证据均完整，才允许最终通过；仅存活检查必须由策略显式选择并在报告中标识。

Report Generator 从持久化任务和检查结果生成 JSON 与面向人的 Markdown/静态 HTML。实时快照可显示排队/运行，但不得发布最终失败清单。最终结果和重跑清单引用同一已完成快照；采用单写者或有序发布，防止旧实时快照覆盖新终态。

`dashboard.py` 由控制器单线程调用。case 分析和执行记录落盘后，完成回调唤醒控制器；LSF 状态事件也触发更新。维护增量计数，原子保存 schema 3 的有界 live.json 摘要后，直接重绘 stderr 上的终端面板；重定向时追加纯文本，stdout 的最终 JSON 保持原契约。无事件时不写盘、不重绘，没有 Dashboard 定时器、HTTP 服务或额外 job。final.json 发布后才显示整批判定；中断、异常和未知作业显示停止状态。HTML 只作为自包含的最终分页报告。见 [终端 Dashboard](dashboard.md)。

Output Store 在同一文件系统内用唯一临时文件与原子替换发布完整文件。多文件结果以最后发布的完成 manifest 关联各文件身份/hash；任何必需文件发布失败，都不能将该批次标为完整。单文件原子替换不等价于多文件事务、断电持久性或备份。

### 重跑与恢复来源

失败重跑从最终快照选取未满足该批策略的 task，保存 source regression/task/attempt，默认使用原 seed、有效 submit、plusargs 和可验证的工程/编译身份。用户更换 seed 或输入时必须记录差异。编译产物损坏时只能从可验证输入重新构建并记录新身份。

单 case 的 `rerun CASE_DIR` 与 `recover CASE_DIR` 完全等价，默认校验复用编译，显式 `--rebuild` 才重新编译；`rerun COM_DIR` 与 `recover COM_DIR` 都只重新编译。case_dir 是 sim_dir 下的具体 case 目录，sim_dir 父目录不作此入口。原产物保留，新结果使用独立目录；两者都读取当前输入；下文区分 rerun/recover 的规则适用于整轮回归，批量回归也支持按记录逐行重跑。详见 [按目录重跑](rerun.md)。

显式 rerun/recover 通过 parent 和 attempt 记录保存来源。rerun 从历史结果提取完整执行但结果失败的 case/seed，重新规划当前输入，在原回归下独立的 rerun_<时间_UUID>/ 中强制编译、仿真及检查，保留历史产物。recover 继续校验历史输入，在独立 recover_<时间_UUID>/ 下强制重新编译、仿真和检查未完成任务。两者都要求来源有可信终态；未确认旧 job 已终止时不得重叠启动副本。手动汇总见 [重跑、恢复与报告](replay_reports.md)。

## 存储与执行环境故障契约

`storage.py` 提供预检和复检，LSF worker 也在执行节点检查存储、输入与编译产物。预检记录主机、路径、设备号、可用字节和 inode，并进行小文件写入、readback 和 fsync。当前不查询配额，统一记录 `quota: unknown`。运行时的 EDQUOT 等错误保留原始 errno、操作与路径；这些现象不单独证明整块磁盘损坏。本次执行只选择一个输出根。

LSF 队列配置必须表达或引用所需工具环境与存储条件；编译和仿真不要求同一主机，但执行环境必须兼容、产物必须一致可见。提交机上的文件检查不能替代执行节点检查；不把 `nightly` 或 `/space8` 写死为平台默认值。

| 故障类别 | 检测与记录 | 处理与恢复条件 |
|---|---|---|
| 路径缺失、权限不足、命令启动失败 | 执行节点检查并保留路径、主机、异常及 stderr；不单凭 No such file 区分缺文件和 loader 问题 | 按实际异常记录存储、调度或执行失败；需要修改队列/工具配置时重新规划，当前不自动隔离集群节点 |
| 磁盘空间、inode 或配额耗尽 | 启动前检查并记录运行中 ENOSPC/EDQUOT 等原始错误 | 启动前选定根检查失败则不执行；单个 case 的错误只影响该 case，除非整批状态另行无法保存 |
| NFS 延迟、短暂不可用 | 将 job 完成与产物可见分开；记录有截止时间的重读与重试 | 缺失或不完整的必需产物均允许有限等待；超预算记为证据不完整/存储故障，不能无限等待或判 PASS |
| 持续 I/O 错误 | 保存每个受影响 case 的错误与最后可信状态 | 不由 NSIM 推断磁盘损坏或自动切根；用户排查后显式 rerun/recover |
| 截断日志、校验失败、发布失败 | 对照完成 manifest、检查器要求与 hash 验证证据 | 禁止完整通过；保留原故障，必要时从可信输入重跑 |

不再设置独立 event-root。每个仿真 case 的目录保留一个 `run.json`；回归报告目录的 `state.json` 在派发和 case 完成时保存整批进度。单个 `run.json` 写失败时该 case 不通过，若 `state.json` 仍能保存错误则后续 case 继续；整批 `state.json` 写失败才停止新派发，且不发布完成标记。LSF 的 `job.json` 和保护文件记录远端 job 的占用，未知 job 保留一个并发名额。所有记录保持文件形式，不引入数据库。

默认恢复粒度为从输入重新运行一个 attempt，不承诺仿真进程断点续跑。显式 recover 会重新检查用户选定的输出根。节点选择由 LSF 队列与资源配置控制，当前没有自动节点黑名单。物理硬盘、文件系统修复和备份管理由基础设施负责。

## 架构约束与后续验证

- `nsim` 以 Python CLI 工具形态交付，而不是服务端平台或 Web 应用。
- ASIC 工程通过明确的工程描述文件被发现和识别。
- `nsim` 工具、ASIC 工程源码和仿真输出目录彼此独立。
- 不同 ASIC 工程可以使用不同版本的 `nsim`。
- 使用 fake、command 和本地进程级 LSF 服务验证共用流程。
- VCS、Verdi 和 LSF 通过 adapter 接入，不进入 CLI 业务逻辑。
- DPI 编译使用工程提供的独立 Makefile，产物以 `.so` 形式存在。
- Regression metadata 初期使用文件形式存储。

真实 farm 上的路径、跨主机锁、工具/Python 环境和故障行为仍需验收；原生 EDA 执行及归档策略的后续工作见 [待办](backlog.md)。

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
- 不同 submit、case、seed 可并行；同一目录禁止并行覆盖，串行新运行替换旧产物；慢任务不会使其它空闲槽停顿。
- 实时与最终报告并发发布、日志延迟出现/截断、报告写入失败，不能产生错误最终快照或重跑清单。
- 模拟挂载不可见、只读路径、空间/inode/配额不足、I/O 错误，确认原始错误呈现与逐 case 继续执行；真实 LSF 环境再验证执行节点差异。
- 单个 case 的记录写失败但整批状态仍可保存时，其他 case 继续；整批状态无法保存时停止新派发，已提交 job 的状态不伪造。
- 原 seed 与输入身份的显式重跑可追溯；未知 LSF job 保留占用，不能盲目重投。

`batch.py` 复用 CLI 的 prepare_execution，将 regression list 全部行预先规划后顺序执行。
子控制器事件驱动批次状态、Dashboard 和报告更新；未知作业状态阻断后续行。详见 [批量回归](regression_batches.md)。

正常回归 case 的 run.json 在开始和 attempt 结束各发布一次；单跑还在控制器完成时发布最终状态。JSON 从内存序列化后原子替换，并直接使用已写字节的哈希，避免为获取该哈希再读文件。日志沿用阶段扫描得到的哈希，终态/显式查询仍执行证据校验。批量回归的子任务进度只更新当前行与总计，不重写全部批次状态；完整批次记录在回归行边界和结束/异常时保存。
