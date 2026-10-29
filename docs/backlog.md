# NSIM 开发待办

此文件按 2026-10-28 的工作区实现整理后续工作，不设置阶段冻结或文档审批门禁。验证记录见 [实现状态](implementation_status.md)，使用入口见 [README](../README.md)。实现了客户端调用不等于已经通过真实 EDA 或集群验收。

| 领域 | 当前能力 | 后续工作 |
|---|---|---|
| Python 包和 CLI | 可安装，支持版本、工程发现、回归、报告和恢复命令；独立安装器支持离线包、版本切换和稳定启动器 | 发布稳定版本，补齐远端认证和跨平台安装验证 |
| 项目管理 | bootstrap/list/status/update/switch、用户级注册索引、schema 2 双源码路径、子目录识别、事务恢复 | 显式迁移与注销/搬移工作流；真实共享文件系统锁行为验收 |
| Submit 配置 | mode 公共模板、单层 extends、对象合并、数组与 hook 阶段替换、父子配置快照 | 在实际工程中验证模板与包装器；已有项目按需手工接入模板 |
| 编译与回归 | com/sim/run/regr、command 实际编译、产物哈希与复用、多 caselist/submit/seed、随机 repeat、滚动并发 | 继续验证工程构建入口、结果解析器与性能检查集成 |
| 输出与并发隔离 | 固定 case/seed 目录、覆盖前归属检查、编译读写锁、LSF 持久占用、运行时隔离 | 真实多用户共享存储、跨主机锁与原子替换验收 |
| Hooks 与日志 | 四阶段有序 hooks、VCS/UVM 分析、自定义分析协议、日志稳定性及扫描预算、case 摘要 | 用真实工具版本日志验证解析边界与开销 |
| Dashboard 与报告 | 事件驱动终端进度、有界 live.json、自包含分页 HTML、最终证据校验 | 真实大回归下的资源开销和终端体验验收 |
| VCS 与波形 | 原生适配器配置及命令预览、filelist 解析；实际执行可接 command 包装器 | 原生 VCS 执行、专用编译产物验证、FSDB 与 Verdi 调试 |
| DPI | Makefile 构建命令与缓存范围预览 | 实际 `.so` 构建、校验和生命周期管理 |
| LSF | bsub/bjobs/bkill、执行节点 worker、队列/资源/license、全局并发窗口、取消确认与未知作业保护 | 真实 farm 的客户端格式、Python/工具环境、共享路径与故障场景验收 |
| 运维 | 失败重跑、证据校验、健康输出根恢复 | 报告比较、归档、日志索引与安全清理 |

真实 EDA 与 farm 能力需要有相应工具和节点的环境验证；本地 dry-run 只证明命令可规划。

## 2026-10-28 实现审查发现

本节记录对 `src/nsim` 的代码审查与针对性实验。每条给出源码位置与复现方式；**完成一条后必须在 [实现状态](implementation_status.md) 追加带日期的验收记录，并在测试中同时覆盖正例与边界**。

适用范围说明：下述实验使用 fake 适配器、合成日志与本地进程级 LSF 桩，不构成真实 VCS、LSF 或 farm 验收。工作区当前有大量未提交改动，历史记录中的测试数量不能代表当前统一验收状态，实施每条前先跑一次全量回归并记录当时的提交或快照身份。

### A. 已确认缺陷

**A-1 日志错误覆盖判定，且会吞掉原始失败类型。** 两个独立问题：

1. 结构化 checks 全部通过、日志出现行首 `Error:` 的 case 被判为 `FAIL`。复现：command 适配器打印 `Error: expected 5 transactions got 3 (recovered, non-fatal)` 并写入全部通过的结果，得到 `verdict: FAIL`、`reason: all required checks passed | log: ...`、`checks` 全为 `passed: True`。位置：[log_analysis.py](../src/nsim/log_analysis.py:18) 的 `RUNTIME` 正则、[runner.py](../src/nsim/runner.py:107)。
2. 同一段覆盖逻辑对 `INCOMPLETE`、`NO_COMPARE` 也生效，原始失败类型被写成 `FAIL`，丢失“未比较”“分析不完整”等信息。

方向：检查结果、日志诊断、解析完整性、最终判定分开保存；`log_policy` 与精确豁免（`ignore_patterns`/`ignore_codes`）必须显式声明，默认策略需先与使用方确认，不经审查不默认放宽为 PASS；覆盖前保留原 verdict（或直接不再覆盖非 ACCEPTED 的判定）。

**A-2 LSF 占用残留缺少安全恢复路径。** 控制器被杀或取消无法确认终态时留下 `.lsf-active.json` 与 `.lsf-readers/`，之后同 case/seed 的 `nsim run` 永久失败，CLI 无任何恢复命令。复现：手工写入 `.lsf-active.json` 后以相同 seed 重跑，得到 `LSF job reservation still active or unresolved`。位置：[lsf.py](../src/nsim/lsf.py:28)、[layout.py](../src/nsim/layout.py) 的 `simulation_lock`、[compilation.py](../src/nsim/compilation.py) 的 `reserve`。

方向：`nsim lsf status` 只读展示；恢复命令必须**核实作业身份与终态**后，在相应目录锁保护下同时处理 `.lsf-active.json` 与 `/workspace/.lsf-readers/<token>.json`，写入恢复记录。**不得以“超过保留时限”作为删除依据**——时间流逝不证明远端作业已结束，错误删除会让后续运行覆盖仍在写的日志与编译产物。

**A-3 日志扫描与判定边界的实测缺口。** 日志位置正则只识别 `"file.sv", 123` 与 `file.sv(123)` 两种形态，VCS 的 `at file.sv:123`、含空格路径不匹配；扫描超出 `max_scan_bytes` 时 `complete=False` 会把 ACCEPTED 结果降级为 `INCOMPLETE`。位置：[log_analysis.py](../src/nsim/log_analysis.py:19)。

方向：补充位置正则并加边界测试；把“扫描预算不足”与“分析失败”区分为不同状态并用不同文案呈现。

### B. 需要先定契约再实施

**B-1 重复哈希成本（需先定快照语义）。** 每个 attempt 在编译前后各调用一次 `check_identity`，对全部输入重新计算摘要；`Builds.validate` 再校验一次。实测 2000 个输入文件（34 MB）：1 case 0.51 s，50 case 4.29 s（user 7.59 s）。

注意：`(size, mtime_ns, inode)` 相同**不保证内容相同**，元数据缓存不能无条件替代内容校验。建议顺序：先优化单次校验内的去重（报告侧 `assessed` 已按 `(path, sha256)` 做单次调用内去重，可对齐）、明确“不可变输入快照”的边界（源码树在运行期间不变是否作为契约成立）；再引入缓存，且缓存失效必须回落到内容校验。`report show --quick` 若跳过证据验证，输出必须明确标注“证据未重新验证”，且不能承诺与严格模式判定一致。

**B-2 LSF 未知作业与 `--jobs` 额度的关系（需先定策略）。** 任一作业查询持续未知超过 `unknown_seconds` 会触发全局取消（[lsf.py](../src/nsim/lsf.py:204)）。该取消同时承担“限制未确认结束作业数”的作用：若未知作业实际仍在运行，单纯释放槽位并继续补交会突破 `--jobs`。方向：允许其他已运行作业继续完成，未知作业保留额度；是否停止新派发作为显式策略（例如 `on_unknown: hold|stop`），默认取保守值。

**B-3 集中轮询。** 每个活跃作业各有一个 `bjobs` 轮询循环，`poll_seconds` 默认 1 s，`-jobs 200` 时约每秒 200 次调用。方向：按调度配置分组批量查询，**只查本控制器记录的受管 job ID**，不拉取整个集群；`run_process` 改为消费状态事件。保留单作业查询作为兜底路径。

**B-4 历史证据、覆盖语义、保留策略与 GC 必须统一设计。** 现状：同一 case/seed 重跑会清理并覆盖仿真目录（含该目录下的 `plan/state/final`），`com` 的 journal 就是编译目录本身，`regr_dir` 只增不减。这是 [执行阶段](execution_stages.md) 与测试明确规定的行为，**不是单纯的实现遗漏**。

仅把 journal 移到 `regr_dir` 不能保住被覆盖的日志、结果和编译证据，旧报告仍可能变为 `INCOMPLETE`。需要先定义：保留哪些证据、证据之间的引用关系、活动作业/写入锁的保护规则、GC 的删除前置条件。改动前先更新 `execution_stages.md` 的契约与对应测试。

**B-5 项目迁移与注销。** `bootstrap` 要求“显式迁移到 schema 2”，但没有迁移命令；项目目录被删除或搬移后注册表留下脏条目。注意：发现逻辑**明确拒绝 `.nsim/` 与根目录旧配置同时存在**（[project_paths.py](../src/nsim/project_paths.py:72)，已复现）。迁移必须把旧配置移入备份位置、同步更新注册表、并复用现有事务与中断恢复机制，不能只新增文件。注销/搬移同属一套事务设计。

**B-6 报告体积与自包含契约。** 实测 5 万 case 时最终 `report.html` 为 9.7 MB（`view`+`page` 约 0.2 s），分页只在 DOM 层。分片方案会改变“自包含报告”的交付方式，需先明确离线打开、拷贝分享、跨机器查看的要求（当前分析文件链接已是相对路径，最终报告内为 `file://`）。先定交付要求，再决定是否分片。

### C. 可直接实施的小项

**C-1 submit 合并的 `null` 删除语义。** [submit_config.py](../src/nsim/submit_config.py) 的 `merge` 对字典递归合并，缺少删除键的表达。需定义 `null` 语义并与“hook 阶段整体替换”的既有规则一致，补充合并优先级测试。

**C-2 原子写的父目录持久化。** [storage.py](../src/nsim/storage.py:29) 的 `atomic_write` 只对文件 `fsync`，掉电后 `os.replace` 可能未落盘。补 `O_DIRECTORY` 打开与 `fsync`，并注明平台边界（非 POSIX 环境的行为）。

**C-3 编译输入依赖分类细化。** 计划已区分 `input_hashes`（全量）与 `build_inputs`（`eda.prepare` 返回的编译相关集合，见 [eda.py](../src/nsim/eda.py:148)）。后续是细化分类：确保 command 构建所需的 Makefile、脚本、include 目录等既不被漏掉，也不把无关测试资源算进编译身份。

**C-4 归属标记补 mode。** `run.json` 已记录 mode（[runner.py](../src/nsim/runner.py:76)），但 `.nsim-owner.json` 的 `identity()` 未包含 mode 与提交名。补字段属于归属校验契约变更，需同步 `claim_product` 的兼容策略与旧标记迁移。

**C-5 LSF 工具缺失提示。** 规划阶段已通过 `unresolved_tools` 报告 `executable unavailable`，可改善文案（说明 `-scheduler local` 或安装 LSF client）。**默认 `lsf` 是当前明确约定，自动切换 local 属于行为变更**，需单独评审。

**C-6 模板与 golden 默认值。** 默认 RTL 模板故意设置 `required_checks=['functional']` 与 `golden_enabled=false`，可以正常加载（[submit_templates.py](../src/nsim/submit_templates.py:9)）。若后续要让 golden 成为默认检查，需同时调整模板、`config.load_submit` 默认值与文档，并加“默认模板可直接 `nsim submit check`”的回归测试。

### D. 发布与安装

拆成独立任务，不与执行核心混在一起：

- 安装器 `default_root()` 经符号链接调用时的行为一致性；共享安装下的读取验证。
- 安装失败回滚的具体触发条件需要写清楚（有效安装凭据会使安装提前返回），补“失败删除时是否存在同版本运行中进程”的分析与保护。
- 仓库无 CI，259 项测试（历史记录）仅本地执行；未提交改动较多时需要先建立可重复的全量回归基线。
- `pyproject.toml` 缺 `license`、`classifiers`、`[project.urls]`。
- 离线包与 `SHA256SUMS` 流程在生产 Release 上的验收；`--download-deps` 在目标平台的验收。

### 实施顺序（校正后）

1. **校正与复现**：为每条附复现命令、源码位置、快照身份（commit 或 wheel hash），并跑一次全量回归建立基线。
2. **定契约**：A-1 的日志判定规则、B-2 的未知作业策略、B-4 的证据保留与覆盖语义。这三项决定后续实现形态。
3. **实现**：A-2 的安全占用恢复、B-3 分组轮询、B-1 经去重与快照边界验证的哈希优化。
4. **统一设计**：B-4 历史证据 + GC + B-5 项目迁移注销，一次完成契约、实现与文档。
5. **按实测收益**：B-6 报告体积、C-1 `null` 语义、C-2 持久化、C-3 依赖分类、C-4 归属标记、D 发布安装。

本地 fake 与进程级 LSF 桩通过不代表真实 EDA 或 farm 验收。
