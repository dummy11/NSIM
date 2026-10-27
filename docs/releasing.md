# `nsim` 打包与版本发布

NSIM 是开发环境；发布物是 `src/nsim` 对应的 Python distribution，以及 `nsim` CLI。`pyproject.toml` 是包名、运行依赖、最低 Python 版本、入口点和**唯一版本号来源**。`src/nsim/__init__.py` 从已安装的 distribution metadata 读取版本。ASIC 工程、仿真输出、控制事件和本机 `.venv` 不属于 wheel。

## 版本规则

- 初期使用手工维护的 PEP 440 版本，例如 `0.1.0`、`0.2.0rc1`。每次发布只修改 `pyproject.toml` 中的 `project.version`，不在 Python 源码另存一份。
- 评审通过后提交发布内容，再将该提交标记为 `v<version>`。发布检查要求干净的工作树、该 tag 指向当前提交、构建产物与当前源码逐文件一致。
- 新工程通过 `.nsim/.nsim_version` 选择精确版本，稳定启动器把该版本分派到独立虚拟环境。版本选择不再写入 `nsim.toml`。
- 当前 `0.1.0` 是待评审实现的候选版本；没有 tag，也没有正式发布。

## 本地构建与验收

以下命令在 NSIM 仓库根目录执行。构建产物放在独立目录，避免混入其它版本：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip build
.venv/bin/python scripts/sync_project_layout.py --check
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m build --outdir dist .
.venv/bin/python scripts/check_distribution.py dist
```

`build` 生成 wheel 和 sdist。`check_distribution.py` 验证包名、版本、CLI 入口及两个归档中的源码字节，避免把开发环境内容误打进 wheel。再用全新虚拟环境安装**本地 wheel 文件路径**，从仓库外执行 `nsim --version` 和 `nsim version --json`，确认安装与命令入口可用。受控环境可对 wheel 运行本地 fake regression smoke。

正式发布时，在评审、提交及 `v<version>` tag 完成后从该提交重新构建，并运行：

```sh
.venv/bin/python scripts/check_distribution.py dist --release
shasum -a 256 dist/*
```

归档 hash、Git commit、tag、测试结果及发布渠道共同组成一次 release 记录。不要把此前未提交工作区生成的候选归档直接标成正式版本。版本一旦发布，不覆盖同一版本文件；修订应增加版本号重新构建。Release 还应附带根目录的 `nsim-install.py`，供用户人工下载一次。安装器不进入版本化 `nsim` wheel。

可在联网、与目标服务器兼容的机器上从发布 wheel 构建离线包：

```sh
python3 nsim-install.py bundle create \
  --wheel dist/nsim-0.1.0-py3-none-any.whl \
  --download-deps --output dist/nsim-offline-0.1.0.tar.gz
```

命令同时写出 `.sha256`；离线包包含安装器、主 wheel、依赖 wheel 和每个 wheel 的 SHA-256。目标 Python/平台不同可传 `--platform`、`--python-version`、`--implementation`、`--abi`，并必须在实际目标环境验收。目标机导入包时用 `--no-index --find-links` 安装，不能把公网包索引作为隐式后备。发布 Release 前验证 `nsim-install.py install --bundle`、`nsim switch`、普通运行和历史重跑；未配置 Release 时只能验证本地候选流程。

## 分发渠道

- **当前建议：**把 wheel、sdist、hash、安装器和目标平台离线包作为受控 Git release 交付。联网安装器按 tag 下载 wheel 与 `SHA256SUMS`；网络受限机器人工传入离线包。
- **公开 PyPI：**截至 2026-10-27，[`nsim` 名称已被另一个项目占用](https://pypi.org/project/nsim/)。若以后公开发布，需要先决定新的 distribution 名称并评估包、依赖约束和文档迁移；CLI 命令名仍可为 `nsim`。确认公开内容及所有权后，再配置发布凭据或 Trusted Publishing。
- **当前不做：**不自动上传 PyPI、不创建或推送 tag、不将参考工程源码或运行产物打包。

构建流程采用 [PyPA 打包指南](https://packaging.python.org/en/latest/tutorials/packaging-projects/) 的 wheel + sdist 形式。若选择 GitHub Actions 公开发布，可按 [PyPA Trusted Publishing 指南](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)把构建和发布拆成两个作业，并在发布环境设置人工审核。

稳定启动器内嵌与核心相同的项目路径发现代码，以便没有任何可用工具版本时仍能提供基本查询。修改 `src/nsim/project_paths.py` 后运行 `scripts/sync_project_layout.py` 更新安装器，测试会检查两者逐字一致。
