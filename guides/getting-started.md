# 开始使用 Engineering CI

环境准备见本页；实际操作见[常用操作](common-tasks.md)，命令与输出见[CLI 参考](cli-reference.md)。

如果是在另一个新 Python 仓库使用共享 CI，直接从[新仓库接入](new-repository.md)
开始；本页面向 engineering-ci 自身的开发。

## 环境与目录

开发目录与分支按实际任务选择，普通 checkout 和 linked worktree 均适用。
在实际操作的检出目录执行 Git 和工程检查，不绑定宿主机绝对路径或主 checkout。

根项目要求 Python 3.14、uv。ECI 宿主工具环境的目标是由 uv 统一管理，
供多个开发检出和 Agent 复用；不因创建 worktree 就重复安装同一组工具依赖。
实际受检 Source、Index、配置、Plan、Invocation 和结果仍按各自执行范围独立。

在负责管理 ECI 锁定依赖的项目目录准备工具环境，安装 GitHub CLI 并确认权限：

```sh
uv sync --locked --group dev
gh auth status
```

开发组包含本仓 pytest、Coverage、Ruff、Lefthook 等工具。环境更新由负责管理
该环境的项目按其锁文件执行；不同分支不应无协调地用不同锁文件同步同一个环境。
不需要在每次 Hook 或每个 Job 中重新同步依赖。

uv 支持使用绝对路径的 `UV_PROJECT_ENVIRONMENT` 指定共享项目环境，
以及 `--project` / `UV_PROJECT` 指定项目管理位置。当前 ECI 的 CLI 和
Lefthook 使用 uv 原生入口，但尚未统一接线共享环境的选择和初始化。
在接线完成前，确认调用实际使用的环境已准备好，并核对 ECI 自身开发时实际加载的
Python 实现及依赖；共享 editable installation 不自动代表当前开发分支的源码。
不要将临时环境变量或其他检出的 `.venv` 当作已经落实的统一环境合同。

Git Hook 安装及运行见[常用操作](common-tasks.md#lefthook-本地入口)。
Run 等待、日志和 Artifact 下载使用原生 gh，无额外下载器。
`--out` 和具体请求意图路径相对于调用目录，命令见[CLI 参考](cli-reference.md)。

## 工程验证入口

本仓日常综合验证使用[显式本地综合验证](common-tasks.md#显式本地综合验证)
的默认入口，由 ECI CLI 保留同次原生 Lefthook 调用结果与计时。
Git commit/push 仍走原生 Git Hook；单项或定向检查使用适用的原生入口，
不为局部修改默认扩大为完整验证。

Windows 的完整本地 PR 检查默认在 Dagger Linux 容器内执行，需要 Docker Linux
Engine 和固定版本的 Dagger，安装见[Dagger 配置](../docs/configuration/dagger.md)。
例如单项检查：

```sh
dagger check --engineering-ci --check=lint
```

需要单项选择或报告导出时，见[本地检查](../docs/architecture/local-checks.md)。
原生 Windows pytest 定向诊断按实际测试目标执行；Linux 专用测试的 skipped
不代表已在 Linux 验证。
