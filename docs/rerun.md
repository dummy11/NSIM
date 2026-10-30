# 按目录重跑与重新编译

`rerun/recover` 对单跑目录完全等价，都使用当前输入；根据来源目录决定执行范围。`sim_dir` 是多个 `case_dir` 的父目录，不是单 case 入口。

| 来源 | 默认行为 | `--rebuild` / `-rebuild` |
| --- | --- | --- |
| `com_dir` | 读取当前编译输入，直接重新编译，只执行编译阶段 | 可省略 |
| `sim_dir/<case>_<seed>`，即 `case_dir` | 固定 submit/case/seed，校验并复制原编译产物，只仿真和检查 | 先重新编译，再仿真和检查 |
| 单 case rerun 产生的根目录或 `report/` | 继续按单 case 处理，默认复用这次来源的编译 | 先重新编译，再仿真和检查 |
| 单次/批量回归根目录或 `report/` | rerun 选 FAIL/NO_COMPARE；recover 选不完整任务。均重新编译 | 保持相同行为 |

```sh
# com_dir：只重新编译，不执行其下游 case
nsim rerun /data/output/base/rtl/com_dir

# case_dir：默认不重新编译，即使来源 case 已通过也可以重跑
nsim rerun /data/output/base/rtl/sim_dir/smoke_42

# 修改 testbench、RTL 或编译参数后，显式重新编译再跑该 case
nsim rerun /data/output/base/rtl/sim_dir/smoke_42 --rebuild
```

单 case 可使用 `rerun` 或 `recover`，两者不再按 PASS、功能失败、超时、崩溃或证据不完整筛选。
它读取当前工程和 submit，沿用原 case/seed 与原 CLI plusargs。编译产物缺失、损坏、
被替换，或与当前编译输入不兼容时，默认报错并提示 `--rebuild`，绝不自动转为编译。
只有运行阶段输入改变且编译身份仍兼容时，可以不重新编译。
只有预构建产物的 command submit 可以校验复用，但不能使用 `--rebuild`。

复用会在共享读锁下校验原 `build.json` 及产物，再复制到本次独立 `com_dir`，不使用
硬链接，不运行编译命令或编译 hooks。原 `com_dir`、原 case 结果和报告保留；显式
重新编译也写入本次新目录。再次重跑仍生成同级新目录，不接管已存在的目录。

`com_dir` 可以来自 com、run、回归或重跑快照。新 `build.json` 保存 `record_dir`，
并由完成记录校验其归属；旧记录优先使用自身编译计划或所属回归，标准单跑布局也可
定位唯一匹配的兄弟 case。不能明确归属时拒绝猜测，应使用对应 case_dir 加 `--rebuild`。
纯编译重跑有独立报告，但不会替代回归汇总中的仿真结果，也不能作为 `report --include` 的来源。

所有入口仍要求可校验的终态；未知 LSF 作业、损坏的控制记录不能通过改目录类型绕过。
单跑 recover 使用与 rerun 相同的当前输入、当前版本选择及编译策略，生成相同的 rerun_ 前缀目录；两者都不接受 seed 或输出根覆盖。历史输入恢复语义仅用于整轮回归。

## 整轮回归的失败任务重跑

`nsim rerun REGRESSION_DIR` 从已结束且控制记录校验通过的来源中选取完整执行但结果失败的任务，固定
**case、seed 和 submit 选择**，读取当前输入，强制重新编译后完整执行仿真与检查。
不需要人工判断修改属于编译、仿真还是结果检查阶段。

```sh
# 修改 RTL、testbench、数据或配置后，重跑该回归的失败任务
nsim rerun /data/output/regr_dir/<原回归ID> --jobs 4

# 也可传 report 目录，或只选择某个失败 case 的目录
nsim rr /data/output/regr_dir/<原回归ID>/report
nsim rr /data/output/regr_dir/<原回归ID>/<submit>/rtl/sim_dir/<case>_<seed> --rebuild
```

批量回归也可直接传 regr_batch_dir 下的批次总目录或其 report/，见下文。
没有匹配任务时不创建目录、不编译。报告展示文件损坏但任务结果完整时，使用
`report --rebuild` 手动重建。

## 整轮回归的输入与编译

- case、seed 和 submit 名称来自来源任务；仅重跑这些精确组合，不把失败 case 与所有
  seed 重新做笛卡尔积，不重新生成随机 seed。
- 重新读取来源工程当前登记、RTL/TB、原 submit 路径及其父模板、原用例来源清单、
  YAML、显式输入文件和脚本。原命令的 CLI plusargs 保留；文件中的配置使用当前内容。
  当前清单新增 case 不会扩大任务范围；失败 case 被删除、submit 被改名或输入丢失会报错。
- 当前 submit 决定编译/仿真命令、hooks、运行参数与调度配置；默认并发沿用来源，可用
  `--jobs` 修改。稳定启动器使用来源工程当前版本 pin，无 pin 时使用历史版本。
- 每个选中的 submit 在本次独立编译目录中强制编译一次，再执行对应 case 的完整流程。
  即使来源是 sim，也重新编译；编译失败时不运行该 submit 的仿真。
- command submit 必须声明 compile；只有预构建产物时明确报错。fake 仅用于测试，
  会重新生成模拟编译记录，仍标记 compiled=false。原生 VCS 实际执行的既有限制不变。
- 为本次输入建立新的 hash、配置快照与运行时身份，不拿旧 hash 阻止修复后的重跑。
  本次执行前后仍检查输入一致性，运行期间修改输入不能得到可信 PASS。

rerun 不接受 `-seed/--seed/-S`、`-repeat`、`-o/--output-root`（包括全局 -o）、
`--project`、`-sub`、`-l`、`--dry-run` 或队列覆盖。换 seed、换任务选择或换输出根应发起新运行。
`--retries` 和 `--retry-seconds` 仅保留兼容入口，必须为 0。

## 目录与来源关系

```text
regr_dir/<原回归ID>/
├── report/                         # 原报告保持原样
├── <submit>/rtl/                   # 原编译、日志、结果、波形保持原样
├── rerun_<时间_UUID>/
│   ├── report/                     # 独立计划、状态、报告与完成校验
│   └── <submit>/rtl/
│       ├── com_dir/
│       └── sim_dir/<case>_<seed>/
└── recover_<时间_UUID>/report/
```

新目录默认使用操作名、时间戳与 UUID；`-dir` 可指定单个目录名，目录已存在时拒绝接管。重跑保留来源工作区名，
编译和仿真使用新目录内的 com_dir、sim_dir，不复用原绝对路径或自定义产物路径。
返回的 record_dir 指向新 report；report 与单 case 的 run.json 均能读取新结果。

对一次 rerun 的失败结果再次 rerun 时，在同一原回归下创建新的同级目录，
parent 指向直接来源任务。原回归及其批次报告不会因为重跑通过而改成 PASS。
单跑来源没有回归根时，在原 submit 工作区的 rerun_<时间_UUID>/ 下生成同样的独立回归结构，
不覆盖来源 case 目录。删除原回归或单跑工作区会连同其中的 rerun 结果一起删除。

## 与 recover 的区别

整轮回归的 recover 保持历史输入恢复语义，校验原输入与运行时，选择未形成完整结果的失败任务。
它与整轮回归的 rerun 一样在新目录强制编译、仿真和检查；来源为 sim 时也重新编译。
整轮回归重编译时，没有 compile 步骤的 command submit 会报错。
recover 仍支持输出根和 seed 覆盖。两者都要求来源具有可信终态，不绕过未知 LSF
作业或损坏控制记录的检查。完整的目录与手动报告汇总规则见 [重跑、恢复与报告](replay_reports.md)。

## 批量回归重跑

```sh
nsim rerun /data/output/regr_batch_dir/BATCH_ID --jobs 4
nsim recover /data/output/regr_batch_dir/BATCH_ID/report
nsim recover /data/output/regr_batch_dir/BATCH_ID -o /backup/output -dir recover_storage
```

批次入口按保存的子回归成员逐行选择任务。rerun 使用当前输入，只选每行原有的
FAIL/NO_COMPARE；recover 校验历史输入，选择未形成完整结果的任务。两者均在各自
子回归的独立目录重新编译、仿真和检查，不改变原子回归和原批次的结果。
不重新解析 regression list 来添加新行，也不合并不同回归行中的同名 case/seed。
所有选中计划在第一项执行前完成规划和输入校验；按行顺序执行，--jobs 覆盖每行并发。

新批次报告位于原批次根下的 rerun_时间_UUID/report/ 或 recover_时间_UUID/report/。
-dir 只命名本次批次目录，子回归使用各自自动生成的目录名；重名拒绝覆盖。
再次对该新批次重跑时，在同一原批次下生成同级目录，并记录直接来源。
跨输出根 recover 保留相同的相对批次目录结构。

新总览只统计本次实际选择，保存无匹配任务的原行列表；全部无匹配时不创建目录。
普通 case/编译失败仍继续后续行；出现未确认终态作业则停止启动后续行，不发布批次
完成记录。控制记录损坏、预览批次或没有可信终态的批次不能自动续跑。
稳定启动器支持批次来源；整批需使用一致的 NSIM 版本，当前项目 pin 不一致时明确报错。
