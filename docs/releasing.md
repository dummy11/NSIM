# `nsim` 打包与版本发布

NSIM 是开发环境；发布物是 `src/nsim` 对应的 Python distribution，以及 `nsim` CLI。`pyproject.toml` 是包名、运行依赖、最低 Python 版本、入口点和**唯一版本号来源**。`src/nsim/__init__.py` 从已安装的 distribution metadata 读取版本。ASIC 工程、仿真输出、控制事件和本机 `.venv` 不属于 wheel。

## 版本规则

- 初期使用手工维护的 PEP 440 版本，例如 `0.1.0`、`0.2.0rc1`。每次发布只修改 `pyproject.toml` 中的 `project.version`，不在 Python 源码另存一份。
- 评审通过后提交发布内容，再将该提交标记为 `v<version>`。发布检查要求干净的工作树、该 tag 指向当前提交、构建产物与当前源码逐文件一致。
- 工程描述文件的 `requires_nsim` 约束和回归元数据中的 `nsim` 版本均按已安装 distribution 判定。不同 ASIC 工程应使用各自虚拟环境安装经批准的版本。
- 当前 `0.1.0` 是待评审实现的候选版本；没有 tag，也没有正式发布。

## 本地构建与验收

以下命令在 NSIM 仓库根目录执行。构建产物放在独立目录，避免混入其它版本：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip build
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

归档 hash、Git commit、tag、测试结果及发布渠道共同组成一次 release 记录。不要把此前未提交工作区生成的候选归档直接标成正式版本。版本一旦发布，不覆盖同一版本文件；修订应增加版本号重新构建。

## 分发渠道

- **当前建议：**先把 wheel、sdist、hash 和 release 记录作为内部制品或受控 Git release 交付；使用明确的文件路径或受控私有索引安装。
- **公开 PyPI：**截至 2026-10-27，[`nsim` 名称已被另一个项目占用](https://pypi.org/project/nsim/)。若以后公开发布，需要先决定新的 distribution 名称并评估包、依赖约束和文档迁移；CLI 命令名仍可为 `nsim`。确认公开内容及所有权后，再配置发布凭据或 Trusted Publishing。
- **当前不做：**不自动上传 PyPI、不创建或推送 tag、不将参考工程源码或运行产物打包。

构建流程采用 [PyPA 打包指南](https://packaging.python.org/en/latest/tutorials/packaging-projects/) 的 wheel + sdist 形式。若选择 GitHub Actions 公开发布，可按 [PyPA Trusted Publishing 指南](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)把构建和发布拆成两个作业，并在发布环境设置人工审核。
