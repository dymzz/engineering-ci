# 常用操作

在 engineering-ci 的原 checkout 操作。环境准备见[开始使用](getting-started.md)，参数见[CLI 参考](cli-reference.md)。
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
dagger check
```

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

`git commit` 唤醒 Lefthook 的 Index 采集 Job。采集一次本次 Index 和基线，
再把 Invocation 引用传给 `pre-commit-checks` 的准备、检查及最终对账 Job。
本仓当前仅为 `pre-push` 绑定了根级策略与 [Profile](../config/hook-profiles)，根目录尚无
`ci-effective-profile.json` 或 `ci-effective-profile.toml`，因此本仓 `pre-commit` 默认仍使用
本地 Git 规则运行原生暂存空白检查（不产出 `Plan` 与多 Job 拆分）。当受检树声明了
`ci-selection.toml` 和已绑定的 `ci-effective-profile.json` 或
`ci-effective-profile.toml` 时，准备 Job 从隔离检出读取暂存策略。该检出
采用 Git blob 的原始字节，原生 Git 检查采用受检 Tree 内的 `.gitattributes`
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
执行原生 Git 空白检查。新建以本机记录的 `refs/remotes/origin/main` 为比较基线，
更新和强推使用 Git 交付的旧对象。删除读取待删 Ref 的本地旧树，保留 `ref_deletion`
及逐 Ref 回执。`select_tests = false` 仅用于本地推送的测试选择，完整工程检查走 `verify`。

启用前将本仓策略带入要推送的分支。首次推送新 Ref 前，若本机还没有主线跟踪引用，
先获取 `origin/main`。缺少已声明基线、目标树策略、删除旧对象或最终回执时，Git 拒绝推送。
修改 Profile 时，在 `ci-selection.toml` 的 `[push].effective_profiles` 登记目标树内
的文件，并同步更新对应 `[[effective_profile]]` 的修订和审批摘要。

本仓的 `pre-push`、`pre-push-checks` 及顶层 `lefthook:` 参数帧包装已配置在
[lefthook.yml](../lefthook.yml) 中：采集 Job `capture-refs` 通过 `files` 与 `{files}`
用 Hook 配置路径唤醒，规避 Lefthook 默认 `push` 文件比较可能为空的问题，并从 stdin
读取 `ECI-PRE-PUSH` 参数帧和全部 Git Ref；子任务成功后消费同一次 Invocation 的逐 Ref
Receipt，缺失最终对账时阻断。

`uv run --locked --no-sync lefthook run verify --no-auto-install` 运行 Dagger 的
lint、格式、版本与锁文件检查和核心测试。各项保留原生结果，Lefthook 汇总退出。
用 `uv run --locked --no-sync lefthook uninstall` 卸载本地 Hook。
[#152](https://github.com/dymzz/engineering-ci/issues/152#eci-%E9%AA%8C%E8%AF%81%E5%AE%9E%E6%B5%8B%E8%B5%B7%E6%AD%A2) 记录 ECI 入口调用 `verify` 时实测起止的待实现方案。
上述现有命令仍是原生验证入口。计划中的时间记录范围是当次 `verify`，
远端触发、Runner 和制品上传继续使用 GitHub Actions。

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
