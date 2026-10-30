# 修复后的失败 case 重跑

`nsim rerun SOURCE` 从已结束且控制记录校验通过的来源中选取完整执行但结果失败的任务，固定
**case、seed 和 submit 选择**，读取当前输入，强制重新编译后完整执行仿真与检查。
不需要人工判断修改属于编译、仿真还是结果检查阶段。

```sh
# 修改 RTL、testbench、数据或配置后，重跑该回归的失败任务
nsim rerun /data/output/regr_dir/<原回归ID> --jobs 4

# 也可传 report 目录，或只选择某个失败 case 的目录
nsim rr /data/output/regr_dir/<原回归ID>/report
nsim rr /data/output/regr_dir/<原回归ID>/<submit>/rtl/sim_dir/<case>_<seed>
```

批量回归需先选择其中的具体回归，不直接接受 regr_batch_dir 下的批次总目录。
没有匹配任务时不创建目录、不编译。报告展示文件损坏但任务结果完整时，使用
`report --rebuild` 手动重建。

## 输入与编译

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

recover 保持历史输入恢复语义，校验原输入与运行时，选择未形成完整结果的失败任务。
它与 rerun 一样在新目录强制编译、仿真和检查；来源为 sim 时也重新编译。
没有 compile 步骤的 command submit 会报错，纯 com 来源没有仿真 case，也会报错。
recover 仍支持输出根和 seed 覆盖。两者都要求来源具有可信终态，不绕过未知 LSF
作业或损坏控制记录的检查。完整的目录与手动报告汇总规则见 [重跑、恢复与报告](replay_reports.md)。
