# init - 生成一个NSIM ASIC 仿真平台

- 你的任务是一个生成一个完整的工业级的项目
- 你是一个asic 验证工程师  + python infrastructure engineer
- NSIM是一个asic 仿真平台；
- 项目的名称是NSIM

# 目标

生成一个完整的可执行的项目：
- nsim,asic工程，输出独立解耦
- 多个工程共享nsim，nsim能够自动发现工程
- nsim支持版本控制
- cli tool（nsim），支持--version ，可以识别不同的工程
- Synopsys VCS 编译与仿真
- Verdi 波形调试
- 可回溯regression 回归,一次回归是一个工程tag+nsim版本+nsim配置
- FSDB 波形 dump
- C / C++ DPI 编译
- log 管理
- build,log,wave,report目录管理
- 多进程调度+LSF 调度（大规模回归）
- testcase 控制
- 支持python 包管理
- 可以通过setup.py 安装
- 所有代码是完整，可执行的，no placeholders


# 目录结构

nsim/
  __init__.py
    version.py

  cli/
    main.py

  core/
    regress.py
    run.py
    lsf.py

  utils/
    cmd.py
    fs.py
    git.py
    parallel.py
    config_loader.py

  report/
    dashboard.py
    diff.py

  config/
    default.py
    loader.py

  sim/
    config.py

  AGENT.md
  setup.py
  pyproject.toml
  EADME.md
  MANIFEST.in
  requirements.txt

# 编程风格 

- Python 3.8+
- No pseudo code
- Modular design
- Proper imports
- Error handling

# 输出要求
- 每一个文件独立输出
- 输出完整代码

# 执行

生成整个工程，不要解释，只输出文件



