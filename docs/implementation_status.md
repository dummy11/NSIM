# nsim 开发文档：实现状态与验证记录

## 当前能力与验证边界

本页按 2026-10-30 工作区整理。`pyproject.toml` 版本仍为候选 `0.1.0`；本地代码、测试通过与远端发布是独立状态。下文各次测试及构建记录只描述当时快照，不能自动延伸到后续改动。

## 2026-10-30 LSF inspect/release、记录合并与延迟创建目录

- 新增 `nsim lsf inspect DIR [--check-lsf] [--json]`，支持单 case、编译目录和单轮回归。默认只读本地记录，显示未解决占用、缺失/损坏/冲突信息；可选查询区分保存状态与实时状态。
- 新增 `nsim lsf release DIR --token TOKEN`。取得产物锁后重新核对作业名、用户、输出路径、ID/提交时间及终态；查不到、身份不符、仍运行或锁被占用均拒绝。没有按时间释放、强制释放或重新提交。旧记录缺少后处理包含约定时，要求明确的后处理终态。
- 释放使用独立 `.lsf-release-<token>.json`，保留原 `run.json/job.json` 及回归状态的字节与证据摘要，只处理对应 token 的保护记录。部分失败可重试，其他仿真读者不受影响。
- 新仿真用 `run.json.lsf` 保存提交意图、固定请求、ID、worker 结果和日志收集信息；在 `bsub` 之前持久化身份和编译读者。普通 PEND/RUN 查询不重写 case 记录。回归请求引用公共计划；完整协议只放在 case 记录，回归汇总保留调度摘要。
- 控制日志按本轮共用文件追加，按 token 取回该 job 的内容并合入 `run.log`。正常收集完成后清除临时 worker/farm 文件，报告不引用已删路径。未知或收集失败保留临时证据。编译 job 的旧协议与编译读者保护仍保留。
- 独立回归的 case 目录在任务获得 `--jobs` 名额、完成编译准备后逐个创建；未派发不创建空目录。单 case 目录失败不取消其他 case，单跑及旧共享布局保留原有加锁顺序。
- 提交/释放的关键文件和目录项增加同步；查询和取消先核对 job 身份，新提交设置 `LSB_JOB_INCLUDE_POSTPROC=Y`。支持旧占用/旧 job 记录、编译快照证据；已有受管 case 的记录丢失或损坏时拒绝覆盖。

基线：提交 `2bc8914` 的完整 unittest 套件 **425 项通过（308.890 秒）**。受限外层沙箱不能运行测试内的 `sandbox-exec`；在允许该机制生效的环境重跑通过。实现期间的定向测试覆盖 46 项 LSF 管理/输出/记录场景，另有独立编译、编译快照与记录损坏边界测试。最终完整 unittest 套件 **450 项通过（356.262 秒）**，覆盖新增 25 项管理与故障测试；所有验证均使用 fake/command 与本地 LSF 进程桩，没有提交真实集群 job。

小文件对照使用 12 个 fake case、`--jobs 4`、本机进程级 LSF 桩，比较 `2bc8914` 临时副本与本次实现。包装控制器的 `storage.atomic_write` 和 `os.fsync` 计数；不包含 worker 子进程内部的系统调用，也不等同于实际硬盘/NFS I/O。以下是一组实测值，轮询和任务完成时序可能使计数变化：

| 指标 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 最终保留的普通文件数 | 160 | 89 |
| 每 case 的 LSF 附加文件总数（12 case 合计） | 72 | 0 |
| 控制器 JSON 原子写次数 | 253 | 191 |
| 控制器 fsync 调用次数 | 797 | 651 |
| 控制器 JSON 写入字节数 | 2,759,827 | 3,006,057 |

文件数和同步次数下降，JSON 写入字节仍增加约 8.9%，主要换取提交前身份、固定请求和释放所需记录；不能声称所有 I/O 都减少或据此保证真实 NFS 加速。正常运行仍创建临时 worker/farm 文件与编译读者。真实 LSF 客户端输出、集群后处理、多用户跨主机锁、共享存储/断电持久性和大回归性能仍需目标环境验收。

## 2026-10-29 report 入口与只读重建

`nsim report SOURCE` 直接接收单 case、回归或批次目录，不再使用 `show` 子命令。单 case 在终端打印结果摘要；完整回归或批次给出已校验 `report.html` 的路径和浏览器地址。报告文件或证据不完整时显示简短原因；原回归的 `--rebuild` 从可校验的原结果及最新 rerun/recover 生成独立的 `report_<时间_UUID>/` 汇总目录，保留原 `final.json` 和任务记录，绝不提交或重启任务。批次仍使用原有 `report.rebuilt.html` 展示重建。损坏的控制记录不能用于重建；已变更的通过证据显示为 INCOMPLETE。稳定启动器继续按历史记录选择 NSIM 版本。

验证：定向报告、批次、简写和 Dashboard 测试 52 项通过，补充回归内单 case 与缺失子报告测试后，报告和批次测试 28 项通过。最终完整 unittest 套件 **418 项通过（297.015 秒）**。测试使用 fake 适配器和本地 LSF 进程桩，不代表真实 VCS 或 LSF farm 验收。

## 2026-10-29 Submit 文件管理与完整 bootstrap 示例

新增 submit `files` 输入表及 `{file:NAME}` 引用。文件、目录、glob 与显式子依赖统一按验证工程根解析，按 compile/simulate/analysis 记录内容摘要与成员集合；同名声明可继承覆盖，null 删除继承项。小文件保存有界 base64 快照，snapshot=false 仍追踪内容身份。普通参数、hooks、DPI 输入和用例配置支持文件引用，错误名称和阶段不匹配会拒绝；error_filter 保留旧路径语义，并支持 analysis 文件引用。

编译身份只加入 compile 文件，运行和分析输入的内容变化允许新计划复用兼容编译；原有 project.inputs 继续作为保守的编译依赖。精确重跑校验全部已登记输入和目录成员，符号链接改指向也会拒绝。local/LSF 共用输入校验和只读保护，工程外输入及空输入目录不能被执行进程写入或输出目录覆盖。show/check 展示 resolved_files 并检查显式输入，dry-run 展示文件与各阶段摘要。

bootstrap 现在生成 19 个 submit 相关文件：公共 mode 模板、显式展示文件管理的 submit_demo.json、完整语法说明、VCS 全字段/command/fake 参考，以及 coverage hierarchy、Xprop、partition、UCLI 子脚本、HEX 和分析规则教学输入。已有用户文件和已注册工程不自动覆盖；submit new 继续生成最小差异配置。示例中的 DUT 层次、YOUR_* 与工具选项需按实际工程配置。

验证：改动前全量基线 381 项通过；完成后全量 unittest **405 项通过（289.610 秒）**。覆盖按阶段编译复用、多 submit 隔离、继承删除、目录/glob 变化、符号链接、二进制快照、带空格/花括号路径、case/CLI 引用、实际 command 消费、外部输入只读保护，以及本地进程级 LSF worker。bootstrap 项目测试、生成文档一致性、JSON 与文档链接检查通过；wheel/sdist 构建与逐文件分发校验通过。

边界：files 管理已存在的输入，不提供通用生成任务 DAG、partition 持久增量缓存、coverage 自动合并或独立重新分析入口。生成文件与可写产物仍由 command/hooks 及 compile.artifacts 管理。原生 VCS 仍仅 dry-run；未执行真实商业 EDA、真实 LSF 集群或跨主机共享存储验收。

## 2026-10-29 日志源码位置解析修复

修复了位置解析把 `at dir with spaces/file.sv:123` 误写成 `spaces/file.sv:123`，以及把 `at /home/user/rtl/top.sv:123` 丢失开头 `/` 的问题。解析器现在优先使用引号或明确的 `at` 边界识别含空格路径；对无可靠边界的文字不猜测源码路径，保留日志文件与行号供排查。既有无空格路径及括号行号形式继续支持。扫描预算超限与分析失败的原因分类仍待处理。

日志扫描、hooks、合并日志、Dashboard 和 case 记录共 78 项针对性测试通过；其中执行级测试使用本地模拟工具和进程级 LSF 桩，不代表真实 VCS/LSF 验收。

## 2026-10-29 日志原判定保留与 error_filter

仿真结果先保存 `result_verdict/result_reason`，再合并日志诊断；未比较与结果不完整保留原分类。未豁免的日志 error/fatal 只会把原本通过的仿真结果改成 FAIL。新增可选 `log_analysis.error_filter` 独立 JSON 文件，按 compile/simulate 阶段、可选扫描日志路径、精确 code 与首行 message 豁免已确认的 error；fatal 与未列出明细的 UVM 汇总差额不能豁免。原始日志、错误总数和首错位置保留，执行摘要另记阻断数量、豁免数量、规则命中与文件摘要。编译规则影响编译身份，规则文件纳入计划输入哈希；local/LSF 共用分析器，规则修改会触发身份校验失败。具体格式见 [Hooks 与日志分析](hooks_and_log_analysis.md#已确认错误的精确豁免)。

针对性测试：日志扫描 17 项、submit 继承 17 项、hooks 23 项，全部通过。随后在允许建立系统只读保护的环境中运行全量 unittest，380 项全部通过（约 290 秒）。最后补充自定义分析器相对日志路径与豁免显示测试后，日志扫描 18 项和 hooks 23 项再次通过；wheel 与源码包构建及 `check_distribution.py` 校验通过。LSF 验证使用本地进程级桩服务；未执行真实 LSF 集群或商业 EDA 工具。首次在嵌套沙箱内运行 hooks 测试时，系统拒绝 `sandbox-exec` 建立只读保护，测试在编译前失败；允许系统保护启动后重跑通过，未关闭产品保护。

| 功能 | 当前状态 |
| --- | --- |
| 包与安装 | Python 3.9+，稳定启动器、个人/系统/自定义安装、独立版本环境与历史记录版本分派；真实 Release 下载仍待验收。 |
| 项目管理 | bootstrap/list/status/update/switch、schema 2 双源码登记、子目录发现、用户级索引、锁与可恢复事务。 |
| 配置与规划 | 严格 submit JSON、单层继承、六种 mode 公共模板、多 caselist/submit/seed、随机 repeat、输入与工具快照。 |
| 逻辑用例 | caselist 默认值和行内覆盖、uvm_test/seq/c_test 映射、直接 plusargs、可选 YAML 扩展与覆盖、8 个 bootstrap demo。 |
| 编译与执行 | com/sim/run/regr；command 实际编译与产物校验，sim 显式复用；fake/prebuilt 标记 compiled=false。 |
| 调度 | 默认 LSF，也支持 local；单个 job 失败不取消整批，未知 job 保留名额，其余名额继续补交；真实 farm 待验收。 |
| 输出与隔离 | 固定 case/seed 目录、批次记录、编译共享/独占锁、用户/项目/版本归属检查；不按单次执行错误自动隔离或切换输出根。跨主机共享文件系统待验收。 |
| 分析与报告 | 四阶段 hooks、VCS/UVM 分析、轻量 case 摘要、终端 Dashboard、有界 live.json、最终分页 HTML 与证据校验。 |
| 重跑与恢复 | rerun 固定完整执行但结果失败的 case/seed，读取当前输入，在原回归的独立目录下强制重编译并保存报告；recover 在独立目录下恢复未完成任务。记录盘整体丢失不保证恢复。 |
| 原生 EDA | VCS/DPI/FSDB/Verdi 配置与命令预览；尚无原生实际执行或真实工具验收。 |
| 后续功能 | 报告比较、归档、日志索引、安全清理，以及项目迁移/注销/搬移，见 [待办](backlog.md)。 |

2026-10-27 的远端核对记录为尚无 tag/Release；当前远端发布状态需在交付时重新核验。发布步骤见 [打包与发布](releasing.md)，使用入口见 [使用指南](../user_guide.md)。

## 2026-10-29 单 case 错误与 LSF 名额修复

- local 的命令启动、日志和结果错误保留原始操作、errno、路径及证据是否存在；失败只作用于该 case，后续 case 各自尝试执行。取消自动输出根隔离、切根和基础设施重试；本次执行只选择一个输出根。
- 每个仿真 case 仍只有一个 `run.json`。其写入失败使该 case 不通过；回归控制器在派发和 case 完成时保存 `state.json`，该整批记录可写时其他 case 继续。整批状态不可写则停止新派发，不取消已提交 job，也不发布完成标记。
- LSF 的明确拒绝只影响对应 job；回复丢失或查询状态未知时保留 job 保护文件和一个 `--jobs` 名额。其他空闲名额继续补交；未知状态不重投、不自动取消，也不发布 `final.json`。LSF 日志收集和单个 job 记录写入错误不会把取消信号传播到其他 job。
- `live.json` 和 Dashboard HTML 属于展示文件；写入错误在 stderr 报告，不取消已提交任务。批量回归的展示路径同样按此处理。旧记录读取与显式 rerun/recover 保持可用。

验证：在允许 macOS 进程隔离的环境中，主修改后的完整 unittest 套件 **370 项通过**（276.598 秒）；随后对批量回归展示边界补充的 **18 项 BatchTests 与 1 项 BatchLsfTests 通过**，对 LSF job 记录读取错误的 1 项定向测试通过。故障注入覆盖 local 编译/仿真启动、`run.log`、单 case `run.json`、整批 `state.json`、LSF 拒绝/未知状态、`job.json`、日志收集及并发上限。受限沙箱内的基线测试因系统拒绝嵌套 `sandbox-exec` 而失败，不作为代码回归结论。这些测试使用 fake 适配器和本地 LSF 进程桩；真实 VCS、LSF farm 和跨主机共享存储仍未验收。

## 2026-10-28 Caselist 语法、映射与初始化示例

case 统一为逻辑用例名。新增清单级 @defaults、逐行字段/plusargs、null 清除、可选 @config
引用 case.yaml；内置字段为 uvm_test、seq、c_test，seq 替代讨论稿中的 sequence。
映射可替换默认参数名或增加运行字段，也支持直接写逻辑名称与 plusargs。
submit.case_defaults 与原有运行参数参与合并，优先级为 submit → 清单默认值 → 当前行 → YAML 用例覆盖。
同层同键冲突、跨清单同名不同有效配置、未知字段和非法模板均在规划时拒绝。
默认值不跨清单传播；无入口字段不自动推断，纯旧格式 VCS 名称清单保留兼容回退。

bootstrap 为新项目生成 caselist/ 下 8 个 demo、可选 case.yaml 和 README.md，展示 UVM、共享 seq、
C test、原始 plusargs、混合/清空、自定义映射及 YAML 覆盖。保留已有文件，已注册项目重复运行
不回填模板；失败沿用事务回滚。清单和 YAML 的原文、哈希、解析定义与最终运行参数写入计划，
重跑校验输入身份；运行参数不进入仿真器编译身份。完整规则见 [Caselist 语法](caselist.md)。

在独立临时源码快照中完整运行 306 项测试，全部通过（196.955 秒）。随后补强非法 YAML 复合键的
配置错误提示及 command submit 显式传递 seed/result 的兼容路径，并在最终工作区复测全部
18 项 caselist 测试，全部通过。覆盖生成 demo 的实际 CLI
预览、参数覆盖/去重、输入快照与重跑校验、编译身份稳定、本地 command/fake 执行、bootstrap
文件保护/幂等/失败回滚。初次嵌套沙箱下执行测试被 macOS sandbox_apply 拒绝，重跑在允许建立
系统只读保护的环境中完成，未关闭产品只读保护。模板说明与仓库文档一致，本地链接和代码围栏检查通过。
真实 VCS 与真实 LSF 集群未执行；本次没有发布版本制品。

## 2026-10-28 Submit 日常配置简化

新增 `nsim submit new/show/check`。new 按模式生成显式继承的最小 submit，支持自定义父路径；旧项目缺少所选默认 common 时一并补齐，保留已有文件，事务失败整体回滚。硬件模板未接入 argv 时明确标记待配置。show/check 提供合并结果与字段检查，不提交作业；输入与工具检查仍由 dry-run 负责。

运行时省略 `-mode` 会使用有效 submit 的 mode 和对应默认工作区；显式 mode 不匹配、同一次调用混合 mode 时拒绝。默认 demo 删除重复空参数，公共模板后续修改正常传递到 demo。配置编写以 common 集中维护、具体 submit 只保留差异为原则。

本次首先有 48 项针对性测试通过。全量运行期间只读保护代码并行更新，281 项中出现 21 个失败和 1 个错误，涉及旧/新源码混用及外层 macOS 沙箱拒绝建立保护，不能作为稳定快照的全量结果。随后固定源码快照，在允许建立系统只读保护的环境中验证 56 项 submit/执行阶段/保护/初始化测试，并复测其余 20 个受影响用例，合计 76 项全部通过；未关闭只读保护。此记录不代表当前并行变化后的整个工作区完成新一轮全量验收。

## 2026-10-28 文档同步验证

同步 README 导航、项目模板与继承说明、执行目录、模块和记录文件、CLI 基线、实现状态及待办；保留此前按日期记录的验收范围。检查了 14 份 Markdown 的本地链接和代码围栏，16 个 JSON/TOML 配置块通过语法检查，7 个 submit 示例通过实际配置加载，74 条核心 CLI 示例通过参数解析；生成的 Submit 说明与 `guide()` 一致。

文档核对时源码仍有并行更新，因此使用独立临时快照验证。该快照共运行 259 项测试，253 项首次通过；6 项 LSF 相关测试因 `/tmp` 与 `/private/tmp` 源码路径不同触发身份校验，统一为真实路径后全部复跑通过。该路径要求已记入 LSF 环境说明。此验证使用本地进程级测试服务，不代表真实 farm、商业 EDA、远端 Release 或本次重新打包验收。

## 2026-10-28 Submit 继承与默认模板

新项目 bootstrap 按 mode 生成 `submit/common/{rtl,gate,fpga,haps,zebu,palladium}.json`，并生成继承 RTL 模板的 `submit_demo.json` 与完整语法说明 `submit_demo.md`。已有同名文件保留，重复注册不回填模板，发布沿用项目事务与回滚机制。

submit 支持 `extends` 显式单父类、一层继承。对象递归合并、数组整体替换、hook 按阶段替换，合并后补默认值并校验；相对工具路径绑定声明文件所在目录。计划保存父子快照、哈希与展开后的配置，旧计划检测父模板变更；编译身份使用有效编译配置，仿真参数变更不要求重编译。mode 由最终配置确定，命令行只校验匹配。原生 VCS 仍为 dry-run，其他平台模板需补真实 command 接口。详见 [Submit 语法](submit.md)。

本次完整本地回归 259 项通过，包含继承配置实际 command 执行、默认 demo 的 CLI dry-run、六种 mode 模板、父文件变更检测、编译身份稳定性、已有文件保护和模板写入失败回滚。未执行真实商业工具或硬件平台。

## 2026-10-28 Case 分析合并与大回归报告优化

每个 case 的 `run.json.analysis`（内嵌 schema 3）只保存身份、结论、单行原因、首错位置、分阶段计数和扫描完整性，不复制诊断数组；原始日志和执行记录中的哈希证据仍保留。独立仿真 `log_analysis.json` 不再发布；共享编译诊断仍单独保留。自定义分析 hook 的 `{analysis}` 指向临时协议文件。local/LSF 继续共享解析流程，worker 只传递摘要；case 完成必须晚于最终分析与执行记录保存。

Dashboard 按变更任务维护摘要，完成事件直接更新终端面板；live.json schema 3 仅保存统计、最多 8 个最近 case 和 4 个最近作业，不复制全量 attempts。HTML 仍自包含、每页最多渲染 100 行；实时 HTML 及轮询文件已由终端方案替代。重跑及最终证据校验继续使用完整终态记录。详见 [终端 Dashboard](dashboard.md) 与 [分析输出](hooks_and_log_analysis.md)。

## 2026-10-28 终端 Dashboard

控制器在每个 case 的 post_sim 分析与执行记录保存后直接更新终端 Dashboard，local/LSF 共用此流程。交互终端原地重绘，非交互 stderr 追加纯文本，stdout 保留最终 JSON。无页面轮询、心跳文件、额外服务或 LSF job。最终完成记录发布后才显示整批判定，中断、错误或未知作业显示对应状态。使用方法和验证边界见 [终端 Dashboard](dashboard.md)。

## 2026-10-28 Hooks 与日志分析

单跑/回归、local/LSF 共用有序阶段执行器。submit 支持 pre_compile、post_compile、pre_sim、post_sim，一个对象或多个对象按声明顺序执行。post_sim 最后执行日志分析；同名 log_analysis 用户 hook 可替换内置解析器，配置在中间会拒绝。

内置 VCS/UVM 分析提取诊断、源码/日志位置及原因，处理 severity 汇总而不重复计数；case 单独生成分析文件，回归汇总并只统计一次共享编译。支持写入稳定检查、扫描期间变更检测、有界重试、读取量及诊断数量上限。用户分析器的协议和日志身份独立校验。hooks 与分析在原 LSF job 内执行，无新增作业。详见 [使用文档](hooks_and_log_analysis.md)。测试使用合成 VCS/UVM 日志和进程级 LSF 服务；不表示真实商业工具或 farm 验收。

## 2026-10-28 LSF 执行

默认调度器改为 LSF，支持 `-scheduler local`；`-jobs N` 限制整次调用已提交且未确认结束的作业，完成回调触发滚动补位，所有 submit 共用额度。`-queue` 覆盖 submit 队列；不配置时使用集群默认。实现真实 bsub/bjobs/bkill 进程调用、执行节点 worker、共享目录与输入/编译产物检查、状态历史、取消终态确认及未知作业持久占用。具体配置和使用方式见 [LSF 执行](lsf_execution.md)。

新增进程级测试服务会启动实际 worker，覆盖默认值与 local 覆盖、队列、多 submit 并发、完成补位、实际 command 编译、编译产物复用、执行超时、Ctrl-C、控制器被杀、提交回执丢失、无效 job ID、查询故障、取消无法确认、缺失/损坏 worker 结果，以及排队期间输入/产物变化。测试服务运行于本机；真实 LSF 集群与商业 EDA 工具验收尚未完成，原生 VCS 仍仅提供预览。

## 2026-10-28 当前输出布局

新运行使用 `output/<submit_name>/<workspace>/com_dir` 与 `sim_dir/<case_name>_<seed>`，批次记录和汇总放在 `output/regr_dir/<batch-id>`。独立 event-root 参数、环境变量入口和新项目配置字段已移除；旧配置字段仅兼容读取并忽略，输出配置 update 时移除。旧 event-root 文件不迁移、不删除；重新执行的 case/seed 目录直接清理并覆盖，不保留内部运行子目录。

单跑保存最新的计划、运行引用、结果和完成校验，不生成实时 HTML 或回归汇总；多个 submit/seed 按批次处理。report/rerun/recover 可从单次仿真目录定位经过校验的批次任务。输出记录写入失败即停止，不承诺输出盘丢失后的独立恢复。LSF 已支持实际提交、状态跟踪与取消；原生 VCS 仍仅支持 dry-run，command 支持显式 compile 步骤；fake 和预构建输入登记均以 compiled=false 区分。执行入口现为 com/sim/run/regr，统一支持 -mode/-dir/-com-dir/-sim-dir/-o；regr 支持多 caselist、多 submit、多 seed，以及与显式 seed 互斥的 -repeat 随机 seed 数量，旧两级入口已删除。详细规则见 [执行阶段](execution_stages.md)。下文日期明确的验收记录描述各次验收时的布局和范围。

覆盖写入受目录锁保护；dry-run 保留已有结果。旧回归记录仍保留，但已覆盖的通过证据在重新查询时标记为 INCOMPLETE；统一 seed 后重复目标合并执行并保留来源。

此前目录与回归接口的完整本地测试为 154 项全部通过。覆盖多 caselist/submit/seed 组合、任务去重与来源、随机 repeat 与 seed 互斥、重跑保留 seed、regr 自定义编译/仿真目录、-o 位置与历史版本分派，以及 com/sim/run 阶段分离、实际本地 command 编译、编译失败/超时/缺产物阻断仿真、同批编译复用、相对/绝对编译路径、模式校验、编译共享/独占锁、旧入口拒绝，以及固定目录覆盖、控制器异常退出后子进程保留目录锁、dry-run 保留产物、重跑来源与历史证据校验。wheel/sdist 已构建并通过与源码一致性检查；这些验证不代表原生 EDA/farm 验收。

## 2026-09-20 至 2026-10-27 基础包与离线安装记录

2026-09-20 本地测试扩展到 57 项全部通过；fake CLI 验收覆盖正常、失败、失败重跑与存储恢复。npu8860 dry-run 生成 3 个 smoke 任务预览、记录 322 个输入身份；未执行商业工具，未生成 DUT verdict。2026-10-27 本地扩展到 64 项测试通过，构建 `0.1.0` wheel/sdist 并通过归档检查；从实际候选 wheel 制作含依赖的离线包，在独立安装根中导入后通过统一 `nsim` 启动器运行 3 个 fake smoke 任务。还生成了 Linux x86_64/Python 3.9 目标依赖包，但没有 Linux 目标机安装验收。以上测试不能代替真实 GitHub Release、VCS、LSF、Verdi、Syscan 和硬盘故障验收。

以上构建和离线安装记录只对应当时源码；发布候选需要重新构建并核验与交付提交一致，详见 [打包与发布](releasing.md)。

## 2026-10-27 项目初始化与注册验收

按新的项目管理接口实现并通过 87 项本地测试。覆盖默认/自定义目录、已有 `.nsim`、重名与并发注册、已有文件保护、子目录识别、事务失败回滚与进程中断恢复、外部编辑保护、latest/显式预发布版本、缺失运行时诊断、双源码 filelist、include/library 成员变化以及历史回归绑定。

本次构建的候选 wheel/sdist 通过分发内容检查，并制作含依赖的离线包。在独立临时安装目录实际导入该离线包后，通过稳定 `nsim` 入口完成 bootstrap、list/status、update/switch、fake 回归通过、失败回归在注册更新后的重跑、历史 plan 字节保持不变、VCS dry-run 读取更新后的 RTL，以及工具版本缺失时的基本查询。真实 VCS/LSF 未执行，发布状态未改变。

新布局的 `filelist/sim.f` 是工具维护的组合来源说明；实际编译 token 与 argv 固化到每次计划和命令预览，不能直接把该来源说明作为原生 VCS filelist 使用。旧布局继续可读；自动迁移、项目注销/搬移不包含在本次实现中。

## 2026-10-27 提交前复查

新增回归测试先复现并修复了 `.nsim/` 内部路径误识别、单个项目访问失败中断全局列表、异常能力元数据导致版本选择退出，以及更新/恢复跟随被替换的父目录符号链接的问题。同时校正安装器的数字版本排序，拒绝损坏的安装凭据与恢复记录。完整本地测试扩展到 95 项并全部通过。

源码归档补充包含 `scripts/check_distribution.py`，分发检查同时核对该脚本、项目发现同步脚本、文档和模块内容。原生 EDA/farm 的执行边界保持不变。

## 2026-10-28 回归隔离与 regression list

新增 `regr -rl/--regr-list`，行内复用单次回归选项，先规划全部行再顺序执行。
新增 `--plusargs` 最终运行覆盖，保持编译身份不受运行参数影响。回归产物与单跑工作区隔离，
每次执行/回归重跑独立目录；汇总 JSON、HTML 和 records 放在 `report/`。
子回归与批次分别保存 Dashboard 和报告，统计按计划成员和控制器事件更新。
LSF worker 请求保留回归 ID/类型以校验隔离路径；sim 多组合复制已校验编译到独立快照。
用法、恢复限制及数据布局见 [单次与批量回归](regression_batches.md)。

本次验证：完整 unittest 套件 333 项全部通过（221.164 秒）；项目发现/CLI 简写嵌入一致性检查与
`git diff --check` 通过。仓库 regression list 示例执行两次回归、6 个任务全部通过，重新校验
批次与子回归证据通过，并检查 6 个 HTML 文件的静态本地链接。LSF 验证使用本地进程级测试服务，
未在真实 farm 或商业 EDA 工具上验收。
