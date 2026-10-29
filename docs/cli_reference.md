# nsim 子命令与选项完整清单

本清单对应当前工作区的包内 CLI 和独立安装器。日常复制使用见 [命令简版](cli_quickstart.md)，安装及项目配置见 [使用指南](../user_guide.md)。

`nsim` 有两种入口：稳定启动器会管理安装版本并分派到项目所选版本；直接安装 wheel 或 editable install 得到的包内 CLI 只提供项目和仿真命令。`install`、`versions`、`bundle create` 由稳定启动器提供。所有命令层级都支持 `-h / --help`。正式简写见下方对照表；原命令与选项继续支持，长短写法可以混用。

## 全部子命令

| 命令 | 用途 | 位置参数 |
| --- | --- | --- |
| `nsim bootstrap` | 初始化或注册验证工程 | 无 |
| `nsim list` | 列出当前用户已注册的工程 | 无 |
| `nsim status` | 查看工程配置、版本及问题 | 无 |
| `nsim update` | 更新已注册工程的配置 | 无 |
| `nsim switch VERSION` | 切换工程所选 nsim 版本 | `VERSION` 必填 |
| `nsim project list ROOT...` | 在指定根目录发现工程描述文件 | 一个或多个 `ROOT` |
| `nsim project inspect PATH` | 读取指定工程描述文件 | `PATH` 必填 |
| `nsim submit new NAME` | 创建继承公共模板的 submit | `NAME` 必填，可带 `.json` |
| `nsim submit show SUBMIT` | 展示合并后的配置与来源 | `SUBMIT` 必填 |
| `nsim submit check SUBMIT` | 校验 submit 字段、继承与显式文件输入 | `SUBMIT` 必填 |
| `nsim com` | 只编译或准备编译输入 | 无 |
| `nsim sim` | 使用已有编译产物仿真并检查结果 | 无 |
| `nsim run` | 编译、仿真及结果检查 | 无 |
| `nsim regr` | 按用例清单批量编译、仿真及汇总 | 无 |
| `nsim rerun SOURCE` | 重跑已结束记录中未通过的任务 | `SOURCE` 必填 |
| `nsim recover SOURCE` | 恢复存储或证据失败的任务 | `SOURCE` 必填 |
| `nsim report show SOURCE` | 校验证据并显示终态报告 | `SOURCE` 必填 |
| `nsim storage check ROOT` | 检查已有输出目录的空间及读写能力 | `ROOT` 必填 |
| `nsim version` | 显示当前工具版本 | 无 |
| `nsim capabilities` | 显示支持的项目 schema | 无 |
| `nsim install [VERSION]` | 安装远端版本、本地 wheel 或离线包 | `VERSION` 按来源选填；稳定启动器提供 |
| `nsim versions` | 列出已安装版本，可查询远端 | 无；稳定启动器提供 |
| `nsim bundle create` | 制作离线安装包 | 无；稳定启动器提供 |

`list` 读取用户注册表；`project list` 扫描给定目录。`version` 是当前运行版本；`versions` 是安装版本库。

## 命令简写

| 原命令 | 简写 |
| --- | --- |
| `bootstrap` | `init` |
| `list` | `ls` |
| `status` | `st` |
| `update` | `up` |
| `switch` | `use` |
| `project` | `proj` |
| `submit` | `sub` |
| `capabilities` | `caps` |
| `version` | `ver` |
| `rerun` | `rr` |
| `recover` | `rc` |
| `report` | `rpt` |
| `storage` | `disk` |
| `install` | `inst` |
| `versions` | `vers` |
| `bundle` | `pack` |

`proj ls` 等价于 `project list`；`sub new/show/check` 保留原二级命令。`com/sim/run/regr` 已较短，保持原名。

## 选项简写

下表只增加拼写，不改变参数的含义、默认值、适用命令或互斥关系。`-s` 是 submit，`-S` 是 seed，区分大小写；`-r` 是回归随机 seed 数，`-retry` 是存储故障重试次数。

| 原选项 | 正式简写或补充写法 |
| --- | --- |
| `--version` | `-V` |
| `--json` | `-J` |
| `--project` | `-p` |
| `--name` | `-N` |
| `--verification-root` | `-vr` |
| `--rtl-root` | `-rtl` |
| `--rtl-filelist` | `-rf` |
| `--verification-filelist` | `-vf` |
| `--nsim-version` | `-nv` |
| `--output-root` | `-o` |
| `--case` | `-c` |
| `--submit` | `-s` |
| `--seed` | `-S` |
| `-mode` | `-m`、`--mode` |
| `-dir` | `-d`、`--dir` |
| `-com-dir` | `-cd`、`--com-dir` |
| `-sim-dir` | `-sd`、`--sim-dir` |
| `-repeat` | `-r`、`--repeat` |
| `--jobs` | `-j`、`-jobs` |
| `--retries` | `-retry` |
| `--retry-seconds` | `-rt` |
| `--min-free-bytes` | `-fb` |
| `--min-free-inodes` | `-fi` |
| `--dry-run` | `-n` |
| `--scheduler` | `-sched` |
| `--resource` | `-R` |
| `--license` | `-lic` |
| `--extends` | `-e` |
| `--plusargs` | `-pa` |
| `--bundle` | `-b` |
| `--wheel` | `-w` |
| `--wheelhouse` | `-wh` |
| `--checksums` | `-sum` |
| `--sha256` | `-sha` |
| `--all` | `-a` |
| `--user` | `-u` |
| `--system` | `-sys` |
| `--output` | `-o` |
| `--download-deps` | `-deps` |
| `--platform` | `-plat` |
| `--python-version` | `-py` |
| `--implementation` | `-impl` |

`--caselist` 的 `-l`、`--queue` 的 `-q / -queue`、`--seed` 的 `-seed`、`--submit` 的 `-sub`、`--scheduler` 的 `-scheduler` 等原有写法均保留。`-o` 在执行与项目配置中表示输出根，在 `pack create` 中表示输出包文件。

短选项与值建议分开输入，例如 `-p demo`、`-S 42`；也支持 `-p=demo`、`-S42`。使用稳定启动器时应更新启动器脚本；启动器会把运行时简写展开为原写法再选择和调用版本。直接运行包内 CLI 则需要包含简写支持的工具版本。

## 全局选项

| 选项 | 含义 |
| --- | --- |
| `-h / --help` | 显示当前层级帮助，如 `nsim regr -h` |
| `--version` | 输出当前包版本并退出，等价于普通文本形式的 `nsim version` |
| `-o / --output-root PATH` | 放在子命令前时，为 `com/sim/run/regr/rerun/recover` 指定本次输出根；可重复 |

子命令后也指定输出根时，后面的整组值优先。`nsim -o ... update` 不合法；保存项目默认目录使用 `nsim update --output-root ...`。

## 项目管理选项

### bootstrap

| 选项 | 含义及默认行为 |
| --- | --- |
| `--name NAME` | 工程名称，必须等于验证根目录名 |
| `--verification-root PATH` | 验证根目录；省略时发现当前工程，否则使用当前目录下的 `NAME` 或 `verification` |
| `--rtl-root PATH` | RTL 根目录；新建工程必填 |
| `--rtl-filelist PATH` | RTL filelist；新建工程必填 |
| `--verification-filelist PATH` | 验证 filelist；省略时使用并按需创建验证根下的 `filelist/tb.f` |
| `--nsim-version VERSION` | 选择已安装的版本；省略或 `latest` 时选择兼容的已安装稳定版本 |
| `--output-root PATH` | 保存项目可选的输出根；若保存多个，执行时需用 `-o` 明确选一个 |
| `--json` | 输出 JSON |

已有工程再次 bootstrap 用于注册或确认现有配置。修改配置使用 update。

### update

| 选项 | 含义 |
| --- | --- |
| `--project NAME_OR_PATH` | 选择已注册工程；省略时从当前目录及祖先发现 |
| `--rtl-root PATH` | 修改 RTL 根目录 |
| `--rtl-filelist PATH` | 修改 RTL filelist |
| `--verification-filelist PATH` | 修改验证 filelist |
| `--nsim-version VERSION` | 修改所选工具版本，支持 `latest` |
| `--output-root PATH` | 替换默认输出根列表；可重复，省略则保留原列表 |
| `--json` | 输出 JSON |

至少指定一个配置修改项。工程注册和更新命令中的路径按调用目录解析，保存时转换为工程相对路径。

### 其他项目命令

| 命令 | 全部选项 |
| --- | --- |
| `list` | `--json` |
| `status` | `--project NAME_OR_PATH`、`--json` |
| `switch VERSION` | `--project NAME_OR_PATH`、`--json` |
| `project list ROOT...` | 除帮助外无其他选项；输出 JSON |
| `project inspect PATH` | 除帮助外无其他选项；输出 JSON |

`switch VERSION` 对应 `update --nsim-version VERSION`，不会下载工具版本。

## Submit 命令选项

| 命令 | 选项 | 含义 |
| --- | --- | --- |
| `submit new NAME` | `--project NAME_OR_PATH` | 工程，默认发现当前工程 |
| `submit new NAME` | `-mode / --mode MODE` | 默认 `rtl`；指定父模板时可推导其模式 |
| `submit new NAME` | `--extends PARENT` | 父模板路径，相对工程的 `submit/` 或使用绝对路径 |
| `submit show SUBMIT` | `--project NAME_OR_PATH` | 工程，默认发现当前工程 |
| `submit check SUBMIT` | `--project NAME_OR_PATH` | 工程，默认发现当前工程 |

`MODE` 可选 `rtl`、`gate`、`fpga`、`haps`、`zebu`、`palladium`。这三个命令均输出 JSON，无需另加 `--json`。

`SUBMIT` 支持工程 `submit/` 下的裸名称、带 `.json` 的名称或显式文件路径。当前目录存在同名文件时优先使用该文件；显式相对路径按调用目录解析。`check` 校验配置、继承及 `files` 声明的存在性和成员集合；`show/check` 返回 `resolved_files`。RTL/TB 清单、工具及完整命令预览用执行命令的 `--dry-run` 检查。辅助文件的 `{file:NAME}` 引用、阶段和路径规则见 [Submit 语法](submit.md)。

## 编译、仿真与回归选项

### 用例、清单与 seed

| 选项 | com | sim / run | regr | 含义 |
| --- | --- | --- | --- | --- |
| `--case CASE` | 不支持 | 与 `-l` 二选一，必须选一种 | 不支持 | 一个逻辑用例名 |
| `-l / --caselist FILE...` | 不支持 | 与 `--case` 二选一 | 与 `-rl` 二选一 | 一个或多个用例清单；可重复 |
| `-seed / --seed N...` | 不支持 | 可选 | 可选 | 一个或多个固定 seed；可重复 |
| `-repeat N` | 不支持 | 不支持 | 可选 | 生成 N 个不同随机 seed，与 `-seed` 互斥 |
| `-rl / --regr-list FILE` | 不支持 | 不支持 | 与 `-l` 二选一 | 批量回归列表，每行是一组 regr 参数 |
| `--plusargs / -pa ARG...` | 不支持 | 可选 | 可选 | 最终运行参数覆盖，可给多个 `+KEY=VALUE` |

批量模式 `regr -rl FILE` 的外层仅接受项目、输出根、预览选项；其余执行选项写入列表各行。普通单次回归必须提供 submit。

seed 范围为 1–2147483647。未指定 seed 或 repeat 时生成一个随机 seed，保存到计划。回归任务按去重后的 case × submit × seed 展开。

### 四个执行命令共用的全部选项

| 选项及已有别名 | 默认值 | 含义 |
| --- | --- | --- |
| `--project NAME_OR_PATH` | 当前工程 | 指定工程名称或路径 |
| `-sub / --submit SUBMIT...` | 必填 | 一个或多个 submit 名称或 JSON 路径；可重复；批量回归可在列表每行提供 |
| `-mode MODE` | 从 submit 推导 | 显式值须与 submit 模式一致，多个 submit 须同模式 |
| `-dir NAME` | 所选模式 | 工作区名称 |
| `-com-dir PATH` | `com_dir` | 编译目录，相对工作区或绝对路径 |
| `-sim-dir PATH` | `sim_dir` | 仿真目录，相对工作区或绝对路径；com 不执行仿真 |
| `-o / --output-root PATH` | 按下述优先级 | 本次选定的唯一输出根；不自动切根 |
| `-j / -jobs / --jobs N` | `1` | 本次调用允许的最大未完成任务数，正整数 |
| `--retries N` | `0` | 兼容旧调用；自动基础设施重试已停用，只接受 0 |
| `--retry-seconds SECONDS` | `0` | 兼容旧调用；自动基础设施重试已停用，只接受 0 |
| `--min-free-bytes N` | `0` | 输出根最少剩余字节数，非负整数 |
| `--min-free-inodes N` | `0` | 输出根最少剩余 inode 数，非负整数 |
| `--dry-run` | 关闭 | 保存执行计划及命令预览，不执行真实编译/仿真 |
| `-scheduler / --scheduler KIND` | submit 配置；未配置为 `lsf` | `local` 或 `lsf` |
| `-q / -queue / --queue QUEUE` | submit 配置 | 覆盖调度队列 |
| `--resource EXPR` | submit 配置 | 覆盖 LSF 资源表达式列表；可重复 |
| `--license RESOURCE=COUNT` | submit 配置 | 覆盖 LSF license 资源配置；可重复，COUNT 为正整数 |

执行阶段的 `-mode`、`-dir`、`-com-dir`、`-sim-dir`、`-repeat` 同时支持对应的双横线形式和上表的短选项。

输出根优先级：子命令后的 `-o` → 子命令前的 `-o` → 项目默认输出根 → `NSIM_OUTPUT_ROOT`。都未配置时报错；默认配置含多个根时必须显式选一个。选定的根必须预先存在，检查失败不会自动改用其他根。

回归使用批次隔离目录，编译与仿真路径必须留在该批次对应 submit 的目录内；自定义相对路径或绝对路径仍受布局校验。

重复 `--resource` 或 `--license` 构成新的覆盖列表或映射，不追加到 submit 的原配置。资源表达式含括号等字符时加引号，例如 `--resource 'rusage[mem=4096]'`。

`--retries` 与 `--retry-seconds` 非零时报配置错误。仿真超时在 submit 的 `timeout_seconds` 中配置；再次执行或更换输出根需要显式使用 rerun/recover。

## 报告、重跑、恢复与存储

`SOURCE` 使用执行命令返回的 `record_dir`，也可使用带有效运行记录的仿真目录。

| 命令 | 全部选项 | 默认行为 |
| --- | --- | --- |
| `rerun SOURCE` | `-o / --output-root PATH`、`-seed / --seed N`、`--jobs N`、`--retries 0`、`--retry-seconds 0` | 沿用历史输入和版本；历史多根计划需明确选择本次输出根 |
| `recover SOURCE` | 与 rerun 相同 | 仅选择存储/证据失败；报告发布失败时重建相关任务报告 |
| `report show SOURCE` | `--json` | 默认输出 Markdown 文本报告 |
| `storage check ROOT` | `--min-free-bytes N`、`--min-free-inodes N` | 两个阈值均默认 0；输出 JSON |

重跑的 `-o` 可重复，`-seed` 只能给一个新值。rerun/recover 的并发选项也支持 `-j / -jobs / --jobs`。两者不接受 `--project`、`--dry-run`、`-sub`、`-l`、`-repeat` 或队列覆盖；输入和版本取自历史记录。

`storage check` 会在目录内创建临时探测文件，完成读写与 fsync 后删除。`recover` 重新执行相关任务，不是仿真断点续跑。

## 版本与能力查询

| 命令 | 全部选项 | 输出 |
| --- | --- | --- |
| `version` | `--json` | 默认文本，指定选项后输出工具名称与版本 JSON |
| `capabilities` | `--json` | 当前无论是否指定该选项都输出 JSON，包含版本和支持的项目 schema |

## 安装、多版本与离线包

以下命令由稳定启动器提供，也可以使用 `python3 nsim-install.py ...` 或 `nsim-install ...` 调用。

### install

| 参数或选项 | 含义 |
| --- | --- |
| `[VERSION]` | 远端版本；本地 wheel/bundle 模式可选填，用于核对版本 |
| `--all` | 安装所有已发布的稳定版本，不能同时给 VERSION |
| `--bundle FILE` | 从离线包安装 |
| `--wheel FILE` | 从本地 wheel 安装 |
| `--wheelhouse DIR` | 本地 wheel 的依赖目录，配合 `--wheel` |
| `--checksums FILE` | 本地 wheel 的校验清单，配合 `--wheel` |
| `--sha256 HASH` | 期望的本地 wheel 或离线包 SHA-256 |
| `--user` | 安装到 `~/.local/share/nsim`；省略位置选项也是个人安装 |
| `--system` | 安装到 `/opt/nsim`，需目标目录写权限 |
| `--root DIR` | 自定义安装目录 |

`--bundle`、`--wheel`、`--all` 互斥；`--user`、`--system`、`--root` 互斥。离线包已包含依赖和校验信息，不与 `--wheelhouse / --checksums` 组合。远端安装要求对应 Release 和制品已发布。

### versions

| 选项 | 含义 |
| --- | --- |
| `--remote` | 同时查询远端已发布版本 |
| `--root DIR` | 指定要查询的安装版本库 |

### bundle create

| 选项 | 含义 |
| --- | --- |
| `--wheel FILE` | 必填，nsim wheel |
| `--output FILE` | 必填，输出离线包 |
| `--wheelhouse DIR` | 本地依赖 wheel 目录 |
| `--download-deps` | 下载依赖 wheel |
| `--platform PLATFORM` | 目标平台 |
| `--python-version VERSION` | 目标 Python 版本 |
| `--implementation IMPL` | 目标 Python 实现，如 `cp` |
| `--abi ABI` | 目标 ABI，如 `cp39` |

跨目标下载时，后四个选项必须成组提供，并同时指定 `--download-deps`。`--output` 是打包文件路径，与仿真的 `--output-root` 不同；此处也支持 `-o`。

### 独立安装器的同名命令

以下形式使用 `python3 nsim-install.py` 或 `nsim-install` 前缀；含义与项目管理入口不同。

| 命令 | 位置参数和全部选项 | 含义 |
| --- | --- | --- |
| `bootstrap` | `--root DIR` | 创建稳定启动器 |
| `update` | `--to VERSION`、`--bundle FILE`、`--sha256 HASH`、`--root DIR` | 安装工具版本；没有来源时列出远端版本；此处 sha256 要求 bundle |
| `switch VERSION` | `--project PATH`（必填）、`--root DIR` | 选择指定版本库中已安装的版本；新布局工程委托给项目管理命令 |

独立安装器还提供上面的 `install`、`versions`、`bundle create`。除 install 默认个人安装这一规则外，安装根按启动器所属目录、`NSIM_HOME`、个人默认目录选择，可用对应命令的 `--root` 覆盖。

## 进一步缩短日常命令

| 较长写法或复杂设置 | 当前支持的简化方式 |
| --- | --- |
| `--output-root PATH` | 执行、重跑和项目配置命令用 `-o PATH`；长期默认值用 `nsim update --output-root PATH` 保存一次 |
| `--caselist FILE...` | `-l FILE...` |
| `--submit FILE...` | `-s NAME...`，文件放在工程 `submit/` 下即可用裸名称 |
| `--seed N...` | `-S N...`；回归随机采样用 `-r N` |
| `--jobs N` | 执行及重跑命令用 `-j N` |
| `--queue QUEUE` | `-q QUEUE` |
| `--project NAME_OR_PATH` | 进入工程或其子目录后省略 |
| `update --nsim-version VERSION` | `switch VERSION` |
| `-mode MODE`、`-dir MODE` | 使用 submit 的模式及默认工作区时均可省略 |
| `--scheduler`、`--resource`、`--license` | 将常用值写进 submit 的 scheduler 配置 |
| 多个 submit 重复的大段配置 | 配置 `submit/common/*.json`，再用 `submit new NAME` 生成继承文件 |

例如 `nsim regr -s rtl -l caselist/smoke.list -S 42 -j 8 -n` 使用固定 seed 预览回归；去掉 `-n` 后执行。

简写定义：[cli_aliases.py](../src/nsim/cli_aliases.py)。实现入口：[cli.py](../src/nsim/cli.py)、[projects.py](../src/nsim/projects.py)、[submit_commands.py](../src/nsim/submit_commands.py)、[nsim-install.py](../nsim-install.py)。

## Regression list 批量入口

`nsim regr -rl/--regr-list FILE` 按行执行完整回归，每行复用单次参数。
外层仅接受项目、输出根和 dry-run 默认值，行内指定 submit/caselist/repeat/seed/jobs/queue 等。
`--plusargs` / `-pa` 支持 run/sim/regr 的最终运行参数覆盖。
回归记录位于 `regr_dir/<id>/report/`，编译与仿真在同级 submit 工作区；批次汇总位于
`regr_batch_dir/<id>/report/`。详见 [批量回归](regression_batches.md)。
