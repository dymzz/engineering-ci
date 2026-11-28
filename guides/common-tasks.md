# 常用操作

在实际任务对应的 engineering-ci checkout 或 linked worktree 操作。宿主工具环境准备及共享边界见[开始使用](getting-started.md)，参数见[CLI 参考](cli-reference.md)。
新消费者使用[新仓库接入](new-repository.md)，不把消费者当成本仓库的发布目标。

## 工程验证入口与选择

先按实际变更、受影响工程性质及验收要求确定验证义务，再选适用入口。下列按验证目的分工，不是每次必须执行的命令清单。

| 验证目的 | 标准入口 |
| --- | --- |
| 开发诊断、局部反例、专项测试 | 适用的原生工具和实际测试目标；本仓 pytest 见[本仓 pytest 调用报告](#本仓-pytest-调用报告) |
| 已定义的单项工程检查 | `dagger check --engineering-ci --check=...`；见[本地检查](../docs/architecture/local-checks.md) |
| 完整本地综合验证 | [显式本地综合验证](#显式本地综合验证)：`uv run --locked --no-sync engineering-ci verify --out .artifacts/verify` |
| Index / Ref 检查 | 原生 Git commit/push 触发 [Lefthook](#lefthook-本地入口) |
| 远端候选检查 | 现有 GitHub Actions，按实际候选和要求查询 |
| 发布验证与证据接纳 | [发布准备](../docs/delivery/release-readiness.md)及既有发布、证据合同 |

同一任务可使用多个入口；直接调用原生工具合法，不要求经过 ECI Python CLI。开发诊断不能自动替代具有不同输入、环境、范围或结果合同的工程检查；单项检查不等于完整综合验证。已有证据足以证明实际义务时直接复用，不为回执或形式扩大范围、重复执行。按实际变化和风险选择定向或综合验证，现有 Actions 保留自己的执行路径。

### 执行前的环境与对象绑定

执行前按[环境准备](getting-started.md)确认实际工作目录、受检对象、适用配置、工具环境及候选实现。切换 checkout、worktree、消费者或执行宿主时重新确认受影响的绑定，不能假定切换目录自动更新继承变量。实际采用共享环境时，调用前完成适用的宿主环境及候选源码绑定；不得先运行可能创建错误环境、导入其他检出源码或隐式修改共享环境的命令再清理。共享环境的具体准备和调用按实际采用版本的技术合同执行，不将未合并候选能力当作现行能力。

同一有效绑定直接复用；仅相关目录、解释器、依赖、配置等变化时重新确认，不要求每条命令重复检查。验证错误行为的定向探针可以使用不同入口，但须明确目的和隔离范围。

### 原生结果与验证义务对账

执行后保留实际入口、受检对象、验证范围、原生退出状态、结果及可追溯引用。输出截断、Shell 管道或后续命令的成功不得覆盖原生失败。实际产生结构化报告时，按本次执行的显式引用读取相关原生结果及交付状态，并用于适用义务对账；本仓 pytest 按[调用报告](#本仓-pytest-调用报告)执行，不只引用终端通过数量。

按义务区分本次证据已证明、已有适用证据已证明、失败、未验证和有依据的不适用。Hook 对账只证明实际 Invocation 的适用范围；CI 结果只证明相应候选及实际 Job 的范围，不自动证明其他性质或受信接纳。先履行当前提交或推送前的适用本地义务，再按实际 Hook/远端结果补充后续对账；后置证据未取得前不能提前宣称全部通过。

同一远端执行复用实际 Run/Attempt/Job 身份和原生等待结果；已有有效等待时，仅因独立事实需求、矛盾或等待失败另行查询。将义务、证据、剩余缺口及下一动作关联到原任务、PR 或 Review，按[提交关系维护](../docs/architecture/agent-instructions.md#graph-maintenance-on-every-submission)交接，不建第二份证据台账。

### CI 候选适用性

`ci.yml` 的 `change-applicability` Job 按实际累积变更集决定既有 Dagger Check 是否执行，
规则与边界见 [CI 检查适用性](../docs/architecture/ci-applicability.md)。不适用只记录
`not_applicable` 与原因，不产生原生退出或 PASS；正式 Release 与手动 Dispatch 保持全量。
决策报告为 Artifact `ci-applicability`，查询既有 Run 时直接下载，不重跑 Workflow 或 pytest：

```sh
gh run download RUN_ID --repo dymzz/engineering-ci -n ci-applicability
```

本地只读复算同一决策（不执行 Check）：

```sh
uv run --locked --no-sync python -m engineering_ci.cli ci-applicability \
  --event pull_request --base origin/main --head HEAD --json
```

相对既有有效证据的增量复用仍属 #63 接纳，未实现前不因此跳过 Required 义务；
测试目标选择仍用 #66/#165 的 Selection 入口，不把本决策当作受影响测试清单。

## 开发、审查与合并

先确认当前分支、未提交内容与版本；版本必须与功能代码进入同一 PR。
若在复用主管理 checkout 共享 `.venv` 的 **linked worktree** 中直接运行命令，先在当前开发会话作用域绑定环境变量（主管理 checkout 在自身目录可直接省略；若会话已设 `PYTHONPATH` 应前置拼接 `<当前检出>/src`）：

```powershell
# PowerShell（消费 linked worktree 会话初始化，填绝对路径）
$env:UV_PROJECT_ENVIRONMENT = "<主管理 checkout>/.venv"
$env:PYTHONPATH = "<当前检出>/src"
```

```sh
# sh / bash（消费 linked worktree 会话初始化，填绝对路径）
export UV_PROJECT_ENVIRONMENT="<主管理 checkout>/.venv"
export PYTHONPATH="<当前检出>/src"
```

仅切换目录（`cd` / `Set-Location`）**不会**自动重绑环境变量，会话作用域变量会随子进程继承传播：
- 在同一 Shell 切换至另一个 ECI worktree 时，必须重设 `PYTHONPATH="<新检出>/src"`；切回主管理 checkout 时应移除 `PYTHONPATH`（含守卫的新版 editable CLI 在同仓另一检出触发 `PYTHONPATH` 错配时会以退出码 `2` 阻断提示，直接 Python 调用仍依赖正确的会话绑定）。
- 进入独立消费者项目（如 `cd ../consumer`）执行消费者 `git` / `uv` / `dagger` 时，必须先清除 `UV_PROJECT_ENVIRONMENT`、`UV_PROJECT`、`VIRTUAL_ENV`、`PYTHONPATH`（PowerShell：`Remove-Item Env:UV_PROJECT_ENVIRONMENT, Env:UV_PROJECT, Env:VIRTUAL_ENV, Env:PYTHONPATH -ErrorAction SilentlyContinue`；sh：`unset UV_PROJECT_ENVIRONMENT UV_PROJECT VIRTUAL_ENV PYTHONPATH`）或显式指向消费者自身环境，避免继续继承 ECI 的共享 `.venv` 与 `src/`。若曾激活 ECI 虚拟环境，须先使用原生 `deactivate`（若可用）或从未激活 ECI 环境的 Shell 恢复原 `PATH`；仅删除 `VIRTUAL_ENV` 不会移除宿主 `Scripts`/`bin` 路径。随后按消费者自己的环境准备并执行。本条针对本人在消费者目录直接运行的原生命令；由 `tools.consumer` 从 ECI 检出代跑时按其宿主/目标环境区分处理，见下条。
- 从 ECI 会话调用 `python -P -m engineering_ci.tools.consumer --consumer <目录>` 时注意工具会向 `cwd=consumer` 拉起 `dagger` 与 `git`：只 `cd` 到消费者目录不会改变本进程已继承的变量，也盖不住这些子进程。`tools.consumer` 以当前解释器前缀（`sys.prefix`）区分**ECI 工具的宿主环境**与**消费者目标环境**：等于宿主前缀且不在消费者内的外置共享环境按宿主剥离；指向其他路径的 `UV_PROJECT_ENVIRONMENT` 仍作为消费者目标保留。

外置共享环境下的可执行方式——以该宿主解释器直接启动工具，`UV_PROJECT_ENVIRONMENT` 等宿主变量无需手工清除：

```powershell
$env:PYTHONPATH = "<当前检出>/src"   # 需要当前检出候选源码时
& "<共享 .venv>/Scripts/python.exe" -P -m engineering_ci.tools.consumer --consumer <消费者目录>
```

```sh
PYTHONPATH="<当前检出>/src" "<共享 .venv>/bin/python" -P -m engineering_ci.tools.consumer --consumer <消费者目录>
```

标准入口 `uv run --locked --no-sync python -P -m engineering_ci.tools.consumer ...` 在 `UV_PROJECT_ENVIRONMENT` 指向共享环境时同样适用。若改用其他解释器启动，工具无法从解释器前缀识别原宿主环境。先退出已激活的 ECI 虚拟环境（`deactivate`），或在未激活该环境的新 Shell 中执行；若曾手动修改 `PATH`，先恢复原 `PATH`，确认不含 ECI 宿主 `Scripts`/`bin` 项。仅清除环境变量不会恢复 `PATH`。随后使用明确选定、已满足 ECI 运行时依赖的解释器绝对路径，并在本次调用作用域清除或重设 ECI 绑定。不要假定系统 Python 已安装所需依赖：

```powershell
Remove-Item Env:UV_PROJECT_ENVIRONMENT, Env:UV_PROJECT, Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
$env:PYTHONPATH = "<当前检出>/src"
& "<已准备的其他解释器绝对路径>" -P -m engineering_ci.tools.consumer --consumer <消费者目录>
# 或仅把本次调用的消费者目标环境改指消费者自己的环境：
# $env:UV_PROJECT_ENVIRONMENT = "<消费者目录>/.venv"
# & "<共享 .venv>/Scripts/python.exe" -P -m engineering_ci.tools.consumer --consumer <消费者目录>
```

```sh
env -u UV_PROJECT_ENVIRONMENT -u UV_PROJECT -u VIRTUAL_ENV \
  PYTHONPATH="<当前检出>/src" "<已准备的其他解释器绝对路径>" -P -m engineering_ci.tools.consumer --consumer <消费者目录>
```

查看状态并核对或准备版本（消费端始终携带 `--no-sync`，避免触发隐式同步改写共享环境的 editable `.pth` 或误删超集包）；例如准备 v0.7.0：

```powershell
git status --short --branch
git diff
uv run --locked --no-sync engineering-ci version show
# 仅在本轮需要准备该版本时执行。
uv run --locked --no-sync engineering-ci version set v0.7.0
```

开发中的检查按上方[工程验证入口与选择](#工程验证入口与选择)执行。

### 本仓 pytest 调用报告

本仓根 `pytest` / `python -m pytest` 每次正常调用会在
`.artifacts/pytest/<invocation-id>/` 自动保留原生 `pytest-reportlog.jsonl`
（pytest-reportlog）、`junit.xml`，以及 `metadata.json`（配置时）与 `summary.json`
（结束时）。summary 分别记录阶段 `exitstatus`（`exitstatus_scope=pytest_sessionfinish_hook_stage`）、
`selection`（选中/未选 node ID 清册）、`collection`（是否完整、收集错误、usage/interrupt/internal
信号）与 `execution`：后者以原生 `pytest_runtest_logstart`/`logfinish` 表达已启动/结束，
setup/call/teardown report 仅作阶段结果（`phase_counts`），并保留 selected-but-not-started、
started-not-finished 及重复实例多重性；collect-only 标 `execution.status=not_run`，不当作执行通过。
sessionfinish hook 异常（不归因具体 writer）记录在 `sessionfinish_hook_exception`；
`native_exit_observation` 在进程内把 `outer_process_exit` 标为 `unknown`，真实进程退出由
外层调用者观察，二者不互相替代。
`collection_finish` 的 finally 回调及“无 failed CollectReport”不等于收集成功；异常退出或
未正常返回时标 `unknown`/`incomplete`。
报告交付分别表达：`report_sealed` 仅表示 summary 封存成功；`report_delivery.status` 为
`limited`/`unknown`/`incomplete`/`failed`；`native_artifacts_available` 仅表示文件存在且非空
（物理可用），`limited_checks_passed` 仅表示定向末尾/格式检查通过，`content_completeness`
恒为 `unverified`（本插件不做完整内容解析），`attribution_verified` 表示同次目录归属。
显式传入的 `--report-log` / `--junitxml` 路径不被覆盖；JUnit 路径按 pytest 原生
`expanduser`/`expandvars` 展开；不能证明同次归属时 `report_delivery.status=unknown`，
不声明完整。历史 `report.jsonl` 目录不迁移、保持可读。metadata 初始化失败时自动
reportlog/JUnit 选项不会提交，显式路径仍由原生 writer 使用。`--durations` 默认值由根
`addopts` 提供，显式 CLI 仍覆盖。可用
`ECI_PYTEST_NO_AUTO_REPORT=1` 或 `--eci-no-auto-report` 关闭。报告是本地观察：
`outer_invocation_ref` 为 `unknown`、不证明外层 Invocation/Receipt；未观察到选择范围时标
`unknown`，不把事件行数当用例数。报告交付缺失或无法验证以 `report_delivery.status` 独立
表达，不能当作通过。本仓 Dagger self-test 会加载 `tests/conftest.py`，容器内可能生成同样
本地报告但不导出；Dagger/CI 原生报告导出仍归 #167 R3/#86。报告文件仅在本机 `.artifacts/`
（已忽略）保留，不上传、不作为发布证据；共享或上传时按
[#48](https://github.com/dymzz/engineering-ci/issues/48) 敏感信息出口合同处理。不另起报告
服务，也不改变消费者通用 Dagger `_run_pytest`。

实际消费：从本次 pytest 输出的 `dir=` 完整路径（包括空格）或已核实的显式 Invocation 引用定位目录，保留外层实际进程退出；读取该目录的 `metadata.json`、`summary.json` 及所需原生报告，按实际义务对账目标、收集、选中/未执行、已启动/结束、失败、封存及归属。已有可用的同次报告直接复用，不按最新目录、相同 HEAD、时间接近或文件名猜测归属，不自动跟随未获授权的外部路径。未取得实际目录或报告时保留已观察退出及缺失/未知，不为补造报告再次运行 pytest。已有只读查询能力时使用其显式输入；尚未采用时直接读取上述文件，不新建解析服务。报告的有限检查和进程内观察不替代独立解析、实际退出及证据接纳。

当前交付边界：本地 pytest 自动生产已接入；综合 Verify、Dagger 及 Actions 使用各自实际导出和引用，不能将容器内报告生成当作宿主交付。新增原生报告的同次导出、CI 出口与外层关联分别沿 [#167 R3/#86 原交接](https://github.com/dymzz/engineering-ci/issues/167#issuecomment-7226828756)和 [#169 消费交接](https://github.com/dymzz/engineering-ci/pull/169#issuecomment-7226830766)维护，按实际已采用版本消费，不以本地报告宣称所有入口已贯通。Live Collector 专项仍按其 opt-in 入口选择，不扩大默认验证范围。

已保存的本地 pytest Invocation 可用 `engineering-ci verify report --pytest-invocation <dir>` 查询。
该入口读取最终生产合同并进行独立对账。节点、阶段及历史查询参数见[显式本地综合验证](#显式本地综合验证)。

### 提交与交付

检查当前目录快照。完成修改后用原生 Git 审阅并提交本次内容；
`git add -p` 逐块选择已跟踪改动，新文件用 `git status` 中的实际路径逐个暂存。
发布入口不会自行提交或把无关文件加入 PR。

```powershell
git add -p
git diff --cached
git commit -m "说明本次实际变更"
git push -u origin HEAD
gh pr view --json number,url,headRefOid
```

若当前分支尚无 PR，再执行 `gh pr create --draft --fill`；已有 PR 不重复创建。
提交信息与已合并 PR 供 GitHub 自动 Release Notes 使用，无需另造发布说明来源。

### Lefthook 本地入口

`lefthook==2.2.2` 定义在开发依赖组 `--group dev` 中。由**主管理 checkout**（通常为主 checkout `main`）从锁文件同步共享环境（必须保留 `--inexact`），并安装共用 Hook：

```sh
uv sync --locked --inexact --group dev
uv run --locked --no-sync lefthook validate
uv run --locked --no-sync lefthook install
```

在已共享同一 `.git` Hook 的 **linked worktree（消费端）**，不要运行省略 `--no-install-project` 的 `uv sync`；直接验证配置或检查启动器身份时绑定 `UV_PROJECT_ENVIRONMENT` 并使用 `--no-sync`（Hook 前置校验因使用 `--no-dev` 不检查 `--group dev`，若共享环境缺少 `lefthook`，`uv run --no-sync` 会回退查找 `PATH`，必要时可用原生 `lefthook version` 核对）：

```sh
UV_PROJECT_ENVIRONMENT=<共享环境路径> uv run --locked --no-sync lefthook validate
UV_PROJECT_ENVIRONMENT=<共享环境路径> uv run --locked --no-sync lefthook version
```

已有 `core.hooksPath` 时，默认安装会返回 Lefthook 的冲突诊断，已有 Hook
文件保持原字节。普通仓和它的 linked worktree 共享安装的 Git Hook，各次
调用各自保留 Invocation（若 linked worktree 共享主仓的 editable `.venv`，
默认 `python` 仍解析主仓源码而非候选 worktree 源码；该候选环境绑定归口
`#66` / `#152` / `#162 A0` 入口维护）。

并行开发使用 linked worktree 时，默认统一复用主管理 checkout 维护的共享 `.venv`，不在每个
worktree 重复维护环境（支持标准 `.git` 及配置了 `core.worktree` 的 `--separate-git-dir`
布局；bare 仓或未配置 `core.worktree` 的 `--separate-git-dir` 因 Git 自身不保存指向主
checkout 的反向路径，不隐式推导 `.venv`，须显式传入 `UV_PROJECT_ENVIRONMENT`）：
- **共享环境更新责任与 `--inexact` 语义**：
  - 仅由指定的主管理 checkout（通常为主 checkout `main`）执行
    `uv sync --locked --inexact --group dev` 维护共享环境及其 editable `.pth` 绑定；
  - 其他 worktree 新增或需要兼容锁定依赖时，在命令作用域通过
    `UV_PROJECT_ENVIRONMENT=<共享环境路径> uv sync --locked --no-install-project --inexact --group dev`
    增量补齐依赖（`--no-install-project` 避免覆盖主管理 checkout 的 editable 绑定，
    `--inexact` 保留其他并行分支需要的兼容超集包）；
  - 分支存在不兼容版本冲突时，使用显式独立环境
    `UV_PROJECT_ENVIRONMENT=<独立环境> uv sync --locked --group dev` 并在运行时显式传入。
- **Git Hook（自动默认共享环境 + 运行时就绪校验 + 候选源码绑定）**：
  - 未显式设置 `UV_PROJECT` / `UV_PROJECT_ENVIRONMENT` 时，
    [`scripts/lefthook-arguments.sh`](../scripts/lefthook-arguments.sh) 默认将
    linked worktree 的 `UV_PROJECT_ENVIRONMENT` 指向主 checkout 的 `.venv`，不把
    worktree 目录内任意遗留的 `.venv` 当作显式授权；
  - 调用 Lefthook 前执行只读原生
    `uv sync --locked --check --no-install-project --no-dev --inexact --offline`
    确认运行时环境已准备且满足核心锁定依赖，防止 `uv run --locked --no-sync` 建出空
    `.venv`，同时避免每次 `git commit` / `git push` 在 Lefthook 调度前把不适用的全开发
    依赖（如 `ruff`、`pytest`）强加为 Hook 门禁（若显式指定 `UV_PROJECT`，校验与诊断
    均对应所选 `UV_PROJECT/pyproject.toml` 与 `UV_PROJECT/uv.lock`；`lefthook==2.2.2`
    属于 `--group dev`，由环境准备确保其在共享或所选环境中可用；完整开发依赖校验在
    环境准备或测试前由 `uv sync --locked --check --no-install-project --inexact --offline`
    承担；当前工作区 lock 校验也不替代受检 Index/逐 Ref Source 的验证）；
  - 在 ECI 自身 checkout 中独立将当前检出的 `src/` 前置到 `PYTHONPATH`（无论使用默认
    主仓 `.venv`、显式外部共享 `UV_PROJECT_ENVIRONMENT` 还是显式 `UV_PROJECT`），确保
    Hook 执行当前 worktree 的候选源码而非共享环境里另一检出的 editable `.pth`。
- **Hook 之外的直接命令（`verify` / CLI / `pytest` / `ruff`）**：
  - `scripts/lefthook-arguments.sh` 仅覆盖 Git Hook；在 linked worktree 开发会话或单次
    命令直接执行 `uv run --locked --no-sync ...` 时，须显式设置
    `UV_PROJECT_ENVIRONMENT=<共享环境路径>`；
  - 调用 **ECI 自身 CLI / `verify`**（如 `engineering-ci verify --out .artifacts/verify`
    或 `python -P -m engineering_ci.cli ...`）时，**必须同时设置**
    `PYTHONPATH=<当前检出>/src`，确保执行当前 worktree 的候选 CLI 实现；本 PR 版本起的
    `engineering_ci.cli` 内置辅助诊断守卫，当从同一仓库另一检出的 editable `src/` 加载时
    返回退出码 `2`（该守卫仅对包含它的 CLI 版本与 editable 形态生效，不能替代显式
    `PYTHONPATH` 绑定）；
  - 定向 `pytest <target>` 已由 `pyproject.toml` 的 `pythonpath = ["src"]` 优先加载当前
    检出的 `src/`，`ruff` 为独立可执行文件，二者传 `UV_PROJECT_ENVIRONMENT` 即可。

`git commit` 唤醒 Lefthook 的 Index 采集 Job。采集一次本次 Index 和基线，
再把 Invocation 引用传给 `pre-commit-checks` 的准备、检查及最终对账 Job。
本仓根目录已提供 `ci-effective-profile.json`（Index `whitespace` / `forwarding`
及 Workflow/脚本安全义务）与 `ci-selection.toml` 中的 `[commit] select_tests = false`：
普通 commit 激活 Profile→Plan→Host Job→Receipt，但不选择 pytest / 不因选中测试拉起 Dagger。
`forwarding` 仅在适用的 Python 变更上执行；`.github/workflows/` 变更唤醒同 Job 内的
`actionlint` / 离线 `zizmor`，`scripts/` 变更唤醒独立 `shellcheck`（与 Workflow 内联
ShellCheck 分域）。`workflow_shellcheck` 仍在引擎合同与既有单测中，但本仓 `checks`
与本地 Index/Push Profile **均未启用**：pinned Actionlint 1.7.12 向 ShellCheck 喂入较大
`run:` 脚本时存在 stdin 管道死锁风险（rhysd/actionlint#650/#702；actionlint-py 仍止于
1.7.12.25）。内联死锁的安全采用尚未完成，仍归 #58 / #162 A3；本切片不另建 Host
timeout / 进程树清理，也不按宿主 OS 抹除适用输入或伪造 inline PASS。PR/main 上的正式
Workflow 安全 Check 接线仍属 #63，不把本切片的本地 Profile 或 fixture 测试冒充生产
Source 覆盖。
根目录已交付 `tach.toml` 与 `ci-selection.toml`（全模块 `[module_tests]`、`[[non_code_rules]]` 与显式 `.dagger/src/` 选择范围），可用于影响选择及独立执行 `tach check` / `tach check-external`；但 `docs`（Lychee）/ `vulture` / `tach` / `tach_external` 以及默认增量测试 `select_tests = true` **均尚未纳入本仓默认 Index/Push Profile 与 Verify Jobs**，`AC-06` Tach 等价去重仍留待后续消费切片。无 Profile
的兼容路径仍可只跑本地暂存空白检查。
准备 Job 从隔离检出读取暂存策略；
该检出采用 Git blob 的原始字节，原生 Git 检查采用受检 Tree 内的 `.gitattributes`
和干净的 Git 默认值。

配置版 `pre-commit` 先准备同一份 Plan。Lefthook 按适用性唤醒独立的 Host
Check Job 并安全并行。共享 Workflow 分析的检查在同一 Job 内复用原生结果，
含共享前置条件的 Plan 和 Dagger Plan 仍整体执行。最后的对账 Job 核对同一次
Invocation 的全部适用结果。失败、缺失、过滤及异源结果都会阻断最终消费。
正式接纳由 #63/#61 决定。

需要 Dagger 时，在 Lefthook 准备 Job 显式传入 `--dagger-module`。ECI 开发检出
使用 `--dagger-module .`，由本次暂存或目标 Ref 的 ECI 模块源码提供函数。
当 Python 包从同一 ECI 仓库的另一检出导入时，Git 的共同目录和 worktree 注册
确认调用方属于该仓库。模块源码仍来自本次受检树。
消费者把该参数设为 `github.com/dymzz/engineering-ci@<40 位提交 SHA>`，固定到
提供兼容 `plan-execution` 接口的 ECI 修订。消费者自己的 Dagger 配置可以保留，
它的受检树作为独立的 `--source` 输入。缺少模块引用或使用非固定修订的调用会
在准备阶段阻断。Hook 保留一次 Dagger 执行的原生结果包，核对包中的 Plan、
目录字节、原始退出和本次 Invocation。

复验固定修订的跨仓调用时，将 `ECI_TEST_DAGGER_MODULE` 设为已发布的 ECI 模块引用，
然后运行 `uv run --locked --no-sync pytest -q tests/test_native_hooks.py::test_installed_lefthook_uses_pinned_dagger_module`。
测试使用已有的 Dagger CLI、Engine 和 Tach，在隔离 Git 仓库中运行 Lefthook，核对能通过的首次提交和因暂存空白失败被拒绝的提交（未设置 `ECI_TEST_DAGGER_MODULE` 时在默认 PR CI 中跳过；消费者仓库的长期接入配置由 [新仓库接入](new-repository.md) 在后续阶段承接）。

需要 Git 比较的 Dagger Plan 会携带受检 Tree 与基线所需的 Git 对象和 Index，
由隔离检出生成 Git 配置。该输入不包含调用方的 Git 配置、其他引用或完整历史。
Git 须支持 `--attr-source` 和 `show-ref --exists`。
要单独采集新一次 Invocation、运行暂存空白检查并输出原生结果，运行
`uv run --locked --no-sync python -P -m engineering_ci.cli hooks check pre-commit --json`。

本仓的 `lefthook install` 安装 `pre-commit` 和 `pre-push`。同次推送的每个 Ref
在自己的目标树内读取 `ci-selection.toml`、`ci-hygiene.toml` 和对应动作的
[Profile](../config/hook-profiles)。`refs/*` 下指向提交的新建、更新和强制更新
执行原生 Git 空白检查，并在适用的 `src/` Python 变更上执行 `forwarding`
（纯转发提醒为 Advisory：保留 findings、展示路径、Git exit 0；空白失败、
缺失/不可验证 Source 与基线仍阻断）。Workflow / `scripts/` 变更按适用性执行
actionlint、独立 ShellCheck 与离线 zizmor（项目策略见
[.github/zizmor.yml](../.github/zizmor.yml)；本地 Profile 暂不承担
`workflow_shellcheck` 内联义务，见上文）。新建以本机记录的
`refs/remotes/origin/main` 为比较基线，更新和强推使用 Git 交付的旧对象。删除读取
待删 Ref 的本地旧树，保留 `ref_deletion` 及逐 Ref 回执（不引入无执行目标的检查义务）。
`select_tests = false` 仅用于本地推送的测试选择，完整工程检查走 `verify`。

启用前将本仓策略带入要推送的分支。首次推送新 Ref 前，若本机还没有主线跟踪引用，
先获取 `origin/main`。缺少已声明基线、目标树策略、删除旧对象或最终回执时，Git 拒绝推送。
修改 Profile 时，在 `ci-selection.toml` 的 `[push].effective_profiles` 登记目标树内
的文件，并同步更新对应 `[[effective_profile]]` 的修订和审批摘要。

顶层 `lefthook:` 调用参数交接脚本，Git Hook 仍由 Lefthook 原生安装。更新这项配置后，
运行 `uv run --locked --no-sync lefthook install` 同步已安装的 Hook。复用配置时带上同版脚本。
[lefthook.yml](../lefthook.yml) · [参数交接脚本](../scripts/lefthook-arguments.sh)

`pre-push` 的采集 Job `capture-refs` 通过 `files` 与 `{files}`
用 Hook 配置路径唤醒，规避 Lefthook 默认 `push` 文件比较可能为空的问题，并从 stdin
读取 `ECI-PRE-PUSH` 参数帧和全部 Git Ref；子任务成功后消费同一次 Invocation 的逐 Ref
Receipt，缺失最终对账时阻断。

用 `uv run --locked --no-sync lefthook uninstall` 卸载本地 Hook。

### Live Collector E2E（opt-in）

本仓的两条 Live Collector E2E（`tests/test_github_receiver.py`、
`tests/test_issue52_githubreceiver_e2e.py`）默认不运行，只在显式 opt-in 时执行；该开关
只约束这两条测试，不承诺其他测试或原生工具调用不使用 Docker / Dagger。

```powershell
$env:ECI_RUN_LIVE_COLLECTOR_TESTS = "1"
uv run --locked --no-sync pytest -q tests/test_github_receiver.py::test_live_collector_to_otlp_to_weaver_v1_45_0_e2e
uv run --locked --no-sync pytest -q tests/test_issue52_githubreceiver_e2e.py::test_live_githubreceiver_full_issue52_terminal_trusted_required_e2e
```

需要本机 `docker` / `dagger` 在 PATH、已拉取 `otelcol-contrib` 固定镜像，以及 Issue #52
路径可用的 `grpcio`。已设置 `ECI_RUN_LIVE_COLLECTOR_TESTS=1` 但缺少依赖时用例
**fail-closed**（`pytest.fail`），不会静默 skip；`ECI_SKIP_LIVE_COLLECTOR_TESTS=1`
优先，可临时压制。两条 E2E 使用固定工作目录
（`.artifacts/pytest-githubreceiver-e2e`、`.artifacts/pytest-issue52-e2e`）和固定端口
（`19428`、`19429`），不能与同一宿主上的另一个实例并发，需串行执行；本轮不实现并发隔离。
正式 #52/#74/#75 live 验收仍须在具备依赖的环境下显式开启并保留原有义务，普通 CI 通过
不证明 live 通过。

### 显式本地综合验证

本仓在确实需要一次完整本地综合验证时，默认使用：

```powershell
uv run --locked --no-sync engineering-ci verify --out .artifacts/verify
```

在复用共享 `.venv` 的 linked worktree 中执行上述命令时，须在当前命令作用域同时指定
`UV_PROJECT_ENVIRONMENT=<共享环境路径>` 与 `PYTHONPATH=<当前检出>/src`（也可等价运行
`uv run --locked --no-sync python -P -m engineering_ci.cli verify --out .artifacts/verify`），
确保实际运行当前 worktree 的候选 `cli/verify.py` 而非共享环境里另一检出的 editable `.pth`。

该入口启动一次原生 `lefthook run verify --no-auto-install`。Lefthook 并行调度 lint、
format、version/lock、核心测试四组。前三组运行原生 Dagger Check。核心测试组调用
`repository-test-artifacts` 导出同一次 pytest 的原始退出、JSON、可用的 JUnit 与摘要，
随后运行导出产物的原生 `ci.py gate`。裸 Lefthook 调用仍使用 `tests` Check。
本次 Invocation 目录保存 `lefthook.stdout`、`lefthook.stderr` 和 `verify-timing.json`。
`child_references` 记录 `core-tests/` 中实际导出的相对路径与 SHA-256。查询从回执直接定位，
并分别核验原始退出、引用和报告交付。报告声明 JUnit 可用时，必须存在实际引用，且
JUnit 的摘要、格式和计数与原生 JSON 一致。报告不可用时仍可查看已核验的原始退出。
预检从 Lefthook 生效配置取得完整 Job 路径，并保存在成功、失败和中断回执中。封存失败或报告准备中断时，
已取得的实际子产物引用仍可核验。顶层 `core-tests` 声明具有交付义务，缺少整个子记录、记录类型错误或生产者不符均使证据不完整。
预检尚未取得声明或历史回执缺少声明时，查询显示该义务未声明。其他 Job 按其完整路径核验。
`verify-timing.json` 同时记录过程内 `perf_counter` 单调耗时
（按 `cli_preflight`、`lefthook_native`、`report_preparation` 拆分，显式标注排除
`seal` 后写盘与外层 `uv`/Shell 启动耗时）、有界解析得到的 Lefthook Job/嵌套 Group
分级与跳过/失败耗时明细（区分 Group 的原生 `cumulative_child_durations` 累计和、`wall_clock_seconds="unknown"` 与根部 `lefthook_reported_wall_seconds` 及 `bytes_parsed`；缺失 summary 或缺项显式记为 `"unknown"`），以及宿主 UTC
起止边界（发生时钟回拨时 `completed_at_ns=null` 并保持单调耗时与
`time_source_verified=false`）；HEAD/config 为启动前观察，不认证工作区 Tree。

若准备工作失败（含输出文件打开或进程拉起失败 `output_open_failed` / `spawn_failed`）、
收到中断信号（含统一 `subprocess.Popen` 启动后等待异常 `wait_failed`、原生返回后报告准备阶段中断 `report_preparation_interrupted`，以及 `Popen` 前后尚未观察到任何子进程输出即中断时记 `native_started="unknown"` 与 `started_at_ns=null`）或最终回执落盘失败（`seal_failed`），
会在同目录写入 `verify-status.json`（按实际进展区分 `native_started`、已有观察事实、`native_exit_code`、明确来源 `native_exit_source` 及 `outer_exit_code=130`，不将已捕获/reap 的子进程退出码盲目抹成 `null`，亦不写虚假完成回执 `verify-timing.json`）；若 `verify-timing.json` 与 `verify-status.json` 并存或 timing 文件损坏，`verify report` 会显式记录 `verify_timing_status_conflict`/`verify_timing_invalid` 而不掩盖封存失败。其中
`seal_failed` 会保留真实的 `native_exit_code`，而 `verify` CLI 以 `native_exit_code or 1`
退出（即原生检查退 `0` 但无法封存回执时返回 `1`）；`SIGKILL` 等不可捕获强杀仅保证强杀前已落盘字节。
返回原生检查退出状态；被中断时返回 `130`、保留已写入的部分输出并在 `stderr` 打印目录路径。原始输出写文件而非实时透传终端，
请从 `stderr` 提示的 Invocation 目录定位输出。

真实 Git Hook（`pre-commit` / `pre-push`）沿用已有 Invocation、Plan、Execution 和 Receipt。
`.timing-*` 记录实际 prepare、run、reconcile、consume 阶段，保留首次有效激活计时。
pre-push 逐 Ref 记录耗时及已观察的退出。后验 Source 校验失败或 Execution 封存失败时，
`.observation*` 尽力保存原始报告，计时记录保留已知退出。生产对账只读取完成 Source
校验后封存的 `.execution*`。外层 Git、Shell 和 uv 启动成本由单独测量记录（#162 R17）。

`verify report` 从显式路径读取原始证据。传入 `--verify-dir` 可查询该次核心测试交接。
Hook 支持 `--hook-root` 与 `--invocation-ref` 成对使用，或单独使用 `--hook-invocation`。
历史 Hook 的字节、Plan、激活、执行包络与 Receipt 检验复用生产端校验。查询分别显示
Plan 预期单元、观察单元、缺失单元和未封存观察。Git 实时 Source 检验由 Hook 自身完成。

报告分别显示具体执行身份、Artifact 成员关系、内容等价和 Run/Attempt/Trace 上下文。
Hook 和独立 Report 共用原生结果发生判定。封存的报告仍保留未执行状态和结果拒绝原因。
Plan 范围声明与观察到的兼容投影分别记录。#87 A.1 保留原 Trace 和 isolation，
B 执行绑定保持未知。GitHub 性能汇总保留原始指标，固定 Attempt 的 Job 清册由 #131/#75 提供。
Dagger 和查询共用标准库 pytest/JUnit 计数规则。`--pytest-invocation` 读取[本仓 pytest 调用报告](#本仓-pytest-调用报告)
所声明的文件，并保留其 selection、collection、execution、report_delivery、封存状态和
`sessionfinish_hook_exception`。
生产者的 Hook 阶段退出与外层进程退出分别记录。ReportLog 的独立解析状态、节点实例与阶段结果计数对账均可查询。
阶段计数差异进入完整性冲突，保留原始节点观察。历史比较要求节点和阶段计数均已对账，
缺少阶段事实的旧报告仍可查询。查阅 Invocation 外的文件时，显式传 `--junit-xml` 或
`--pytest-reportlog`。查询保留原始文件，完整性冲突或输出路径覆盖输入时退出 2。

```powershell
uv run --locked --no-sync engineering-ci verify report `
  --verify-dir .artifacts/verify/<verify-invocation> `
  --hook-root . `
  --invocation-ref invocation:<32hex> `
  --execution-report <bundle-dir-or-report.json> `
  --pytest-invocation <pytest-invocation-dir> `
  --junit-xml <junit.xml> `
  --pytest-reportlog <pytest-reportlog.jsonl> `
  --dagger-telemetry <dagger-trace-receipt.json> `
  --ci-performance <ci-performance-report.json>
```

验证范围、证据复用和各入口的区别见[工程验证入口与选择](#工程验证入口与选择)。
裸 `lefthook run verify --no-auto-install` 可用于原生诊断，不代替 ECI verify 的同次输出记录。

可对已保存的产物使用 `--test-records`、`--test-node`、`--test-module` 和
`--test-phase setup|call|teardown|case_total`。记录受原始读取上限约束，结果保留
重复实例的观察顺序、阶段结果及未知耗时。ReportLog 用 pytest node ID 和模块路径，
JUnit 用 `classname::name` 和 `case_total`。模块/阶段的统计为实际可见记录累计。

```powershell
uv run --locked --no-sync engineering-ci verify report `
  --pytest-invocation <baseline-invocation-dir> `
  --pytest-invocation <candidate-invocation-dir> `
  --test-node 'tests/test_sample.py::test_case' --test-phase call --compare-tests
```

`--compare-tests` 以第一份 ReportLog 样本为基线。比较要求每份样本各有一个边界完整的
Session，并要求不同 Invocation、相同的经对账选中实例范围和一致的 pytest/Python/插件版本、
平台、根目录及调用参数。来源冲突、交付不完整、范围未知或实例无法配对时，查询保留原始记录
及不可比原因。查询不启动测试。

Windows 终端下若遇到 Dagger 非交互调用无输出挂起（历史观察与诊断见
[上游观察](../.agent/upstream-observations.md#dagger-beta-windows-非交互版本探测挂起)），
可仅在当前命令进程作用域设置 `$env:FORCE_DARK_MODE = "1"` 后执行，不改动全局环境变量
（非所有 Windows 调用都会触发该现象，例如当前 `dagger version` 捕获输出测试可在不设置时正常返回）。

### 审查与合并

在当前分支执行以下命令，取得当前 PR 的身份和真实检查：

```powershell
$pr = gh pr view --json number,url,headRefOid | ConvertFrom-Json
$pr.url
gh pr checks $pr.number --watch
```

核对当前 head 的四个实际检查 `python-ci / checks`、`sync-tool-tests`、`version-metadata`、`dagger-native-telemetry / a1-gate`，
完成代码审查后再 Ready 和合并。合并权限遵循 [AGENTS.md](../AGENTS.md)。
如果检查尚未完成或失败，在此停止；修复并提交到同一分支，再查同一 PR。

```powershell
gh pr ready $pr.number
gh pr merge $pr.number --squash --delete-branch --match-head-commit $pr.headRefOid
# 以下在主管理 checkout 切回 main 同步共享环境（保留 --inexact 超集并恢复主仓 editable .pth 绑定）
git switch main
git pull --ff-only
uv sync --locked --inexact --group dev
uv run --locked --no-sync engineering-ci version check
```

若合并后仍有未交付改动，保留它们并另行交付，不能认为已合并 PR 的 CI 已验证这些内容。
版本不符时先核对实际提交，不通过补改 main 元数据绕过 PR。

## 默认发布流程

main 已包含相应版本且完成 Release Readiness 后，由获授权的操作者请求一次正式发布。
下面命令会触发真正的 Release Workflow；它不提交、不 Push、不创建或合并 PR（`scripts/release.py` 会自动加载所在 checkout 的 `src/`；若在复用共享 `.venv` 的 linked worktree 调用，先设置 `UV_PROJECT_ENVIRONMENT=<共享环境路径>` 并始终携带 `--no-sync`）：

```powershell
uv run --locked --no-sync python scripts/release.py publish --repo dymzz/engineering-ci --version v0.7.0 --wait
```

GitHub 在同一 Run 完成完整验收、生成 Manifest、准备 Draft、读回验证 Asset，再正式发布。
本地检查与 PR 检查不替代正式发布门禁。不需要先执行一次 validate 再重复完整验收。
成功必须显示 `published` 和实际 Release URL，不能仅依据请求命令退出 0。

不加 `--wait` 时只请求并返回已知 Run；中断时保存终端输出的完整继续命令。
例如输出带有真实 Run ID 的 `release.py wait ... --run ...`，复制它即可等待同一执行。
只想查询则将动作改成 `status` 或 `verify`，保留输出中的 repo、version、run。
这些动作不会新建 Workflow，不再扫描旧状态库或猜测 latest。

## 显式非发布验证

只有需要单独验证候选分支时使用；BRANCH 必须替换为确实包含该版本的远端分支：

```sh
uv run --locked --no-sync python scripts/release.py validate --repo dymzz/engineering-ci --version v0.7.0 --ref BRANCH --wait
```

它只运行非发布模式；`validated` 与 `published` 分别表示验证通过和正式发布通过。
候选分支验证不能冒充合并后 main 的发布证据。

## 大阶段分支备份

每个已验收的大阶段完成后备份已提交、已 Push 且验证对应的 SHA；同一版本仍用同一开发 PR，
不提前合并中间态。未提交文件不在备份中。合并后备份以 GitHub 确认的 merge SHA 为准。

使用任务确定的阶段、版本与 SHA 命名备份分支。以下变量来自当前阶段的交付记录：

```powershell
git branch $backup $verifiedSha
git push origin "refs/heads/${backup}:refs/heads/${backup}"
git ls-remote --exit-code origin "refs/heads/${backup}"
```

核对远端 SHA 等于来源。同名不同 SHA 或 Push 失败时停止并报告，不覆盖备份，不改账号或工作区。
需要继续开发时从备份创建开发分支，不能在备份分支上继续修改；操作前保留当前未提交改动。
备份不代表正式发布通过。

## GitHub 认证与工程调用入口

复用当前账号已有认证，按宿主 gh、Dagger 模块源、容器私有依赖和业务 API 区分凭据边界。
只读诊断及调用方 Secret 入口见[Dagger 认证](../docs/configuration/dagger.md#按执行位置选择-github-认证入口)。
遇到配置或凭据存储权限错误时，先确认实际执行上下文，不自行重新登录、换账号、扩大权限或建立替代工作区。
已删除的 Vesryn 依赖不再需要认证桥；实际私有消费者依赖继续由调用方显式提供受支持的 Secret。

## 停止与继续

| 实际停点 | 继续方式 |
| --- | --- |
| 本地检查失败 | 按 Dagger 的原始失败修复，再执行受影响 Check；不读取历史 passed 收据 |
| PR 检查或审查未完成 | 使用上面当前分支的 `gh pr view / checks`，继续同一 PR |
| 已知 Run，等待中断、超时或权限不足 | 使用输出中的相同 Run 执行 status；需要等待时使用 wait |
| 明确 HTTP 拒绝 | 修正仓库、ref、版本或权限；该次意图已清除，可重新请求 |
| POST 响应丢失、204 无 Run ID 或结果异常 | 未决意图保留，停止 POST；按输出的 repo / workflow / commit 查询，人工确认实际 Run 后用显式 `--run` |
| Tag / SHA / 版本 / Manifest 冲突 | 检查实际远端事实；不覆盖 Tag、不补写历史正式 Release |
| Artifact 或日志下载失败 | 保留已成功文件，选择新的输出目录按同一 Run 重新下载；不承诺字节恢复 |

未决意图位于命令输出的 `.test-tools/release-*.json`。它只阻止本目录同请求重复 POST，
不是跨机器锁。不要在确认远端是否接收之前删除它或换目录重发。文件损坏同样停止。
fine-grained PAT 不支持 `gh run watch` 时，status 仍只查同一 Run API；不更换账号或扩大权限。

## 下载已有 Run

将输出中的真实 Run ID 传入 `--run`（`scripts/download.py` 会自动加载所在 checkout 的 `src/`；若在复用共享 `.venv` 的 linked worktree 调用，先设置 `UV_PROJECT_ENVIRONMENT=<共享环境路径>` 并始终携带 `--no-sync`）。下例 RUN_ID 表示该明确 ID：

```sh
uv run --locked --no-sync python scripts/download.py --repo dymzz/engineering-ci --run RUN_ID --save-log
uv run --locked --no-sync python scripts/download.py --repo dymzz/engineering-ci --run RUN_ID --artifact core-tests --out .test-tools/run-evidence
uv run --locked --no-sync python scripts/download.py --repo dymzz/engineering-ci --interactive
```

下载不会 Dispatch。日志为选定 attempt 的 `run.log`，Artifact 由 gh 按 Run 下载；
输出目录和原生工具规则见[CLI 参考](cli-reference.md)。取到失败 Run 的日志不表示测试通过。
