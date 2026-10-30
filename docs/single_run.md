# 独立单跑：配置、目录与重跑

本页只讨论 `com`、`run --case`、`sim --case` 以及独立单跑目录的 `rerun/recover`。
用 caselist 派发任务、建立回归报告请使用 [回归流程](regression_workflow.md)。
submit 字段见 [Submit 语法](submit.md)，所有命令拼写见 [CLI 参考](cli_reference.md)。

## 用目录映射选择 submit

新工程 bootstrap 会把所选 submit 目录登记到项目 `.nsim/nsim.toml` 的
`[catalog.submit.roots]`。例如 `local = "flows/vcs"` 时，`-sub rtl` 查找验证工程根下的
`flows/vcs/rtl.json`，`-sub local:rtl` 明确选此目录；同名文件出现在多个配置目录时，
使用命名空间或别名消除歧义。可先执行 `nsim catalog resolve submit rtl` 查看实际路径。

```sh
nsim com -sub rtl
nsim run -sub local:rtl --case smoke -seed 42
nsim sim -sub ./flows/vcs/rtl.json --case smoke -seed 42
```

最后一行的 `./flows/vcs/rtl.json` 是相对命令启动目录的显式路径，绕过映射；
`--case smoke` 是逻辑用例名，不查找 caselist。`submit` 文件位置由 catalog 决定，
编译和 case 产物位置由下文的 `-o/-dir/-com-dir/-sim-dir` 决定。
修改 `[catalog]` 只影响新执行；已有 `case_dir/com_dir` 的 rerun/recover 仍绑定来源文件。
配置、自定义 bootstrap 目录和旧工程迁移见 [TOML 名称与文件映射](catalog.md)。

## 入口与默认行为

| 命令 | 选择 | 编译 | 仿真 | 默认覆盖率 | 默认波形 |
| --- | --- | --- | --- | --- | --- |
| `com -sub SUBMIT` | 不接收 case/seed | 写固定 `com_dir` | 无 | 关 | 无仿真阶段 |
| `run --case CASE -sub SUBMIT` | 一个或多个 case、submit、seed | 每个 submit 在本次调用编译一次 | 每个组合独立执行 | 关 | 开 |
| `sim --case CASE -sub SUBMIT` | 一个或多个 case、submit、seed | 校验并复用已有编译 | 每个组合独立执行 | 关 | 开 |

`run/sim` 不接受 `-l`，也不建立 `regr_dir` 或回归汇总。`--case a b -sub x y -seed 1 2`
展开为八个独立单跑；同一 submit 在一次 `run` 调用中只编译一次。未指定 seed 时生成一个随机
seed 并记录。`sim` 不会补编译；源码、TB 或编译选项改变时先 `com`，或改用 `run`。
相同 submit/case/seed 的再次独立单跑会覆盖原 case 结果，活动目录和编译读写锁会阻止并发接管。

## 常用选项

| 类型 | 选项 | 行为 |
| --- | --- | --- |
| 选择 | `--case CASE...`、`-sub SUBMIT...`、`-seed N...` | `run/sim` 的显式组合；`com` 只选 submit |
| 位置 | `-o ROOT`、`-mode MODE`、`-dir NAME`、`-com-dir PATH`、`-sim-dir PATH` | 选择输出根、模式、工作区及编译/仿真路径 |
| 调度 | `-scheduler local/lsf`、`-q QUEUE`、`--resource EXPR`、`--license R=N`、`-j N` | 本次覆盖 submit 调度设置；`-j` 默认 1 |
| 覆盖率、波形 | `--coverage on/off`、`--waves on/off` | 显式覆盖上表默认值；`com` 无 `--waves` |
| 编译 | `--compile-arg=VALUE`、`--define NAME[=VALUE]`、`--cm-metrics`、`--cm-hier FILE` | `com/run` 可用；`sim` 不能改变已有编译 |
| 仿真 | `--sim-arg=VALUE`、`--plusargs +KEY=VALUE...`、`--cm-dir PATH` | `run/sim` 可用；`com` 没有仿真参数 |
| 预览 | `--dry-run` | 保存计划和命令，不创建有效编译或仿真产物 |

参数值以 `-` 开头时，用 `--compile-arg=-flag` 或 `--sim-arg=-flag` 的等号写法。
`--cm-*` 只用于 VCS 覆盖率参数且要求本次有效覆盖率为开；`--cm-hier` 文件作为编译输入校验。
`--retries` 和 `--retry-seconds` 仅兼容旧调用，必须为 0。完整默认值与别名见 [CLI 参考](cli_reference.md)。

## submit 与命令行的优先级

1. submit 先按父模板到子 submit 合并；对象递归合并，数组由子配置整体替换。VCS 的
   `vcs.coverage.compile_args/run_args` 只提供覆盖率参数，`vcs.fsdb_plusargs` 只提供波形参数。
   command 包装器可用顶层 `coverage.compile_args/run_args` 和 `waveform.run_args` 定义自己的参数。
2. 覆盖率和波形先取单跑默认值；显式 `--coverage/--waves` 覆盖。旧 submit 的 `vcs.fsdb`
   布尔值仍作为兼容默认值，显式 `--waves` 优先。新 submit 不需要这两个开关字段。
3. `--cm-metrics/--cm-hier/--cm-dir` 替换 submit 对应的覆盖率参数。
   `--compile-arg` 和 `--define` 追加编译参数；其他通用编译选项的同名解释由工具决定，
   应检查命令预览。
4. 运行参数按 submit → `--sim-arg` → `--plusargs` 合并，同名选项按键覆盖。
   `--plusargs` 优先级最高；NSIM 管理的 seed 和结果路径不能覆盖。单跑使用 `--case`，
   不加载 caselist 的 `@defaults`、行覆盖或 case.yaml。
5. 调度器、队列、资源和 license 的 CLI 显式值覆盖 submit；资源/许可证覆盖是替换。
   输出根按子命令后的 `-o` → 子命令前的 `-o` → 项目默认 → `NSIM_OUTPUT_ROOT` 选择。

VCS 在覆盖率打开时分别加入编译插桩和运行采集参数；`sim --coverage on` 要求已有编译包含插桩。
在已插桩编译上使用 `sim --coverage off` 只关闭本次采集。波形开关只决定是否传入波形运行参数；
TB 或包装器仍需实际生成波形。command 未声明参数组时，开关不会自动产生覆盖率或波形。
原生 VCS 当前仅支持 `--dry-run`；真实运行可使用已配置的 command 包装器。

## 目录何时创建

```text
<output-root>/<submit.name>/<workspace>/
├── com_dir/                    # com/run 写入；sim 校验读取
└── sim_dir/
    ├── smoke_42/               # 一个独立 case_dir
    └── fast_43/                # 另一个独立 case_dir
```

`workspace` 默认为 submit 的 mode；`-dir` 改其名称。`-com-dir/-sim-dir` 可改对应路径，
相对路径从工作区解析；`sim_dir` 只是父目录，下面可有多个 `case_dir`。
`com` 创建或更新编译目录，不创建 case；`run` 编译并为已派发组合创建 case；
`sim` 校验已有编译后创建 case。单跑预览写入独立 `preview_dir/<id>/report/`，
不会清理原编译和 case 产物。路径、归属和锁的细节见 [执行阶段](execution_stages.md)。

## 对独立单跑目录使用 rerun/recover

| 来源 | `rerun` 与 `recover` 的共同默认行为 | 显式改变 |
| --- | --- | --- |
| `case_dir`：`sim_dir/<case>_<seed>` | 固定原 submit/case/seed，读取当前输入；校验并复制原编译快照，再仿真和检查 | `--rebuild` 先编译；运行参数可直接覆盖 |
| `com_dir` | 读取当前编译输入，直接重新编译，不执行下游 case | 可覆盖编译参数；`--rebuild` 可省略 |

传 `sim_dir` 父目录不代表单个 case。两种命令对独立单跑的选择、输入和编译策略相同，
不按原结果的 PASS/FAIL 筛选；单跑 `recover` 也不能改 seed 或输出根。默认复用时，
编译产物缺失、损坏，或当前 TB/RTL、define、覆盖率编译参数与来源不匹配，会在创建新输出前
报错并提示 `--rebuild`，不会自动编译。只改 `--waves`、`--sim-arg`、`--plusargs`
等运行参数时可复用。要从未插桩编译切换到覆盖率采集，使用 `--coverage on --rebuild`。

重跑默认继承来源的有效覆盖率、波形和阶段参数；本次显式选项优先。
新增的同名 `--plusargs` 替换来源 CLI plusarg。`com_dir` 来源只接收编译相关覆盖。
每次重跑在来源工作区新建 `rerun_<时间_UUID>/report/` 及其独立 `com_dir`、必要的
`case_dir`，保留原产物；复用采用校验后复制，不使用硬链接。无编译步骤的预构建 command
submit 可以复用，但不能 `--rebuild`。目录入口与更多例子见 [按目录重跑](rerun.md)。

```sh
nsim run -sub rtl --case smoke fast -seed 42 -o /data/output
nsim com -sub rtl --coverage on -o /data/output
nsim sim -sub rtl --case smoke -seed 42 --coverage on -o /data/output
nsim rerun /data/output/rtl/rtl/sim_dir/smoke_42 --waves off
nsim rerun /data/output/rtl/rtl/sim_dir/smoke_42 --coverage on --rebuild
nsim recover /data/output/rtl/rtl/com_dir
```

以上路径中的第二个 `rtl` 是工作区名，须按实际 `submit.name` 和 `-dir` 替换。
