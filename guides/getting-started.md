# 开始使用 Engineering CI

安装本地环境并查看两个用户入口。实际操作见[常用操作](common-tasks.md)，命令与输出见[CLI 参考](cli-reference.md)。

如果是在另一个新 Python 仓库使用共享 CI，直接从[新仓库接入](new-repository.md)
开始；本页下面的安装和 `dagger check` 面向 engineering-ci 自身的主 checkout。

## 环境与目录

在 `D:\workspace\engineering-ci` 使用当前分支开发，不需要额外 worktree。
根项目要求 Python 3.14、uv。安装项目锁定环境、GitHub CLI，并确认 `gh auth status` 和目标仓库权限：

```powershell
Set-Location D:\workspace\engineering-ci
uv sync --locked --group dev
gh auth status
uv run --locked python scripts/release.py --help
uv run --locked python scripts/download.py --help
```

开发组安装本仓 pytest、Coverage 与 Ruff 测试工具。基础操作可使用
`uv sync --locked --no-dev`，并通过同一环境的 Python 调用入口。

Linux 使用相同的 uv 命令。Run 等待、日志和 Artifact 下载使用原生 gh，无额外下载器。
`--out` 和具体请求意图路径相对于调用目录，命令见[CLI 参考](cli-reference.md)。

Windows 的完整本地 PR 检查默认在 Dagger Linux 容器内执行，需要 Docker Linux
Engine 和固定版本的 Dagger，安装见[Dagger 配置](../docs/configuration/dagger.md)。
安装后仍在原 checkout 执行：

```powershell
dagger check
```

需要单项选择或报告导出时，见[本地检查](../docs/architecture/local-checks.md)。
可以直接运行 `uv run --locked pytest -q` 诊断原生 Windows；Linux 专用测试的 skipped 不代表已在 Linux 验证。
