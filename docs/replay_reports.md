# rerun、recover 与回归汇总报告

## 职责

`rerun` 处理已完整结束、具有有效结果但检查未通过的任务（当前为 `FAIL`、
`NO_COMPARE`）。它保留原 case/submit/seed 选择，读取当前输入并重新编译。
`recover` 处理没有形成完整有效结果的任务，例如 `INCOMPLETE`、`CRASH`、
`TIMEOUT`、`COMPILE_FAIL`、`HOOK_FAIL` 和基础设施错误；它校验历史输入与运行时，
从头重新执行。`INPUT_CHANGED` 不作精确恢复，需发起新运行。
两者都不自动调用 `report --rebuild`。

当前两种操作仍要求来源有可校验的终态控制记录。没有终态、旧 LSF 作业状态未明、
控制记录损坏或依赖输入不可验证时，不能盲目启动新副本。单独损坏的 HTML 不构成
重跑 case 的理由，可手动重建报告。

## 目录与命名

```text
<output-root>/regr_dir/<原回归ID>/
├── report/                         原报告
├── rerun_时间_UUID/                一次 rerun，内含独立 report/
├── recover_时间_UUID/              一次 recover，内含独立 report/
└── report_时间_UUID/               一次手动汇总，内含 report.html、summary.json
```

`nsim rerun SOURCE [-dir NAME]`、`nsim recover SOURCE [-dir NAME]` 在来源回归下
创建独立执行目录。`-dir/--dir` 只指定这次新目录的单个名称，原计划的工作区名沿用。
未指定时生成 `rerun_时间_UUID` 或 `recover_时间_UUID`。每次执行分别生成
自己的 case 产物、控制记录和报告，只统计本次选中的任务。

无论是否指定 `-dir`，新目录都通过原子创建检查重名。指定名称重名时立即报错，
绝不覆盖或改名；自动名称重名时生成另一个名称再尝试。已有文件或符号链接也算
重名，`-dir` 不接受路径分隔符、空名称、`.` 或 `..`。
`recover -o ROOT` 可改用其他输出根，在新根的 `regr_dir/<原回归ID>/` 下创建目录；
跨根的结果须用 `report --rebuild --include` 指定。单 case 来源则在原 submit
工作区下创建独立目录。

## 手动汇总

```sh
nsim report /data/output/regr_dir/<原回归ID> --rebuild
nsim report /data/output/regr_dir/<原回归ID> --rebuild -dir review_1
nsim report /data/output/regr_dir/<原回归ID> --rebuild \
  --include /backup/regr_dir/<原回归ID>/recover_20261030T150000_<UUID>
```

默认从原回归同级目录中，每类只选**完成时间最新的一次 rerun 和一次 recover**，
加上原回归生成新的汇总目录 `report_时间_UUID/`。较早的执行目录不自动合并。
以可信完成记录中的 `ended_at` 判断最新；若时间缺失、并列、来源无法确认或
目录记录不完整，则报错，要求用 `--include` 指明相应执行目录。不凭目录名称排序
猜测结果。`--include` 每种类型最多指定一个目录；指定的类型采用该目录，
其余类型仍自动选择最新。指定路径必须与原回归的来源 ID 匹配。

汇总按原任务 ID 对应 case/submit/seed。任务同时出现在所选 rerun 和 recover 时，
采用完成时间较晚的可校验记录；均未出现则沿用原结果。新 seed 的结果不能取代
原 seed。最终状态以选中的记录重新校验；最新结果失败或证据不完整时，不悄悄
退回更早的 PASS。`summary.json` 保存原回归、所选目录和每个任务的来源。

`-dir/--dir` 可指定新汇总目录名；重名时报错退出，不覆盖旧汇总。未指定时
自动生成唯一名称。先完成来源选择与校验，再创建目录。原回归和各次执行报告
都保留。`--rebuild` 不重新执行 case，也不修改其终态记录。

## 与旧记录的兼容范围

旧版位于 `rerun/<ID>/` 的结果仍可用 `report` 阅读；需要纳入新汇总时可
用 `--include` 显式指定。旧记录没有可靠的来源根 ID 或完成时间时，汇总会拒绝
猜测。批次目录 `regr_batch_dir` 的 `report --rebuild` 仍按批次原有规则重建
展示，不把其他回归自动加入批次。
