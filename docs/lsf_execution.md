# nsim LSF 与 local 执行

`com`、`sim`、`run`、`regr` 默认使用 LSF；submit 可设置 `scheduler.kind`，命令行 `-scheduler` 优先。`command` 的真实编译和仿真命令通过 `bsub` 提交，在执行节点运行 nsim worker；`fake` 也可走同一调度链路。原生 `adapter: vcs` 仍只提供 dry-run；当前实际 VCS 工程应使用符合结果协议的 `command` 包装器。

## 日常命令

执行节点必须具备[环境只读保护](read_only_environment.md)：Linux 需要可用的 `bwrap` 和 namespace
支持，macOS 使用 `sandbox-exec`。编译、仿真和 hooks 都受保护；无法建立保护就报错，不启动无保护任务。

以下命令在已登记项目内使用，输出根已通过 `nsim update --output-root /data/output` 配置并准备好。工程外加 `--project 项目名`。

```sh
# 默认 LSF；两个 submit 共用最多 8 个未结束作业
nsim regr -l smoke.list stress.list -sub submit_0 submit_1 \
  -seed 42 43 -jobs 8 -queue simulation

# 随机生成 10 个不同 seed；并发窗口仍为 8
nsim regr -l smoke.list -sub submit_0 -repeat 10 -jobs 8 -queue simulation

# 分开编译与仿真，保持相同工作区和编译目录
nsim com -sub submit_0 -dir nightly -queue simulation
nsim sim -sub submit_0 -dir nightly -com-dir com_dir \
  --case smoke -seed 42 -queue simulation

# 本地执行；无需 bsub/bjobs/bkill
nsim regr -l smoke.list -sub submit_0 -seed 42 -scheduler local -jobs 4
```

`-j`、`-jobs`、`--jobs` 等价，默认 1，必须为正整数。`-q`、`-queue`、`--queue` 等价；`-scheduler`、`--scheduler` 等价。不指定队列时不传 `bsub -q`，使用集群自身默认队列。CLI 队列覆盖 submit 配置；local 不使用队列或 LSF 资源配置。目录、mode、seed 和 repeat 语义与 [执行阶段](execution_stages.md) 一致，切换调度器无需更换目录接口。

## 并发窗口与完成钩子

`-jobs N` 限制本次调用已经提交、尚未确认结束的作业数，所有 submit 共用额度。排队、运行、暂停中的作业都占名额。它不是总提交次数，也不是单个作业申请的 CPU 核数；两次独立 nsim 调用各自维护额度。

控制器以线程等待各作业状态，任务完成回调唤醒派发循环，检查空余名额并补交后续任务。例如 N=3，先提交 A/B/C；B 先结束，立即补交 D，无需等 A/C。这个过程持续到计划任务耗尽；没有就绪任务时不会为凑满 N 而重复提交。

`run/regr` 为同一 submit/输出根只编译一次，编译成功后各 case/seed 共用产物；编译作业也占并发额度。等待同一编译产物的任务不会另交编译作业，因依赖关系可能暂时未占满窗口。fake 准备和 prebuilt 输入登记不启动编译器，因此没有对应编译作业。

## Submit 配置

在现有 submit JSON 中添加下面的 `scheduler` 对象即可；省略整个对象时也默认 LSF。

```json
{
  "kind": "lsf",
  "queue": "simulation",
  "bsub": "bsub",
  "bjobs": "bjobs",
  "bkill": "bkill",
  "resources": ["select[mem>4096]"],
  "licenses": {"vcs": 1},
  "poll_seconds": 1,
  "command_timeout_seconds": 30,
  "deadline_seconds": 3600,
  "unknown_seconds": 30,
  "cancel_seconds": 30,
  "result_wait_seconds": 5
}
```

`queue`、`resources`、`licenses` 均可省略，示例中的名称必须按实际集群配置替换。`--resource` 可重复指定，`--license vcs=1` 可重复指定不同资源；CLI 列表整体替换 submit 中对应列表。LSF 客户端名称在规划时解析为绝对路径，也可直接配置绝对路径。

| 设置 | 含义 |
| --- | --- |
| `poll_seconds` | 查询作业状态的间隔 |
| `command_timeout_seconds` | 每次 LSF 客户端命令的等待上限 |
| `deadline_seconds` | 每个已提交作业的总等待预算，含排队和执行；到期发起取消 |
| `unknown_seconds` | 连续无法确定作业状态的预算；到期保留该 job 的名额和保护记录 |
| `cancel_seconds` | 发出 bkill 并查询确认终态的总预算 |
| `result_wait_seconds` | 已确认终态后等待共享存储上的 worker 结果可见的预算 |

这些值必须为正数。`compile.timeout_seconds` 和 submit 顶层 `timeout_seconds` 分别限制执行节点上的编译和仿真进程运行时间，不计排队时间；scheduler 的总预算仍独立生效。长作业应同时设置合适的两类预算。

## Hooks 与分析开销

编译与仿真的前后 hooks 均在各自原 job 中按 submit 声明顺序执行，不额外提交 LSF 作业。最后的 post_sim log_analysis 默认使用 VCS/UVM 分析器，也可由用户同名 hook 替换；替换项必须位于列表末尾。完成后才释放本次作业的并发名额。日志稳定性检查、扫描上限、结果格式及自定义示例见 [Hooks 与日志分析](hooks_and_log_analysis.md)。

## 状态、取消和证据

实际提交包含唯一作业名、`-cwd`、独立 stdout/stderr 路径及队列/资源设置。提交成功后记录返回的正整数 job ID，使用 `bjobs -a -json -o "jobid stat exit_code exec_host" JOB_ID` 查询对应记录。LSF 客户端需要支持该 JSON 输出格式。

DONE/EXIT 等明确终态才释放名额；PEND/RUN/暂停占用名额；UNKWN/ZOMBI、缺失记录、不可解析输出或查询失败都不能证明进程已停止。状态语义依据 [IBM bjobs 文档](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=bjobs-description)。短暂查询失败会继续查询；超过预算仍未知时，该 job 保留保护文件和一个 `--jobs` 名额，不自动重投或取消。其他已运行 job 继续；只有其余名额确实空闲时才提交新 job。`--jobs=1` 且唯一名额未知时，后续 case 显示未执行，不被伪记为取消；整轮不发布 `final.json`。

`bsub` 无可靠 job ID 时，受理状态未知；仅在失败回执明确表示拒绝时记为已拒绝。未知受理保留名额与保护文件，不重复提交。单个 job 的提交、查询、执行或日志收集错误不会设置整批取消信号。Ctrl-C 和该 job 的调度等待超时可对已知 job ID 执行 `bkill`，随后继续查询，只有确认终态才解除目录占用。取消命令返回成功本身不算终态证据；不使用 `bkill -r`，因为强制从调度器移除不能证明操作系统进程已经终止。参见 [IBM 强制移除说明](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=jobs-forcefully-removing-job-from-lsf)。

记录位于实际编译或仿真目录内，无需 event-root：

```text
com_dir/ 或 sim_dir/case_seed/
  .lsf-jobs/<token>/
    request.json       # 带哈希的执行请求
    job.json           # job ID、提交回执、状态历史、取消诊断
    worker.json        # 执行主机、退出状态、输入/存储检查结果
    controller.log     # 提交、查询、取消及状态事件的内部中转
    farm.out           # LSF worker 标准输出和作业报告，最终合入主日志
    farm.err           # LSF worker 标准错误，最终合入主日志
  .lsf-active.json     # 在途或状态不明的写入占用
com_dir/.lsf-readers/  # 远程仿真对编译产物的占用记录
```

主日志为 `compile.log`（编译）或 `run.log`（仿真），包含主命令和前后处理的 stdout/stderr，以及 LSF 提交、查询、取消命令及其输出、状态变化、执行主机、worker 结果、farm.out/farm.err 和作业报告。提交拒绝、启动失败或排队超时也能直接查看对应主日志。工具自身的 `-l` 日志分别为 `vcs.log`、`sim.log`，默认不重复扫描。

主日志在提交前创建，worker 追加执行输出；调度事件带时间戳，先写入内部中转文件，在 worker 分析结束、作业终态确认、LSF 输出文件稳定后追加到主日志尾部。这样不会让查询输出干扰日志稳定性判断，也不改变工具诊断行号。原始中转文件和机器记录继续保留作证据，用户无需逐个打开。LSF 可以在作业结束后才将临时输出复制回来，具体行为见 IBM 的 [-o](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=o-o) 和 [-e](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=o-e) 说明。

成功 worker 的 LSF 输出必须在 `result_wait_seconds` 内可见且稳定；缺失或合并过程中变化会返回调度错误，不能发布 PASS。合并前核对 worker 的执行日志哈希，合并后保存整份主日志及原始输出的哈希。worker 的日志分析记录描述合并前的执行内容；`job.json.log_collection` 描述完整主日志的收集结果。状态未确认时仅汇集当时可见内容，主日志明确标记不完整，目录占用继续保留。

回归终端 Dashboard 和 `live.json` 保留最近作业的 job ID、阶段、状态与终态标记，最终报告保留全部作业。单跑保留机器记录；单个 com 可从 build.json 中的 scheduler 查看作业。

每个 case 的分析与执行记录保存后，控制器直接更新终端 Dashboard；LSF 查询到状态变化也会触发更新。DONE/EXIT 本身不能替代 case 判定。Dashboard 无定时轮询，不增加 LSF job；调度器自身的 bjobs 状态查询仍保留。见 [终端 Dashboard](dashboard.md)。

LSF DONE 只表示调度层结束。nsim 还要核对 worker 结果身份、执行退出码、声明的编译产物或仿真结构化结果、必需检查与输入身份，才能判为通过。缺失/损坏 worker 结果为 SCHEDULER_ERROR；状态无法确认则为 LSF_UNKNOWN，保留占用记录，不发布 final.json。

控制器被强制杀死、提交回执丢失、取消无法确认时，占用记录会保留，阻止覆盖原仿真目录或重建被使用的编译目录。此类记录不支持自动 rerun/recover。应先由操作人员依据 job.json 的 job ID 或唯一作业名向集群核实实际进程已结束，再处理残留占用；不能只依据控制器已经退出而删除。其他目录仍可独立使用。

## 集群工作环境

提交机需要可用的 LSF 客户端和已经配置好的 LSF 登录环境；执行节点必须通过相同绝对路径访问项目输入、nsim 安装、Python、编译产物及输出存储。当前采用共享文件系统，不负责上传输入或复制运行时。运行时和依赖身份严格校验，提交机/执行节点的 Python、nsim 文件及依赖必须兼容并匹配记录。

开发模式通过 `PYTHONPATH` 引入源码时，应使用解析符号链接后的真实路径。控制器若从 `/tmp/.../src` 导入，而 worker 从 macOS 的 `/private/tmp/.../src` 导入，即使文件相同，也可能触发 `current NSIM source differs from the recorded run`。统一源码入口为真实路径后重新生成计划；已有计划仍按原路径与哈希校验。

默认 worker 使用控制器的 Python 路径；可配置 `scheduler.worker_python` 指定可在提交机校验、在执行节点访问的解释器，但仍需符合上述运行时身份约束。该选项不提供跨操作系统部署转换。

提交命令读取提交机已有 LSF 客户端环境；worker 的工具子进程只获得计划记录的 PATH/LANG/LC_ALL/TZ 和 submit.env。所需 EDA 环境应写在 submit.env 或受版本管理的工程包装器中；env 会明文保存，不应包含秘密。worker 在执行前后校验输入、运行时代码及所引用编译产物的哈希，在执行节点检查工作目录、输出存储可写性、空间/inode 及占用标记。挂载不可见或依赖变更不会被发布为通过。

本地测试服务通过真实 subprocess 模拟 bsub/bjobs/bkill 并启动实际 nsim worker，覆盖提交、滚动补位、队列、并发上限、取消、状态丢失和节点校验。这证明 nsim 调用链通过测试；真实 LSF 集群、商业编译器和执行节点差异仍需在目标环境验收。
