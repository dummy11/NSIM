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

## case 目录什么时候创建

现代回归使用 `isolated-regression-v1` 独立目录布局。启动时创建本轮回归根和 `report/`，检查全部计划路径，并准备共享编译目录；**不预先创建全部 case 目录**。每个任务取得 `--jobs` 名额、通过编译准备后，才创建并锁定自己的 `sim_dir/case_seed/`，建立 `env`，写运行记录，再执行或提交仿真。目录创建或加锁失败仅记录为该 case 的问题，其他 case 继续。

例如有一万个 case、`--jobs 8`，启动阶段只准备已派发任务的 case 目录；后续随名额空出逐个创建。若唯一名额被状态不明的 job 占用，后续 case 不会留下空目录。已完成 case 的目录仍保留，所以本轮最终执行完一万个 case 后仍会有一万个结果目录。单跑及旧的共享目录布局保留原有预先加锁规则，避免改变历史覆盖行为。

local 的正常回归 case 通常有两次 `run.json` 逻辑写入（执行前、attempt 完成）；正常单跑通常有三次（开始、attempt 完成、单跑最终发布）。这不是物理磁盘 I/O 次数。LSF 额外在提交身份、job ID、未知/终态等关键节点落盘；普通 PEND/RUN 状态轮询不反复重写 `run.json`。

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

实际提交包含 token 派生的作业名、`-cwd`、独立 stdout/stderr 路径及队列/资源设置。收到正整数 job ID 后，使用 `bjobs -a -json -o "jobid stat exit_code exec_host job_name user output_file error_file submit_time" JOB_ID` 查询。除了 ID，还核对完整作业名、提交用户、输出路径和已观察到的提交时间；同一个 ID 被复用或身份字段缺失都不能确认终态。客户端必须支持这些 JSON 字段，不能使用截断的作业名。

DONE/EXIT 等明确终态才释放名额；PEND/RUN/暂停占名额；UNKWN/ZOMBI、缺失记录、不可解析输出或查询失败不能证明进程已停止。状态依据 [IBM bjobs 文档](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=bjobs-description)。连续查询未知超过预算后，该 job 保留占用和一个 `--jobs` 名额。其他空闲名额仍可补交；`--jobs=1` 且唯一名额未知时，后续 case 显示未执行，整轮不发布 `final.json`。

`bsub` 回复中没有可靠 ID，仍可能已经受理，不能自动重投。只有失败回执明确拒绝提交时才视为未提交。单个 job 的提交、查询、执行或日志收集错误不设置整批取消信号。Ctrl-C 或该 job 的调度等待超时可请求 `bkill`，但须先核对作业身份，之后再查询终态；取消命令成功本身不能解除占用。不使用 `bkill -r`，参见 [IBM 强制移除说明](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=jobs-forcefully-removing-job-from-lsf)。

新提交显式设置 `LSB_JOB_INCLUDE_POSTPROC=Y`，使调度终态包含集群后处理；不能把单纯的 DONE/EXIT 默认解释为后处理也结束。旧记录没有这个提交约定时，`release` 需要 PDONE/PERR/POST_DONE/POST_ERR 等明确的后处理终态，否则拒绝释放。具体环境仍须验收，尤其是跨集群转发。依据 [IBM 后处理配置](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=processing-configuration-modify-pre-post-execution) 与 [LSF 环境变量参考](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=variables-environment-variable-reference)。

### 记录、token、ID 和占用分别是什么

| 名称 | 通俗含义 | 保存位置/用途 |
| --- | --- | --- |
| token | nsim 在提交前生成的本次提交编号，32 位十六进制字符串；不是密码 | `run.json.lsf.token`；重跑产生新 token，`release` 精确选中它 |
| job name | 向 LSF 提交的名字，形式为 `nsim_<token>` | ID 丢失时的查询线索；名字匹配后仍核对用户和输出路径 |
| job ID | LSF 受理后返回的数字编号 | `run.json.lsf.job_id`；回复丢失时可为空 |
| 占用 | “这个目录或编译产物可能仍被这个 job 使用”的保护 | 未确认停止时阻止覆盖；不是认定磁盘有故障 |
| `run.json` | 单 case 的运行记录，同时保存新格式仿真 job 的身份、固定请求、状态和 worker 结果 | 新仿真不再另外生成 `job.json` 或 `.lsf-active.json` |
| `job.json` | 旧格式及当前编译 job 的独立调度记录 | `com_dir/.lsf-jobs/<token>/job.json` 等旧位置，继续兼容读取 |
| 编译读者记录 | 说明哪一个远程仿真仍可能读取这个编译产物 | `com_dir/.lsf-readers/<token>.json`；确认结束后删除对应一项 |

`run.json.lsf.terminal` 表示调度 job 已确认结束；顶层 `terminal`/`lifecycle` 表示 case/控制器记录的生命周期。**调度结束、日志收集完整、case 通过是三个独立结论**，不能互相替代。

### 新仿真的文件布局与写入顺序

```text
regr_dir/<本轮目录>/
  report/
    plan.json, state.json, ...     # 本轮共用的计划/汇总
    lsf.log                       # 本轮共用的控制日志，按 token 标记
    .lsf/<token>/                 # 运行中的临时交换位置
      worker.json                 # worker 结果
      farm.out, farm.err          # LSF 输出
  <submit>/<workspace>/
    sim_dir/<case_seed>/
      run.json                    # 内含 lsf 身份、固定请求与收集结果
      run.log                     # 完整仿真主日志
      ...                         # 正常工具结果和波形
    com_dir/.lsf-readers/<token>.json
```

单跑没有回归 `report/`，临时交换位置改为 `sim_dir/.lsf/<run-id>/<token>/`，共用日志为 `sim_dir/.lsf/<run-id>/lsf.log`。临时路径包含本次运行/token，不会被下一次同 case/seed 的运行复用。编译 job 数量较少，本次保留原有 `.lsf-jobs/<token>/` 请求、job、worker、控制日志和 farm 输出，以及 `.lsf-active.json`；不把可变读者列表塞进有哈希约束的 `build.json`。

每个仿真的顺序是：

1. 取得名额并准备 case 目录，核对旧 `run.json`、旧标记及锁；受管旧目录的记录缺失/损坏时拒绝覆盖。
2. 在 `run.json.lsf` 保存 token、作业名、提交意图和固定执行请求，保存编译读者记录，并同步关键目录项，然后才调用 `bsub`。写不进去就不提交。
3. 收到 ID 后保存；回复丢失保留“可能已提交”的记录。worker 校验固定请求摘要、公共计划摘要、token 和编译产物后执行。回归请求引用公共 `plan.json`，不在每个 case 中复制整份回归计划。
4. 查询到状态变化时更新内存和显示；关键结果落盘。控制日志使用批次共用文件，普通轮询不每次 `fsync`，提交与结束等关键节点同步。
5. 确认调度终态、收齐结果并将输出合入 `run.log` 后，先保存结果和主日志摘要，再清理已被收集的临时 worker/farm 文件及本 token 的读者记录。状态不明或收集失败时保留临时证据。

这是减少永久小文件和每次轮询同步操作，不是取消所有 I/O。运行中仍需临时 worker/farm 文件、编译读者记录和原子写入的临时文件；每个已执行 case 最终仍有自己的结果目录。更多真实 NFS 性能数据须在目标环境测量。

主日志为 `compile.log` 或 `run.log`，工具自身日志为 `vcs.log` 或 `sim.log`。调度输出在 worker 分析完成后追加，不改变工具诊断行号。成功 worker 的 farm 输出必须在 `result_wait_seconds` 内可见且稳定，否则为调度错误，不能发布 PASS。新格式把 worker 结果和收集源的摘要内嵌到 `run.json.lsf`，报告只引用保留的主日志/结果，不能继续引用已清理的临时文件。LSF 输出回传行为见 IBM 的 [-o](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=o-o) 和 [-e](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=o-e) 说明。

回归终端 Dashboard 和 `live.json` 显示 job ID、阶段、状态与终态。DONE 本身不能代替 case 判定；worker 结果身份、退出码、必需检查与输入身份仍要校验，见 [终端 Dashboard](dashboard.md)。

## 检查与安全释放

```sh
# 只读查看本地保存的占用，不查询 LSF
nsim lsf inspect /data/output/regr_dir/本轮目录
nsim lsf inspect /data/output/base/rtl/sim_dir/smoke_42
nsim lsf inspect /data/output/base/rtl/com_dir

# 增加当前集群查询，分别显示保存状态和本次查询状态
nsim lsf inspect /data/output/regr_dir/本轮目录 --check-lsf --json

# 将 TOKEN 替换为 inspect 给出的完整 32 位 token
nsim lsf release /data/output/regr_dir/本轮目录 --token TOKEN
```

目录参数可为单个 case 目录、`com_dir`、单轮回归目录或其中的 `report/`。回归检查根据计划列出相关 case 和编译目录；`com_dir` 检查编译 job 及其全部仿真读者。批量回归请选其中一轮的目录，不递归猜测任意输出根。`report/` 是公共汇总目录，不是每个 job 的记录目录。

`inspect` 只读，默认列出未解决占用及异常记录，显示 token、job ID（若有）、保存状态、保护路径和缺失/损坏/冲突信息。`--check-lsf` 才查询当前集群，不写回旧记录。延迟创建尚未派发的 case 显示为未创建；已派发但记录缺失的情况明确提示，不能把不完整扫描当作“没有占用”。检查完整返回 0（即使仍有占用），记录或查询异常返回 2。

`release` 每次只处理一个明确 token，重新读取记录并取得 case 独占锁和编译保护锁，再重新查询集群，**不使用上一次 inspect 的查询结果直接解锁**。作业仍活动、查询失败、没有匹配项、多个同名匹配、身份不符、记录损坏、其他控制器持锁，均拒绝释放。没有 `--force` 或超时强制释放选项。ID 缺失时按完整作业名查询并核对用户和唯一输出路径；查不到不代表 job 已结束。当前不会自动解析 `bhist/bacct` 历史档案，超出 bjobs 保留范围的 job 需管理员进一步调查，使用新目录也可继续独立工作。

释放前写 `.lsf-release-<token>.json` 处理记录，绑定原记录摘要、实时查询证据、用户和保护路径；仅删除对应 token 的旧标记/编译读者，最后提交释放结论。中断时保留可重试的状态。正常 case 不增加这个文件；它只用于事后异常释放。原 `run.json`、旧 `job.json`、回归状态和原 PASS/FAIL 结论不改，避免破坏历史证据哈希。重复释放已经成功的 token 返回已释放，不重新提交、不取消作业。

释放占用不等于补齐结果，原先不完整的回归不会因此自动获得 `final.json`。`report --rebuild` 仍只收集可校验的已有证据。若需再执行，必须显式发起新运行；`rerun/recover` 仍遵守原有完整终态记录要求。

旧标记和 `job.json` 不批量删除或迁移。只有标记、没有可校验 job 记录时，inspect 尽量显示剩余 token 和路径，但 release 拒绝猜测。所有记录和 ID 都丢失时，nsim 无法自行证明目录空闲。旧版本不认识新 `run.json` 占用协议，与旧版本并行运行必须使用互不重叠的输出目录。

关键 LSF 提交和释放使用文件同步及父目录 `fsync`；不支持目录同步的文件系统会明确失败。POSIX 同步与锁的真实跨主机/断电保证取决于挂载和服务端实现，本地测试不证明 NFS 已通过验收。

## 集群工作环境

提交机需要可用的 LSF 客户端和已经配置好的 LSF 登录环境；执行节点必须通过相同绝对路径访问项目输入、nsim 安装、Python、编译产物及输出存储。当前采用共享文件系统，不负责上传输入或复制运行时。运行时和依赖身份严格校验，提交机/执行节点的 Python、nsim 文件及依赖必须兼容并匹配记录。

开发模式通过 `PYTHONPATH` 引入源码时，应使用解析符号链接后的真实路径。控制器若从 `/tmp/.../src` 导入，而 worker 从 macOS 的 `/private/tmp/.../src` 导入，即使文件相同，也可能触发 `current NSIM source differs from the recorded run`。统一源码入口为真实路径后重新生成计划；已有计划仍按原路径与哈希校验。

默认 worker 使用控制器的 Python 路径；可配置 `scheduler.worker_python` 指定可在提交机校验、在执行节点访问的解释器，但仍需符合上述运行时身份约束。该选项不提供跨操作系统部署转换。

提交命令读取提交机已有 LSF 客户端环境；worker 的工具子进程只获得计划记录的 PATH/LANG/LC_ALL/TZ 和 submit.env。所需 EDA 环境应写在 submit.env 或受版本管理的工程包装器中；env 会明文保存，不应包含秘密。worker 在执行前后校验输入、运行时代码及所引用编译产物的哈希，在执行节点检查工作目录、输出存储可写性、空间/inode 及占用标记。挂载不可见或依赖变更不会被发布为通过。

本地测试服务通过真实 subprocess 模拟 bsub/bjobs/bkill 并启动实际 nsim worker，覆盖提交、滚动补位、队列、并发上限、取消、状态丢失和节点校验。这证明 nsim 调用链通过测试；真实 LSF 集群、商业编译器和执行节点差异仍需在目标环境验收。
