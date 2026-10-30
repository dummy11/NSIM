# nsim 编译、仿真与工作区

执行入口为 `nsim com`、`nsim sim`、`nsim run`、`nsim regr`。`com` 只编译或登记预构建输入；`sim` 只使用已有且校验通过的编译产物；`run` 先编译，成功后仿真并检查结果。`run/sim --case` 可列出多个显式 case，`-sub` 可列出多个 submit；每个 case/submit/seed 组合都是独立单跑。它们不接受 `-l`。读取 caselist、建立回归目录和汇总使用 `regr -l`。`com` 不接收 case、caselist 或 seed。旧的两级执行入口已删除。

## 参数和目录

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `-mode` | 从有效 submit 推导 | 执行模式：`rtl`、`gate`、`fpga`、`haps`、`zebu`、`palladium`；显式指定时必须匹配 submit 的 `mode` |
| `-dir` | 当前 mode 的名称 | 工作区名称，只接受一个安全目录名；不改变执行模式 |
| `-com-dir` | `com_dir` | 编译目录路径；相对路径以当前工作区为基准，也接受绝对路径 |
| `-sim-dir` | `sim_dir` | 仿真父目录；相对工作区解析，也接受绝对路径，下面仍按 case_seed 分目录 |
| `-o` / `--output-root` | 项目登记的唯一输出根 | 本次选定一个输出根，不修改项目设置；项目登记多个根时必须显式选择 |
| `--case` / `-c` | run/sim 必填 | 一个或多个显式 case；每个组合独立单跑 |
| `-l` / `--caselist` | regr 必填，或用 -rl | 一个或多个 caselist 文件；仅 regr 接受 |
| `-sub` / `--submit` | 必填 | 一个或多个 submit 名称或文件路径 |
| `-seed` / `--seed` | 随机生成一个 seed | 一个或多个正整数 seed；run/sim 将每个组合独立执行，regr 将它们纳入回归 |
| `-scheduler` / `--scheduler` | submit 配置，未指定为 `lsf` | 选择 `lsf` 或 `local` |
| `-jobs` / `--jobs` / `-j` | `1` | 本次调用最多 N 个未结束作业，完成后补位 |
| `-queue` / `--queue` / `-q` | submit 配置或集群默认 | LSF 队列 |
| `-repeat` | 不指定时生成一个 seed | 仅 regr：生成 N 个不同随机 seed，与显式 -seed 互斥 |

submit 及其父模板均未声明 `mode` 时为 `rtl`。运行时省略 `-mode`，从合并后的 submit 推导模式；后仿真 submit 的 `mode=gate`，不接受 `post`。模式是配置选择与隔离接口；名称本身不启用 FPGA/HAPS/ZeBu/Palladium 原生执行后端。显式指定 `-mode` 时检查与有效配置一致；命令行不会自动切换父模板，一次调用不接受不同 mode 的 submit 混跑。完整字段见 [Submit 语法](submit.md)。默认使用 LSF，也可切换 local；`command` 支持实际编译/仿真，`fake` 用于测试。原生 VCS 仍只支持命令预览。调度配置和运行要求见 [LSF 执行](lsf_execution.md)。

单跑工作区为 `W = output_root / submit.name / (-dir 或 -mode)`；回归工作区为
`W = output_root/regr_dir/<regression-id>/submit.name/(-dir 或 -mode)`。
编译与仿真分别位于 `W/-com-dir` 和 `W/-sim-dir/<case>_<seed>`。回归路径必须保留在
本次回归所属 submit 目录内，不能指向单跑或其他回归。
回归汇总文件统一位于 `output_root/regr_dir/<regression-id>/report/`。

`regr -rl/--regr-list` 按行执行多个完整回归，每行复用单次参数；批次汇总位于
`output_root/regr_batch_dir/<batch-id>/report/`。完整目录和规则见 [单次与批量回归](regression_batches.md)。

## 仿真环境根目录链接

nsim 在编译目录和每个 case/seed 仿真目录中自动创建 `env -> <验证工程根目录>`。
链接整个仿真环境顶层，不枚举其内部目录，不需要配置资源列表、环境变量或 hook。
任意工程文件都可通过 `env/<工程内路径>` 访问，新增目录也会直接可见。
schema 2 的目标是包含 `.nsim/` 的 verification 根，schema 1 的目标是解析后的 `source_root`，
均来自本次计划保存的项目根；从工程外启动或修改输出目录不会改变目标。

链接在可选的 pre hook 和主命令之前就绪；local 与 LSF 使用相同规则，LSF 执行节点会再次校验。
`com` 准备编译目录的链接，`sim` 校验复用编译的链接并准备用例链接，`run/regr` 准备两者。
`--dry-run` 只在 `commands.json` 的 `environment_links` 中列出计划建立的链接，不创建实际链接。

`env` 是工作目录中的保留入口，不能声明为编译产物。同名普通文件、目录、失效链接或指向其他目标的
链接会报错，nsim 不覆盖它们；重新执行时清理输出只删除链接本身，不遍历或删除验证工程。
nsim 启动的编译、仿真、全部 hooks 及其子进程必须只读访问环境。执行器通过操作系统隔离阻止
创建、覆盖、追加、删除、重命名和修改权限；直接使用源码绝对路径同样受保护。验证根、RTL 根及其
内部符号链接指向的外部资源一并保护。运行产物应写到编译/用例目录，需要修改的资源先复制到工作目录。
Linux 执行节点需要可用的 bubblewrap（bwrap）及 namespace 支持，macOS 使用 sandbox-exec；
无法建立保护时直接失败，不允许降级为可写执行，详见 [环境只读保护](read_only_environment.md)。

只读限制属于 nsim 启动的进程，不修改源工程权限，也不会把普通终端中的符号链接变成只读挂载。
重跑输入校验依据 filelist、`project.inputs` 和 submit 的 `files` 声明，不会自动把整个工程纳入输入哈希。
源码清单的相对路径解析规则保持不变。

## 常用命令

以下示例假设已登记 `verification`，通过 `bootstrap` 或 `update --output-root` 保存输出根，且 submit 路径从当前目录可访问。`--project verification` 可从工程外调用；在项目目录或其子目录中可省略 --project。裸 submit 名称从项目的 submit/<名称>.json 解析；显式路径按当前调用目录解析；当前目录已有同名文件时优先使用该文件。

```sh
# 一次完成编译、仿真及结果检查
nsim run --project verification --submit /work/verification/submit/rtl.json \
  --case smoke --seed 42

# 分开执行；sim 不会隐式补编译
nsim com --project verification --submit /work/verification/submit/rtl.json
nsim sim --project verification --submit /work/verification/submit/rtl.json \
  --case smoke --seed 42

# 自定义工作区，run 的编译和仿真同时进入 issue_1234
nsim run --project verification --submit /work/verification/submit/rtl.json \
  -dir issue_1234 --case smoke --seed 42

# 从 issue_1234 复用 rtl 工作区的编译，仿真输出仍位于 issue_1234
nsim sim --project verification --submit /work/verification/submit/rtl.json \
  -dir issue_1234 -com-dir ../rtl/com_dir --case smoke --seed 42

# 自定义编译目录名
nsim com --project verification --submit /work/verification/submit/rtl.json -com-dir compiled
nsim sim --project verification --submit /work/verification/submit/rtl.json \
  -com-dir compiled --case smoke --seed 42

# 回归；合并两个列表，使用两个 submit 和两个 seed
nsim -o /scratch/output regr --project verification -dir nightly \
  -l /work/verification/smoke.list /work/verification/stress.list \
  -sub rtl rtl_trace -seed 42 43 --jobs 4 \
  -com-dir build -sim-dir results

# 随机回归；生成 10 个不同 seed，实际值保存到计划和报告
nsim regr --project verification -l /work/verification/smoke.list \
  -sub rtl -repeat 10 --jobs 4

# gate submit 必须声明 "mode": "gate"；原生 VCS 后仿真当前仅预览
nsim run --project verification --submit /work/verification/submit/gate.json \
  -mode gate --case smoke --seed 42 --dry-run
```

`-com-dir` 的语义在四个入口一致：`com` 写入，`sim` 读取，`run/regr` 写入后读取。无需更改 output-root 即可分开编译来源与仿真输出。显式绝对编译路径也可位于输出根之外，其父目录必须预先存在。-sim-dir 使用相同的路径解析规则；例如 -sim-dir results 输出到 W/results/case_seed。拒绝与源码、工具、仿真、批次记录或另一个编译目录互相包含，也拒绝符号链接目录。

## 回归展开、随机 seed 与去重

`regr` 执行批量编译、仿真和汇总。同一 submit 的编译在同一输出根只执行一次，所有 case/seed 共用编译产物。只有一个 case、submit、seed 时，regr 仍生成批次报告。

`-l a.list b.list` 按清单顺序合并 case；`-sub submit_0 submit_1` 选择多个 submit；`-seed 42 43` 选择多个 seed。三者展开为 case × submit × seed，唯一任务键为 submit + case + seed。重复清单、清单内重复 case、重复 submit 路径和重复 seed 不会导致重复派发；同一 case 来自哪些清单记录在 tasks[].caselists，所有清单内容与哈希也进入计划。相同名称但不同配置文件的 submit 会报冲突，避免误合并配置。

case 指逻辑用例名，不要求等于 UVM test、seq 或 C test 名。清单支持 `@defaults`、
`uvm_test/seq/c_test` 字段、直接 plusargs 与可选 `@config` 自定义映射/用例覆盖。
同名 case 只有在每个 submit 下有效参数一致时才去重，否则报出冲突来源。
submit 的参数可被清单默认值及当前行覆盖，显式 YAML 用例覆盖优先于用例行，命令行 --plusargs 的优先级最高；
清单及 YAML 原文、哈希、解析定义与最终运行参数都保存到计划。
bootstrap 生成 8 份清单示例，完整规则见 [Caselist 语法](caselist.md)。

`-repeat N` 表示生成 N 个不同随机 seed，并将这一组 seed 应用于所有 case/submit，不表示重复覆盖同一个 seed。它必须是正整数，不能和 -seed 同时使用；随机数在规划时生成一次，保存在 seed_selection.values 和各任务中。例如 3 个 case × 2 个 submit × repeat 5 = 30 个仿真任务。任务可按 --jobs 并发，不会因 repeat 写入同一个 case_seed 目录。rerun 固定失败任务记录的 seed，不重新抽样，也不接受 seed 覆盖；整轮 recover 默认复用历史 seed，仍可显式覆盖；单跑 recover 与 rerun 一样不接受 seed 或输出根覆盖。

`-o` 支持放在子命令前或后，例如 `nsim -o /scratch/out regr ...` 和 `nsim regr -o /scratch/out ...`。两处同时指定时，子命令后的值覆盖前面的值；本次执行只选择一个输出根，不会自动切换备用根。它只影响本次运行，持久修改项目默认位置使用 `nsim update --output-root`。固定的绝对 -com-dir/-sim-dir 不会随 -o 改变。

## command 编译契约

submit 可增加 `compile` 对象：

```json
{
  "schema_version": 1,
  "name": "rtl",
  "mode": "rtl",
  "adapter": "command",
  "compile": {
    "argv": ["/usr/bin/make", "-f", "{project}/Makefile.sim", "OUTPUT={build_dir}"],
    "artifacts": ["simv", "simv.daidir"],
    "timeout_seconds": 600
  },
  "argv": ["{build_dir}/simv", "+UVM_TESTNAME={case}", "+ntb_random_seed={seed}", "+NSIM_RESULT={result}"],
  "required_checks": ["functional", "golden"],
  "timeout_seconds": 60
}
```

工程需将 Makefile、源文件、包装器和依赖文件显式列入项目 `inputs`。上述 Makefile 必须遵守 `OUTPUT` 输出约定，仿真程序或包装器必须按 [结果协议](local_execution.md#result) 输出结构化检查结果。nsim 会在仿真后的 log_analysis hook 中解析标准 VCS/UVM 诊断；日志没有错误仍不能替代 Golden 或功能检查。顺序、用户覆盖与日志稳定性规则见 [Hooks 与日志分析](hooks_and_log_analysis.md)。

`compile.argv` 仅接受 `{project}` 和 `{build_dir}`，不依赖 case/seed；`artifacts` 是至少一个相对编译目录的文件或非空目录，禁止逃逸路径和符号链接。编译器零退出、声明产物完整、输入身份未变才发布 `ready: true`。非零退出、超时或缺产物会阻止仿真。

无 `compile` 的 command submit 登记显式输入为 `kind: prebuilt`；fake 准备测试产物，标记 `kind: fake`。两者均为 `compiled: false`，不宣称执行了 EDA 编译器。`com` 的通过表示编译阶段/输入准备通过，不表示 DUT 验证通过。

## 覆盖、锁与重跑

`com`、`run` 在固定工作区编译；一次 `run --case` 展开出多个组合时，每个 submit 只编译一次，各 case/seed 独立保存在固定目录。`regr` 在新的独立回归目录编译，保留历史结果。`sim --case` 校验已有成功编译并直接复用；多个显式组合仍各自保存单跑记录。case、seed、caselist、`-dir` 和仿真 plusargs 不参与编译身份；源码和编译选项变化要求重新编译。

编译写入持有独占锁，仿真消费编译产物持有共享锁。多个 `sim` 可以复用同一编译目录，但不能在其运行期间重新编译。同一 case/seed 输出目录持有独占锁；不同 case/seed 或工作区可并发。local 锁由子进程继承；新 LSF 仿真使用 `run.json.lsf` 保护自己的目录，编译目录仍保留对应的 `.lsf-readers/<token>.json`。编译 job 和旧记录继续使用原有占用标记。控制器意外退出时仍阻止覆盖活动产物，可通过 `nsim lsf inspect/release` 检查和安全释放，见 [LSF 执行](lsf_execution.md#检查与安全释放)。

独立回归在启动时校验全部路径并建立回归根、report 和共享编译目录；case 目录在取得 `--jobs` 名额、通过编译准备后逐个创建并加锁。未派发的 case 不创建空目录，单 case 创建失败不停止其他 case。`run/sim --case` 的多个组合不建回归根或汇总，每个组合分别持有自己的单跑记录；同一 submit 内逐个执行，不同 submit 最多并行 `--jobs` 个。

相同工作区、case、seed 串行覆盖旧日志、结果和波形。单次仿真仅保留最新计划、终态及证据；单个 com 的阶段记录在编译目录中。批量运行保留 `regr_dir` 的历史计划与汇总。重新编译或覆盖仿真产物后，引用旧产物的历史通过证据会变为 `INCOMPLETE`。

### 多项目、多版本与多用户

输出路径不会自动增加用户、项目或 nsim 版本层级。共享存储时，应为每个用户、项目检出和 nsim 版本分配独立输出根，例如 `/scratch/$USER/chip_a/rev_a/nsim_0.1.0`；也可以使用不同的 `-dir`。显式绝对 `-com-dir` / `-sim-dir` 不随输出根或 `-dir` 改变，必须单独隔离。不同 RTL/验证版本建议使用不同检出目录，避免运行期间修改同一份输入。

编译和仿真目录的 `.nsim-owner.json` 持久记录用户 UID、项目真实根路径、nsim 版本、wheel 身份和产物类型。每次运行在清理产物前校验归属并取得编译及仿真锁。其他用户（即使目录允许组写）、项目、nsim 版本或产物类型不能接管目录；跨调用相互嵌套的产物目录也被拒绝。同一用户、项目及版本可以按上述规则覆盖自己的结果或共享编译读取锁。编译身份额外包含项目路径与实际 nsim 源文件内容，防止同版本不同代码误用缓存。

没有归属记录的非空旧目录会被拒绝自动接管，应选择新输出根或工作区后重新编译。已有历史报告仍可读取。此保护需要运行版本支持该协议；旧版 nsim 不认识归属记录，因此与旧版并行时必须使用互不包含的独立输出目录。共享文件系统还必须支持各客户端之间一致的 `flock` 与原子替换；本地测试不证明真实 NFS/LSF 集群满足这些条件。

默认工具安装及项目注册表分别在各用户的 `~/.local/share/nsim` 与 `~/.config/nsim` 下。`NSIM_HOME`、`XDG_CONFIG_HOME` 可以覆盖这些位置，不应无意中指向其他用户的可写目录。同一项目检出的 `.nsim/.nsim_version` 是共享设置，`nsim switch` 会影响该检出后续调用的版本选择；若不同用户需要独立切换，应使用各自检出。已生成计划保留原运行时和输入；report 和整轮 recover 选择历史版本，rerun 与单跑 recover 选择来源工程当前版本 pin（无 pin 时使用历史版本）。

项目配置更新期间还会写入项目内的事务标记。若更新进程异常退出，使用其他个人注册表的用户也会被阻止读取或修改半完成配置；原更新用户需使用原注册表重新执行 bootstrap/update 完成恢复。不要手动删除活动事务标记。

同一注册表中的项目名必须唯一，当前 bootstrap 使用验证根目录名作为注册名。例如两个都以 `verification` 命名的检出不能同时按该名称注册，应使用 `chip_a_verification`、`chip_b_verification` 等不同的验证根目录名。

稳定启动器使用隔离的 Python 启动并清除继承的 Python 搜索路径配置。内置本地 worker 和 LSF worker 绑定控制器的代码路径，避免当前目录或其他项目的 `PYTHONPATH` 替换 nsim。用户配置的编译器、仿真器和 hook 仍使用 submit 声明的环境。

单 case 的 `rerun CASE_DIR` 与 `recover CASE_DIR` 完全等价，默认校验复用编译，显式 `--rebuild` 才重新编译；`rerun COM_DIR` 与 `recover COM_DIR` 都只重新编译。case_dir 是 sim_dir 下的具体 case 目录，sim_dir 父目录不作此入口。原产物保留，新结果使用独立目录；两者都读取当前输入；下文区分 rerun/recover 的规则适用于整轮回归，批量回归也支持按记录逐行重跑。详见 [按目录重跑](rerun.md)。

`report`、`rerun`、`recover` 使用返回的 `record_dir`，不需要 event-root。`rerun` 固定完整执行但结果失败的 case/seed，重新读取当前工程登记、源码、submit、caselist/YAML 与输入文件，不要求它们匹配历史 hash。每次在来源回归下建立独立 `rerun_<时间_UUID>/report/`、编译和仿真目录；每个 submit 强制编译一次，再运行对应失败 case，包括原先由 sim 产生的任务。原产物与报告保留，详见 [重跑、恢复与报告](replay_reports.md)。`recover` 校验历史输入，在新的独立目录强制编译、仿真和检查；来源为 sim 时也不复用原编译产物。com_dir 来源只重新编译，不执行仿真。

`--dry-run` 只写独立预览记录，不写编译成功记录，不清理编译或仿真产物。单跑预览放在 `preview_dir/<id>/report/`，回归预览放在 `regr_dir/<id>/report/`。com 预览仅含编译；sim 预览仅含仿真；run/regr 包含两阶段。预览成功不代表所引用编译产物存在或真实 EDA/farm 作业可运行。
