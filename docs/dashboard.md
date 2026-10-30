# nsim 终端 Dashboard

运行回归的 nsim 主进程就是控制器。它负责调度、收集结果，并在收到 case 完成或 LSF 状态变化事件时直接更新终端 Dashboard。不需要额外服务、端口、浏览器、环境变量或 LSF job。

```sh
nsim regr -l smoke.list -sub rtl -seed 42 -jobs 8
```

交互式终端会原地显示类似以下内容：

```text
NSIM 回归 <batch-id> | 运行中 | 尚未最终确认
完成 5/20 | PASS 4 | 未通过 1 | 运行 8 | 待执行 7 | 未确认 0
LSF: DONE=5  PEND=2  RUN=6
最近 Case: case / submit / seed / 判定 / Error/Fatal / 位置与原因
smoke  rtl  seed=42  FAIL  E/F=1/0  tb/checker.sv:27  data mismatch
...
```

`regr` 即使只选一个 case，也提供 Dashboard；`run` 使用 caselist 或多个 submit/seed 时同样提供。真正的单跑只保存 case 分析和执行记录，不显示回归面板。`--dry-run` 不创建 Dashboard。

## 更新流程

1. 仿真进程结束，执行 submit 声明的 `post_sim` hooks，最后完成内置或用户替代的日志分析。
2. 尝试保存该 case 的 `run.json`；若写入失败，该 case 记为错误，不能通过。
3. 完成回调唤醒 nsim 主进程；控制器先把该 case 结果持久保存到回归 `state.json`，再增量更新计数和补交任务。若整批状态无法保存，停止新派发。
4. 控制器原子保存 `live.json` 摘要，然后直接重绘终端，无需等待整批完成或下一秒的刷新周期。展示文件写失败不取消已提交的 job。

同一次唤醒中已经完成的多个任务可以一起显示。无状态变化时 Dashboard 不重绘、不扫描日志、不轮询文件、不写心跳。移除的是 Dashboard 的定时刷新；LSF 后端仍按调度配置查询 `bjobs`，查询到状态变化后通知控制器。

LSF 的 PEND/RUN/DONE/EXIT 等状态独立显示，不直接代替 case 判定。即使 job 已结束，也必须取得可信的分析和执行结果才能判定 case。终端中的“运行”计数表示已经派发给执行器但尚未取得最终 case 结果的任务，可能包括编译、排队、仿真或 post_sim；具体 LSF 排队状态查看 LSF 计数和最近作业。

## 交互终端与重定向

- 支持 ANSI 的交互终端原地重绘有界面板，最多保留最近 8 个 case 和 4 个作业；终端较小时按可见行列截断，最近更新优先显示。完整原因、分析和历史结果仍在输出文件中。
- 面板输出到 **stderr**；stdout 继续输出原有的最终 JSON，便于脚本解析。
- stderr 重定向、管道或 `TERM=dumb` 时，按事件追加普通文本，不发送光标控制字符。
- 诊断中的换行、转义及其他控制字符会清理，避免日志内容改变终端显示。
- 终端关闭或写入失败会停止显示，不改变仿真判定；输出记录的存储失败仍按原有错误处理规则停止派发。

例如分别保存最终 JSON 和过程记录：

```sh
nsim regr -l smoke.list -sub rtl -seed 42 -jobs 8 \
  > run-result.json 2> progress.log
```

## 完成与中断

| 阶段 | 含义 |
| --- | --- |
| 运行中 | 等待 case 完成或调度状态变化 |
| 正在发布最终报告 | case 已结束，正在发布最终记录，尚不显示整批 PASS/FAIL |
| 运行结束 | `final.json` 已发布，显示整批 PASS/FAIL |
| 控制器已中断 | 已处理 Ctrl-C，并停止派发 |
| 控制器发生错误 | 控制器捕获执行或存储异常 |
| 存在状态未确认的作业 | 无法确认 LSF 终态，未发布最终完成记录 |

中断、错误和未知作业状态不会显示整批 PASS。终端在命令结束后保留最后一次显示，不启动后台刷新进程。若控制器被强制杀死或输出盘不可写，可能来不及显示停止状态；最后显示和 `live.json` 都只是上一次保存的快照，不能据此认为 LSF job 已停止或整批已完成。

## 输出文件与 HTML 报告

```text
output/regr_dir/<regression-id>/report/
├── live.json       # schema 3：事件触发保存的小型摘要
├── dashboard.html  # 有界进度快照，展示最近用例/作业，可进入最终报告
├── state.json      # 运行中逐 case 更新，结束时保存完整状态
├── report.md       # 最终完整文本报告
├── report.html     # 自包含的最终 HTML，支持分页
└── final.json      # 最终完成记录与校验
```

新运行不再生成 `live.html`、`live-meta.js`、`live-data.js` 或 `live-cases/live-jobs` 分页文件。旧回归记录保持原样。

`live.json` schema 3 包含 `run_id`、`revision`、`lifecycle`、`published_at`、`terminal`、`passed`、`totals`、`log_summary`、`recent_cases`、`recent_jobs`、`job_counts`、`job_count` 和 `message`。它不复制全量任务或证据，不再带分页索引，也没有定时心跳。运行中完整 attempt 通常在各 case 的 `run.json.task.attempts`；若某个 case 的记录写失败，错误和该 attempt 保存在回归 `state.json`。

最终 `report.html` 每页最多渲染 100 个 case 或作业，可翻页、跳转页码和查看分析/证据链接。HTML 不建立连接、不读取实时文件、不设置刷新定时器。用 `nsim report <record_dir>` 校验完成记录、页面与证据；完整时打印最终 HTML 的本地路径与浏览器地址，不完整时只给出简短原因。原回归的 `--rebuild` 从原结果及最新一次 rerun、recover 建立新的 `report_<时间_UUID>/` 汇总目录，不覆盖由 `final.json` 校验的原报告，也不运行 case。批次的旧版展示重建规则保持不变。

控制器按变更任务维护计数，共享编译按路径去重；每次事件只更新相关条目和有界摘要。任务索引、最终状态及完整报告仍随任务数增长，不能据此推断无限规模支持。

## 验证范围

测试覆盖真实伪终端中快 case 完成时慢 case 仍运行、stdout JSON 与 stderr 面板分离、重定向无 ANSI、终端尺寸与控制字符处理、终端断开、post_sim 未结束不提前结算、写入失败不提前显示、异常中断、LSF 未知状态，以及 10,000 case 的增量统计。HTML 分页通过执行实际 JavaScript 验证，并禁止定时器、网络或动态脚本加载。LSF 使用本地进程级测试服务；这些结果不代替真实 farm 或商业 EDA 工具验收。

批量入口 `regr -rl` 保留每次回归的独立面板，同时维护批次总览；详见 [批量回归](regression_batches.md)。

每次回归额外保存 `report/dashboard.html`，展示同一控制器快照的总计数、最近用例与作业。
页面不轮询，不复制全量任务；运行时刷新浏览器读取最新保存状态。批次总览可链接到各次回归的
Dashboard；结束后通过页面进入包含全部任务的独立 report。
