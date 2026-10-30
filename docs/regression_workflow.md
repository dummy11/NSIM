# 回归：派发、目录与收尾

本页以 `regr` 的单次回归和 regression list 批量回归为主线，集中说明选项、参数优先级、
目录以及 `inspect/release/rerun/recover`。独立 `run/sim --case` 见 [独立单跑](single_run.md)；
caselist 的行语法见 [Caselist 语法](caselist.md)，submit 字段见 [Submit 语法](submit.md)。
`-sub/-l/-rl` 支持 [TOML 名称映射](catalog.md)，配置后可以直接写 `-sub rtl -l smoke` 或 `-rl nightly`。

## 创建单次或批量回归

```sh
# 一轮回归：多清单、多 submit、两个 seed
nsim regr -l caselist/smoke.list caselist/basic.list \
  -sub rtl rtl_trace -seed 42 43 -j 8 -o /data/output

# 多轮回归：文件每个有效行是一轮独立 regr
nsim regr -rl regression/nightly.list -o /data/output
```

`regr -l` 与 `regr -rl` 互斥。单轮按 submit × 去重后的 case × seed 派发；同一轮内每个
submit 编译一次，case/seed 共用该编译。`-repeat N` 生成 N 个不同 seed，与 `-seed` 互斥；
不指定时随机生成一个并记录。即使只有一个组合，`regr` 仍建立回归根和汇总。

`-rl` 外层只接受批次文件、项目、输出根和预览选项。每行写完整的一轮 `regr` 参数，
包括 submit、`-l`、seed/repeat、覆盖率、波形、调度和并发；行间不继承参数，也不跨行
合并编译。项目和输出根可从外层继承，行内显式值优先。批次按行顺序执行，
每行有自己的回归 ID 和报告；批次另有总览。完整格式见 [单次与批量回归](regression_batches.md)。

## 默认行为与选项

| 项目 | 默认 | 显式选项及作用 |
| --- | --- | --- |
| 覆盖率 | 开 | `--coverage on/off`；控制 VCS 或 command recipe 的编译与运行参数 |
| 波形 | 关 | `--waves on/off`；控制 VCS 或 command recipe 的运行参数 |
| 调度 | submit 值，未配置为 LSF | `-scheduler`、`-q`、`--resource`、`--license` 覆盖本轮配置 |
| 并发 | `-j 1` | `-j N` 限制本次未结束作业，不是总 case 数 |
| 输出根 | 项目默认值，或环境变量 | `-o ROOT` 选本次唯一输出根，不自动故障切根 |
| 工作区/目录 | mode、`com_dir`、`sim_dir` | `-mode`、`-dir`、`-com-dir`、`-sim-dir` 在本轮 submit 空间内解析 |

`--compile-arg`、`--define`、`--cm-metrics`、`--cm-hier` 控制编译，
`--sim-arg`、`--plusargs`、`--cm-dir` 控制仿真。`--cm-*` 需要本轮有效覆盖率为开，
并替换 submit 中相应的 VCS 覆盖率参数；普通编译参数追加到 submit 参数之后。
`--dry-run` 只保存计划与预览，不形成可用编译或可信终态。`--retries` 和
`--retry-seconds` 必须为 0。完整命令别名和各阶段可用性见 [CLI 参考](cli_reference.md)。

## submit、caselist 和 CLI 的优先级

| 配置层 | 合并与覆盖规则 |
| --- | --- |
| submit 继承 | 父模板 → 子 submit；对象递归合并，数组由子配置整体替换 |
| 场景开关 | 回归默认覆盖率开、波形关；显式 `--coverage/--waves` 优先；旧 `vcs.fsdb` 仅作兼容默认值 |
| 覆盖率参数 | submit 的 VCS/command recipe 提供参数；CLI `--cm-*` 替换对应 VCS 项；关闭开关时不应用 recipe |
| case 运行参数 | submit → caselist `@defaults` → 当前行 → case.yaml → `--sim-arg` → `--plusargs`；按选项键合并，同名 plusarg 以 `--plusargs` 为准 |
| 编译和调度 | `--compile-arg/--define` 追加；显式调度、队列、资源、license 覆盖 submit；资源和 license 列表为替换 |
| regression list | 外层项目/输出根是默认值；行内显式值优先，行间互不继承 |

caselist 中同名逻辑 case 仅在该 submit 下的有效运行参数一致时去重；否则规划阶段报错。
清单只改运行参数，不改变编译身份；源码、TB、define 和覆盖率编译参数会改变编译身份。
`--plusargs` 不能改 NSIM 管理的 seed/result。`--sim-arg` 的同名选项覆盖清单与 submit，
但不会覆盖同名 CLI plusarg。原生 VCS 目前只支持 `--dry-run`；command 包装器的实际
覆盖率/波形取决于它是否声明并实现对应 recipe。

## 本轮目录与创建时机

```text
<output-root>/
├── regr_dir/<regression-id>/
│   ├── report/                         # plan、状态、最终报告与完成校验
│   └── <submit.name>/<workspace>/
│       ├── com_dir/                    # 本轮 submit 的编译
│       └── sim_dir/<case>_<seed>/       # 已派发的 case_dir
└── regr_batch_dir/<batch-id>/report/   # 多轮回归的额外总览
```

每次 `regr -l` 和 `-rl` 的每一行都新建自己的 `regr_dir/<id>`，不复用独立单跑
或其他回归的可写 `com_dir`。启动时认领回归根与报告位置；case 目录在取得 `-j` 名额、
准备好编译后逐个创建，未派发的 case 不会有空目录。`-dir/-com-dir/-sim-dir` 必须留在
本轮所属 submit 目录内。批量总览引用各行回归，不把各行 case 合成一轮。
返回的 `record_dir` 对回归指向 `report/`。目录和 ID 格式见 [单次与批量回归](regression_batches.md)。

## 回归结束：report、inspect、release

先用 `nsim report REGR_DIR` 校验完成记录与结果。正常完成的 local/LSF 回归无需逐个
`inspect/release`。有状态未确认、锁占用或记录异常时，对**具体一轮回归**运行：

```sh
nsim lsf inspect /data/output/regr_dir/本轮ID
nsim lsf inspect /data/output/regr_dir/本轮ID --check-lsf --json
nsim lsf release /data/output/regr_dir/本轮ID --token 完整TOKEN
nsim lsf inspect /data/output/regr_dir/本轮ID --check-lsf
```

`inspect` 默认只读本地占用记录；`--check-lsf` 才查询集群，仍不改保存状态。
它可接收单轮回归根或 `report/`、具体 `case_dir` 或 `com_dir`；不递归扫描整个
`regr_batch_dir`。`release` 每次只处理一个 token，重新核对作业身份、当前终态和锁；
作业仍活动、身份不明或记录损坏时拒绝释放。释放仅移除该 token 的遗留保护，
不补 `final.json`、不改变原 PASS/FAIL，也不启动新任务。若整轮没有可信终态，
解除占用后仍须新建回归。详见 [回归收尾流程](post_regression_workflow.md)及
[LSF 占用规则](lsf_execution.md#检查与安全释放)。

## rerun 与 recover：来源不同，处理不同

| 来源与命令 | 选择与输入 | 编译和输出 |
| --- | --- | --- |
| `rerun REGR_DIR` | 只选原轮 `FAIL/NO_COMPARE` 的精确 submit/case/seed；读取当前工程、submit 和清单输入 | 每个选中 submit 在新目录编译一次，再仿真检查 |
| `recover REGR_DIR` | 只选未形成完整结果的任务；校验并使用历史输入和运行时 | 在新目录重新编译、仿真、检查；不续跑 checkpoint |
| `rerun/recover BATCH_DIR` | 按保存的子回归成员逐行应用上述规则，不重新解析批次清单 | 各子轮有独立新目录和新批次总览 |
| `rerun/recover CASE_DIR` | 从回归中明确挑一个具体 case 时，两命令都按单 case 当前输入处理，不按原判定筛选 | 默认校验并复制来源编译；`--rebuild` 才编译 |
| `rerun/recover COM_DIR` | 明确指向编译目录 | 直接重新编译，不仿真 |

整轮 `rerun` 保留来源 case/seed/submit 选择及有效开关、原 CLI 阶段参数；本次显式
`--coverage/--waves/--compile-arg/--sim-arg/--define/--cm-*/--plusargs` 可覆盖，
`--jobs` 可改并发，`-dir` 可命名新目录。不能改 seed、输出根或任务集合。
整轮 `recover` 保留历史命令，拒绝这些阶段/plusarg 覆盖；可用 `-o` 换输出根，
`-seed` 指定一个新 seed，`--jobs` 改并发，`-dir` 命名目录。两者都要求来源
有可校验的终态；状态未知的 LSF job 和损坏控制记录不能靠重跑绕过。

新 `rerun_<时间_UUID>/` 或 `recover_<时间_UUID>/` 位于来源回归下；跨输出根
recover 在新根的对应原回归 ID 下建立新目录。原结果和报告保持原样；新结果
不会把原回归自动改为 PASS。批次重跑另有自己的汇总。若需按已有证据生成新的展示报告，
使用 `report --rebuild`，它不提交作业。完整失败判定、目录与再次重跑规则见
[按目录重跑](rerun.md)和[回归收尾流程](post_regression_workflow.md)。
