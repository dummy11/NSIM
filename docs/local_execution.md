# nsim 工程接入与结果协议

本文件描述工程包装器、输入和结果协议；local 与 LSF worker 共用这些执行约束。LSF 已支持实际客户端调用，真实 EDA/farm 验收尚未完成。

## Project

编译与用例目录自动提供 `env -> 验证根`；nsim 启动的所有任务通过操作系统限制只读访问源环境。
输出写到工作目录，不能在 `env` 下生成文件。后端要求及保护边界见 [环境只读保护](read_only_environment.md)。

新项目使用 verification 根目录下的 `.nsim/nsim.toml` 和 `.nsim/.nsim_version`，通过 `nsim bootstrap` 登记；查询用 `nsim list/status`，更新用 `nsim update`。命令与路径规则见 [使用指南](../user_guide.md)。schema 2 的相对路径均相对 verification 根目录：

```toml
schema_version = 2
name = "my-project"
rtl_root = "../rtl"
rtl_filelist = "../rtl/rtl.f"
verification_filelist = "filelist/tb.f"
inputs = ["wrapper.py", "build/simv", "config.json"]
output_roots = ["/data/nsim-output"]  # optional project default
```

工程名必须与 verification 目录名一致。`inputs` 是明确的文件列表。command 包装器使用的可变输入（脚本、二进制、数据、配置）必须列出；command 不自动解析 HDL 依赖。VCS 规划独立解析两套登记的 filelist，并为列出的文件、include/library 目录中的现有 HDL 文件记录哈希及目录成员。未声明的输入、编译器隐式输入和外部服务不在复现保证内。

schema 2 可选 `output_roots` 非空路径数组，配置中的相对路径以 verification 根为基准。bootstrap/update 的同名 CLI 设置按调用目录解析后保存。新 run 在锁保护下将默认设置随项目配置固化到计划，按 CLI > 项目配置 > 兼容环境变量选择实际目录，并保存到 `policy`。设置目录不自动创建输出根；运行仍执行原有可用性与目录隔离检查。rerun/recover 使用原计划的 `policy`，不读取更新后的默认目录。

根目录的 schema 1 `nsim.toml/nsim.yaml` 继续可读，其路径仍相对描述文件；新旧布局同时存在报冲突。schema 1 的描述文件保留为重跑哈希依赖。schema 2 的描述文件保存为不可变计划快照，修改注册信息不会改绑历史计划；真实执行输入仍按原哈希校验。两套源码的 Git 身份分别作为溯源信息保存，Git commit 不替代文件内容校验。

regr 的 -l 可接收多个 caselist，-sub 接收多个 submit，-seed 接收多个 seed；-repeat N 改为生成 N 个不同随机 seed，与显式 -seed 互斥。任务按 submit/case/seed 去重，并记录所有清单来源和实际 seed。重跑读取已保存的 seed，不重新抽样。完整参数及目录覆盖见 [执行阶段](execution_stages.md)。

纯文本 caselist 每行一个标识，忽略空行和 `#` 注释、按出现顺序去重。标识允许字母、数字、下划线、点和连字符，必须以字母或数字开头，长度不超过 128。拒绝路径、空列表或一行多个名字。

默认调度器为 LSF；本页示例显式选择 local。真实远程提交、最大并发和队列配置见 [LSF 执行](lsf_execution.md)。

## Submit

```json
{
  "schema_version": 1,
  "name": "default",
  "adapter": "command",
  "scheduler": {"kind": "local"},
  "argv": ["/path/to/python", "{project}/wrapper.py", "{case}", "{seed}", "{result}"],
  "env": {"MY_TOOL_SETTING": "value"},
  "plusargs": ["+dump=0"],
  "required_checks": ["functional", "golden", "performance"],
  "timeout_seconds": 60,
  "settle_seconds": 0.5
}
```

submit 可独立定义，也可用 `extends` 继承一个公共 JSON；合并后再补默认值和校验。对象递归合并、数组及每个 hook 阶段整体替换，父子文件同时参与计划快照与重跑校验。完整规则和各适配器字段见 [Submit 语法](submit.md)。

未知字段、重复 JSON/YAML key、错误类型、非有限数、空必需检查集合均被拒绝。检查策略只有明确列出的必需检查影响总 verdict；可选检查失败会被保留但不影响通过。

`argv[0]` 必须是确定的可执行文件，或已在 compile.artifacts 声明的 `{build_dir}/<产物>`；带路径的相对可执行文件以声明它的 JSON 所在目录解析，继承父模板时保持父文件的目录基准。纯命令名在规划时解析为绝对路径。其余参数只支持 `{case}`、`{seed}`、`{result}`、`{project}`、`{wave_dir}`、`{build_dir}` 占位符。不调用 shell，不展开 shell 变量或命令替换。

子进程使用规划时保存的 PATH、LANG、LC_ALL、TZ，以及 submit 的显式 env；其余用户环境不自动继承。env 会明文进入计划，不应填入秘密。nsim 版本、Python 身份、运行代码 hash 和解析库版本也进入计划。

`command` 可以通过 `compile.argv`、`compile.artifacts`、`compile.timeout_seconds` 执行真实编译，完整示例见 [编译、仿真与工作区](execution_stages.md)。没有 compile 对象时登记显式预构建输入，标记 kind=prebuilt、compiled=false。fake 准备测试产物，标记 kind=fake、compiled=false；两者不宣称执行了 EDA 编译器。com 只准备编译，sim 只消费成功且兼容的编译记录，run 编译成功后才仿真。

编译身份包含 submit 名、模式、编译配置、显式环境及声明输入和工具哈希；不包含 case、seed、caselist 和工作区。com/run/regr 重建所选编译目录，sim 在清理旧仿真输出前验证编译记录和产物。共享/独占编译锁防止仿真期间重建。

`fake` 使用相同调度与检查流程，并真实启动独立 Python 子进程。可设置 `fake.outcomes` 和 `fake.durations`（按 case 名映射），结果模式为 pass、fail、no_compare、missing、malformed、crash、performance_fail。具体示例在 `examples/projects/fake`。

## Hooks 和日志分析

四阶段 hooks 与 VCS/UVM 日志分析同时适用于 local/LSF、单跑/回归。每阶段支持一个或多个有序 hook；post_sim 的 log_analysis 可以由用户覆盖，但必须放在最后。日志诊断、扫描不完整、hook 失败都参与最终判定。日志读取前等待写入稳定，发生变化时按预算重试；详见 [接口与协议](hooks_and_log_analysis.md)。

## Result

包装器必须同步结束所有子工作，并把最终 JSON 写到本次执行的 `{result}` 路径。该文件位于固定的 case/seed 目录；新 attempt 会覆盖旧产物，不创建独立 attempt 子目录：

```json
{
  "schema_version": 1,
  "case": "smoke",
  "seed": 42,
  "complete": true,
  "checks": {
    "functional": {"executed": true, "passed": true},
    "golden": {"executed": true, "passed": true, "comparisons": 16},
    "performance": {"executed": true, "passed": true, "message": "all required metrics satisfied"}
  }
}
```

每个检查要求布尔 executed/passed；可选 comparisons 为非负整数，message 为诊断信息。必需的 golden 检查还要求 comparisons > 0。推荐包装器先写临时文件再原子替换结果文件。

判定优先级：

1. 取消/超时/非零退出不能被成功记录覆盖。
2. JSON 缺失、截断、身份不匹配、版本错误或缺必需检查为 INCOMPLETE。
3. 必需检查未执行、Golden 比较数为零为 NO_COMPARE。
4. 必需检查失败或日志 ERROR/FATAL 为 FAIL；日志分析完整、无错误且全部必需检查有效通过才为 PASS。
5. hook 失败为 HOOK_FAIL，日志分析不完整不能通过；日志规则不会覆盖超时、崩溃或取消。
6. 只有策略为单独 `["liveness"]` 且该检查实际通过，才返回 PASS_LIVE。

零退出后的缺失或不完整 JSON 可在 settle_seconds 时间内重读，超预算返回 INCOMPLETE。输出 I/O 错误是 INFRA_ERROR；进程非零退出但存储预检/复检正常时归为 CRASH，不通过猜测 stderr 内容把 DUT 错误自动当作存储故障。

## Controller and Recovery

输出根必须预先存在，且与源码和工具树不嵌套。运行记录自动放在 output 内：单跑位于 submit 的 sim_dir，回归汇总位于 regr_dir/<id>/report/，编译与仿真也位于该回归目录内。运行时按并发上限滚动补位，子进程以所选工作区的仿真目录（默认 submit/workspace/sim_dir/case_seed）为工作目录，并拥有独立进程组。单跑固定目录串行覆盖；回归每次新建目录，历史日志与波形保留；互斥锁禁止并行覆盖。超时/取消会终止该进程组；包装器不能依赖脱离进程组的后台服务作为本 attempt 的未完成工作。

控制器是批次实时/最终报告的单写者；单跑将计划、执行记录、分析、终态与完成校验合入 run.json，不生成 live 或 HTML 汇总。每个 attempt 保存在所属 case 的 run.json 中，仿真不再生成 records/ 副本。记录写入失败会停止继续派发、终止尚在执行的本地进程，并且不发布终态完成记录。记录与产物共享存储，不保证输出盘整体故障后仍有恢复依据。异常中断记录不能自动重跑，以免与状态未知的旧进程重叠。

自动重试仅针对 INFRA_ERROR，使用下一个未被隔离的配置根目录，受次数和总重试预算限制。本次运行不会自动解除已隔离的根；之后显式 recover 会重新检查指定的存储。线程负责等待与协调，仿真本身由独立子进程执行。

`rerun` / `recover` 读取校验通过的终态控制记录，生成新的 regression、attempt 和 parent 引用。每次 attempt 启动前和完成后校验输入 hash；变化时为 INPUT_CHANGED，不宣布功能通过。已丢失的日志、波形等产物通过重新运行生成新证据。相同 case/seed 直接覆盖旧目录；批次的历史记录保留，单跑只保留最新记录。dry-run 不写入编译完成记录，也不清理已有编译或仿真结果。

最终报告发布失败时删除部分发布的人类可读报告，在仍可写的机器记录中保留错误并使整体未通过；后续 recover 在健康位置重新生成整批相关证据。完全丢失控制记录、依赖输入、或旧任务没有确定终态时停止自动恢复。

退出码：0 为已通过/成功查询/成功 dry-run；1 为编译或仿真未通过；2 为配置、存储或完整性错误以及报告发布失败；130 为用户中断。未确认终态的 LSF 作业返回 2，保留占用标记并禁止自动重派。没有可选择失败任务的重跑会返回空任务列表，并保留源批次的通过状态。
