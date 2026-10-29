# NSIM 开发仓库

NSIM 用于开发、测试和发布 `nsim`，保存工具源码、测试、示例与设计文档。安装后的 `nsim` 是面向 ASIC 验证工程的 Python 命令行工具。本文面向仓库开发者；安装和日常使用请从 [nsim 使用指南](user_guide.md) 开始。

## 名称与文档范围

| 名称 | 含义 | 文档入口 |
| --- | --- | --- |
| **NSIM** | 开发仓库与开发环境：源码、架构、测试、构建和发布 | 本 README 及下方开发文档 |
| **nsim** | 安装后的 Python 包与 CLI：项目管理、编译、仿真、回归和报告 | [nsim 使用指南](user_guide.md)及其专题文档 |
| 验证工程 | 工程自己的 RTL、testbench、filelist、caselist 和 submit 配置 | 由 nsim 注册和使用，保存在工程目录中 |

正文用 `NSIM` 指仓库、`nsim` 指工具。已有环境变量、接口字段、文件路径与真实终端输出保持原有拼写。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `src/nsim/` | 版本化 Python 包的实现 |
| `nsim-install.py` | 独立安装器与稳定启动器 |
| `tests/` | 单元测试、流程测试和本地 LSF 测试服务 |
| `examples/` | fake 示例项目及参考工程 dry-run 脚本 |
| `scripts/` | 项目发现代码同步和分发内容检查 |
| `pyproject.toml`、`MANIFEST.in` | 包版本、依赖、入口和分发文件定义 |
| `docs/`、`user_guide.md` | 开发文档与工具使用文档 |

工具安装、ASIC 工程和仿真输出分别管理。wheel 交付 Python 包及 CLI；安装器另行交付，工程源码和运行产物不进入 wheel。

## 准备开发环境

需要 Python 3.9+，执行与锁测试当前面向 POSIX（Linux/macOS）。取得源码后在 NSIM 仓库根目录运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/nsim --version
.venv/bin/nsim version --json
```

开发环境的 `.venv/bin/nsim` 直接运行当前源码。面向用户的稳定 `nsim` 启动器由 `nsim-install.py` 创建，负责版本安装和按项目分派；使用方法见 [工具安装指南](user_guide.md#安装器和版本目录)。克隆仓库或 editable install 本身不会把候选版本导入稳定启动器的版本库。

## 运行开发测试

执行测试和实际任务都需要系统级[环境只读保护](docs/read_only_environment.md)：Linux 提供 `bwrap`
及 namespace 支持，macOS 允许启动 `sandbox-exec`。缺少后端时执行会失败，不降级为可写源码。

```sh
.venv/bin/python scripts/sync_project_layout.py --check
.venv/bin/python -m unittest discover -s tests -v
```

测试使用 fake/command、本地进程级 LSF 服务、合成日志和存储故障注入。它们验证工具行为，真实商业 EDA、LSF 集群、共享文件系统和目标机安装仍需单独验收。按日期保存的验证范围见 [实现状态与验证记录](docs/implementation_status.md)。

## 仓库 smoke 示例

以下示例使用仓库内的 fake 项目，已显式配置 local，无需商业工具或 LSF。输出根必须预先存在，并位于工具与工程源码树之外：

```sh
mkdir -p /tmp/nsim-output /tmp/nsim-backup
.venv/bin/nsim project list examples/projects
.venv/bin/nsim regr \
  --project examples/projects/fake \
  --caselist examples/projects/fake/smoke.caselist \
  --submit examples/projects/fake/pass.json \
  --seed 42 --jobs 2 \
  --output-root /tmp/nsim-output \
  --output-root /tmp/nsim-backup
```

命令返回本次 `record_dir`。增加 `--submit examples/projects/fake/fail.json` 可演示未比较和性能失败，此时预期返回非零状态；增加 `--dry-run` 可只保存计划和预览。每次回归使用独立目录，汇总文件位于 `regr_dir/<id>/report/`；批量入口见 [Regression list](docs/regression_batches.md)。报告、重跑与证据规则见 [nsim 使用指南](user_guide.md#检查报告重跑和恢复)。

## 构建与交付

`pyproject.toml` 是包版本的唯一来源，当前工作区使用候选 `0.1.0`。构建 wheel/sdist、分发内容核验、离线包制作和 Release 流程统一见 [构建与发布 nsim](docs/releasing.md)。验证记录只对应当时源码；发布时重新核对提交、版本、制品和目标环境。

## 开发文档

| 主题 | 文档 |
| --- | --- |
| 目标、范围和接口决策 | [开发规划](docs/plan.md) |
| 模块、数据流与执行契约 | [nsim 实现架构](docs/architecture.md) |
| 逻辑用例、seq/plusargs、自定义映射与 demo | [Caselist 语法](docs/caselist.md) |
| 当前实现和历史验证范围 | [实现状态](docs/implementation_status.md) |
| 待实现功能及真实环境验收 | [开发待办](docs/backlog.md) |
| 构建、模板说明同步与发布 | [构建与发布](docs/releasing.md) |

工具使用专题由 [nsim 使用指南](user_guide.md#阅读入口)统一导航。
