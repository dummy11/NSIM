# NSIM 使用指南：项目注册、配置与版本管理

NSIM 是开发仓库；`nsim` 是按版本发布的 Python 包。`nsim-install.py` 是只依赖 Python 标准库的稳定安装器和启动器。它把不同版本安装到独立虚拟环境，并在指定安装目录的 `bin/` 下提供统一的 `nsim` 命令。ASIC 工程、仿真输出和控制记录不包含在 `nsim` wheel 中。

## 当前状态

`pyproject.toml` 的 `0.1.0` 仍是候选版本。2026-10-27 核对远端时，`dummy11/NSIM` 尚无版本 tag 或 GitHub Release；因此本指南中的远端安装命令要等首次发布后才能使用。本地可从当前源码构建候选 wheel 和离线包做验证。

## 项目初始化与注册

`nsim bootstrap` 初始化并注册验证项目。RTL 与 verification 可以分属不同目录和 Git 仓库，不需要登记 RTL commit。第一次使用前须安装至少一个支持 schema 2 的 NSIM 版本；克隆源码或创建稳定启动器本身不等于安装了工具版本。

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
└── filelist/
    ├── tb.f
    └── sim.f
```

未提供 verification filelist 时，创建带注释的 `filelist/tb.f` 模板。已有的 `tb.f` 和 submit 文件保持原内容；新 `submit/` 可以为空。指定外部 verification filelist 时，保留并直接登记该文件。空 TB 清单显示待填写，VCS 规划会拒绝空清单。

`filelist/sim.f` 是带所有权标记的 NSIM 组合来源说明，不是直接传给 VCS 的原生 `-f` 文件。每次规划分别按 RTL 和 verification 根解析登记的列表，保留嵌套 `-f/-F` 语义，实际编译 token 和 argv 固化到本次 `plan.json`、`commands.json`。不要手工编辑工具维护的 `sim.f`；同名用户文件不会被覆盖。schema 2 的 VCS submit 省略 `vcs.filelist`，也可显式填 `filelist/sim.f`；其他覆盖路径被拒绝，以免绕过登记的两套输入。

## 配置文件与路径规则

```toml
# conv_verif/.nsim/nsim.toml
schema_version = 2
name = "conv_verif"
rtl_root = "../rtl"
rtl_filelist = "../rtl/filelist/rtl.f"
verification_filelist = "filelist/tb.f"
inputs = []
```

配置中的所有相对路径以 verification 根目录为基准。`inputs` 是额外执行输入的文件列表，例如模型、脚本、数据或预构建二进制。工具版本只写入 `.nsim/.nsim_version`，内容是一行具体版本及换行，不能写 `latest`；不要在 TOML 中加入 `requires_nsim` 或重复版本字段。

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

`status` 只查询，能显示未注册、目录失效、清单为空、版本未安装等问题；单个项目无法访问会显示独立的问题行，不中断其他项目的列表查询；配置有效不代表仿真通过。`update` 和 `switch` 必须针对已注册的 schema 2 项目。只更新传入字段，全部校验后一起保存；失败保留原配置，并展示成功更新前后的值。只改 `rtl-root` 不会自动移动 `rtl-filelist`。无参数 update 报用法，不升级版本。普通 update 不搬移或重命名 verification 目录。

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

稳定启动器读取 `--project` 或当前目录所属项目的版本文件。`rerun`、`recover` 和 `report show` 优先读取原控制记录的版本，不受项目后续切换影响。没有项目上下文时，普通执行命令在只安装一个版本时使用该版本，否则可设置 `NSIM_VERSION` 明确选择。

## 旧项目兼容

根目录中的 schema 1 `nsim.toml/nsim.yaml`、`.nsim_version` 继续支持读取和历史运行；schema 1 的路径仍相对描述文件解析，不改变旧语义。新项目只生成 `.nsim/` schema 2。新旧配置同时存在时报冲突，不自动搬移或转换。旧项目可继续通过安装器入口 `python3 nsim-install.py switch VERSION --project PATH` 切换；新的注册和 update 要求显式迁移到 schema 2。

原 `nsim project list <目录>` 保留为文件扫描，扫描不自动登记；日常登记查询使用 `nsim list/status`。

## 安装器和版本目录

默认目录是 `~/.local/share/nsim`，也可通过 `--root` 指定。结构如下：

```text
<root>/bin/nsim                 # 稳定启动器
<root>/bin/nsim-install         # 同一管理程序
<root>/versions/0.1.0/venv/    # 该版本的 Python 环境
<root>/versions/0.1.0/receipt.json
<root>/versions/0.2.0/venv/
```

`nsim bootstrap` 始终用于项目注册，`nsim update` 始终用于项目配置；安装工具使用 `nsim install`。安装器本身是仓库根目录的单文件 `nsim-install.py`。首次安装时可从可信的 NSIM 源码、未来 Release 或离线包手动取得它，然后执行 `python3 nsim-install.py bootstrap --root ~/.local/share/nsim`。把 `<root>/bin` 加入 shell 的 `PATH` 一次；之后直接使用 `nsim`。目标机器需要 Python 3.9+ 和可创建 `venv`/运行 pip 的 Python 安装。当前启动器面向 POSIX Linux/macOS。

```sh
python3 nsim-install.py bootstrap --root "$HOME/.local/share/nsim"
export PATH="$HOME/.local/share/nsim/bin:$PATH"
nsim versions
```

## 联网获取与切换

发布后，在可访问私有 GitHub Release、已登录 GitHub CLI、且 pip 可获取依赖的机器上：

```sh
nsim install 0.1.0 --root "$HOME/.local/share/nsim"
nsim install --all --root "$HOME/.local/share/nsim"  # 所有已发布的稳定版
nsim versions --remote
nsim install 0.2.0                     # 下载/安装，不切换工程
nsim switch 0.1.0 --project ./project-a
nsim run regression --project ./project-a ...
```

联网模式默认访问 `dummy11/NSIM`，也可用 `NSIM_RELEASE_REPO=OWNER/REPO` 指向受控镜像。它从指定 tag 的 GitHub Release 下载本项目 wheel 和 `SHA256SUMS`，核对 SHA-256，再用该 wheel 路径安装；不会从公开 PyPI 按 `nsim` 名称取包。不同版本只装入各自的目录，不覆盖已安装版本。`install` 不修改项目版本文件。公开 PyPI 的 [`nsim`](https://pypi.org/project/nsim/) 是另一个项目，不要以 `pip install nsim` 获取本项目。

## 网络受限服务器：离线包

在与目标服务器 Python/平台兼容的联网机器上，从已验证的 `nsim` wheel 生成离线包。`--download-deps` 下载该 wheel 的依赖；若构建机与服务器不同，可增加 `--platform`、`--python-version`、`--implementation` 和 `--abi` 目标选项，并在目标服务器验收。构建后会同时写出 `<bundle>.sha256`。

```sh
python3 nsim-install.py bundle create \
  --wheel dist/v0.1.0/nsim-0.1.0-py3-none-any.whl \
  --download-deps --output nsim-offline-0.1.0.tar.gz
```

也可以先人工准备依赖 wheel，再用 `--wheelhouse ./wheelhouse` 打包。离线包包含安装器脚本、主 wheel、依赖 wheel 和逐文件哈希清单。通过受控渠道把离线包及其 `.sha256` 文件传到服务器；先核对外部 SHA-256，再安装：

```sh
sha256sum -c nsim-offline-0.1.0.tar.gz.sha256  # Linux；macOS 用 shasum -a 256 -c
tar -xOf nsim-offline-0.1.0.tar.gz nsim-install.py > nsim-install.py
python3 nsim-install.py install --bundle nsim-offline-0.1.0.tar.gz \
  --root "$HOME/.local/share/nsim"
export PATH="$HOME/.local/share/nsim/bin:$PATH"
nsim switch 0.1.0 --project ./project-a
```

安装器先核对包内 wheel 哈希，然后使用 pip 的 `--no-index --find-links` 从包内安装，不回退到公网索引。离线导入新版本使用 `nsim install --bundle <新离线包>`；它只安装版本，仍需 `nsim switch` 才改变工程版本。每个离线包对应一个 `nsim` 版本；需要多个版本时，可向同一 `--root` 连续导入多个包。纯手工下载 wheel 时，也可以执行 `nsim install --wheel <wheel> --wheelhouse <依赖目录> --checksums SHA256SUMS --root <目录>`。

## 历史回归与身份

每次新布局运行分别记录 RTL 和 verification 的 Git 身份（非 Git 目录显式为空），以及实际参与执行的输入文件哈希；同时记录工具版本、Python 路径与字节哈希、`nsim` 源文件哈希、依赖版本，以及通过启动器安装时的 wheel SHA-256。`nsim rerun <journal>` 从旧 `plan.json` 选择原版本；如果该版本未安装，会明确报错。精确重跑还要求原 Python 环境、工具代码和工程输入仍与记录一致。schema 2 的项目配置作为快照保存，后续 update 或 switch 不替换旧计划的绑定；旧计划仍校验原源码、submit、caselist、显式输入和运行环境。源码已改变或丢失时不能精确重跑，也不自动归档或恢复源码。schema 1 的旧计划继续校验原描述文件哈希，不放宽历史规则。

## 发布流程

`pyproject.toml` 是包版本的唯一来源。完成测试和候选包验证后，为提交创建 `v<version>` tag，从该提交构建 wheel/sdist，通过 `scripts/check_distribution.py --release`，生成 SHA-256，并将 wheel、sdist、`SHA256SUMS` 和 `nsim-install.py` 作为受控 Release 制品发布。可额外针对目标平台发布离线包及其 `.sha256`。不得覆盖同一版本的已发布文件；修订需增加版本号。具体命令见 [打包与发布](docs/releasing.md)。

开发者需要源码时仍可 `git clone git@github.com:dummy11/NSIM.git`，再 `git fetch --tags`，按发布 tag 检出源码。克隆仓库不会自动安装工具版本；普通使用者优先获取 wheel 或离线包。
