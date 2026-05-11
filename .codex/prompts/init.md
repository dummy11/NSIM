# init - nsim project init prompt

 you'r a senior asci verification engineer and python tooling architect.

 the reporsitory is currently empty.

 your task is not to immediately implement a large and complete system.

 your task is to init the project using a harness-engineering style workflow

# 目标

生成一个完整的可执行的项目nsim：
- 项目的名称nsim
- nsim是一个 ASIC 仿真回归平台
- nsim，asic 工程，仿真输出 三者独立
- 支持多个工程共享nsim，nsim能够自动发现工程
- nsim支持版本控制
- 支持 Python 包管理，使用pyproject.toml
- cli tool（nsim），支持--version ，可以识别不同的工程
- 支持Synopsys VCS 编译与仿真
- Verdi 波形调试
- 可回溯regression 回归,一次回归是一个工程tag+nsim版本+nsim配置
- FSDB 波形 dump
- C / C++ DPI 编译
- log 管理
- build,log,wave,report目录管理
- 多进程调度+LSF 调度（大规模回归）
- testcase 以 caselist的方式组织
- 不同的 vcs 仿真选项以submit json的方式组织
- 仿真一个 case可以指定不同的 submit 
- 一次回归可以指定多个 submit

首先生成一个规划文档,其他的不要做

在编写实际代码之前，首先生成以下文件:
docs/plan.md
docs/architecture.md
docs/roadmap.md
docs/decisions.md
