# Python 项目接入 Engineering CI

消费者使用公共 Dagger Functions 和共享 Workflow，按自己的测试范围声明检查。
本仓库不再提供交互建仓、Git 生命周期程序或工程检查 Provider。
现有项目保留自己的源码、测试和依赖；新目录使用原生 `git init`、`gh repo create` 建仓。

## 环境与固定引用

准备 uv、Git、gh、Docker Linux Engine 和 Dagger `1.0.0-beta.16`，见
[Dagger 配置](../docs/configuration/dagger.md)。下面沿用已发布基线
[v0.6.5](https://github.com/dymzz/engineering-ci/releases/tag/v0.6.5)，完整 SHA 为
`804d791c60d34468166b6910c88fbc8149fdbf26`。升级时同步替换 Workflow 与模块的固定引用，
核对目标 Release 的 Manifest，不从本仓候选元数据推断已经发布。

消费者在自己的目录操作。以下新仓示例中的 `$repo` 使用实际 OWNER/name；已有仓库
直接使用自己的开发分支，不重复初始化或创建远端：

```powershell
$repo = 'dymzz/my-python-app'
git init -b main
Set-Content README.md '# My Python App' -Encoding utf8NoBOM
git add -- README.md
git commit -m 'chore: initialize repository'
if ($LASTEXITCODE -ne 0) { throw '初始提交失败；先处理 Git 身份或原始错误' }
gh auth status
if ($LASTEXITCODE -ne 0) { throw '先处理 gh 认证错误' }
gh repo create $repo --private --source . --remote origin --push
if ($LASTEXITCODE -ne 0) { throw '远端创建或 Push 失败；保留当前目录' }
git switch -c codex/add-ci
```

已有远端不重复创建；Git/gh 报错时保留原目录并处理实际错误，不覆盖远端历史或切换账号。
本例使用 Python 3.14；消费者的实际支持版本、测试路径和质量要求由自己的项目明确声明。
在接入下面的 CI 前，项目应有自己的 `pyproject.toml`、真实测试与 `uv lock` 生成的锁文件；
本页不生成业务代码。使用 `uv sync --locked` 安装消费者环境，无需安装宿主 engineering-ci Python 包。

## 声明消费者 Dagger Checks

本例使用既有消费者的 Python 模块兼容配置 `dagger.json`。
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
Coverage、Matrix 或安全扫描。


## 生成 SDK 并执行检查

在消费者根目录使用已有的 Git / Dagger 认证生成本项目 SDK，不复制别仓 `.venv` 或 SDK：

```powershell
dagger api call --no-load-module module-source --ref-string . generated-context-directory directory --path .dagger export --path .dagger
if ($LASTEXITCODE -ne 0) { throw 'SDK 生成失败；保留原始错误' }
uv lock --project .dagger
if ($LASTEXITCODE -ne 0) { throw '模块锁文件生成失败' }
dagger check -l --format cli
if ($LASTEXITCODE -ne 0) { throw '模块未成功加载' }
dagger check
if ($LASTEXITCODE -ne 0) { throw '修复实际检查失败后继续交付' }
```

SDK、模块锁文件和原生 `dagger.lock` 由 Engine / uv 生成后审查提交，不手写。
检查列表应包含 `my-python-app-ci:lint`、`my-python-app-ci:format-check` 和
`my-python-app-ci:tests`；以实际列表核对 Workflow 的 `pytest_check`。

## 共享 Workflow 与权限

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

这里的 `pytest_check` 必须与本地实际发现的检查一致。Workflow 导出一次 pytest
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


## 提交、审查与升级

审查本项目的实际 diff，使用原生 Git 提交和 Push，再用 `gh pr create` 创建开发 PR。
`gh pr checks --watch` 查询本 PR 的真实 CI；合并遵守消费者自己的授权与保护规则。
本地检查、报告导出和远端检查分别判断，不以 Artifact 成功覆盖 pytest 失败。

后续修改继续更新同一个开放 PR。遇到中断先查看 `git status`、`gh pr view` 和
`gh run list`，按原生工具状态继续，不使用已删除的 bootstrap checkpoint。
升级时在同一个消费者 PR 中对齐 Workflow 引用、Dagger source/pin 和生成锁文件。
已有 Renovate 的仓库沿用现有更新规则。

公共接口见[支持范围](../docs/delivery/public-components.md)；本仓自己的 `checks`、
`version` 和 `release` CLI 需要 engineering-ci checkout，不是消费者安装的通用 Pipeline。
上级入口：[用户指南](README.md)。
