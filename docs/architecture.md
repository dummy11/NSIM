# nsim 架构

## 系统边界

`nsim` 以 Python CLI 工具形态交付，而不是服务端平台或 Web 应用。架构上有三个相互独立的领域：

1. **工具领域**：已安装的 Python 包、CLI、适配器、调度器和报告代码。不同 ASIC 工程可以使用不同版本的 `nsim`。
2. **工程领域**：ASIC 工程仓库、工程描述文件、源码树、filelist、testbench 资源、DPI 源码、caselist 和 submit JSON 文件。
3. **输出领域**：生成的 build 目录、仿真 log、FSDB wave、report 和 regression 元数据。

架构必须防止这些领域发生意外耦合。`nsim` 默认只读取工程输入、创建输出工作区，并将结果写入工程源码树之外的独立 output root，不修改 ASIC 工程源码。

## 高层组件

```text
CLI
 |
 v
Command Services
 |
 +-- Discovery Service
 +-- Config Loader
 +-- Project Model
 +-- Regression Planner
 +-- Scheduler Interface
 |     +-- Local Multiprocessing Scheduler
 |     +-- LSF Scheduler
 +-- Simulator Interface
 |     +-- Synopsys VCS Adapter
 |     +-- Verdi Adapter
 +-- Output Store
 +-- Report Generator
```

## 组件职责

### CLI

解析用户命令，校验参数，选择目标工程，并委托给 command service。CLI 不应包含仿真器或调度器业务逻辑。

### Discovery Service

通过扫描配置的搜索根目录并识别工程描述文件来发现 ASIC 工程。工程描述文件采用明确文件名和格式，当前基线允许 `nsim.yaml` 或 `nsim.toml`。Discovery Service 返回结构化工程身份，而不只是原始路径。

### Config Loader

加载并校验：

- nsim 全局或 workspace 配置
- 工程描述文件
- 纯文本 caselist
- submit JSON 文件，并按 `nsim` 定义的 schema 强校验

loader 应基于声明路径的配置文件本身来规范化相对路径，并识别工程对 `nsim` 版本的约束或选择。

### Project Model

表示 ASIC 工程身份、版本 tag、源码根目录、支持的 caselist、submit 文件、filelist、DPI 源码、DPI Makefile、`nsim` 版本约束和仿真器默认值。工程 tag 优先来自 git tag 或 commit SHA。

### Regression Planner

将用户请求展开为具体运行任务。例如，一个包含 20 个 case 的 caselist 和 3 个 submit JSON 文件会被展开为 60 个仿真任务，并在需要时附加编译任务。VCS compile 当前按 submit 独立规划，后续可在 artifact 复用模型成熟后再优化共享。

### Scheduler Interface

定义 planned task 的执行契约。调度器适配器接收任务描述，并返回状态变化、log、退出码和资源元数据。

### Local Multiprocessing Scheduler

在当前机器上以可配置并发度运行任务。它应作为第一个实现的调度器，并可配合 fake simulator 验证核心流程，因为它不依赖真实 VCS、Verdi 或 LSF。

### LSF Scheduler

将任务提交到 LSF，用于大规模 regression。队列、资源和 license 约束可以来自 submit JSON，也可以由 `nsim` 命令行覆盖。该适配器应把 `bsub`、`bjobs` 和状态解析隔离在稳定 Python 接口之后。

### Simulator Interface

定义仿真器操作，例如 compile、simulate、waveform 配置和 debug 启动。

### Synopsys VCS Adapter

根据工程元数据和 submit JSON 构建 VCS 编译与仿真命令。它应支持：

- 编译选项
- 仿真选项
- plusargs
- FSDB dump 选项，允许 submit 和 testbench plusarg 共同配合控制
- DPI C 和 C++ 编译输入；DPI 编译使用独立 Makefile，编译结果以 `.so` 形式存在；DPI 产物默认工程共享，也可以通过 submit 控制为按 regression 或 run 隔离
- 环境变量
- timeout 和 retry 策略扩展点

### Verdi Adapter

基于已完成或进行中的 run，使用正确的 FSDB、设计数据库和工程上下文启动 Verdi。Verdi adapter 不负责仿真流程本身。

### Output Store

创建和管理输出目录：

```text
<output-root>/
  regressions/
    <regression-id>/
      metadata.json
      config/
      build/
      logs/
      waves/
      reports/
      tasks/
```

Output root 的默认位置来自用户配置或环境变量。Output Store 负责稳定路径、元数据写入以及后续保留策略。

### Report Generator

读取任务元数据和 log，生成面向人的报告和机器可读摘要。报告应基于已记录 artifact 生成，而不是依赖实时调度器状态。

## 数据流

1. 用户运行 `nsim` CLI 命令。
2. CLI 解析配置和工程搜索根目录。
3. Discovery 识别目标 ASIC 工程。
4. Config Loader 读取工程描述文件、caselist 和 submit JSON。
5. Regression Planner 将输入展开为任务定义。
6. Output Store 创建 regression 目录并快照配置。
7. Scheduler 使用 simulator adapter 执行任务。
8. 每个任务写入 log、wave、build 输出、状态和元数据。
9. Report Generator 汇总完成后的 regression。

## 可追溯模型

每次 regression 应写入一个 `metadata.json` 记录，包含：

- regression id
- 工程名
- 工程根目录
- 工程 tag 或 commit
- 当前工程使用的 `nsim` 版本
- `nsim` 配置 hash
- caselist hash
- submit JSON hash
- 调度器适配器
- 仿真器适配器
- 命令行调用
- 环境摘要
- 任务列表和最终状态

该元数据是 rerun 和 debug 的主要审计记录。长期存储先保持文件形式；只有当文件元数据和索引能力被证明不足时，才考虑引入数据库。

## 错误处理

错误应被归类为明确类型：

- 配置错误
- 工程发现错误
- 规划错误
- 调度提交错误
- 调度运行时错误
- 仿真器编译错误
- 仿真器运行错误
- 报告生成错误

每个失败任务都应保留足够上下文，使工程师能够手动复现失败命令。

## 测试策略

架构应支持在没有商业 EDA 工具的情况下测试：

- 配置解析、工程发现、任务规划和输出路径的单元测试
- 使用 fake simulator adapter 测试命令生成
- 使用 fake scheduler 测试任务状态变化
- 使用示例 ASIC 工程 fixture 的集成测试
- 面向真实 VCS、Verdi 和 LSF 安装的可选环境门控测试
