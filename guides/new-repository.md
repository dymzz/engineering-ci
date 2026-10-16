# 新建 Python 仓库并接入 Engineering CI

从空目录建立一个私有 Python 仓库，接入共享检查，完成本地验证、PR、审查和合并。
已有仓库从第 3 节开始，保留自己的代码、测试、依赖和目录。

下面使用 `dymzz/my-python-app` 作为具体示例。执行前把仓库名和路径改成自己的目标；
这些命令供操作者执行，本说明不会替你创建仓库、修改权限或发布版本。

## 选择公共组件与固定版本

先按[公共组件支持范围](../docs/delivery/public-components.md)选择接口。
完整容器 CI 使用 Dagger；远端 Job 编排使用共享 Workflow；只复用执行状态和恢复时可直接安装 Python 包。
本仓库 `checks pr` / `release publish` 需要完整 checkout，不应在消费者中复制这套本仓库操作。

下面的安装命令固定已发布基线 `v0.6.5`；`v0.6.7` 的新接口在正式 Release 完成前属于候选，
只能用经过验证的固定 Commit 验收。通过 PR、构建 wheel 或存在同名元数据都不代表已经发布。
不要消费浮动 main。私有仓库需要当前 Git/gh 身份具备 Contents 读取权限；失败时先修复权限，
不要切换账号、复制源码或把 Token 放进 URL。

仅安装 Python 公共执行组件，在消费者自己的环境中运行：

```powershell
uv pip install 'git+https://github.com/dymzz/engineering-ci.git@v0.6.5'
python -c "from engineering_ci.execution import ExecutionFSM, StateStore; print(ExecutionFSM.__name__, StateStore.__name__)"
```

Python 包要求 3.14，不自动安装 dev/可选 Harness 或启动数据库。
已安装的下载入口使用 `python -m engineering_ci.cli download --help`；它只能查询和下载已有 Run。
完整 Python 状态示例见[执行 API](../docs/architecture/execution/state-and-recovery.md#python-组件消费)。

Dagger 消费者按本文原有步骤安装固定模块并生成 SDK；已有项目直接从第 3 节配置开始。
Workflow 的 `uses: ...@TAG` 与模块引用应采用同一个已发布版本，原生 `dagger.lock` 一起审查。
输入、Secret、输出和失败语义见[Dagger 公共函数](../docs/configuration/dagger.md#公共函数与凭据)。
报告模式用一次 `python_test_artifacts` 替换指定的 pytest check，不再调用第二次 pytest 获取 JSON。
使用 `git_token` 的单版本接口从 v0.6.7 提供；旧版及 Matrix 不声明该参数。

升级时先确认目标正式 Release 及 Manifest 的 Tag/SHA，再同步 Workflow 与模块固定引用，
生成 SDK/锁文件并在消费者 PR 中执行检查。保留业务测试；失败退出、报告和下载分别判断。
失败恢复见[常用操作](common-tasks.md)，本项目发布准备要求见[Release Readiness](../docs/delivery/release-readiness.md)。

## 交互式建仓入口

新目录可以使用下面的工具生成骨架，并继续到 SDK、检查和 Draft PR。在 **engineering-ci 的主
checkout** 执行；新项目使用你输入的绝对路径，不复制本仓的 `.git`、SDK 或虚拟环境：

```powershell
Set-Location D:/workspace/engineering-ci
uv run --locked python -m engineering_ci.tools.bootstrap
```

第一步输入新目录的绝对路径，例如 `D:\workspace\my-python-app` 或
`D:/workspace/my-python-app`；可以粘贴外层带单/双引号的路径，目录名包含空格也支持。
第二步输入 GitHub 仓库名 `OWNER/name`，例如 `dymzz/my-python-app`，这里不填本地路径。
然后选择是否连接 GitHub **私有仓库**；不存在时创建，已存在时单独选择处理方式。
工具显示固定版本、SHA、文件列表和 Git 操作，最后输入 `create` 才执行。
仓库名使用小写 kebab-case；目标目录必须不存在，父目录必须已经存在。
交互输入格式不正确时，工具说明原因并留在当前字段重新输入；Ctrl+C 可取消。
输入结束时会指出具体停点；最终确认之前不会创建目录或执行 Git/gh。
已有项目继续按本文的手工接入步骤操作，工具不会自动接管已有目录或远端仓库。

生成后会留在交互流程，要求明确选择下一阶段：

- `1`：连接 GitHub，准备依赖和 SDK，执行 Dagger 检查，再引导到 Draft PR。
- `2`：只准备依赖和 SDK、执行本地 Dagger 检查。
- `q`：保存生成结果并退出。

选择 `1` 后，工具显示目标新仓的改动，分别确认 `git add --all`、`git commit`、Push 和
Draft PR；提交信息在 `git commit -m` 提示中输入。每次确认只授权显示的操作，拒绝即停，
保留已完成步骤。Draft PR 创建后显示 URL、实际 Head 和查看 CI 的命令，停在人工审查。
不会合并或发布，也不把本地检查通过当成远端 CI 通过。

先预览、不调用 Git/gh、不创建文件：

```powershell
uv run --locked python -m engineering_ci.tools.bootstrap --repo dymzz/my-python-app --path D:/workspace/my-python-app --plan
```

Codex 或其他非交互调用可显式确认操作范围：

```powershell
# 只建立本地仓库和骨架。
uv run --locked python -m engineering_ci.tools.bootstrap --repo dymzz/my-python-app --path D:/workspace/my-python-app --yes

# 二选一：加 --github 才会创建私有远端并 Push 初始 main。
uv run --locked python -m engineering_ci.tools.bootstrap --repo dymzz/my-python-app --path D:/workspace/my-python-app --github --yes

# 只准备依赖/SDK 并检查，不连接远端、不 Commit/Push。
uv run --locked python -m engineering_ci.tools.bootstrap --resume D:/workspace/my-python-app --setup --yes

# 显式请求完整交付；非交互 --yes 确认这些操作，-m 是 Git 提交信息。
uv run --locked python -m engineering_ci.tools.bootstrap --resume D:/workspace/my-python-app --deliver --yes -m 'ci: 接入共享检查'
```

`--yes` 只确认已请求的操作，本身不隐含远端、安装依赖、检查或交付；`--deliver` 明确包含
连接远端、检查和 Draft PR，已有远端仍需选择连接或覆盖。工具复用当前 gh 认证，不登录、切换账号
或修改 Workflow 共享权限。请求远端创建时会核对本节下方固定的正式版本、Tag SHA 和私有
Workflow 共享条件。创建权限或认证失败会停止；不会另选仓库、路径或公开可见性。

生成后，本地 `main` 只有 README 初始提交，当前分支为 `codex/bootstrap-ci`；业务示例和
CI 文件留作待审查改动。新建或空远端只收到这一个初始 `main` 提交；已有远端按下一节明确选择，
不因文件已生成就自动覆盖历史。
生成步骤本身不安装依赖或执行检查。选择后续阶段才调用 `uv lock`、`uv sync --locked`、
原生 Dagger SDK 生成、模块锁定、检查发现及 `dagger check`。它们在目标新仓运行，
显示实际命令和原始输出；同一次流程只调用一次 `dagger check`，不另跑 pytest 收集报告。
工具不配置 Secret 或共享权限；私有源访问的人工准备见第 5 节。手工步骤第 1～9 节仍可
按实际停点独立使用，不需要在工具完成后重复执行整份教程。
两份 lockfile 必须由实际 `uv lock` 生成，工具不会编造锁文件或检查通过记录。

执行结果以一份 JSON 写到 stdout，提示和预览写到 stderr；`--help` 使用标准参数帮助。
`status=generated` 只说明骨架生成完成，
`checks=not_run`、`pull_request=not_created` 明确保留未执行事实。结果包含目标上下文、
固定版本/SHA、初始提交 SHA、恢复文件、保留的用户修改以及带 `cwd` 的后续 argv。
继续执行时，`local_setup.steps` 记录已执行、失败及未执行步骤和原始退出码；
`operations` 记录 Git/PR 写操作。`status=checked` 表示本次本地检查完成，
`status=draft_pr` 表示实际读回了 Draft PR，`remote_ci=not_inspected` 保留远端未验收事实。
退出码 0 表示生成/预览完成或主动取消；缺少工具和参数错误为 2，其他预检/恢复阻碍为 3，
原生命令失败保留其非零退出码，Ctrl+C 为 130。失败不能按生成成功处理。

### 停止后继续

本地上下文原子保存在新项目的 `.test-tools/bootstrap.json`，受文件锁保护，且被 Git 忽略。
保留该目录，在 engineering-ci checkout 显式恢复，不重复运行新建命令：

```powershell
uv run --locked python -m engineering_ci.tools.bootstrap --resume D:/workspace/my-python-app
# 需要创建/确认远端时再加 --github。
uv run --locked python -m engineering_ci.tools.bootstrap --resume D:/workspace/my-python-app --github
```

第一条会重新显示阶段菜单，选择 `1` 继续到 Draft PR，无需逐项复制 JSON 中的命令。
显式 `--setup` 或 `--deliver` 可直接选择范围；去掉 `--yes`，仍分别交互确认 Git 写操作。
缺失工具、检查失败或 Ctrl+C 时保留目标、实际步骤结果和恢复命令。修复原因后恢复同一目录；
未完成 Merge 则先按原生 Git 解决冲突并 `git merge --continue`。

恢复时重新调用原生准备与检查命令，由 uv/Dagger 自己处理增量与缓存，不把上次 JSON
中的 passed 无条件复用。检查前后及 Commit/Push/PR 前核对源码、工具版本和相关环境摘要；
检查期间编辑、Commit Hook 改动源码等会将结果标为 `stale` 并停止交付。生成 SDK 的源文件
即使被生成器的 ignore 规则忽略，也纳入检查身份并明确暂存，缓存和虚拟环境不随之提交。
Push 已完成时核对实际远端 Head；PR 创建响应丢失时查询该分支已有 PR，不重复创建。

恢复不会覆盖已经存在的文件；与模板不同的文件在 `preserved_changes` 中列出。
Git 身份未配置、当前分支异常、main 已变化或 origin 不符时，先按返回的具体原因处理。
工具缺失且目录尚未创建时，输出会保留原来的 `--repo/--path` 新建命令，而非无效恢复命令。

如果远端创建请求发出但响应丢失，结果为 `remote_result=unconfirmed`，不会再次创建。
先执行返回的只读 `gh api` 命令，核对仓库名称、私有可见性和实际数字 ID；确认是本次目标
后，在返回的恢复命令中显式提供 `--confirm-remote-id`。该选项仅用于未确认的创建意图，
不能代替已有远端的连接/覆盖选择。Push 失败后保留目录，按同一个仓库身份继续恢复。

### 远端仓库已经存在

直接执行上面的 `--resume ... --github`，交互模式会显示仓库 ID、实际 main SHA 与本地
README 初始提交，然后提供 `1 连接 / 2 覆盖 main / q 取消`。不会删除重建整个仓库。

- **连接：** 核对私有可见性与 Push 权限，配置 origin。空 main 可以接收本地初始提交；
  非空 main 保留原有历史。如果与本地初始提交不同，交付阶段先显示并单独确认原生
  fetch/merge，将远端历史合入当前开发分支；README 等冲突由人解决，不自动选边。
  仅生成时，`next_actions` 仍给出手动 fetch/merge 命令。
- **覆盖 main：** 显示将被替换的完整 SHA、本地 README-only 初始提交和实际备份 Ref。
  必须再输入工具显示的 `overwrite OWNER/name SHA前12位` 确认文字。先将旧 main 保存到
  `codex/bootstrap-backup/完整旧SHA`，读回核对，再用 `--force-with-lease` 替换 main。
  其他分支不会作为覆盖目标；业务和 CI 文件仍待后续 PR。结果返回实际 `backup_ref` 和
  `backup_sha` 供人工恢复，工具不自动回滚后续工作。
- **取消：** 保留已生成的本地文件，不连接或改写远端。

备份失败、备份 Ref 已指向其他提交、仓库身份/权限变化或确认后的 main SHA 变化时停止。
分支保护拒绝 Push 时保留原始失败码，不修改保护规则。覆盖响应丢失后，恢复会核对同一个
仓库、main 和备份；已完成的覆盖不再重发，未完成的操作仍受原 SHA 条件约束。

非交互连接已有仓库可显式执行：

```powershell
uv run --locked python -m engineering_ci.tools.bootstrap --resume D:/workspace/my-python-app --github --existing-remote connect --yes
```

非交互覆盖还必须显式指定 `--existing-remote overwrite` 与 `--expected-main-sha`，使用
实际读到的完整 SHA；停止结果的 `observed_remote` 和待人工确认的 `next_actions` 提供
实际值和完整 argv。`--yes` 本身不会选择覆盖，不能用猜测 SHA。

恢复上下文 Schema 2 在原有字段之外记录 `remote_mode`（create/connect/overwrite）与
`overwrite_main_sha`。Schema 1 在已确认恢复、取得文件锁后原子迁移；保留 seed SHA、
创建意图与远端 ID，原先未确认的创建仍禁止再次 POST。它独立于执行 FSM 的状态 Schema。
暂只支持默认分支 main 的已有私有仓库，其他分支布局需人工处理。

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
