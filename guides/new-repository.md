# 新建 Python 仓库并接入 Engineering CI

从空目录建立一个私有 Python 仓库，接入共享检查，完成本地验证、PR、审查和合并。
已有仓库从第 3 节开始，保留自己的代码、测试、依赖和目录。

下面使用 `dymzz/my-python-app` 作为具体示例。执行前把仓库名和路径改成自己的目标；
这些命令供操作者执行，本说明不会替你创建仓库、修改权限或发布版本。

## 1. 确定版本和环境

本例固定已正式发布的 [v0.6.5](https://github.com/dymzz/engineering-ci/releases/tag/v0.6.5)，
对应提交 `804d791c60d34468166b6910c88fbc8149fdbf26`。
Workflow 和 Dagger 模块使用同一提交；候选 PR 通过不等于新版本已经发布。

准备 Git、GitHub CLI、uv、Python 3.14、Docker Linux Engine 和 Dagger
`1.0.0-beta.16`。Dagger 的安装及版本核对见[Dagger 配置](../docs/configuration/dagger.md)。
这里的 Python 3.14 是示例项目的选择，不要求所有消费者使用相同 Python。

在 PowerShell 7 设置本次操作上下文：

```powershell
$repo = 'dymzz/my-python-app'
$project = 'D:/workspace/my-python-app'
gh auth status
git --version
uv --version
dagger version
docker info --format '{{.OSType}}'
```

确认 gh 是预期账号、Dagger 是指定版本、Docker 输出 `linux`。认证或工具检查失败时，
先处理对应环境问题，不继续创建仓库或把未执行的检查当作通过。

当前上游为私有仓库。下面的共享 Workflow 接入面向 GitHub 允许访问它的私有消费者；
公开消费者不能直接调用私有上游 Workflow。

## 2. 建立仓库与开发分支

以下步骤仅用于不存在的新目录；已有目录不重复初始化：

```powershell
if (Test-Path $project) { throw '目标目录已存在；先检查其中的 Git 状态' }
New-Item -ItemType Directory -Path $project | Out-Null
Set-Location $project
git init -b main
if ($LASTEXITCODE -ne 0) { throw 'Git 初始化失败' }
Set-Content README.md '# My Python App' -Encoding utf8NoBOM
git add -- README.md
git commit -m 'chore: initialize repository'
if ($LASTEXITCODE -ne 0) { throw '初始提交失败，检查 Git 身份和错误' }
gh repo create $repo --private --source . --remote origin --push
if ($LASTEXITCODE -ne 0) { throw '远端创建或 Push 失败，保留当前目录并处理错误' }
git switch -c codex/bootstrap-ci
```

这样远端已有 `main`，后续代码与 CI 改动进入开发 PR。不要复制其他仓库的 `.git`、
`.venv` 或缓存。项目目标写入自己的 README；AGENTS.md 只约定本项目的职责、检查、
权限和交付规则，不照搬 engineering-ci 的内部目录和发布约束。

## 3. 准备最小 Python 项目

在当前项目创建以下文件；已有项目使用实际测试和范围，不替换业务代码。

```powershell
New-Item -ItemType Directory -Force -Path 'tests', '.dagger/src/my_python_app_ci', '.github/workflows' | Out-Null
```

`pyproject.toml`：

```toml
[project]
name = "my-python-app"
version = "0.1.0"
requires-python = ">=3.14"

[dependency-groups]
dev = ["pytest==9.0.3", "ruff==0.16.10"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]

[tool.ruff]
target-version = "py314"
extend-exclude = [".dagger"]
```

`app.py`：

```python
def add(left: int, right: int) -> int:
    return left + right
```

`tests/test_app.py`：

```python
from app import add


def test_add():
    assert add(2, 3) == 5
```

`.gitignore`：

```gitignore
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.test-tools/
.env
.env.*
!.env.example
dist/
```

解析并锁定依赖，先取得简单的本地反馈：

```powershell
uv lock
if ($LASTEXITCODE -ne 0) { throw '依赖解析失败' }
uv sync --locked
if ($LASTEXITCODE -ne 0) { throw '安装锁定依赖失败' }
uv run --locked ruff check .
if ($LASTEXITCODE -ne 0) { throw '修复 Lint 后继续' }
uv run --locked ruff format --check .
if ($LASTEXITCODE -ne 0) { throw '处理格式差异后继续' }
uv run --locked pytest -q
if ($LASTEXITCODE -ne 0) { throw '修复真实测试失败后继续' }
```

这些直接命令方便开发反馈；后面的 `dagger check` 才是与本例共享 Workflow 相同的
容器检查入口。报告功能不要求额外安装 engineering-ci 或 Vesryn Python 包。

## 4. 建立消费者的 Dagger 模块

本例沿用现有 Vesryn 消费者已经使用的 Python 模块兼容配置 `dagger.json`。
Dagger 1.0 支持该格式；它不是本仓库原生 TOML 配置的复制品。beta.16 的 Python
workspace SDK 初始化存在已记录限制，因此本例显式给出模块文件，再用 Engine
生成 SDK，不使用旧版 `dagger init --sdk python` 或未验证的 workspace SDK 初始化。
限制与原生配置说明见[Dagger 配置](../docs/configuration/dagger.md#python-工作区生成限制)。

在项目根目录创建 `dagger.json`：

```json
{
  "name": "my-python-app-ci",
  "engineVersion": "v1.0.0-beta.16",
  "sdk": {"source": "python"},
  "source": ".dagger",
  "dependencies": [
    {
      "name": "engineering-ci",
      "source": "github.com/dymzz/engineering-ci@v0.6.5",
      "pin": "804d791c60d34468166b6910c88fbc8149fdbf26"
    }
  ]
}
```

创建 `.dagger/pyproject.toml`：

```toml
[project]
name = "my-python-app-ci"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["dagger-io"]

[build-system]
requires = ["uv_build==0.12.23"]
build-backend = "uv_build"

[tool.uv.sources]
dagger-io = { path = "sdk", editable = true }
```

创建 `.dagger/src/my_python_app_ci/__init__.py`：

```python
from .main import MyPythonAppCi

__all__ = ["MyPythonAppCi"]
```

创建 `.dagger/src/my_python_app_ci/main.py`：

```python
from typing import Annotated

import dagger
from dagger import DefaultPath, Ignore, check, dag, function, object_type

Source = Annotated[
    dagger.Directory,
    DefaultPath("/"),
    Ignore([
        ".git", ".venv", ".dagger", ".test-tools", "dist",
        "**/__pycache__", "**/.pytest_cache", "**/.ruff_cache",
        ".env", ".env.*",
    ]),
]
PYTHON = "3.14"
TEST_POLICY = {"test_path": "tests"}


@object_type
class MyPythonAppCi:
    @function
    @check
    async def lint(self, source: Source) -> None:
        """Check the project's Ruff policy."""
        await dag.engineering_ci().python_lint(
            source=source, python_version=PYTHON, lint_path="."
        )

    @function
    @check
    async def format_check(self, source: Source) -> None:
        """Check formatting without changing source."""
        await dag.engineering_ci().python_format_check(
            source=source, python_version=PYTHON, format_path="."
        )

    @function
    @check
    async def tests(self, source: Source) -> None:
        """Propagate the original pytest failure."""
        await dag.engineering_ci().python_test(
            source=source, python_version=PYTHON, **TEST_POLICY
        )

    @function
    async def test_results(self, source: Source) -> dagger.Directory:
        """Export the same test policy for CI reporting."""
        return await dag.engineering_ci().python_test_artifacts(
            source=source, python_version=PYTHON, **TEST_POLICY
        )
```

共享模块实现检查，消费者只声明源码、范围、Python 与测试策略。新增质量要求时，
先选定实际策略，再参考[质量门禁](../docs/architecture/testing/quality-gates.md)；本例不默认启用
Coverage、Matrix、安全扫描或 Harness。

## 5. 配置私有源码访问并生成 SDK

本地使用已有 gh 账号；确认其能读取上游后，由操作者配置 Git 凭据助手：

```powershell
gh repo view dymzz/engineering-ci --json nameWithOwner,viewerPermission
if ($LASTEXITCODE -ne 0) { throw '当前账号不能读取上游，先解决权限' }
gh auth setup-git
if ($LASTEXITCODE -ne 0) { throw 'Git 凭据助手配置失败' }
git ls-remote https://github.com/dymzz/engineering-ci refs/tags/v0.6.5
if ($LASTEXITCODE -ne 0) { throw 'Git 无法读取固定版本' }
```

输出应包含第 1 节的完整 SHA。不要把 Token 写进 Git URL、TOML 或命令参数。
然后在**新项目根目录**生成本项目的 SDK：

```powershell
dagger api call --no-load-module module-source --ref-string . generated-context-directory directory --path .dagger export --path .dagger
if ($LASTEXITCODE -ne 0) { throw 'SDK 生成失败，保留诊断，不复制别仓 SDK' }
if (-not (Test-Path .dagger/sdk/pyproject.toml)) { throw '没有生成 SDK，不继续检查' }
uv lock --project .dagger
if ($LASTEXITCODE -ne 0) { throw 'Dagger 模块锁文件生成失败' }
dagger check -l --format cli
if ($LASTEXITCODE -ne 0) { throw '模块未成功加载' }
```

检查列表应有 `my-python-app-ci:lint`、`my-python-app-ci:format-check` 和
`my-python-app-ci:tests`。以实际列表为准；名字不一致时先修正模块或 Workflow。
SDK 和模块锁文件由 Engine 生成，不手写；本教程将生成的 `.dagger/sdk`、
`.dagger/uv.lock` 与根 `dagger.lock` 随首次接入提交。不要复制宿主 `.venv`。
若生成的 `.dagger/.gitignore` 排除了 `sdk/`，只移除该条规则以便提交 SDK，保留
虚拟环境和其他本地文件的忽略规则；在暂存差异中确认实际包含生成文件。

## 6. 添加消费者 Workflow

在新项目创建 `.github/workflows/ci.yml`：

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  python:
    uses: dymzz/engineering-ci/.github/workflows/reusable-python-ci.yml@804d791c60d34468166b6910c88fbc8149fdbf26 # v0.6.5
    with:
      pytest_report_function: test-results
      pytest_check: my-python-app-ci:tests
    secrets:
      ENGINEERING_CI_READ_TOKEN: ${{ secrets.ENGINEERING_CI_READ_TOKEN }}
```

这里的 `pytest_check` 必须与第 5 节实际发现的检查一致。Workflow 导出一次 pytest
结果，跳过对应的严格测试 Check，再运行其他 Check；原始 pytest 退出码仍决定测试
门禁，JSON、Summary 或 Artifact 上传成功不代表测试通过。
不要再增加一个 Job 重复调用同一套 pytest。接口细节见[pytest 报告](../docs/architecture/testing/pytest-reporting.md)。

私有访问有两个独立条件：

1. 上游 Settings → Actions → General → Access 允许同一所有者的私有仓库使用其
   Workflow；消费者的 Actions 策略也要允许调用。说明见
   [GitHub 私有 Workflow 共享](https://docs.github.com/en/actions/how-tos/reuse-automations/share-across-private-repositories)。
2. 在新消费者的 Settings → Secrets and variables → Actions 中配置
   `ENGINEERING_CI_READ_TOKEN`，仅授予实际需要的源码读取权限，供 Runner 读取私有
   Dagger 模块。已有消费者的 Secret 不会自动出现在新仓。

也可由操作者使用交互输入，避免将 Token 放进 shell 历史：

```powershell
gh secret set ENGINEERING_CI_READ_TOKEN --repo $repo
```

Token 不能修复 GitHub 在 Job 启动前拒绝的 Workflow 共享策略。遇到权限问题时停止，
由仓库所有者处理，不自动换账号、改变仓库可见性或复制 Workflow 绕过访问边界。

## 7. 本地检查并提交同一 PR

仍在新项目根目录执行与远端同源的 Dagger Checks：
以下暂存清单对应本教程创建的文件；已有项目先审查自己的差异，再确定实际交付清单。

```powershell
dagger check
if ($LASTEXITCODE -ne 0) { throw '本地检查未通过，修复后继续' }
git status --short
git diff
git add -- README.md .gitignore app.py pyproject.toml uv.lock tests .dagger dagger.json dagger.lock .github/workflows/ci.yml
git diff --cached --stat
git diff --cached
git commit -m 'ci: add shared Python checks and pytest reports'
if ($LASTEXITCODE -ne 0) { throw '提交失败' }
git push -u origin HEAD
if ($LASTEXITCODE -ne 0) { throw 'Push 失败；不要继续合并远端旧提交' }
gh pr create --repo $repo --base main --head codex/bootstrap-ci --title '接入共享 Python CI' --body '添加最小 Python 项目和固定版本的 Dagger 检查；CI 使用单次 pytest 结果生成报告并保留失败门禁。'
if ($LASTEXITCODE -ne 0) { throw 'PR 创建失败；先查询是否已有该分支的 PR，再处理实际错误' }
```

SDK 生成文件可能包含上游生成格式，不手工改写生成代码。

获取当前分支的 PR，而非手工猜测编号：

```powershell
$pr = gh pr view --repo $repo --json number,url,headRefOid | ConvertFrom-Json
$pr.url
gh pr checks $pr.number --repo $repo --watch --interval 20
if ($LASTEXITCODE -ne 0) { throw 'PR 检查未全部通过，先查看实际失败或等待状态' }
```

检查通过后审查该 PR。新仓的合并权限由其自身规则及操作者授权决定；engineering-ci
仓库内的自动合并授权不会自动扩展到新仓。决定合并时保护已审查的 Head：

```powershell
gh pr diff $pr.number --repo $repo
gh pr merge $pr.number --repo $repo --squash --delete-branch --match-head-commit $pr.headRefOid
if ($LASTEXITCODE -ne 0) { throw '合并未完成；检查 CI、Head 变化或冲突' }
if (git status --porcelain) { throw '还有未交付改动，先保留并决定后续交付' }
git switch main
git pull --ff-only origin main
```

## 8. 失败或意外停止后继续

重新打开终端后，先恢复第 1 节的 `$repo`、`$project`，再读取实际状态：

```powershell
Set-Location $project
git status --short
git branch --show-current
git log -1 --oneline
gh pr list --repo $repo --head codex/bootstrap-ci --state all --json number,url,state,headRefOid
gh run list --repo $repo --branch codex/bootstrap-ci --limit 5
```

| 停点 | 继续方式 |
| --- | --- |
| 本地测试或格式失败 | 修改当前开发分支，`dagger check` 取得本次源码结果后继续提交 |
| Commit 成功、Push 失败 | 保留该提交，解决实际 Git 权限/网络问题，再 Push 同一分支 |
| PR CI 失败 | 从 PR 检查链接查看失败项；修复并更新同一 PR，核对新的 Head |
| 原 PR 已合并但仍有本地改动 | 保留文件，决定后续 PR；旧 CI 不覆盖这些改动 |
| PR 有冲突 | 在开发分支更新并解决冲突，再执行检查与 Push；不强制合并 |
| 报告已导出但测试失败 | 读取原始退出状态和失败详情，修复测试；不将 Artifact 成功当作放行 |

现有 Run 的日志或 Artifact 可使用 engineering-ci 的只读查询/下载入口，见
[常用操作](common-tasks.md#下载已有-run)及[CLI 参考](cli-reference.md)。

## 9. 各入口作用与后续升级

| 入口 | 本例中负责什么 |
| --- | --- |
| 新项目内 `dagger check` | 执行消费者自己声明的检查 |
| `reusable-python-ci.yml` | 在 GitHub 执行消费者检查、读取原始测试结果并交付报告 |
| engineering-ci 的 `checks pr` / 当前工程检查 Provider | 检查 engineering-ci 主 checkout，不是任意消费者的通用检查入口 |
| engineering-ci 的 `scripts/release.py` | engineering-ci 自身交付与发布流程，不自动为新项目创建发布流程 |
| engineering-ci 的 `scripts/download.py` | 查询、恢复收集已有 Run；不 Dispatch |

完成接入后，新项目已有独立 CI；是否需要包构建、发布、Coverage、Matrix、安全门禁
或 Vesryn Harness，按本项目实际需求单独选择。不要复制 engineering-ci 自身的
`ci.yml`、`release.yml`、Pipeline 配置及四处版本元数据约定。

升级共享版本时，在同一个消费者 PR 中对齐 Workflow 引用、Dagger `source`/`pin`
及 Engine 生成的锁文件，再以该 PR 的真实 CI 验收。已有 Renovate 配置的仓库可以
沿用现有更新流程；新仓不会因使用共享 Workflow 自动获得 Renovate 配置。

本说明的配置依据是固定版本的共享接口及现有消费者；新项目是否实际通过，需要执行
上述本地和 PR 验证。本次文档维护没有创建消费者或触发其 Workflow。

上级入口：[用户指南](README.md)。
