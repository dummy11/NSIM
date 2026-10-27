# NSIM 使用指南：版本、打包与远端获取

NSIM 是开发 `nsim` 的仓库；`nsim` 是可安装的 Python 包和同名 CLI。Git 管理开发源码，`pyproject.toml` 管理发布包版本，wheel/sdist 是可交付制品。ASIC 工程源码与仿真输出不随 `nsim` 包分发。

## 1. 当前可获取内容

仓库的 `origin` 指向 `git@github.com:dummy11/NSIM.git`。2026-10-27 核对时，远端没有版本 tag 或 GitHub Release。`main` 用于获取开发源码；只有创建并发布版本制品后，才能按版本下载 `nsim` wheel。每次使用前应查询远端状态：`git ls-remote --heads --tags origin` 和 `gh release list --repo dummy11/NSIM`。下文的 tag、Release 下载命令须等首次发布后使用。

本地已验证 `0.1.0` 候选包可以生成 wheel 和 sdist；在独立虚拟环境安装 wheel 后，`nsim --version` 及一个 fake 回归通过。这只是候选包验证，不是远端版本发布。

## 2. 版本控制分别管什么

| 对象 | 版本身份 | 用途 |
|---|---|---|
| NSIM 开发仓库 | Git commit SHA；正式版本另加 `v<version>` tag | 定位一份确定的源码与测试 |
| `nsim` 安装包 | `pyproject.toml` 的 `project.version`，当前为 `0.1.0` | wheel/sdist 文件名、安装元数据、`nsim --version` |
| ASIC 工程 | 工程自己的 Git tag/commit，以及描述文件中的 `requires_nsim` | 记录 DUT 输入身份，并约束可用的 `nsim` 版本 |
| 单次回归 | regression id、任务/attempt id、输入 hash、已安装 `nsim` 版本 | 重跑、报告和故障恢复时追溯具体执行 |

只在 `pyproject.toml` 维护一份包版本；`src/nsim/__init__.py` 从已安装包的 metadata 读取版本。开发中的提交不自动成为发布版本。不同 ASIC 工程可以用各自的虚拟环境固定 `nsim` 版本，例如在工程描述文件中写入 `requires_nsim = ">=0.1,<0.2"`。

版本号遵循 [Python 包版本规范](https://packaging.python.org/en/latest/specifications/version-specifiers/)，可使用 `0.1.0`、`0.1.1`、`0.2.0rc1` 等形式。正式发布后不覆盖同一版本的旧制品；修订应增加版本号。

## 3. 开发者从远端获取 NSIM 源码

有仓库读取权限并配置好 GitHub SSH 后：

```sh
git clone git@github.com:dummy11/NSIM.git
cd NSIM
git fetch --tags origin
git switch main
git pull --ff-only origin main
```

上述命令获取的是**开发仓库**，不是已经安装的 `nsim`。若已有发布 tag，需要查看发布时的源码，可执行 `git switch --detach v0.1.0`。从源码开发时才使用 `python3 -m pip install -e .`；普通使用者优先安装已经发布的 wheel。

## 4. 在 NSIM 中打包 `nsim`

仓库使用 `setuptools` 和 `src/nsim/` 布局。`pyproject.toml` 定义包名、运行依赖、最低 Python 版本和 CLI 入口 `nsim = nsim.cli:main`。wheel 包含运行所需的 `nsim` 模块；sdist 包含从源码构建所需的文件。两者均不包含 ASIC 工程的 RTL、历史仿真输出或本机虚拟环境。

在 NSIM 根目录创建开发环境并构建候选包：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip build
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m build --outdir dist/v0.1.0 .
.venv/bin/python scripts/check_distribution.py dist/v0.1.0
```

`build` 产生 `nsim-0.1.0-py3-none-any.whl` 和 `nsim-0.1.0.tar.gz`；检查脚本核对版本、CLI 入口和归档内的源码字节。构建命令及两种归档的含义见 [PyPA 打包指南](https://packaging.python.org/en/latest/tutorials/packaging-projects/)。

安装验收应使用另一个**全新**虚拟环境，从 NSIM 仓库外执行：

```sh
python3 -m venv /tmp/nsim-package-test
/tmp/nsim-package-test/bin/python -m pip install ./dist/v0.1.0/nsim-0.1.0-py3-none-any.whl
/tmp/nsim-package-test/bin/nsim --version
/tmp/nsim-package-test/bin/nsim version --json
```

本地 `0.1.0` 候选归档仅用于检查，不应因构建成功就视为已发布。

## 5. 正式版本发布

1. 确认发布内容、测试结果和目标版本；提交本地源码与文档。检查 `git status --short` 为空，确认 `pyproject.toml` 的版本号就是计划发布的版本。
2. 给该提交创建 `v<version>` 注释 tag，并将提交和 tag 推送到 `origin`。不能将工作区未提交的候选包当作正式版本。
3. 从这个干净、已标记的提交重新构建到仅含当前版本的 `dist/v<version>/`，运行 `scripts/check_distribution.py dist/v<version> --release`。此模式要求当前提交带对应 tag，且工作区干净。
4. 记录 wheel、sdist 的 SHA-256，并随制品提供 `SHA256SUMS`；在另一台机器验证下载、安装和 `nsim --version`。
5. 确定分发渠道后发布制品，同时保留版本号、Git commit、测试记录与校验和。具体发布操作由有仓库发布权限的人执行。

版本 `0.1.0` 的发布命令示例（须先确认该版本的源码已提交且发布渠道已定）：

```sh
git tag -a v0.1.0 -m "nsim 0.1.0"
git push origin main
git push origin v0.1.0
.venv/bin/python -m build --outdir dist/v0.1.0 .
.venv/bin/python scripts/check_distribution.py dist/v0.1.0 --release
cd dist/v0.1.0
shasum -a 256 nsim-0.1.0-py3-none-any.whl nsim-0.1.0.tar.gz > SHA256SUMS
```

若选择 GitHub Release，可先用 `gh release create v0.1.0 ./nsim-0.1.0-py3-none-any.whl ./nsim-0.1.0.tar.gz ./SHA256SUMS --repo dummy11/NSIM --verify-tag --draft --notes-from-tag` 创建待检查草稿，然后再发布；`--verify-tag` 要求远端 tag 已存在。[GitHub CLI 文档](https://cli.github.com/manual/gh_release_create)说明了该行为。当前远端尚无 Release，也没有配置自动发布流程。

## 6. 远端机器如何取得 `nsim`

**推荐路径：下载固定版本的 wheel。** GitHub Release 发布后，在有读取权限并已认证 GitHub CLI 的机器上执行：

```sh
gh release download v0.1.0 --repo dummy11/NSIM \
  --pattern 'nsim-0.1.0-py3-none-any.whl' \
  --pattern 'nsim-0.1.0.tar.gz' --pattern 'SHA256SUMS' \
  --dir ./nsim-0.1.0
cd nsim-0.1.0
shasum -a 256 -c SHA256SUMS
python3 -m venv ./venv
./venv/bin/python -m pip install ./nsim-0.1.0-py3-none-any.whl
./venv/bin/nsim --version
```

`gh release download` 的 tag、`--repo`、`--pattern`、`--dir` 用法见 [GitHub CLI 文档](https://cli.github.com/manual/gh_release_download)。这个安装命令以**明确的本地 wheel 路径**为准；依赖包仍需要可访问的包索引或预先准备的离线 wheelhouse。

若组织以后配置了私有 Python 包索引，也可以在每个 ASIC 工程的独立虚拟环境中使用专用 `--index-url` 安装固定版本 `nsim==0.1.0`。当前仓库没有配置这样的索引，不能填写一个实际地址。不要把私有索引只放在 `--extra-index-url` 并同时从公开 PyPI 解析同名包，因为 pip 对多个索引没有严格优先级；详见 [pip 安装文档](https://pip.pypa.io/en/stable/cli/pip_install/)。

**公开 PyPI 暂不可用现有包名。** [公开 PyPI 的 `nsim`](https://pypi.org/project/nsim/) 已属于另一个项目。若计划公开发布，需要先为 distribution 另选唯一名称；CLI 命令仍可叫 `nsim`。当前不要使用 `pip install nsim` 从公开 PyPI 获取本项目。

源码安装是开发者的备选路径：发布 tag 存在后，可克隆 NSIM 并切换到该 tag，再用 `python3 -m pip install -e .` 开发；这会安装仓库源码，不是下载发布 wheel。使用固定 Git tag 或 commit 的方法见 [pip 的 VCS 安装文档](https://pip.pypa.io/en/stable/topics/vcs-support/)。
