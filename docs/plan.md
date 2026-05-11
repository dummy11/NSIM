# nsim 项目规划

## 目标

`nsim` 是一个 ASIC 仿真回归平台。它负责协调多个 ASIC 工程的仿真活动，同时保持三个领域彼此独立：

- `nsim` 工具及其有版本管理的 Python 包
- ASIC 工程仓库及其本地元数据
- 生成的仿真输出，例如 build、log、wave 和 report

第一阶段的开发目标是建立一个小而可执行的基础系统：能够发现工程、识别版本、加载 case 和 submit 定义，并运行一次可追溯的本地回归。VCS、Verdi、DPI、FSDB、多进程和 LSF 支持应在工程模型稳定后分阶段加入。

## 产品范围

### 范围内

- 使用 `pyproject.toml` 管理 Python 包
- CLI 命令名为 `nsim`
- 支持 `nsim --version`
- 自动发现 ASIC 工程
- 分离工具安装、工程配置和输出目录
- 有版本可追溯的 regression 记录
- 使用 caselist 组织 testcase
- 使用 JSON 文件组织 submit 配置
- 单个 case 可以指定一个 submit 运行
- 一次 regression 可以指定多个 submit
- 管理 build、log、wave 和 report 目录
- 集成 Synopsys VCS 编译与仿真
- 支持 Verdi 波形调试启动
- 支持 FSDB 波形 dump 配置
- 支持 C 和 C++ DPI 编译
- 本地多进程调度
- 面向大规模回归的 LSF 调度适配器
- log 捕获、索引和保留策略扩展点

### 初始规划基线范围外

- 立即实现完整仿真引擎
- 在文件元数据证明不足之前引入数据库
- 直接管理 license server
- 替代工程已有的 Makefile、filelist 或仿真脚本
- 创建 Web UI

## 假设（Assumptions）

- `nsim` 初期以 Python CLI 工具形态交付，而不是服务端平台或 Web 应用。
- ASIC 工程可以通过一个明确的工程描述文件被发现和识别。
- ASIC 工程自身继续维护源码、filelist、testbench、DPI 源码和已有脚本。
- 仿真输出默认可以写入工程目录之外的独立 output root。
- submit JSON 是仿真选项矩阵的主要组织方式。
- caselist 是 testcase 集合的主要组织方式。
- 初期可用 fake simulator 和本地多进程调度验证核心流程，不要求立即接入真实 VCS、Verdi 或 LSF。
- 真实 EDA 工具相关测试需要通过环境门控运行，普通 CI 不假设存在 VCS、Verdi 或 LSF。
- regression 可追溯性优先依赖文件快照、hash 和机器可读 metadata，而不是人工命名约定。

## 开放问题（Open Questions）

- 工程描述文件的文件名和格式是否固定为 `nsim.yaml`、`nsim.toml`，还是允许多种格式？
- caselist 采用纯文本、YAML、TOML、JSON，还是兼容已有工程格式？
- submit JSON 的 schema 由 `nsim` 强约束，还是允许工程扩展自定义字段？
- ASIC 工程 tag 如何定义：优先使用 git tag、commit SHA，还是允许用户传入工程自定义版本号？
- output root 的默认位置应来自用户配置、环境变量，还是当前工作目录下的默认目录？
- VCS compile 是否按 submit 独立执行，还是支持多个 simulation submit 共享同一个 compile artifact？
- FSDB dump 由 submit JSON 直接控制，还是由 testcase、testbench 或运行时 plusarg 共同决定？
- DPI 编译产物是否按工程共享，还是按 regression/run 隔离生成？
- LSF 队列、资源和 license 约束应建模在 submit JSON 中，还是独立为 scheduler profile？
- regression metadata 的长期存储先保持文件形式，还是需要预留后续数据库索引接口？





## 核心概念

### nsim 安装

安装后的 Python 包提供 CLI、核心编排代码、调度器适配器、仿真器适配器和报告生成能力。

### ASIC 工程

ASIC 工程是一个独立版本管理的仓库或目录树，包含源文件和 `nsim` 工程描述文件。`nsim` 必须能在不把工程复制进 `nsim` 仓库的情况下发现并识别工程。

### 输出根目录

仿真输出默认位于 `nsim` 包和 ASIC 源码树之外。每次运行都有独立输出目录，包含 build 产物、log、wave、report 和机器可读元数据。

### Regression 记录

一次 regression 需要通过以下信息追溯：

- ASIC 工程身份和 tag
- `nsim` 版本
- `nsim` 配置快照
- 使用的 submit 定义
- caselist 快照
- 调度模式
- 开始和结束时间
- 结果摘要

## 预期仓库结构

仓库后续应演进为以下结构：

```text
nsim/
  pyproject.toml
  README.md
  AGENTS.md
  docs/
    plan.md
    architecture.md
    roadmap.md
    decisions.md
    freeze_log.md
  src/
    nsim/
      __init__.py
      cli.py
      config/
      discovery/
      project/
      regression/
      scheduler/
      simulator/
      reports/
      storage/
  tests/
    unit/
    integration/
  examples/
    projects/
```

该结构只是规划说明，本次初始化不实现这些文件。

## 配置模型

平台应优先使用显式的文件化配置：

- 工程描述文件：标识 ASIC 工程及其支持的仿真根目录
- caselist：列出 testcase 及每个 case 的属性
- submit JSON：定义编译、仿真、波形、DPI 和调度选项
- nsim 配置：定义输出根目录、调度默认值、保留策略和工具路径等默认项

配置应被复制或渲染到每次 regression 输出中，使结果在工程文件变化后仍可复现。

## CLI 基线

CLI 后续应支持：

```bash
nsim --version
nsim project list
nsim project inspect <project>
nsim case list --project <project> --caselist <file>
nsim run case --project <project> --case <name> --submit <submit.json>
nsim run regression --project <project> --caselist <file> --submit <submit.json> --submit <other.json>
nsim report show <regression-id>
nsim verdi open <run-id>
```

具体命令名称可以在 CLI 设计阶段调整，但接口必须保持显式且便于脚本调用。

## 开发原则

- 按可 review 的阶段推进。
- 进入下一阶段前冻结已 review 的基线。
- 将仿真器相关逻辑封装在适配器边界之后。
- 将工程发现与执行流程分离。
- 优先依赖机器可读的运行元数据，而不是解析人工 log。
- 先实现本地执行，再加入 LSF 扩展。
- 将每次 regression 视为可审计 artifact。

## human desicions
- `nsim` 是以 Python CLI 工具形态交付，而不是服务端平台或 Web 应用。
- ASIC 工程可以通过一个明确的工程描述文件被发现和识别。
- ASIC 工程自身继续维护源码、filelist、testbench、DPI 源码和已有脚本。
- 仿真输出默认可以写入工程目录之外的独立 output root。
- submit JSON 是仿真选项矩阵的主要组织方式。
- caselist 是 testcase 集合的主要组织方式。
- 初期可用 fake simulator 和本地多进程调度验证核心流程，不要求立即接入真实 VCS、Verdi 或 LSF。
- 真实 EDA 工具相关测试需要通过环境门控运行，普通 CI 不假设存在 VCS、Verdi 或 LSF。
- regression 可追溯性优先依赖文件快照、hash 和机器可读 metadata，而不是人工命名约定。
- 工程描述文件的文件名和格式是否固定为 `nsim.yaml`、`nsim.toml`
- caselist 采用纯文本
- submit JSON 的 schema 由 `nsim` 强约束
- ASIC 工程 tag 优先使用 git tag、commit SHA
- output root 的默认位置应来自用户配置、环境变量
- VCS compile 按 submit 独立执行;
- FSDB dump 由 submit和 testbench plusarg共同配合控制
- DPI 编译产物工程共享，还是按 regression/run 隔离生成,可以通过 submit 控制，默认是工程共享
-  LSF 队列、资源和 license 约束可以通过 submit 控制，也可以通过 nsim 命令行控制
- regression metadata 的长期存储先保持文件形式

## human release notes
- 不同的项目下，nsim可以使用不同的版本
-  dpi 的编译使用独立的 Makefile,编译结果以 so 的形式存在
