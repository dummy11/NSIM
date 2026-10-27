# Local Execution Contract v1

本文件描述当前本地执行接口；真实 EDA/farm 集成尚未完成。

## Project

工程根目录恰好包含 `nsim.toml` 或 `nsim.yaml` 之一，两个同时存在时报错。TOML 示例：

```toml
schema_version = 1
name = "my-project"
requires_nsim = ">=0.1,<0.2"
inputs = ["wrapper.py", "build/simv", "config.json"]
```

`inputs` 是明确的文件列表，路径相对于描述文件。命令包装器使用的所有可变输入（脚本、二进制、数据、配置）必须列出。当前没有自动展开 filelist/include 图；未声明的输入或外部服务不在复现保证内。工程自身 git commit/tag、dirty 状态作为额外溯源信息保存，文件内容 hash 是当前重跑校验依据。

纯文本 caselist 每行一个标识，忽略空行和 `#` 注释、按出现顺序去重。标识允许字母、数字、下划线、点和连字符，必须以字母或数字开头，长度不超过 128。拒绝路径、空列表或一行多个名字。

## Submit

```json
{
  "schema_version": 1,
  "name": "default",
  "adapter": "command",
  "argv": ["/path/to/python", "{project}/wrapper.py", "{case}", "{seed}", "{result}"],
  "env": {"MY_TOOL_SETTING": "value"},
  "plusargs": ["+dump=0"],
  "required_checks": ["functional", "golden", "performance"],
  "timeout_seconds": 60,
  "settle_seconds": 0.5
}
```

未知字段、重复 JSON/YAML key、错误类型、非有限数、空必需检查集合均被拒绝。检查策略只有明确列出的必需检查影响总 verdict；可选检查失败会被保留但不影响通过。

`argv[0]` 必须是确定的可执行文件；带路径的相对可执行文件以 submit 所在目录解析，纯命令名在规划时解析为绝对路径。其余参数只支持 `{case}`、`{seed}`、`{result}`、`{project}`、`{wave_dir}` 占位符。不调用 shell，不展开 shell 变量或命令替换。

子进程使用规划时保存的 PATH、LANG、LC_ALL、TZ，以及 submit 的显式 env；其余用户环境不自动继承。env 会明文进入计划，不应填入秘密。NSIM 版本、Python 身份、运行代码 hash 和解析库版本也进入计划。

`command` 目前消费工程预先构建的产物，不自动执行原生 VCS 编译；task 的 `build_id` 表示 submit 与输入的内容身份，并不是虚构的编译成功证据。编译能力与依赖管理仍需在真实适配器阶段实现。

`fake` 使用相同调度与检查流程，并真实启动独立 Python 子进程。可设置 `fake.outcomes` 和 `fake.durations`（按 case 名映射），结果模式为 pass、fail、no_compare、missing、malformed、crash、performance_fail。具体示例在 `examples/projects/fake`。

## Result

包装器必须同步结束所有子工作，并把最终 JSON 写到本 attempt 独有的 `{result}` 路径：

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
4. 必需检查失败为 FAIL；全部有效通过才为 PASS。
5. 只有策略为单独 `["liveness"]` 且该检查实际通过，才返回 PASS_LIVE。

零退出后的缺失或不完整 JSON 可在 settle_seconds 时间内重读，超预算返回 INCOMPLETE。输出 I/O 错误是 INFRA_ERROR；进程非零退出但存储预检/复检正常时归为 CRASH，不通过猜测 stderr 内容把 DUT 错误自动当作存储故障。

## Controller and Recovery

控制目录必须预先存在，且与输出、源码树不嵌套。运行时按并发上限滚动补位，子进程有独立工作目录及进程组。超时/取消会终止该进程组；包装器不能依赖脱离进程组的后台服务作为本 attempt 的未完成工作。

控制器是实时/最终报告的单写者。每个 attempt 在控制事件目录独立持久化，输出盘写入失败时仍尽可能保留错误。控制目录写入失败会使控制器停止继续派发、终止尚在执行的本地进程，并且不创建 final.json。异常中断记录不能自动重跑，以免与状态未知的旧进程重叠。

自动重试仅针对 INFRA_ERROR，使用下一个未被隔离的配置根目录，受次数和总重试预算限制。本次运行不会自动解除已隔离的根；之后显式 recover 会重新检查指定的存储。线程负责等待与协调，仿真本身由独立子进程执行。

`rerun` / `recover` 读取校验通过的终态控制记录，生成新的 regression、attempt 和 parent 引用。每次 attempt 启动前和完成后校验输入 hash；变化时为 INPUT_CHANGED，不宣布功能通过。已丢失的日志、波形等产物不会被恢复成原文件，只能重新运行生成新证据。

最终报告发布失败也保留在控制记录中并使整体未通过；后续 recover 在健康位置重新生成整批相关证据。完全丢失控制记录、依赖输入、或旧任务没有确定终态时停止自动恢复。

退出码：0 为已通过/成功查询/成功 dry-run；1 为回归未通过；2 为配置、存储或完整性错误以及报告发布失败；130 为用户中断。没有可选择失败任务的重跑会返回空任务列表，并保留源批次的通过状态。

## Review

Status: Pending

2026-09-20：依据用户在合并实现问题后回复「继续」创建，待实现整体 review。接口名称与字段尚未声明为长期稳定 API。
