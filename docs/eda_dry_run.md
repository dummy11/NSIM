# nsim EDA dry-run 接口

当前提供 EDA/LSF 的配置解析和命令预览。fake/command 已支持实际 LSF 和 local 执行，默认 LSF；原生 VCS 非 dry-run 请求仍会拒绝。预览成功表示输入与命令可规划，不代表编译、DUT 检查或 farm 验收通过。

使用 `nsim com --dry-run`、`nsim sim --dry-run`、`nsim run --dry-run` 分别预览编译、仿真或两阶段。`regr --dry-run` 可预览批量任务。四个入口统一接受 `-mode`、`-dir`、`-com-dir`、`-sim-dir`、`-o`；默认编译路径为 `output/submit_name/rtl/com_dir`，默认仿真路径为 `output/submit_name/rtl/sim_dir/case_seed`。后仿真用 `-mode gate` 并匹配 submit.mode。预览不清理已有产物，也不发布可供 sim 复用的编译成功记录。详见 [执行阶段](execution_stages.md)。

## 参考 npu8860（需 NSIM 仓库）

从 NSIM 根目录运行，`--work` 必须是不存在的、位于 ASIC 源码树外的新目录：

```sh
.venv/bin/python examples/npu8860/dry_run.py \
  --source ../npu8860_ai/acc \
  --work /tmp/nsim-acc-preview \
  --queue nightly
```

脚本创建外置 descriptor、submit、三个 smoke case（seed 42）、输出目录（批次记录位于其 regr_dir）；读取 filelist 及源码，记录 hash。它不执行参考工程的 Makefile，也不调用 VCS、Syscan、LSF 或 Verdi。`nightly` 是本次参考工程的显式选择，不是 nsim 默认队列。

`summary.json` 汇总结果。`output/regr_dir/<id>/commands.json` 保存每个任务的编译 argv/cwd、仿真 argv/cwd、Verdi 预览、LSF 提交参数模板、查询/取消模板、必需检查和缺失工具。预览记录不生成真实 worker 请求，不能直接当作完整 farm 作业提交；实际 fake/command 执行会生成带哈希的请求和 worker 启动脚本。`plan.json` 保存配置快照、输入 hash、源码 git 身份、工具代码/依赖身份。没有 `final.json`，`passed` 保持 false。

与参考流程的对应关系：

| 项目行为 | nsim 预览 |
|---|---|
| `sim.f` 嵌套 `rtl.f` / `tb.f` | 按工程根解析 `-f`，保持源文件顺序并展平为绝对路径 argv |
| 单次 VCS 编译含 UVM 和调试选项 | 保留参考 `VCS_FLAGS`；每 submit 独立 build id |
| 显式 seed / UVM test name | 每任务记录并传递 seed 42 与对应 case |
| testbench `+fsdb` 写入 `acc.fsdb` | 使用隔离运行目录下实际文件名，Verdi 预览指向同一位置 |
| 默认自动 clean / git pull | 不调用此构建入口；预览不执行清理或更新 |
| Syscan SystemC Golden | 单独列为待接入依赖；不从目录存在推断 Golden 可用，也不套用通用 DPI |
| coverage compile/run 参数 | 当前 profile 未启用，后续单独接入 hierarchy 和 coverage 产物 |
| ACC 日志判定 | 当前只预览 liveness 策略；未接入结果解析器，不能形成任何 DUT verdict |

## 通用配置

新注册项目使用 `.nsim/nsim.toml` schema 2 的 `rtl_root`、`rtl_filelist`、`verification_filelist`。两份入口分别以各自源码根解析，嵌套 `-F` 切换到列表所在目录，`-f` 保持当前解析基准。VCS submit 省略 `vcs.filelist`（或写 `filelist/sim.f`），防止绕过登记输入。空清单无法规划。每次新规划重新收集文件及 include/library 目录成员，输入内容或已记录目录成员变化会阻止旧计划精确重跑。

`verification/filelist/sim.f` 是组合来源说明，不直接交给 VCS；编译 token 固化在计划的 `submits[].vcs.filelist_tokens`，编译 argv 固化在 `commands.json`。同名用户文件不会被 bootstrap/update 覆盖。

以下 `source_root` 规则适用于保留的 schema 1 外置描述文件示例。

descriptor 新增可选 `source_root`，相对 descriptor 解析，默认 `.`；`inputs` 仍相对 descriptor 解析。这样无需在参考仓库写入 nsim 配置文件。

submit 的 `adapter: "vcs"` 接受 `vcs` 对象：

- `filelist`：schema 1 必需，相对 `source_root`。支持 `-f`、`-F`、`-v`、`-y`、`-top`、`+incdir+`、`+define+`、`+libext+` 和源文件。其它选项报错，需放入 `compile_args`。`-F` 内的路径相对该嵌套列表所在目录。
- `executable`、`compile_args`、`run_args`、`compile_timeout_seconds`、`top`。
- `golden_enabled`：默认 false；required_checks 要求 golden 时必须显式 true，这个声明本身不证明比较有效。
- `fsdb`、`fsdb_plusargs`、`fsdb_file`。默认波形在 `{wave_dir}/wave.fsdb`；相对文件名以隔离运行目录解析，工程必须支持相应 plusarg。
- `result_argv`：工程检查器调用预览；默认空。真实接入时需生成本地接口定义的结果 JSON。
- `verdi`：默认 `verdi`，仅用于预览。
- `dpi`：独立 Makefile 的 `makefile`、`make`、`cc`、`cxx`、`target`、`args`、`timeout_seconds`、`scope`（project/regression/run）。生成 `NSIM_DPI_OUTPUT`、`NSIM_PROJECT_ROOT`、CC/CXX 的 make 参数预览，dry-run 不创建 cache 或构建 `.so`。

模板字段支持 `{project}`、`{case}`、`{seed}`、`{result}`、`{wave}`、`{wave_dir}`、`{log}`、`{build_dir}`、`{dpi_library}`。禁止属性访问、转换和格式子句。filelist 的 `$VAR` / `${VAR}` 只从 submit 显式 `env` 展开，不从未声明的主机环境猜测。

`{log}` 指向仿真主日志 `run.log`。编译命令生成 `-l vcs.log`，仿真命令生成 `-l sim.log`（均使用各自输出目录的绝对路径），与 NSIM 汇总 stdout/stderr 的 `compile.log` / `run.log` 分开。自定义 VCS 参数不得重复提供 `-l`。原生 VCS 仍仅支持 dry-run；实际 command 包装器也应采用这个日志约定。

其中 `vcs.compile_args` 允许 `{project}`、`{build_dir}`、`{dpi_library}` 和声明了 compile 阶段的 `{file:NAME}`，避免一个共享编译目录被 case/seed 改变编译配置。
submit.files 的路径相对验证工程根，支持普通文件、目录、glob 和显式 dependencies；文件与成员摘要随计划校验。
只有 compile 阶段文件进入编译身份，simulate/analysis 文件不会因内容变化单独触发重编译。
commands.json 同时展示 files、file_references、file_hashes 和展开后的 argv；完整字段见 [Submit 语法](submit.md)。

`scheduler` 接受 kind local/lsf（默认 lsf），queue、resources 字符串数组、licenses 正整数映射，以及 bsub/bjobs/bkill 路径和轮询/取消预算。CLI 的 `--scheduler`、`--queue`、重复 `--resource`、重复 `--license name=count` 覆盖 submit 对应字段；列表整体替换。工具缺失只在 `--dry-run` 时记录为 unresolved_tools。

输入身份包含嵌套 filelist、列出的源码和 include/library 目录中现有的 `.v/.sv/.vh/.svh` 文件。它不是完整预处理依赖分析；schema 1 新增目录文件、工具内置库、编译器隐式输入和外部服务仍需补充身份约束；schema 2 已校验已登记 include/library 目录的 HDL 成员集合。

LSF 参数语义参考 IBM 官方 [工作目录](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=options-cwd) 和 [作业输出字段](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=information-customize-job-output)。执行节点预检、未知 job 禁止重派和取消终态验证已接入实际调用链，并通过本地进程级测试服务验证；真实 farm 验收仍待目标环境完成。配置与限制见 [LSF 执行](lsf_execution.md)。
