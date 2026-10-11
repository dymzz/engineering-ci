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
