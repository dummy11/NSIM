# 单次与批量回归

`nsim regr -l ... -sub ...` 执行一次回归，保留原有参数。新增 `-rl` / `--regr-list`
批量组织多次回归。每行复用单次 regr 的参数解析、默认值和校验，第一版按行顺序执行。

```sh
nsim regr -sub rtl rtl_trace -l caselist/smoke.list caselist/basic.list -repeat 10 -jobs 8
nsim regr -rl regression/nightly.list -o /data/output
nsim regr -rl regression/nightly.list -o /data/output --dry-run
```

## 清单格式与参数

```text
# 每个有效行等价于一次 nsim regr，省略命令前缀
-sub rtl rtl_trace -l caselist/smoke.list caselist/basic.list -repeat 1 -queue short -jobs 8
-sub rtl -l caselist/conv.list caselist/dma.list -repeat 50 -queue normal -jobs 20
-sub rtl_trace -l caselist/error.list -seed 42 43 -queue normal -jobs 4 --plusargs +ERROR_INJECT=1
```

- `-l` 仍表示一个或多个 caselist，`-rl` 表示批量回归清单，两者互斥。
- 行内支持所有单次 regr 执行选项，包括多个 submit/caselist、repeat/seed、mode、目录、local/LSF、队列、资源和并发。
- 空行与 `#` 注释忽略，参数值支持引号。一个物理行对应一次回归；不执行 shell、变量替换、管道或命令替换，不支持嵌套 `-rl`。
- 相对路径与把这一行复制到终端执行时一致：相对启动命令的工作目录，而非 regression list 所在目录。裸 submit 名仍按工程 submit 目录查找。
- 各行参数互不继承。外层仅接受 `-rl`、`--project`、`-o`、`--dry-run`。项目和输出根作为默认值，行内显式值优先；外层 dry-run 强制所有行预览。
- 行内 `-jobs` 是该次回归未结束作业上限，包含排队/运行/暂停和真实编译作业，不是 CPU 核数；没有批次并发选项。
- 每行展开为 submit × 合并去重后的 case × seed。行内保持原有冲突检查，跨行不组合、不去重。完全相同的两行也是两次执行。
- repeat 在规划时为每行生成一组不同 seed，应用于该行所有 submit/case；与显式 seed 互斥。保存实际 seed，重跑不重新抽样。

新增 `--plusargs`（也支持 CLI 别名 `-pa`）用于 run/sim/regr 的最终运行参数覆盖：

```sh
nsim regr -sub rtl -l caselist/smoke.list -seed 42 --plusargs +TRACE=1 '+LABEL=night run'
```

优先级为 submit → caselist defaults → 用例行 → case YAML → CLI plusargs；按 `+KEY` 覆盖。
同层同键不同值报错，NSIM 管理的 seed/result 参数禁止覆盖。值支持 `{case}`，其余花括号按既有
caselist 转义规则处理。运行覆盖不改变编译身份。每行的 queue/resource/scheduler 沿用单次 regr
语义，覆盖本次编译和仿真的调度配置。本功能不新增 license 资源要求。

## 独立目录与 report 子目录

```text
output/
  submit_A/rtl/                         # 单跑
    com_dir/
    sim_dir/smoke_42/
  regr_dir/<regression-id>/             # 每次回归，包括 list 的每一行
    report/
      plan.json
      state.json
      live.json
      dashboard.html
      report.md
      report.html
      failed.json
      final.json
    submit_A/rtl/
      com_dir/
      sim_dir/smoke_42/
    submit_B/rtl/
      com_dir/
      sim_dir/smoke_42/
  regr_batch_dir/<batch-id>/            # 批次组织与汇总
    report/
      plan.json
      state.json
      live.json
      dashboard.html
      report.md
      report.html
      final.json
```

每次执行使用新的 UUID；相同配置、相同 case/seed、不同 repeat/plusargs 都不会覆盖历史回归。
单次回归的 regression ID 使用 `<用例清单名>_<创建时间>_<完整UUID>`，例如
`-l caselist/smoke.list` 对应：

```text
smoke_20261029T153000_8c4654ad324e4cc689f967769fc054e6
```

多个 caselist 按输入顺序去掉最后一个扩展名，以连字符连接，例如 `-l smoke.list basic.list`
使用 `smoke-basic` 前缀；解析后同一路径只计一次。批量回归内的每一行也使用该行自己的
caselist 名称。未使用 caselist、由多个执行组合形成的回归使用 `regr` 前缀。
完整清单路径保存在 `report/plan.json` 的 `caselists` 中。

批量回归的 batch ID 使用 `<回归清单名>_<创建时间>_<完整UUID>`，例如：

```text
nightly_20261029T153000_8c4654ad324e4cc689f967769fc054e6
```

这里 `20261029T153000` 表示创建计划时所在机器的本地时间：2026 年 10 月 29 日 15:30:00，
不包含时区；`nightly` 来自 `-rl nightly.list` 的文件名去掉最后一个扩展名。
清单名保留 ASCII 字母、数字、点、下划线和连字符，其他连续字符替换为连字符，去掉首尾的
点、下划线和连字符后最多取 48 个字符（多个 caselist 先连接，再截短），整个 ID 最多 97 个字符。
空名称在批次中使用 `batch`、单次回归中使用 `regr`；原始清单路径仍完整保存在计划中。
末尾保留完整的 32 位 UUID v4，同一清单在同一秒内启动也会生成不同 ID。
目录、报告和关联记录使用各自完整的 ID；历史纯 UUID 回归和批次仍可读取。
rerun 在来源回归的 `rerun/<新回归ID>/` 下独立执行；recover 在输出根的 regr_dir 下创建新回归。两者记录来源 ID，保留原回归记录。
目标回归目录已存在时拒绝采用，不隐式恢复。历史目录不参与本次统计。

回归的 `-dir`、`-com-dir`、`-sim-dir` 在该回归自己的 submit 工作区内解析；拒绝逃出所属 submit
目录或与编译/仿真目录重叠。单跑仍保留固定工作区与原有目录选项。`report` 为回归根下保留名称。
同一次 run/regr 回归内同一 submit/输出根只编译一次；不同回归分别编译，不共用顶层单跑或其他回归的可写产物。
`com` 保留原有顶层编译工作区。`sim -l` 或多组合 sim 在共享读锁下校验已有编译，
复制编译产物与证据到本次回归目录后仿真，不重新编译、不使用硬链接；后续顶层重编译不会覆盖这个快照。
此类回归的 recover 继续从原回归的已验证编译快照复制；rerun 使用当前输入重新编译，不复用快照。

`record_dir` 返回 report 子目录。`report`、`rerun`、`recover` 均可接受回归根或 report 子目录；
也可从 case 仿真目录通过 run.json 找到对应记录。回归失败重跑在来源回归下新建 rerun/<ID>/ 与 report，固定原始
case、seed、submit 选择，读取当前配置并强制编译，不改写原回归及批次结论。批次重跑需先从批次报告选择具体回归。
现有 recover 仍针对已发布终态的存储/证据失败，不支持自动接续被中断的批次或状态未知的 LSF 作业。
同一次回归使用一个明确选定的输出根；case 执行错误只记录该 case 的实际问题，不触发自动切根。历史上使用多个输出根的记录仍可读取；recover 可为恢复回归选择其他根；rerun 固定在来源回归所在根。

## 控制器、Dashboard 与统计

控制器以 plan 登记的任务为统计范围，按完成回调与 LSF 状态变化更新；不扫描历史目录。
每次回归有独立的终端 Dashboard、dashboard.html、live.json、report.html。批次清单记录各行对应的回归 ID，
批次总览只汇总这些 ID；同名任务跨回归分别计数。case 出错时后续任务各自执行，不自动进行存储重试。
批次或子回归的实时展示文件写入失败会在 stderr 报告，不取消已提交的任务；`state.json` 与最终完成记录仍决定是否有可信的整批结果。

批次运行在 stderr 显示当前行及总进度，子回归保留原有终端面板。完整批次 state.json
在回归行边界及结束/异常时保存；子任务事件只更新有界 live.json 和展示当前行的 dashboard.html。
HTML 可进入当前回归的 Dashboard、报告或状态文件，行边界与最终报告包含完整回归列表。
运行时刷新页面读取最新保存快照，无额外服务、网络轮询、定时心跳或 LSF job。最终报告为自包含快照。

未启动任务计入计划总数和待执行数。普通 case/编译失败记录后继续下一行；无法确认 LSF 终态时停止
启动后续回归，显示 unresolved/not_started，不发布批次 final.json。Ctrl-C 与控制器错误保存停止状态，
不伪造完成记录；强制杀死可能留下旧快照，不能凭旧 live.json 判断当前调度器状态。

所有行在首次提交前完成参数和配置规划；执行前继续校验输入身份。清单原文、哈希、行号、解析 argv、
工作目录、各子计划及资源设置均保存。recover 要求历史输入仍可验证；rerun 重新读取当前输入，仅选择历史失败任务，不增加清单中新加入的 case。失败 case 从当前清单移除时明确报错。

## 报告、退出码与预览

```sh
nsim report /data/output/regr_dir/RUN_ID
nsim report /data/output/regr_batch_dir/BATCH_ID --json
nsim report /data/output/regr_batch_dir/BATCH_ID --rebuild
nsim rerun /data/output/regr_dir/RUN_ID
```

批次报告校验自身完成清单以及关联子回归的身份、完成记录和结果证据。完整时返回批次 `report.html` 的打开路径；历史日志被损坏时重新查询会提示证据不完整，不把旧 HTML 中的 PASS 当作当前有效证明。`--rebuild` 仅从已保存记录生成新的展示页面，不重新执行任何子回归。

批次全通过返回 0；用例或编译失败返回 1；配置、基础设施或报告发布错误返回 2；Ctrl-C 返回 130。
所有行均 dry-run 时返回 0 表示预览成功，但 passed=false。混合实跑与预览不会标为全部仿真通过。

Dry-run 在 report/ 保存计划、commands.json 和 preflight.json，不创建编译/仿真产物，不清理历史目录。
批次预览同样生成各行预览与总览。真实 VCS 与 farm 的既有能力边界不因该接口改变。

各 case 的 run.json 合并身份、attempt 和 analysis，不重复保存公共计划。子回归 plan.json 只在开始写入，state.json 在派发、case 完成和最终发布时保存；批次自身的 state.json 在回归行边界及结束/异常时保存。批次 live.json 的中间快照可带 partial=true，此时 regressions 仅包含当前行，regression_count 和 totals 表示完整批次；完整行列表见批次 state.json 及最终报告。
