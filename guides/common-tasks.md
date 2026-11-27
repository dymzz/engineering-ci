# 常用操作

在实际任务对应的 engineering-ci checkout 或 linked worktree 操作。宿主工具环境准备及共享边界见[开始使用](getting-started.md)，参数见[CLI 参考](cli-reference.md)。
新消费者使用[新仓库接入](new-repository.md)，不把消费者当成本仓库的发布目标。

## 开发、审查与合并

先确认当前分支、未提交内容与版本；版本必须与功能代码进入同一 PR。
例如准备 v0.7.0：

```powershell
git status --short --branch
git diff
uv run --locked engineering-ci version show
# 仅在本轮需要准备该版本时执行。
uv run --locked engineering-ci version set v0.7.0
```

开发中的单项/定向检查使用原生 `dagger check --engineering-ci --check=...`；本仓日常综合验证见下方[显式本地综合验证](#显式本地综合验证)。
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

在 checkout 里从开发依赖锁文件安装和调用 Lefthook 2.2.2。

```sh
uv sync --locked
uv run --locked --no-sync lefthook validate
uv run --locked --no-sync lefthook install
```

已有 `core.hooksPath` 时，默认安装会返回 Lefthook 的冲突诊断，已有 Hook
文件保持原字节。普通仓和它的 linked worktree 共享安装的 Git Hook，各次
调用各自保留 Invocation。

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
    依赖（如 `ruff`、`pytest`）强加为 Hook 门禁（完整开发依赖校验在环境准备或测试前由
    `uv sync --locked --check --no-install-project --inexact --offline` 承担；当前工作区
    lock 校验也不替代受检 Index/逐 Ref Source 的验证）；
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
`docs` / `vulture` / `tach` 仍未纳入本仓默认 Hygiene，留给后续 A2/B 切片。无 Profile
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

### 显式本地综合验证

本仓在确实需要一次完整本地综合验证时，默认使用：

```powershell
uv run --locked --no-sync engineering-ci verify --out .artifacts/verify
```

在复用共享 `.venv` 的 linked worktree 中执行上述命令时，须在当前命令作用域同时指定
`UV_PROJECT_ENVIRONMENT=<共享环境路径>` 与 `PYTHONPATH=<当前检出>/src`（也可等价运行
`uv run --locked --no-sync python -P -m engineering_ci.cli verify --out .artifacts/verify`），
确保实际运行当前 worktree 的候选 `cli/verify.py` 而非共享环境里另一检出的 editable `.pth`。

该入口启动一次原生 `lefthook run verify --no-auto-install`（Lefthook 仍是调度权威），
运行 `lefthook.yml` 中的 verify Jobs（Dagger lint、format、version/lock、核心测试），
在独立 Invocation 目录保存 `lefthook.stdout`、`lefthook.stderr` 和引用二者摘要的
`verify-timing.json`。保存的是当次 Lefthook 调用的宿主时钟读数，
`time_source_verified=false`；HEAD/config 为启动前观察，不认证工作区 Tree。
返回原生检查退出状态；被中断时返回 `130`、保留已写入的部分输出并在 `stderr`
打印目录路径，不写出完成回执。原始输出写文件而非实时透传终端，请从 stderr
提示的本次目录定位输出。

先根据实际义务决定验证范围，再采用对应标准入口并复用仍适用的原生证据；
不为记录回执而扩大范围或重复执行。定向编辑检查、pre-commit/pre-push、单项
`dagger check`、裸 `lefthook run verify --no-auto-install` 诊断以及 GitHub Actions
保持各自适用入口。`dagger check` 单项选择不等于已配置的完整 Verify Job 集合。
现有 Actions Job 保持原有执行路径，不因本入口自动改写。

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
git switch main
git pull --ff-only
uv sync --locked --group dev
uv run --locked engineering-ci version check
```

若合并后仍有未交付改动，保留它们并另行交付，不能认为已合并 PR 的 CI 已验证这些内容。
版本不符时先核对实际提交，不通过补改 main 元数据绕过 PR。

## 默认发布流程

main 已包含相应版本且完成 Release Readiness 后，由获授权的操作者请求一次正式发布。
下面命令会触发真正的 Release Workflow；它不提交、不 Push、不创建或合并 PR：

```powershell
uv run --locked python scripts/release.py publish --repo dymzz/engineering-ci --version v0.7.0 --wait
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
uv run --locked python scripts/release.py validate --repo dymzz/engineering-ci --version v0.7.0 --ref BRANCH --wait
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

将输出中的真实 Run ID 传入 `--run`。下例 RUN_ID 表示该明确 ID：

```sh
uv run --locked python scripts/download.py --repo dymzz/engineering-ci --run RUN_ID --save-log
uv run --locked python scripts/download.py --repo dymzz/engineering-ci --run RUN_ID --artifact core-tests --out .test-tools/run-evidence
uv run --locked python scripts/download.py --repo dymzz/engineering-ci --interactive
```

下载不会 Dispatch。日志为选定 attempt 的 `run.log`，Artifact 由 gh 按 Run 下载；
输出目录和原生工具规则见[CLI 参考](cli-reference.md)。取到失败 Run 的日志不表示测试通过。
