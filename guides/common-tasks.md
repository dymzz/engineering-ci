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

### 审查与合并

在当前分支执行以下命令，取得当前 PR 的身份和真实检查：

```powershell
$pr = gh pr view --json number,url,headRefOid | ConvertFrom-Json
$pr.url
gh pr checks $pr.number --watch
```

核对当前 head 的四个实际检查 `python-ci / checks`、`sync-tool-tests`、`version-metadata`、
`powershell-analysis`，完成代码审查后再 Ready 和合并。
合并权限遵循 [AGENTS.md](../AGENTS.md)。
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
