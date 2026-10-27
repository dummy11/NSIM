# `nsim` 实现状态

NSIM 是开发环境；`src/nsim` 是待交付的 Python 包。当前实现已完成本地验证，但 `pyproject.toml` 的 `0.1.0` 仍是候选版本；2026-10-27 核对时尚无远端 tag 或 Release。开发源码的远端状态以 `origin/main` 的实际提交为准。

| 功能 | 当前状态 |
|---|---|
| 包与 CLI | 可 editable install；`nsim --version` 和 `version --json` 可用。已构建 wheel 与 sdist 并在独立虚拟环境验证命令入口。 |
| 版本安装 | 单文件安装器、稳定 `nsim` 启动器、按版本独立虚拟环境、工程 `.nsim_version` 切换、离线包与历史记录版本分派已实现；联网拉取仍待真实 Release 验收。 |
| 项目管理 | `bootstrap/list/status/update/switch`、`.nsim/` schema 2、当前目录及子目录发现、用户级索引、重名保护、可恢复更新事务；最新已安装稳定版选择和缺失版本查询已验证。 |
| 配置与规划 | 支持 TOML/YAML 工程描述、caselist、严格 submit JSON、case × submit × seed、输入及工具身份快照。 |
| 本地执行 | fake/command 适配器、滚动并发、独立 attempt、超时与中断、stdout/stderr 分离。 |
| 判定与报告 | 区分 PASS、仅存活检查、未比较、DUT 失败与基础设施失败；最终结果需完整证据与 hash 校验。 |
| 重跑与存储恢复 | 保留原失败与 seed，隔离故障根、有限重试，在健康根重新执行新 attempt；不修复物理磁盘。 |
| VCS/DPI/Verdi/LSF | 配置与命令预览；尚无原生实际执行或真实工具环境验收。 |
| 比较与归档 | 报告比较、稳定归档、日志索引和安全清理尚未实现。 |

2026-09-20 本地测试扩展到 57 项全部通过；fake CLI 验收覆盖正常、失败、失败重跑与存储恢复。npu8860 dry-run 生成 3 个 smoke 任务预览、记录 322 个输入身份；未执行商业工具，未生成 DUT verdict。2026-10-27 本地扩展到 64 项测试通过，构建 `0.1.0` wheel/sdist 并通过归档检查；从实际候选 wheel 制作含依赖的离线包，在独立安装根中导入后通过统一 `nsim` 启动器运行 3 个 fake smoke 任务。还生成了 Linux x86_64/Python 3.9 目标依赖包，但没有 Linux 目标机安装验收。以上测试不能代替真实 GitHub Release、VCS、LSF、Verdi、Syscan 和硬盘故障验收。

发布前需要提交代码、确定版本 tag 与远端制品渠道；详见 `user_guide.md`。当前远端尚无可下载的 `nsim` Release。

## 2026-10-27 项目初始化与注册验收

按新的项目管理接口实现并通过 87 项本地测试。覆盖默认/自定义目录、已有 `.nsim`、重名与并发注册、已有文件保护、子目录识别、事务失败回滚与进程中断恢复、外部编辑保护、latest/显式预发布版本、缺失运行时诊断、双源码 filelist、include/library 成员变化以及历史回归绑定。

本次构建的候选 wheel/sdist 通过分发内容检查，并制作含依赖的离线包。在独立临时安装目录实际导入该离线包后，通过稳定 `nsim` 入口完成 bootstrap、list/status、update/switch、fake 回归通过、失败回归在注册更新后的重跑、历史 plan 字节保持不变、VCS dry-run 读取更新后的 RTL，以及工具版本缺失时的基本查询。真实 VCS/LSF 未执行，发布状态未改变。

新布局的 `filelist/sim.f` 是工具维护的组合来源说明；实际编译 token 与 argv 固化到每次计划和命令预览，不能直接把该来源说明作为原生 VCS filelist 使用。旧布局继续可读；自动迁移、项目注销/搬移不包含在本次实现中。

## 2026-10-27 提交前复查

新增回归测试先复现并修复了 `.nsim/` 内部路径误识别、单个项目访问失败中断全局列表、异常能力元数据导致版本选择退出，以及更新/恢复跟随被替换的父目录符号链接的问题。同时校正安装器的数字版本排序，拒绝损坏的安装凭据与恢复记录。完整本地测试扩展到 95 项并全部通过。

源码归档补充包含 `scripts/check_distribution.py`，分发检查同时核对该脚本、项目发现同步脚本、文档和模块内容。原生 EDA/farm 的执行边界保持不变。
