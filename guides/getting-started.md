# 开始使用 Engineering CI

安装本地环境并查看两个用户入口。实际操作见[常用操作](common-tasks.md)，命令与输出见[CLI 参考](cli-reference.md)。

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

Linux 使用相同的 uv 命令。aria2c 可选，auto 缺失时使用标准库 HTTP；
具体配置见[下载配置](../docs/configuration/downloads.md)。
输出 --out 相对于调用者目录；机器配置相对于 engineering-ci checkout。

Windows 的完整本地 PR 检查默认在 Dagger Linux 容器内执行，需要 Docker Linux
Engine 和固定版本的 Dagger，安装见[Dagger 配置](../docs/configuration/dagger.md)。
安装后仍在原 checkout 执行：

```powershell
uv run --locked engineering-ci checks pr --json .test-tools/checks/pr.json
```

`--executor native` 可诊断原生 Windows；必需 Linux 测试
被跳过时结果为 incomplete，不能用于交付准入。
