# nsim 使用指南

`nsim` 是安装到用户机器上的 Python 命令行工具，用于配置验证项目、编译、仿真、回归、查看报告与重跑。本指南面向工具使用者；开发仓库 NSIM 的源码、开发环境、测试和发布流程见 [NSIM 开发文档](README.md)。

需要 Python 3.9+，当前支持 POSIX（Linux/macOS）。`nsim-install.py` 提供稳定安装器和启动器，将各版本安装到独立虚拟环境，并通过统一的 `nsim` 命令按项目选择版本。取得安装器和版本制品后即可使用，无需克隆 NSIM 开发仓库；ASIC 工程、运行输出和控制记录由各项目单独管理。

## 阅读入口

日常配置和执行按四个主题阅读；安装、项目注册和完整命令表仍保留在本指南及参考页。

| 主题 | 主文档 | 包含的内容 |
| --- | --- | --- |
| Submit 语法和用法 | [Submit 语法](docs/submit.md) | 公共模板、继承、适配器、文件引用、覆盖率和波形参数组、示例 |
| Caselist 语法和用法 | [Caselist 语法](docs/caselist.md) | `@defaults`、行参数、case.yaml、自定义映射、优先级和预览 |
| 独立单跑 | [独立单跑](docs/single_run.md) | `com/run/sim` 的选项、默认值、目录、submit/CLI 优先级及 `case_dir/com_dir` 重跑 |
| 回归 | [回归流程](docs/regression_workflow.md) | `regr -l/-rl` 的选项、目录、参数优先级，以及 `inspect/release/rerun/recover` |

| 要做的事 | 从这里开始 |
| --- | --- |
| 查全部子命令、选项或常用简写 | [完整命令清单](docs/cli_reference.md)、[常用命令简版](docs/cli_quickstart.md) |
| 第一次使用，机器上还没有 nsim 命令 | [先取得安装器](#第一次使用先取得安装器)、[安装包从哪里获取](#安装包从哪里获取) |
| 安装位置或维护多个工具版本 | [安装器和版本目录](#安装器和版本目录)、[离线安装](#网络受限服务器离线包)、[联网获取](#联网获取与切换) |
| 初始化、登记和维护验证项目 | [项目初始化](#项目初始化与注册)、[查看与更新](#查看与更新当前项目)、[选择版本](#工程如何选择版本) |
| 配置输入、输出和 submit | [路径规则](#配置文件与路径规则)、[默认输出目录](#一次配置输出目录日常运行自动读取)、[Submit 语法](docs/submit.md) |
| 编写逻辑用例、seq/C 测试和自定义运行参数 | [Caselist 语法与示例](docs/caselist.md) |
| 编译与独立单跑 | [独立单跑](docs/single_run.md)、[执行阶段细节](docs/execution_stages.md) |
| 单次与批量回归 | [回归流程](docs/regression_workflow.md)、[批量清单与目录细节](docs/regression_batches.md) |
| 接入工程命令与结果检查 | [执行协议](docs/local_execution.md)、[Hooks 与日志分析](docs/hooks_and_log_analysis.md) |
| 查看进度、报告、重跑与恢复 | [终端 Dashboard](docs/dashboard.md)、[报告与重跑](#检查报告重跑和恢复) |
| 回归结束后如何 inspect、release、rerun、recover | [回归收尾流程与场景示例](docs/post_regression_workflow.md) |
| 预览原生 VCS/DPI/Verdi 命令 | [EDA dry-run](docs/eda_dry_run.md) |

## 当前能力

fake 与工程 command 包装器支持 local/LSF 实际执行，默认调度器为 LSF。原生 VCS/DPI/Verdi 当前提供命令预览，真实 EDA/farm 验收范围见 [开发验证记录](docs/implementation_status.md)。以下版本号和路径是接口示例，应替换为实际取得的制品、已安装版本和工程路径；远端安装要求对应 tag 与制品已发布。


## 第一次使用：先取得安装器

`nsim install` 是**稳定启动器提供的子命令**。新机器还没有 `nsim`，应先取得单文件 `nsim-install.py`，用 Python 执行它；它会创建 `<安装根>/bin/nsim` 和 `<安装根>/bin/nsim-install`。把该 `bin` 目录加入 PATH 后，才可以直接使用 `nsim install`。直接安装 wheel 得到的包内 CLI 面向仿真执行，不提供这里的版本安装管理命令。

安装器有三个明确来源，任选一种：

| 你已有的条件 | 安装器从哪里取得 |
| --- | --- |
| 已取得 NSIM 源码 | 使用仓库根目录的 [nsim-install.py](nsim-install.py)，不需要先安装 Python 包。 |
| 能访问 GitHub 仓库 | 从 [安装器源码页面](https://github.com/dummy11/NSIM/blob/main/nsim-install.py)下载单个文件；也可使用下面的 GitHub CLI 命令，无需克隆整个仓库。 |
| 已收到离线包 | 安装器就在包内，按 [离线安装步骤](#网络受限服务器离线包)用 `tar -xOf` 提取。优先使用与该包一起交付的脚本。 |

从仓库下载单个安装器，需要已安装 GitHub CLI（`gh`），并使用有仓库读取权限的账户登录。在新建的下载目录执行：

```sh
gh auth login
gh api -H 'Accept: application/vnd.github.raw+json' \
  'repos/dummy11/NSIM/contents/nsim-install.py?ref=main' > nsim-install.py
```

下载成功后，可以先只创建个人启动器，确认命令可用：

```sh
python3 nsim-install.py bootstrap --root "$HOME/.local/share/nsim"
export PATH="$HOME/.local/share/nsim/bin:$PATH"
command -v nsim
nsim versions
```

此时尚未导入版本，`nsim versions` 显示 `installed: none` 是正常结果。下一步需要取得 wheel 或离线包，或在 Release 已发布后下载指定版本；仅下载安装器不能运行仿真。`main` 上的脚本随仓库更新，正式交付优先使用发布附件或离线包附带的安装器。

如果已经拿到安装器和离线包，也可直接首次安装，省略单独创建启动器的步骤：

```sh
# 文件名按实际收到的包替换；此时还不需要已有 nsim 命令
python3 nsim-install.py install --bundle ./nsim-offline-0.1.0.tar.gz
export PATH="$HOME/.local/share/nsim/bin:$PATH"
nsim versions
```

注意区分：`python3 nsim-install.py bootstrap` 创建启动器；安装后的 `nsim bootstrap` 初始化验证项目。

## 安装包从哪里获取

默认发布源是 **[dummy11/NSIM 的 GitHub Releases](https://github.com/dummy11/NSIM/releases)**，由安装器中的 `NSIM_RELEASE_REPO` 配置控制。安装器根据参数选择来源：

| 命令 | 实际读取的来源 |
| --- | --- |
| `nsim install 0.1.0` | GitHub Release `v0.1.0` 的 `nsim-0.1.0-*.whl` 与 `SHA256SUMS` 附件。 |
| `nsim install --all` | 同一仓库已发布的稳定 Release，跳过草稿和预发布版。 |
| `nsim install --bundle ./包名.tar.gz` | 本机指定的离线包，包内包含工具、依赖 wheel 和校验信息；不下载 GitHub 制品。 |
| `nsim install --wheel ./nsim-版本-py3-none-any.whl --wheelhouse ./wheelhouse` | 本机 wheel 及依赖目录；不下载 GitHub 制品，也不回退公网包索引。 |

联网模式实际调用 `gh release download v<版本> --repo dummy11/NSIM`，要求 Release 中有且仅有一个匹配的 nsim wheel，并提供 `SHA256SUMS`。只有 Git tag、源码压缩包或仓库源码还不够。工具 wheel 来自该 Release；第三方 Python 依赖由 pip 按当前机器的索引/镜像配置获取。离线模式则从本地提供的依赖 wheel 安装。

**当前发布状态（2026-10-28 核对）：该仓库的 Release 列表为空。** 因此此时不能靠 `nsim install 0.1.0` 或 `--all` 从远端取得工具。应由维护者按 [构建与发布流程](docs/releasing.md)提供与目标 Python/平台兼容的候选 wheel 或离线包，使用 `--wheel` / `--bundle` 安装；当前没有可以在这里给出的已发布包下载链接。不要将仓库内未发布的开发改动视为已经包含在远端版本中。

无需先安装 nsim，即可查询是否已有新发布：

```sh
gh release list --repo dummy11/NSIM
```

如使用其他发布仓库，在执行安装命令的同一 shell 中显式设置 `NSIM_RELEASE_REPO`；例如：

```sh
export NSIM_RELEASE_REPO=OWNER/REPO  # 替换为实际发布仓库
nsim versions --remote
nsim install 0.1.0                  # 替换为该仓库实际已发布的版本
```

## 安装器和版本目录

实际编译、仿真及 hooks 会强制只读访问 `env` 指向的源环境。Linux 执行节点需具备 `bwrap` 和
namespace 支持，macOS 使用 `sandbox-exec`；工具安装成功不代表执行节点已具备这些能力。
缺少保护时拒绝执行，详见 [环境只读保护](docs/read_only_environment.md)。

`nsim install` 默认进行个人安装；安装位置由下面三个互斥选项决定，不能组合使用：

| 选项 | 安装目录 | 使用方式 |
| --- | --- | --- |
| 省略选项或 `--user` | `~/.local/share/nsim` | 当前用户安装，无需 sudo |
| `--system` | `/opt/nsim` | 管理员维护、多个用户共享，需要目标目录写权限 |
| `--root <目录>` | 指定目录 | 自定义安装位置，需要目标目录写权限 |

这三个选项适用于指定 Release 版本、`--all`、`--bundle` 和 `--wheel`。`install` 的默认个人目录不受 `NSIM_HOME` 或当前启动器位置影响：即使调用 `/opt/nsim/bin/nsim install`，省略位置选项也会安装到当前用户的个人目录。已有自定义目录需在后续安装时继续显式指定 `--root`，共享目录需显式指定 `--system`。其他命令仍使用当前启动器所属版本库；从独立脚本调用其他管理命令时，原有 `NSIM_HOME` / `--root` 规则保留。

两种安装都使用相同目录结构，每个版本有独立的 Python venv：

```text
<root>/bin/nsim                 # 稳定启动器
<root>/bin/nsim-install         # 同一管理程序
<root>/versions/0.1.0/venv/    # 该版本的 Python 环境
<root>/versions/0.1.0/receipt.json
<root>/versions/0.2.0/venv/
```

`nsim bootstrap` 始终用于项目注册，`nsim update` 始终用于项目配置；安装工具使用 `nsim install`。首次安装时，从可信 Release、离线包或 NSIM 源码取得单文件安装器 `nsim-install.py`，在保存该脚本的目录执行 `python3 nsim-install.py install ...`，会同时创建稳定启动器和版本环境。把 `<root>/bin` 加入 shell 的 `PATH` 一次；之后直接使用 `nsim`。目标机器需要 Python 3.9+ 和可创建 `venv`/运行 pip 的 Python 安装。当前启动器面向 POSIX Linux/macOS。

### 个人用户安装

以已取得的离线包为例，个人安装使用自己的账户执行：

```sh
# 默认个人安装；添加 --user 的效果相同
python3 nsim-install.py install --bundle ./nsim-offline-0.1.0.tar.gz
export PATH="$HOME/.local/share/nsim/bin:$PATH"
nsim versions
# 后续导入新版本，不改变已有项目选择的版本
nsim install --user --bundle ./nsim-offline-0.2.0.tar.gz
```

将上面的 PATH 设置加入自己的 shell 配置文件（如 Bash 的 `~/.bashrc` 或 Zsh 的 `~/.zshrc`），并在当前终端重新加载。需要在尚未准备版本包时只创建启动器，可继续使用独立脚本的 `bootstrap --root`：

```sh
python3 nsim-install.py bootstrap --root "$HOME/.local/share/nsim"
export PATH="$HOME/.local/share/nsim/bin:$PATH"
nsim versions
```

### 系统共享安装

管理员使用所有用户都可访问的 Python 3.9+ 安装。下面的 `python3` 应解析到系统共享的解释器；启动器会记录其绝对路径。解释器若位于管理员的私有目录，其他用户可能无法执行。

```sh
sudo -H python3 nsim-install.py install --system --bundle ./nsim-offline-0.1.0.tar.gz

# 普通用户使用；项目注册和仿真用自己的账户执行
export PATH="/opt/nsim/bin:$PATH"
nsim versions

# 管理员后续维护共享版本库时仍显式指定 --system
sudo -H /opt/nsim/bin/nsim install --system --bundle ./nsim-offline-0.2.0.tar.gz
```

管理员应配置允许其他用户读取文件、遍历目录和执行程序的权限（例如安装时使用 `umask 022`），并将共享目录的写权限保留给维护人员。安装器不自动执行 sudo、不修复已有目录的属主/权限，也不修改 shell 启动文件；无写权限时返回错误，不会自动改装到个人目录。管理员已经有目标目录写权限时，可以不使用 sudo。

需要所有用户默认使用共享安装时，由管理员在系统 shell 配置中加入 `export PATH="/opt/nsim/bin:$PATH"`。Linux 上读取 `/etc/profile.d/` 的登录 shell 可使用 `/etc/profile.d/nsim.sh`；其他 shell 和作业环境按站点配置加载。联网安装还需要执行安装命令的管理员账户能够访问 GitHub Release 和依赖源，离线包导入不要求 GitHub 登录。

共享的是工具和版本环境。项目注册表仍位于各用户自己的 `${XDG_CONFIG_HOME:-~/.config}/nsim/projects.json`；项目版本文件和仿真输出由执行该操作的用户按工程配置管理。

### 自定义目录与两种安装共存

```sh
python3 nsim-install.py install --root /work/tools/nsim --bundle ./nsim-offline-0.1.0.tar.gz
export PATH="/work/tools/nsim/bin:$PATH"
```

自定义安装不与 `--user` 或 `--system` 组合。个人与系统安装可以并存，运行时各自只查找自己的版本库，不自动合并或回退到另一处。PATH 中排在前面的启动器生效；可用绝对路径确认：

```sh
command -v nsim
"$HOME/.local/share/nsim/bin/nsim" versions
/opt/nsim/bin/nsim versions
```

从共享启动器执行默认个人安装后，需要把个人 `bin` 放到 PATH 前面，或直接使用个人启动器，才能使用新安装的版本。

## 联网获取与切换

以下步骤仅适用于对应 Release 和制品已经发布。先查询实际可用版本；首次安装时可以直接从同一 Release 下载安装器，再由它安装工具。机器需要 Python 3.9+、已登录且有仓库读取权限的 GitHub CLI，以及可用的 pip 依赖源：

```sh
gh release list --repo dummy11/NSIM
# 将 0.1.0 替换为列表中实际发布的版本，在空的下载目录执行
gh release download v0.1.0 --repo dummy11/NSIM --pattern nsim-install.py
python3 nsim-install.py install 0.1.0
export PATH="$HOME/.local/share/nsim/bin:$PATH"
nsim versions
```

以上流程要求发布者上传 `nsim-install.py` 附件；若未附带安装器，先按 [首次使用步骤](#第一次使用先取得安装器)取得脚本。已有稳定 `nsim` 命令后，后续安装和切换使用：

```sh
nsim install 0.1.0                     # 默认个人安装
nsim install --user --all              # 所有已发布的稳定版，安装到个人目录
nsim versions --remote
nsim install 0.2.0                     # 下载/安装，不切换工程
nsim switch 0.1.0 --project ./project-a
# 使用工程已准备的 submit 和默认输出根
nsim run --project ./project-a -sub local --case smoke -seed 42 -scheduler local
```

发布源、文件要求和当前发布状态见 [安装包从哪里获取](#安装包从哪里获取)。不同版本只装入各自目录，不覆盖已安装版本；`install` 不修改项目版本文件。不要用 `pip install nsim` 替代上述流程：该命令会按 pip 索引中的同名包安装，无法保证获取本项目的制品，也不会创建这里的稳定版本管理入口。

## 网络受限服务器：离线包

取得与目标 Python/平台兼容的离线包及其 `.sha256` 文件。离线包包含安装器、主 wheel、依赖 wheel 和逐文件哈希清单；维护者制作离线包的方法见 [构建与发布](docs/releasing.md)。

通过受控渠道把离线包及其 `.sha256` 文件传到服务器；先核对外部 SHA-256，再安装：

```sh
sha256sum -c nsim-offline-0.1.0.tar.gz.sha256  # Linux；macOS 用 shasum -a 256 -c
tar -xOf nsim-offline-0.1.0.tar.gz nsim-install.py > nsim-install.py
python3 nsim-install.py install --user --bundle nsim-offline-0.1.0.tar.gz
export PATH="$HOME/.local/share/nsim/bin:$PATH"
nsim switch 0.1.0 --project ./project-a
```

安装器先核对包内 wheel 哈希，然后使用 pip 的 `--no-index --find-links` 从包内安装，不回退到公网索引。离线导入新版本使用 `nsim install --bundle <新离线包>`（默认个人安装），共享安装添加 `--system`；它只安装版本，仍需 `nsim switch` 才改变工程版本。每个离线包对应一个 `nsim` 版本；需要多个版本时，可向同一安装位置连续导入多个包。纯手工下载 wheel 时，也可以执行 `nsim install --user --wheel <wheel> --wheelhouse <依赖目录> --checksums SHA256SUMS`，或用 `--system` / `--root <目录>` 替换 `--user`。

## 项目初始化与注册

`nsim bootstrap` 初始化并注册验证项目。RTL 与 verification 可以分属不同目录和 Git 仓库，不需要登记 RTL commit。第一次使用前须安装至少一个支持 schema 2 的 nsim 版本；克隆源码或创建稳定启动器本身不等于安装了工具版本。

```sh
nsim bootstrap --name conv_verif \
  --rtl-root /work/chip/rtl \
  --rtl-filelist /work/chip/rtl/filelist/rtl.f

# 接入已有 verification 目录，并指定已有的 TB filelist
nsim bootstrap --verification-root /work/chip/conv_verif \
  --rtl-root /work/chip/rtl \
  --rtl-filelist /work/chip/rtl/filelist/rtl.f \
  --verification-filelist /work/chip/conv_verif/filelist/tb.f \
  --nsim-version 0.1.0
```

示例路径需替换为真实路径。新项目的 `--rtl-root`、`--rtl-filelist` 必填；`--name` 默认 `verification`，在当前目录下创建同名目录。提供 `--verification-root` 时，项目名取规范化后的目录名；同时提供的 `--name` 必须一致。所有命令行相对路径均相对于调用命令的当前目录，不相对于另一个参数。

```text
conv_verif/
├── .nsim/
│   ├── nsim.toml
│   └── .nsim_version
├── submit/
│   ├── common/
│   │   ├── rtl.json
│   │   ├── gate.json
│   │   ├── fpga.json
│   │   ├── haps.json
│   │   ├── zebu.json
│   │   └── palladium.json
│   ├── submit_demo.json
│   ├── submit_demo.md
│   ├── demo_inputs/          # coverage/Xprop/partition/UCLI/数据/分析规则
│   └── examples/             # VCS 全字段、command、fake 参考
├── caselist/
│   ├── 00_legacy.caselist
│   ├── 01_uvm.caselist
│   ├── 02_seq.caselist
│   ├── 03_c_test.caselist
│   ├── 04_plusargs.caselist
│   ├── 05_mixed.caselist
│   ├── 06_mappings.caselist
│   ├── 07_overrides.caselist
│   ├── case.yaml
│   └── README.md
└── filelist/
    ├── tb.f
    └── sim.f
```

未提供 verification filelist 时，创建带注释的 `filelist/tb.f` 模板。默认按 mode 生成公共 submit；`submit_demo.json` 通过 `extends: "common/rtl.json"` 继承 RTL 模板。每个父模板声明自己的 mode，命令行 `-mode` 只校验匹配，不动态更换父模板。rtl/gate 使用 VCS 预览模板；其他模式使用待填写真实 argv 的 command 模板。Demo 配套 `submit_demo.md` 覆盖完整语法、其他适配器、hooks 与覆盖规则，仓库副本见 [Submit 语法](docs/submit.md)。已有的 `tb.f` 和 submit 文件保持原内容；已注册项目重复 bootstrap 不回填模板。指定外部 verification filelist 时，保留并直接登记该文件。空 TB 清单显示待填写，VCS 规划会拒绝空清单。

新项目还生成 `caselist/` 的 8 个 demo、可选 `case.yaml` 和语法说明，已有同名文件保持原内容。
`case` 始终为逻辑用例名；`uvm_test`、`seq`、`c_test` 通过内置或自定义映射生成仿真参数，
也可直接写 `逻辑用例名 +KEY=VALUE +FLAG`。使用 `seq`，不接受 `sequence` 字段。
参数优先级为 submit → 清单默认值 → 当前行 → 显式引用的 YAML 用例覆盖。
三个入口字段可以缺省或用 null 清除；工程负责实际启动方式。
完整示例、兼容规则和运行限制见 [Caselist 语法](docs/caselist.md)。

`filelist/sim.f` 是带所有权标记的 nsim 组合来源说明，不是直接传给 VCS 的原生 `-f` 文件。每次规划分别按 RTL 和 verification 根解析登记的列表，保留嵌套 `-f/-F` 语义，实际编译 token 和 argv 固化到本次 `plan.json`、`commands.json`。不要手工编辑工具维护的 `sim.f`；同名用户文件不会被覆盖。schema 2 的 VCS submit 省略 `vcs.filelist`，也可显式填 `filelist/sim.f`；其他覆盖路径被拒绝，以免绕过登记的两套输入。

## 已有 .nsim、注册冲突与项目发现

`bootstrap` 写入前检查当前目录及其祖先最近的 `.nsim`，或者显式指定的目标。已有项目中的重复 bootstrap 返回已注册，不再创建一层 `verification/`，也不覆盖配置。参数不同应改用 `nsim update`。有效配置未登记到本机时，可以通过 bootstrap 补登记；配置损坏、缺失、`.nsim` 不是目录、新旧布局冲突时明确报错。

项目名在当前用户的本机索引内唯一。同名不同路径拒绝注册，同路径同名称可重复登记；符号链接按规范路径处理。重名检查先于目标文件创建。默认名称 `verification` 也不例外，不会自动追加编号。嵌套项目初始化被拒绝；在已有项目中显式指定项目外的新目录仍可注册另一个项目。

索引默认位于 `~/.config/nsim/projects.json`，支持 `XDG_CONFIG_HOME`。索引保存名称、verification 路径和配置入口，完整配置以 `.nsim/nsim.toml` 为准。失效登记保留供诊断，不自动删除或释放名称。登记修改由索引锁和目录锁保护；中断事务留有恢复记录，下次 bootstrap/update 在继续前回滚未完成写入。遇到用户在中断后另行修改相关文件，或将管理目录替换成符号链接时停止恢复，避免覆盖该修改或写入其他目录。损坏的恢复记录会明确报错，不执行恢复写入。

## 查看与更新当前项目

```sh
nsim list
nsim list --json
cd /work/chip/conv_verif/submit
nsim status
nsim status --json
nsim status --project conv_verif

nsim update --rtl-root /work/rtl_new --rtl-filelist /work/rtl_new/rtl.f
nsim update --verification-filelist /work/chip/conv_verif/filelist/new_tb.f
nsim update --project conv_verif --nsim-version 0.1.0
```

`list` 查询当前用户所有登记，不按当前目录过滤。`status`、`update`、`switch` 从当前目录向上识别最近项目，支持子目录（包括 `.nsim/` 及其子目录）；`--project` 可指定注册名称或路径，优先于当前目录。若名称与相对目录有歧义，用 `./目录` 明确选择路径。最近的 `.nsim` 损坏时停止，不跳过它误操作外层项目。

`status` 只查询，能显示未注册、目录失效、清单为空、版本未安装等问题；单个项目无法访问会显示独立的问题行，不中断其他项目的列表查询；配置有效不代表仿真通过。`update` 和 `switch` 必须针对已注册的 schema 2 项目。只更新传入字段，全部校验后一起保存；失败保留原配置，并展示成功更新前后的值。支持 `--output-root` 修改默认输出目录。只改 `rtl-root` 不会自动移动 `rtl-filelist`。无参数 update 报用法，不升级版本。普通 update 不搬移或重命名 verification 目录。

即使项目指定的工具版本缺失，稳定入口也可选择其他兼容管理版本执行查询。所有管理版本都不可用时，`list/status` 仍提供基本登记、路径和版本诊断，并明确标记完整配置校验不可用；Python 3.9/3.10 无 TOML 解析器时基本诊断不解析全部 TOML 字段。实际执行仍必须使用项目指定的版本。

## 工程如何选择版本

新 bootstrap 的 `--nsim-version` 默认 `latest`：按版本号选择本机已安装、可运行且支持该项目格式的最新稳定版本；跳过预发布版本和不支持管理能力的版本。显式版本允许预发布，但仍需已安装并通过能力检查。bootstrap 不隐式联网下载安装。

初始化后将解析出的具体版本写入 `.nsim/.nsim_version`。已有项目重复 bootstrap 省略版本参数时保留原版本；显式版本不同时提示切换。安装新版本不会自动升级项目。

```sh
nsim switch 0.2.0                    # 当前项目，等价于下一条
nsim update --nsim-version 0.2.0
nsim switch latest --project conv_verif
```

以上版本号是接口示例，须先安装实际可用版本。切换要求目标版本支持项目格式，并与其他更新共用校验及事务逻辑。可把 `.nsim/` 内两份配置提交到 verification 仓库；本机索引不需要提交。

稳定启动器读取 `--project` 或当前目录所属项目的版本文件。`rerun` 根据来源记录定位工程，使用该工程当前版本 pin；无 pin 时使用记录版本。`recover` 和 `report` 使用原控制记录的版本。没有项目上下文时，普通执行命令在只安装一个版本时使用该版本，否则可设置 `NSIM_VERSION` 明确选择。

## 旧项目兼容

根目录中的 schema 1 `nsim.toml/nsim.yaml`、`.nsim_version` 继续支持读取和历史运行；schema 1 的路径仍相对描述文件解析，不改变旧语义。新项目只生成 `.nsim/` schema 2。新旧配置同时存在时报冲突，不自动搬移或转换。旧项目可继续通过安装器入口 `python3 nsim-install.py switch VERSION --project PATH` 切换；新的注册和 update 要求显式迁移到 schema 2。

原 `nsim project list <目录>` 保留为文件扫描，扫描不自动登记；日常登记查询使用 `nsim list/status`。

## 配置文件与路径规则

```toml
# conv_verif/.nsim/nsim.toml
schema_version = 2
name = "conv_verif"
rtl_root = "../rtl"
rtl_filelist = "../rtl/filelist/rtl.f"
verification_filelist = "filelist/tb.f"
inputs = []
# 可选：保存后，日常 run 无需重复指定输出位置
output_roots = ["/data/nsim-output", "/backup/nsim-output"]
```

配置中的所有相对路径以 verification 根目录为基准。`inputs` 是额外执行输入的文件列表，例如模型、脚本、数据或预构建二进制。工具版本只写入 `.nsim/.nsim_version`，内容是一行具体版本及换行，不能写 `latest`；不要在 TOML 中加入 `requires_nsim` 或重复版本字段。

## 一次配置输出目录，日常运行自动读取

新项目可在 bootstrap 时保存默认输出根目录，无需维护环境变量或独立记录目录：

```sh
nsim bootstrap --verification-root /work/chip/conv_verif \
  --rtl-root /work/chip/rtl \
  --rtl-filelist /work/chip/rtl/filelist/rtl.f \
  --output-root /data/nsim-output
```

已有项目设置或修改默认目录：

```sh
nsim update --project conv_verif \
  --output-root /data/nsim-output
nsim status --project conv_verif

# 用例和 submit 已准备好时，从任意目录运行
nsim run --project conv_verif \
  --caselist /work/chip/conv_verif/smoke.caselist \
  --submit /work/chip/conv_verif/submit/local.json \
  --seed 42 --jobs 4 -scheduler local
```

`--output-root` 可重复提供，按顺序保存主输出及备用目录。update 中提供该参数会替换整个输出目录列表；未提供的字段保持原值。CLI 相对路径按调用时所在目录解析，保存后的配置路径按 verification 根解析，因此后续切换目录不影响运行位置。`status` 显示解析后的默认目录；删除配置中的可选字段可取消对应默认值。

输出位置采用 **run 命令行参数 > 项目默认配置 > NSIM_OUTPUT_ROOT** 的优先级。环境变量仅为已有脚本保留兼容；新项目可只使用项目配置。没有任何可用设置时，run 会提示通过 update 保存默认目录。schema 1 项目仍使用运行参数或兼容输出环境变量。

bootstrap/update 只保存目录设置，不创建或探测这些输出根。运行前需要准备好已挂载、可写的目录；run 会检查可用性与目录隔离，拒绝输出和源码/工具目录互相嵌套。`status` 显示保存值不代表存储已通过运行预检。相同 submit、工作区、case、seed 的运行复用固定目录，新运行前清理旧日志、结果和波形。rerun 在来源回归下独立创建重跑目录，输出位置不随 update 改变，且不接受输出根覆盖；整轮 recover 默认沿用历史输出设置，需要换根时可传 --output-root；单跑 recover 与 rerun 一样固定原输出根。

## Submit 公共模板与继承

日常先用命令生成具体 submit，自动填好名称、版本和继承路径，再编辑需要改变的参数：

```sh
nsim submit new rtl_debug
nsim submit new gate_debug --mode gate
nsim submit show rtl_debug
nsim submit check rtl_debug
```

`new` 默认选择 RTL，也支持 `--mode` 或自定义 `--extends`。缺少默认 common 的旧项目会补齐所选模板；已有文件不覆盖。公共工具参数、队列和平台命令由工程维护者配置一次，子 submit 只保留差异。未配置真实 `command.argv` 的硬件模板会明确提示待填写。`show` 展示合并结果与来源，`check` 检查字段和继承关系；两者不启动工具或创建运行输出。工程输入和可执行工具用 `run --dry-run` 进一步检查。以上命令都支持 `--project`。

生成的具体 submit 声明自己的 `schema_version` 和 `name`。公共模板没有 `name`，不能直接作为 `-sub` 的执行目标。例如在 `submit/rtl_debug.json` 中保留公共编译配置并覆盖仿真参数：

```json
{
  "schema_version": 1,
  "name": "rtl_debug",
  "extends": "common/rtl.json",
  "vcs": {"run_args": ["+UVM_VERBOSITY=UVM_HIGH"]}
}
```

`extends` 相对声明它的 JSON 所在目录解析，只允许一层、一个父文件。对象递归合并；数组整体替换；每个 hook 阶段也整体替换，`[]` 可清空该阶段。空对象不会清空父对象，`null` 不是通用删除操作，但 `files.NAME: null` 可删除继承的文件条目。合并完成后再校验配置，命令行调度选项覆盖合并后的值。

辅助输入可在 submit 的 `files` 表登记，使用 `{file:名称}` 在参数、hooks 和用例值中引用。path、dependencies 相对工程根；phases 区分 compile、simulate、analysis。目录和 glob 追踪成员集合；内容摘要、受限小文件快照及各阶段输入进入计划。运行专用文件变化后，新计划仍可复用兼容编译；整轮回归 recover 会拒绝历史输入变化；整轮 rerun 重新规划当前输入并强制编译，单跑 rerun/recover 默认校验复用编译。`error_filter` 支持原来的 submit 相对路径，也支持 analysis 文件引用。`submit show/check` 检查显式文件并展示解析路径。

新 bootstrap 的 `submit_demo.json` 显式展示完整文件管理用法，配套 `demo_inputs/` 教学文件可用于命令预览；DUT 层次、YOUR_* 和工具参数仍需按工程修改。`examples/vcs_full.json` 展示全部可选 VCS/调度/hooks/DPI 字段，command 与 fake 示例独立保存。已有文件及已注册项目保持不变。日常 `submit new` 继续生成最小差异配置。

填写真实 RTL/TB 清单并准备输出根后，可在项目内预览：

```sh
nsim run --case smoke -sub rtl_debug -o /data/nsim-output --dry-run
```

运行时可省略 `-mode`，自动使用有效 submit 的 mode，默认工作区也随之确定；显式指定时必须匹配。一次调用的多个 submit 必须使用同一 mode。原生 VCS 模板当前仅用于命令预览；实际运行使用工程 command 包装器或 fake 适配器。父子配置原文和哈希都会进入计划，整轮回归 recover 要求父子配置匹配历史记录；整轮 rerun 读取当前配置并强制编译；单跑 rerun/recover 默认校验复用编译。带斜线的相对可执行路径以声明它的 JSON 目录为基准；普通参数不会自动重定位，可使用 `{project}`。完整字段、占位符及编译身份规则见 [Submit 语法](docs/submit.md)。

## 编译、仿真与回归目录

编译目录和每个 case/seed 仿真目录自动提供 `env -> 验证工程根目录`，可用 `env/...` 访问完整工程环境。无需为内部资源目录逐项配置链接或编写 hook，规则见 [仿真环境根目录链接](docs/execution_stages.md#仿真环境根目录链接)。

执行命令是 `nsim com`、`nsim sim`、`nsim run` 和 `nsim regr`；分别负责编译、复用编译进行仿真、编译后仿真，以及批量回归。`--case` 选择单跑；`regr -l a.list b.list -sub submit_0 submit_1 -seed 42 43` 选择回归组合，按 submit/case/seed 去重。使用 `-repeat N` 可生成 N 个不同随机 seed，与显式 -seed 互斥。四个入口都接受 `-mode`、`-dir`、`-com-dir`、`-sim-dir`、`-o`，完整示例和编译 submit 格式见 [编译、仿真与工作区](docs/execution_stages.md)。

```text
output/
├── submit_name/<workspace>/          # 单跑
│   ├── com_dir/
│   └── sim_dir/<case>_<seed>/
├── regr_dir/<regression-id>/         # 每次回归独立
│   ├── report/                      # plan/state/live/report/final 与 records
│   └── submit_name/<workspace>/
│       ├── com_dir/
│       └── sim_dir/<case>_<seed>/
├── preview_dir/<preview-id>/report/  # 单跑 dry-run 预览
└── regr_batch_dir/<batch-id>/report/ # 批次清单、状态、Dashboard 和总报告
```

使用 `nsim regr -rl regression/nightly.list` 批量执行，每行是一组单次 regr 参数。
支持多个 submit/caselist、独立 repeat/seed、LSF 配置及最终 `--plusargs` 覆盖。
完整示例、参数互通、独立目录与重跑规则见 [单次与批量回归](docs/regression_batches.md)。


`submit_name` 来自 submit 的 `name`，保留名 `regr_dir` 不可用，同批 submit 名不能仅大小写不同。模式支持 rtl、gate、fpga、haps、zebu、palladium，必须匹配 submit.mode；后仿真参数为 `-mode gate`。工作区名与模式解耦，例如 `-mode gate -dir issue_1234`。

`-com-dir` 是相对工作区的编译目录路径或绝对路径，不是工作区名。例如 `-dir issue_1234 -com-dir ../rtl/com_dir` 复用 rtl 工作区下的编译，同时把仿真结果输出到 issue_1234。回归的目录覆盖必须保留在本次回归所属 submit 目录内；-sim-dir 同样按工作区解析，下面按 case_seed 分目录。-o 是 --output-root 的短写，可放在子命令前后，只影响本次运行。

sim_dir 直接按 case 名与 seed 分组；同一组合覆盖旧产物，不创建运行 ID 或 attempt 子目录。编译身份保存在 build.json，不再创建 build-id 子目录。com/run 重建编译，sim 只读取成功且兼容的已有编译。编译持有独占锁，运行期间持有编译共享锁；活动仿真目录拒绝覆盖。

`run/sim --case` 可列多个显式 case，`-sub` 可列多个 submit，`-seed` 可列多个 seed；每个组合都是独立单跑，分别把计划、终态及完成校验保存在自己的仿真目录，不生成实时 HTML 或回归汇总。`run` 在同一次调用内对每个 submit 编译一次；`sim` 直接复用已有编译。多组合命令返回 `runs` 列表，其中逐项给出 `record_dir` 和结果。单个 com 的阶段记录在编译目录。caselist 只通过 `regr -l` 执行；回归记录和产物隔离在 regr_dir/<id>/，汇总文件位于 report/。

`report`、`rerun`、`recover` 接受命令返回的 record_dir，也接受回归中 sim_dir 下具体的 case_dir；后者通过校验过的 run.json 选择单个任务。单跑 rerun/recover 固定 case/seed，默认校验复用原编译，显式 --rebuild 才重新编译；整轮 rerun 强制重新编译。结果存入原回归下独立的 rerun_<时间_UUID>/，保留原产物和报告。单跑来源则在原工作区的同名规则目录下保存独立结果。

独立 --event-root 参数和 NSIM_EVENT_ROOT 已取消；旧 event-root 目录不迁移、不删除。report 和整轮 recover 由启动器选择历史版本；rerun 与单跑 recover 选择来源工程当前版本 pin，读取当前输入并重新建立计划。

普通单跑 run/sim 会覆盖相同目录中的旧结果；回归和 rerun 使用独立目录保留产物。report 将丢失或损坏的通过证据标记为 INCOMPLETE。溯源引用不能替代目录备份；删除来源回归目录也会删除其 rerun 子目录。

`--dry-run` 不写编译成功记录，也不清理编译或仿真目录；单跑预览记录放在 `preview_dir/<preview-id>/report/`，回归预览放在 `regr_dir/<preview-id>/report/`；实际单跑记录位于对应 case_dir，回归记录位于本次独立目录。

运行记录与 output 共享存储。启动时在首个健康输出根建立记录；该记录位置在执行中不可写时停止并返回错误，不伪造完成记录，不承诺整个输出盘丢失后的自动恢复。

回归的 `final.json` 最后发布，绑定计划、状态和成功发布报告的哈希；单跑将完整记录合入 `run.json`，只有其中 terminal=true 且校验通过才视为已完成。`report` 会重新校验证据；终端进度、`live.json` 和最终 HTML 本身不能替代这次校验。

## LSF 与本地运行

未配置 scheduler 时默认 LSF，支持 `-scheduler local` 切换本地。`com/sim/run/regr` 都接受 `-jobs N`（默认 1）和 `-queue 队列名`；命令行覆盖 submit 中的调度配置。`run/sim` 多组合时，同一 submit 下逐个执行，不同 submit 最多并行 N 个；`regr` 对所有任务使用统一的 N 个并发窗口。队列不设置时由集群选择默认队列。

```sh
nsim regr --project conv_verif -l /work/chip/conv_verif/smoke.caselist \
  -sub rtl rtl_trace -repeat 10 -jobs 8 -queue simulation
nsim run --project conv_verif -sub rtl --case smoke -seed 42 -scheduler local
```

N 限制整次调用已提交、尚未结束的作业，包含排队和暂停。任务完成回调触发补交，直到处理完全部任务；它不限制总提交次数。实际执行通过 bsub 提交、bjobs 查询和 bkill 取消；未知状态或取消未确认时保留占用、不自动重投。安装和输出必须在执行节点以相同路径可见。用 `nsim lsf inspect CASE_OR_COM_OR_REGR_DIR` 查看未解决占用，`--check-lsf` 查询当前集群；用 `nsim lsf release DIR --token TOKEN` 在核对身份和终态后只释放一个 job。释放不重启任务、不改原 case 结论。独立回归的 case 目录按 `--jobs` 派发进度逐个创建。详细 submit 配置、运行预算、状态记录和异常处理见 [LSF 执行](docs/lsf_execution.md)。原生 adapter=vcs 仍仅预览，实际工程使用 command 包装器。

## Hooks 与日志分析

submit 的 hooks 支持 pre_compile、post_compile、pre_sim、post_sim；每个阶段可写单个对象或有序数组，按声明顺序执行。默认在所有用户 post_sim hooks 后执行内置 VCS/UVM 分析；自定义名为 log_analysis 的 hook 必须在 post_sim 数组末尾，用来替换内置分析器。

日志分析在原仿真 LSF job 内进行，也适用于 local；不会每个 case 再提交一个分析作业。先等仿真和用户后置 hooks 结束，再检查日志稳定性。每个 case 的 `run.json.analysis` 保存判定、简短原因、首错位置、计数和扫描完整性，详细诊断查看原始日志；共享编译分析保存在 `com_dir/log_analysis.json`。回归按完成事件更新终端 Dashboard。完整配置和协议见 [Hooks 与日志分析](docs/hooks_and_log_analysis.md)，进度、输出重定向和最终 HTML 见 [终端 Dashboard](docs/dashboard.md)。

## 接入工程命令

工程命令接收 `{case}`、`{seed}`、`{result}`、`{project}`、`{wave_dir}`、`{build_dir}` 等参数，并在 `result` 路径写入版本化 JSON。参数通过 argv 传递，不经过 shell。项目特定 Golden 或性能公式由工程包装器判断，`nsim` 汇总必需检查。完整格式见 [本地接口](docs/local_execution.md)。

`nsim` 本身不执行 git pull。com/run 重建所选编译目录，sim/run 清理并覆盖选中的 case/seed 目录，回归批次记录仍保留。工程包装器应遵守只读输入和指定输出目录契约；任意第三方命令的内部副作用并非由 `nsim` 沙箱隔离。

## 检查报告、重跑和恢复

回归启动后，nsim 主进程直接在终端显示 Dashboard。case 的 post_sim 分析与执行记录保存后立即更新计数、错误位置和失败原因；LSF 状态变化也会更新面板。交互终端原地重绘，重定向时向 stderr 追加普通文本，stdout 保留最终 JSON。无需轮询页面、HTTP 服务或额外 LSF job；HTML 为最终静态报告。详见 [终端 Dashboard](docs/dashboard.md)。

命令最终返回 JSON，其中 `record_dir` 是本次运行或回归记录目录；`journal` 保留为同值兼容字段。将下面路径替换为实际返回的 `record_dir`，也可指定回归中某个 `sim_dir/<运行目录>`：

```sh
nsim report /tmp/nsim-output/regr_dir/<regression-id>
nsim report /tmp/nsim-output/regr_dir/<regression-id> --json
nsim report /tmp/nsim-output/regr_dir/<regression-id> --rebuild
nsim rerun /tmp/nsim-output/regr_dir/<regression-id>
nsim recover /tmp/nsim-output/regr_dir/<regression-id> --output-root /tmp/nsim-backup
nsim storage check /tmp/nsim-output --min-free-bytes 104857600
```

`report` 无二级 `show` 命令。传入单个 case 的仿真目录时，它在终端打印已保存的结果摘要；传入回归或批次目录时，先校验完成记录、HTML 和结果证据。完整报告会显示 PASS/FAIL 及 `report.html` 的绝对路径和浏览器 `file://` 地址；回归 FAIL 仍可有完整报告。报告或证据不完整时默认只显示简短原因。`--json` 输出机器可读的状态、`report_complete` 和问题列表。

原回归的 `--rebuild` 汇总原结果与最新一次 rerun、recover，生成新的 `report_<时间_UUID>/`，不修改原 `final.json`、`report.html` 或任务记录；该新报告不属于原完成校验。`-dir NAME` 可指定新目录名，`--include PATH` 可指定来源不在原目录下或自动判定有歧义的 rerun/recover。计划、状态或完成记录损坏时不能重建。批次目录仍按旧规则生成 `report.rebuilt.html`。`report` 从不提交、恢复或重启任务；需要重新执行时使用 `rerun` 或 `recover`。`report` 的退出码：完整且通过为 0，完整但任务失败为 1，报告或证据不完整为 2。

单 case 的 `rerun CASE_DIR` 与 `recover CASE_DIR` 完全等价，默认校验复用编译，显式 `--rebuild` 才重新编译；`rerun COM_DIR` 与 `recover COM_DIR` 都只重新编译。case_dir 是 sim_dir 下的具体 case 目录，sim_dir 父目录不作此入口。原产物保留，新结果使用独立目录；两者都读取当前输入；下文区分 rerun/recover 的规则适用于整轮回归，批量回归也支持按记录逐行重跑。详见 [按目录重跑](docs/rerun.md)。

`rerun` 固定来源记录中完整执行但结果失败任务的 case、seed 和 submit 选择，重新读取当前工程登记、源码、submit、caselist/YAML 与输入文件。每个 submit 强制编译一次，再完整执行对应失败 case，包含原先以 sim 启动的任务；没有 compile 步骤的 command submit 会报错。每次生成独立的 rerun_<时间_UUID>/、report/ 与 parent 引用，不覆盖历史结果。禁止 -seed/--seed/-S 和 -o/--output-root 覆盖，可用 --jobs 调整并发，以 -dir 指定新目录名。详细目录、边界及例子见 [重跑、恢复与报告](docs/replay_reports.md)。

整轮回归的 `recover` 使用历史输入和版本，仅选择没有形成完整有效结果的任务；在独立目录强制重新编译、仿真和检查，再生成本次报告。即使来源为 sim 也不复用旧编译产物；需要重编译但没有 compile 步骤的 command submit 会明确报错。保留原有输出根与 seed 覆盖入口；使用新 seed 的结果不替代原 seed 的汇总结论。

rerun 为当前输入建立新的 hash 和配置快照，不要求与历史输入相同；本次执行前后仍检查输入一致性。整轮回归 recover 要求历史输入和运行时可验证。两者都拒绝损坏的控制记录和未确认终态的来源，不自动重叠派发状态未知的旧任务。

自动故障转移和基础设施重试已停用；`--retries`、`--retry-seconds` 只保留兼容入口，
必须为 0。每次执行选择一个输出根，出现存储或作业异常时保留证据，之后按来源记录
使用 `rerun/recover` 或新建运行；具体边界见 [回归流程](docs/regression_workflow.md)。

## 历史回归与身份

每次执行记录 RTL 和 verification 的 Git 身份、实际声明输入的 hash、配置快照，以及工具、Python、依赖和安装 wheel 身份。rerun 读取当前工程配置重新建立这些记录，保留原任务的 case/seed 及 parent 关联；启动器使用来源工程当前版本 pin，未安装时明确报错。整轮回归 recover 校验历史输入和运行时，schema 2 使用历史注册快照，schema 1 继续校验原描述文件。NSIM 不自动归档或恢复源码。

## 存储检查与能力边界

可检测已返回的 I/O 错误、空间/inode 阈值和路径不可用；配额无法查询时标记 unknown，实际 EDQUOT 写入错误会被记录。目录预检会做一次小文件写入/readback/fsync，并在进程结束后复检。

运行记录与仿真产物共用 output 存储。每次运行选择一个输出根；单 case 目录或记录错误只影响该 case，整批状态无法可靠保存时才停止新派发，不发布完成标记。nsim 不自动诊断磁盘损坏、隔离或切换目录；改用其他输出根需显式发起新运行。不能保证整个输出盘丢失后恢复。不同目录不等于不同物理磁盘。若内核 I/O 陷入不可中断等待，应用层重试预算不能保证终止该系统调用。断电持久性、备份恢复、修盘及仿真断点续跑不在当前能力内。

### case 记录合并

单跑目录中的运行元数据统一为 schema 2 的 run.json：plan 是运行计划，task.attempts 保存执行历史，analysis 是结果摘要，state/terminal/checksums 保存整体状态与完整性校验。单跑不再生成 plan.json、state.json、final.json、analysis.json、attempt.json 或 records/ 副本。回归 case 的 run.json 引用公共 record_dir，公共计划与最终汇总仍在 report/。新 LSF 仿真将提交身份、固定请求、worker 结果和日志收集信息合入 `run.json.lsf`，不再永久保存每个 case 的 `job.json/request.json/controller.log`；运行中的临时文件集中在本轮 `.lsf` 交换目录。编译读者保护继续保留。工具结果 result.json、hook 和编译 job 记录仍单独保存，旧记录继续可读。

查看分析可读取 run.json 的 analysis 字段；report、rerun、recover 的目录参数保持不变。分析或日志损坏会使有效 PASS 失效，控制记录不完整时拒绝自动重跑。回归实时界面只发布小型进度摘要，完整汇总不会每完成一个 case 就重写。
