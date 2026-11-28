# 开始使用 Engineering CI

环境准备见本页；实际操作见[常用操作](common-tasks.md)，命令与输出见[CLI 参考](cli-reference.md)。

如果是在另一个新 Python 仓库使用共享 CI，直接从[新仓库接入](new-repository.md)
开始；本页面向 engineering-ci 自身的开发。

## 环境与目录

开发目录与分支按实际任务选择，普通 checkout 和 linked worktree 均适用。
在实际操作的检出目录执行 Git 和工程检查，不绑定宿主机绝对路径或主 checkout。

根项目要求 Python 3.14、uv。ECI 宿主工具环境由 uv 统一管理，供多个开发检出和
Agent 复用；不因创建 worktree 就重复安装同一组工具依赖。实际受检 Source、Index、
配置、Plan、Invocation 和结果仍按各自执行范围独立。

### 统一环境的管理与更新边界

由**一个指定的主管理 checkout**（通常为主 checkout 的 `main`）负责维护共享
`.venv` 的基础同步与 editable 项目绑定；其余 linked worktree 仅复用该共享环境，
不要直接在其他 worktree 运行带项目安装的 `uv sync`，以免将共享环境的 editable
`.pth` 静默改指某个临时 worktree，也不要不加 `--inexact` 误删其他分支需要的兼容超集包。

在主管理 checkout 准备或更新共享环境，安装 GitHub CLI 并确认权限：

```sh
uv sync --locked --inexact --group dev
gh auth status
```

开发组包含本仓 pytest、Coverage、Ruff、Lefthook 等工具。基础运行可使用
`uv sync --locked --inexact --no-dev`。若某个开发分支在 `uv.lock` 新增了兼容的
锁定依赖，可直接在共享环境补装且**不改写主管理 checkout 的 editable 绑定**：

```sh
UV_PROJECT_ENVIRONMENT=<共享环境路径> uv sync --locked --no-install-project --inexact --group dev
```

当需要按某检出锁文件核对完整开发依赖是否齐备时，在环境准备或测试前运行只读校验：

```sh
UV_PROJECT_ENVIRONMENT=<共享环境路径> uv sync --locked --check --no-install-project --inexact --offline
```

普通薄入口会在调用上述只读校验前先核实环境目录、实际解释器 `sys.prefix`、候选源码及
所需工具的实际安装来源（发行版元数据、控制台脚本与 `uv.lock` 版本）；`uv sync --check`
的退出 0 单独不构成“环境存在且就绪”。入口使用 uv 原生有效 cache 配置，保留合法宿主
自定义 `cache-dir`；若项目/用户配置实际禁用 cache（包括带引号键或 `uv.toml` 覆盖），
入口明确拒绝，而不 grep 猜测。它不把继承的 `UV_CACHE_DIR` / `UV_NO_CACHE` / 调用方
config 覆盖当作宿主权威，也不自动创建、同步或清理环境。维护操作仍显式执行上述
`uv sync` 命令，并按实际需要选择 `--group dev`；不要为普通操作重新指定临时 cache。

若分支引入与共享环境不兼容的锁定版本冲突，通过显式独立的
`UV_PROJECT_ENVIRONMENT=<独立环境路径> uv sync --locked --group dev` 隔离管理，
不把单个分支冲突强行混入共享环境；普通入口用 `--environment <独立环境路径>` 选择它。

### 运行时与 Hook 绑定

uv 通过绝对路径 `UV_PROJECT_ENVIRONMENT` 指定共享项目环境，通过 `--project` /
`UV_PROJECT` 指定项目管理位置（`uv.toml`/`pyproject.toml` 不提供静态
`project-environment` 配置键）：
- **Git Hook（已自动接线）**：[`scripts/lefthook-arguments.sh`](../scripts/lefthook-arguments.sh)
  在 linked worktree 中默认将 `UV_PROJECT_ENVIRONMENT` 指向主 checkout 的 `.venv`
  （支持标准 `.git` 及配置了 `core.worktree` 的 `--separate-git-dir` 布局；bare 仓
  或未配置 `core.worktree` 的 `--separate-git-dir` 须显式传入 `UV_PROJECT_ENVIRONMENT`；
  不把任意遗留 `worktree/.venv` 当作显式授权）。Hook 启动前仅执行只读
  `uv sync --locked --check --no-install-project --no-dev --inexact --offline` 校验
  运行时环境就绪，不在每次 Hook 调度前强加不适用的全开发依赖（如 `ruff`/`pytest`）门禁，
  也不在 Hook 内自动 `uv sync`。在 ECI 自身 checkout 中独立将当前检出的 `src/` 前置到
  `PYTHONPATH`。
- **Hook 之外的本地入口（推荐）**：使用仓库自带的薄入口，入口在任何操作前解析共享环境、
  候选 `src/`、解释器与所需工具，并执行一次只读锁定就绪检查：
  ```powershell
  bash scripts/engineering-ci.sh verify --out .artifacts/verify
  pwsh -NoProfile -File scripts/engineering-ci.ps1 cli version show
  ```
  ```sh
  scripts/engineering-ci.sh pytest -q tests/test_engineering_entry.py
  scripts/engineering-ci.sh ruff check src tests
  ```
  操作类型为 `verify` / `cli` / `pytest` / `ruff`。普通 entry 使用确定性默认：linked
  worktree 复用主管理 checkout 的 `.venv`，候选源码固定为当前检出 `src/`；也可用
  `--environment <已准备路径>` 选择显式隔离环境。就绪范围按实际操作确定：runtime
  CLI/help 只要求 runtime 依赖；`cli version` 与 `verify report`/`cli verify report`
  作为诊断/只读入口不要求当前锁新鲜；`pytest` 要求 pytest 与默认报告的
  `pytest-reportlog`（显式 `--eci-no-auto-report` 可关闭），`ruff` 只要求 Ruff；
  完整 `verify` 才要求 dev 组、pytest、Ruff 与 Lefthook。pytest/Ruff 以所选环境中实际
  安装的发行版、控制台脚本及 `uv.lock` 版本为准并执行同一脚本，不因候选 `src/` 同名
  模块换源。环境缺失、解释器不匹配、工具来源/版本不符、有效 cache 不稳定或锁定检查
  失败时，入口在实际操作前以非零退出并打印维护命令；**不会**创建 `.venv`、执行写式
  `uv sync`、改 cache 或回退系统工具。入口使用 uv 原生参数绑定实际解释器与依赖组，
  清除会改变该请求的继承覆盖，并保留合法宿主自定义 cache-dir；有效配置禁用 cache 时
  明确拒绝。Windows 的 `engineering-ci.ps1` 定位 Git for Windows 的 Bash（可用
  `ECI_GIT_BASH` 显式指定），不会回退到 WSL 的 `bash`；找不到时明确失败。Hook 仍自动走
  [`scripts/lefthook-arguments.sh`](../scripts/lefthook-arguments.sh)。
- 维护或使用未受管原生命令时仍可手工会话绑定：设置
  `UV_PROJECT_ENVIRONMENT=<共享环境路径>`，执行 ECI 自身 CLI / `verify` 时同时设置
  `PYTHONPATH=<当前检出>/src`。同一 Shell 切换目录不会重置环境变量：切到其他 ECI
  checkout 需重绑（或切回主管理 checkout 时移除）`PYTHONPATH`，进入独立消费者项目前
  须清除 ECI 特定 `UV_PROJECT_ENVIRONMENT` / `UV_PROJECT` / `VIRTUAL_ENV` / `PYTHONPATH`
  或在同一命令作用域重设为消费者自己的环境（`tools.consumer` 跨仓调用时剥离 ECI 检出绑定，
  并把等于当前解释器前缀的共享环境视为宿主环境；另一路径的
  `UV_PROJECT_ENVIRONMENT` 仍作为消费者目标保留）。本仓不新建总 Runner，也不强迫原生
  `git` / `ruff` / `pytest` / `dagger` 经过 Python CLI。

Git Hook 安装及运行见[常用操作](common-tasks.md#lefthook-本地入口)。
Run 等待、日志和 Artifact 下载使用原生 gh，无额外下载器。
`--out` 和具体请求意图路径相对于调用目录，命令见[CLI 参考](cli-reference.md)。

## 工程验证入口

本仓日常综合验证使用[显式本地综合验证](common-tasks.md#显式本地综合验证)
的默认入口 `scripts/engineering-ci.sh verify --out .artifacts/verify`，由 ECI CLI 保留同次
原生 Lefthook 调用结果与计时；入口先完成环境与候选源码就绪检查。
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
