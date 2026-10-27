# NSIM

NSIM 是开发 `nsim` 的仓库与开发环境，包含源码、测试、示例工程、设计文档和阶段治理记录。`nsim` 是从这里构建和安装的 Python 命令行工具，面向 ASIC 工程组织仿真回归。当前工具提供可运行的本地流程：工程发现、配置校验、case × submit × seed 展开、滚动并发、结果检查、独立输出、失败重跑和备用存储恢复。

执行使用 fake simulator 或工程提供的命令包装器；新增 VCS、DPI、Verdi、LSF 配置与命令预览，见 [EDA dry-run](docs/eda_dry_run.md)。原生执行适配器仍未完成。合并实现授权和验证记录见 [实现状态](docs/implementation_status.md)。

## 安装

需要 Python 3.9+，本地进程执行目前面向 POSIX（Linux/macOS）。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/nsim --version
.venv/bin/nsim version --json
```

TOML/YAML 与版本约束分别使用 tomli（Python <3.11）、PyYAML 和 packaging。不同 ASIC 工程可使用独立虚拟环境安装不同版本。

版本控制、打包、发布及远端获取见 [使用指南](user_guide.md)；构建细节见 [打包与发布](docs/releasing.md)。

## 跑一次本地回归

以下命令在 NSIM 仓库根目录执行。输出与控制事件目录必须预先存在，并位于工具和 ASIC 源码树之外。根目录缺失时不会自动创建，以免把掉线挂载误当成普通目录。

```sh
mkdir -p /tmp/nsim-output /tmp/nsim-backup /tmp/nsim-events
.venv/bin/nsim project list examples/projects
.venv/bin/nsim run regression \
  --project examples/projects/fake \
  --caselist examples/projects/fake/smoke.caselist \
  --submit examples/projects/fake/pass.json \
  --seed 42 --jobs 2 \
  --output-root /tmp/nsim-output \
  --output-root /tmp/nsim-backup \
  --event-root /tmp/nsim-events
```

命令返回 JSON，其中 `journal` 是本次运行的控制记录目录。每次调用分配独立 regression id，不覆盖上次结果。`--output-root` 可重复配置备用位置，也可通过 `NSIM_OUTPUT_ROOT` 设置单一默认输出位置；事件目录可由 `NSIM_EVENT_ROOT` 设置。

- 增加 `--submit examples/projects/fake/fail.json` 演示未比较和性能失败；这次回归应返回非零状态。
- 重复 `--seed 42 --seed 43` 展开多个 seed。未指定时生成一个有效 seed 并保存在计划中。
- 增加 `--dry-run` 只保存计划、检查存储并创建输出结构，不运行仿真，也不发布最终通过标记。
- 使用 `run case --case smoke` 替代 `run regression --caselist ...` 执行单个 case，其余参数相同。

## 检查报告、重跑和恢复

将下面路径替换为命令实际返回的 `journal`：

```sh
.venv/bin/nsim report show /tmp/nsim-events/<regression-id>
.venv/bin/nsim report show /tmp/nsim-events/<regression-id> --json
.venv/bin/nsim rerun /tmp/nsim-events/<regression-id>
.venv/bin/nsim recover /tmp/nsim-events/<regression-id> --output-root /tmp/nsim-backup
.venv/bin/nsim storage check /tmp/nsim-output --min-free-bytes 104857600
```

`rerun` 仅重跑最终未通过的任务，默认保持原 seed、submit、显式环境和输入身份。`--seed` 可显式改变 seed，来源与变化保存在新计划中。`recover` 选择存储/证据不完整的任务；若整个报告发布失败，则重新运行相关任务以生成完整报告。两者都创建新运行和新 attempt，保留原日志、错误与来源关系。

配置、声明的输入、命令可执行文件、`nsim` 源文件或已记录依赖版本变化时拒绝精确重跑。损坏的结果文件会让原通过结果显示为 INCOMPLETE；丢失或损坏的控制记录不允许自动恢复。控制器异常退出、旧任务状态未知时，也拒绝自动重叠派发。

自动故障转移通过 `--retries`（默认 1）和 `--retry-seconds`（默认 120）限制。检测到存储 I/O 失败后，该输出根在本次运行内被隔离；新 attempt 可以使用尚未隔离的备用根。没有健康位置时保留失败，不盲目重复写故障盘。功能 FAIL 不自动重试。

## 证据与输出

- 控制目录：`plan.json`、独立 attempt 记录、实时 `live.json/live.html`、最终 `state.json`、`report.md/report.html`、`failed.json`。
- `final.json` 最后发布，校验计划、状态、报告和失败清单；缺失时不能把该批次作为可重跑的终态。
- 仿真输出：`<output-root>/regressions/<id>/{config,build,tasks,logs,waves,reports}`。任务文件进一步按 task id 和 attempt id 隔离。
- 输出目录中的报告链接指向控制目录的唯一最终报告，不在不同盘上维护相互矛盾的通过状态。
- `report show` 会重新校验证据 hash。HTML 是生成时的快照，后续存储损坏须通过命令重新检查。

## 接入工程命令

工程命令接收 `{case}`、`{seed}`、`{result}`、`{project}`、`{wave_dir}` 等参数，并在 `result` 路径写入版本化 JSON。参数通过 argv 传递，不经过 shell。项目特定 Golden 或性能公式由工程包装器判断，`nsim` 汇总必需检查。完整格式见 [本地接口](docs/local_execution.md)。

`nsim` 本身不执行 git pull、不清理历史输出。工程包装器也应遵守只读输入和独立输出契约；任意第三方命令的内部副作用并非由 `nsim` 沙箱隔离。

## 验证与边界

```sh
.venv/bin/python -m unittest discover -s tests -v
```

存储测试通过故障注入完成，不损坏真实磁盘。可检测已返回的 I/O 错误、空间/inode 阈值和路径不可用；配额无法查询时标记 unknown，实际 EDQUOT 写入错误会被记录。目录预检会做一次小文件写入/readback/fsync，并在进程结束后复检。

生产环境应把事件目录和备用输出放到独立故障域。不同目录不等于不同物理磁盘，本地工具不能证明存储冗余；也不能仅凭目录存在证明预期远端挂载在线。若内核 I/O 陷入不可中断等待，应用层重试预算不能保证终止该系统调用。断电持久性、备份恢复、修盘及仿真断点续跑不在当前能力内。
