# 常用操作

本地检查、开发 PR、发布以及已有 Run 的下载步骤。命令语义与退出码见[CLI 参考](cli-reference.md)。

## 本地检查与开发 PR

在当前功能分支完成改动。目标版本已经准备好时无需再次修改；需要对齐四处元数据时使用：

```powershell
uv run --locked engineering-ci version set v0.5.4
```

查看当前交付上下文，不需要手工寻找分支、PR 或 SHA：

```powershell
uv run --locked python scripts/release.py status --version v0.5.4
```

`status` 只查询，显示 checkout、分支与提交、未提交文件、对应 PR 和实际 CI 状态、本地检查
是否仍对应当前输入、已有 Release Run 及下一步完整命令。添加 `--diff` 可查看已跟踪文件补丁；
未跟踪文件单独列出。

## 默认发布流程

用同一个 `publish` 入口贯穿交付与发布，沿用 Git 的参数词汇：

```powershell
uv run --locked python scripts/release.py publish --version v0.5.4 --all -m "统一本地与 PR 检查及发布入口"
```

`--all` 对应 `git add --all`，将当前 checkout 中列出的全部改动暂存，包括未跟踪文件；
`-m`（或 `--message`）对应 `git commit -m` 的提交信息。
若同时存在不属于该 PR 的改动，先整理提交范围；入口不会猜测哪些文件可以丢弃。
有未提交改动但没有 `--all` 或 `-m` 时，入口显示实际文件后停止，不修改 Git 索引。

信息链从实际 Git 提交接续：新 PR 标题取提交首行，PR 说明保留完整提交信息、提交列表和文件范围；
更新已有 PR 时保留人工标题和说明。合并后，发布继续使用 GitHub 原生自动 Release Notes，
汇总该版本区间内的已合并 PR，无需再填写一份发布描述。

功能分支上的顺序是：

1. 确认四处版本、Git origin、当前 gh 账号的仓库写权限和已有 PR。
2. 逐项核验当前输入、参数、工具和环境，复用匹配的执行结果，只刷新缺失或过期项；失败和 incomplete 不会自动重复执行。
3. 提交、Push 当前分支，创建 Draft PR 或更新同一个开放 PR。保留人工说明并补充实际提交、改动文件和本地检查信息。
4. 输出 PR URL、待审查 SHA、真实 CI 状态和继续命令，停在 **待人工审查合并**，返回 3。

在输出的 PR 页面审查改动和真实 CI；Draft 就绪后转为 Ready，由人决定合并。
必需 CI 失败、跳过或尚未完成时，入口明确停在 PR CI，不给出当前可合并的提示。
CI 失败或有冲突时，修复当前功能分支后用同一入口更新 PR。
若输出仍是本地检查失败、未提交或未推送，原 PR 的绿色 CI 只对应它显示的 SHA；
先完成当前改动的提交与推送，再审查更新后的 PR。

## 人工审查与合并

人工合并属于上述流程。入口不会自动合并，也不会绕过检查。
已经推送、没有新改动且 PR 尚未合并时，重复 `publish` 只显示停点，不重复本地测试或 Push。
此时下一步是人工审查 PR，不是再次运行 publish。必需 CI 通过且可合并时，输出带实际 PR 号的
Ready / Merge 命令；Merge 用 --match-head-commit 绑定当前已检查的 PR SHA。
只有人工完成合并后，才执行输出中 `After merge, continue` 的发布命令。

人工合并后从同一个 checkout 接续，不要求切换分支或查找合并 SHA：

```powershell
uv run --locked python scripts/release.py publish --version v0.5.4
```

入口确认该 PR 的实际合并提交已包含在远端 main，再请求一次正式 Release Run。
从 main 直接切入时，工作区必须干净且本地 main 与远端 main 一致。

该 Run 完成既有全量检查、证据生成、Tag、Draft 和最终发布；发布门禁仍由 GitHub Actions 决定。
`validate`、`verify` 都不是额外前置步骤。成功终点是经过证据核验的正式 Release URL；
只有 Tag、Draft 或成功 JSON 不算发布完成。机制见[发布说明](../docs/architecture/release/workflow.md)。

## 意外停止后接续

统一先使用：

```powershell
uv run --locked python scripts/release.py status --version v0.5.4
```

不需要猜选 checks、validate 或 verify，也不需要从历史终端输出寻找 ID。
`status` 使用与 publish 相同的层次化交付 FSM 识别实际 Git / PR 阶段，并查询原有 execution checkpoint。
本地阶段不另建持久化记录；中断后从实际 Git / PR 和经过身份核验的报告恢复。

Agent 或脚本需要结构化停点时，在相同命令上加 `--json`：

```powershell
uv run --locked python scripts/release.py status --version v0.5.4 --json
uv run --locked python scripts/release.py publish --version v0.5.4 --json
```

第一条只查询。第二条仍执行原有 publish 行为；有未提交改动时仍需审核文件，并显式提供 `--all -m`。
根据 `stop.code`、`publication.status` 和 `next_actions` 决定后续操作，不将查询的 exit_code=0 当成发布成功。
`next_actions[].argv` 已包含实际仓库、版本以及可确认的 Run / SHA；按参数数组调用，先满足 prerequisite。
requires_human=true 必须由人决定；空 argv 是人工决定事项，不是可执行命令。
JSON 不包含工具日志；需要保存时分别重定向 stdout 与 stderr，并保留 `$LASTEXITCODE`。

| 停点 | 信息与接续 |
| --- | --- |
| 本地检查失败 / 输入过期 | 结果文件、失败步骤和原因（例如缺少 uv）；修复后重复原 publish 命令，沿用原 `-m` |
| Commit / Push / PR 失败 | 已完成步骤、保留的提交、失败操作和权限 / 网络原因；解决后继续 publish |
| 等待人工合并 | PR URL、SHA、实际 CI 状态；人合并后继续 publish |
| PR 已合并，但仍有未交付的本地改动 | 已合并 PR / SHA 与实际文件清单；保留改动，通过后续修复 PR 交付，不能继续更新已合并 PR |
| 确认 Run 后中断 / PAUSED | Run URL、状态文件、实际状态及带真实 `--resume` 的完整命令 |
| Dispatch UNKNOWN | 不再次 POST；列出可核验候选 Run 和命令，须人工确认原请求后恢复 |
| Run 已完成但失败 | 原始结论和 Run URL；修复原因后才主动请求下一次 publish |
| 正式 Release 已发布 | 核验并显示原 Release、提交和验证 Run，不重复发布 |

Git/gh 权限、仓库选择或工作区位置阻碍操作时，在原 checkout 处理，由用户作出必要决定。
入口不会自动换账号、登录或创建替代工作区。
当前调用的接续命令保留原 `-m`；另一次 `status` 查询无法确认尚未提交的信息时，会提示补上自己的
提交信息，不用 PR 标题替代它。已提交后从 Git 读取，人工合并后的接续命令不再需要 `--all` 或 `-m`。
旧版本或其他提交的已确认 Run 标为历史记录，保留状态文件，但不作为当前发布的恢复点。
已合并且改动全部交付时，`status` 核对远端 main、四处版本和合并关系后给出发布命令。
有未交付改动时，以交付停点为准，不用旧 Run 的失败状态替代当前下一步。
修改目标版本后使用当前版本接续。版本不匹配时显示本地真实版本，不再推荐同一条无效请求。
只修改四处一致的本项目版本值时，沿用有效 Ruff / pytest 证据，重新核验版本和锁文件；
源码或依赖变化仍使相关检查过期。具体规则见[证据继承](../docs/architecture/local-checks.md#版本准备与证据继承)。

## 按需独立调用

这些诊断操作不提交、Push、合并或发布：

| 操作 | 命令 |
| --- | --- |
| 完整本地 PR 检查 | `uv run --locked engineering-ci checks pr --json .test-tools/checks/pr.json` |
| Ruff | `uv run --locked engineering-ci checks ruff` |
| Pytest 与 Coverage | `uv run --locked engineering-ci checks tests --coverage` |
| 版本和锁文件 | `uv run --locked engineering-ci checks version lock` |
| 查询本地结果 | `uv run --locked engineering-ci checks inspect .test-tools/checks/pr.json` |
| 复用并补全当前检查 | `uv run --locked engineering-ci checks pr --reuse --json .test-tools/checks/pr.json` |

本地与 PR CI 复用同一份定义。原生 Windows 无法执行的 Bash 测试记为 skipped，完整 profile
返回 3（incomplete），不能视为完整 PR 通过。环境、JSON 和有效性规则见
[本地验证](../docs/architecture/local-checks.md)。

`validate` 只用于主动选择一次独立全量验证，不发布。候选分支和版本显式指定：

```powershell
uv run --locked python scripts/release.py validate --version v0.5.4 --ref codex/release-delivery-fix-v0.4.11
```

`verify --run` 保留为指定 Run 的只读证据核验；日常停点定位用 `status`。
原有单次 POST、UNKNOWN 保护、Run ID 恢复和正式 Release 幂等校验仍生效。
公开入口不提供 `--new-run`；已有正式版本的重复调用核验并返回原 URL。

## 下载已有 Run

```powershell
uv run --locked python scripts/download.py --interactive --repo dymzz/engineering-ci
uv run --locked python scripts/download.py --repo dymzz/engineering-ci --run RUN_ID --save-log --out workflow-artifacts
uv run --locked python scripts/download.py --run RUN_ID --artifact release-manifest --download-backend native
```

显式 --interactive 进入菜单；非交互必须提供实际 ID；--resume RUN_ID 是 --run RUN_ID 的别名。
Workflow 与 ref 从真实 Run 获取；--workflow、--ref 是额外身份约束，冲突时拒绝。
--input、--new-run、无 ID 的 --resume 在下载入口不可用。
失败 Run 仍返回失败，收集到日志不代表 CI 通过。目录、缓存、完整性校验和安全解压
继续使用原实现，详见[执行与恢复](../docs/architecture/execution/state-and-recovery.md)。

## 旧 PowerShell 函数迁移

旧 gh_workflow_artifacts.py、ghwf.py 已移除。将 PowerShell Profile 中的函数改为：

```powershell
function ghwf {
    uv run --project 'D:\workspace\engineering-ci' --locked python 'D:\workspace\engineering-ci\scripts\download.py' @args
}
ghwf --run RUN_ID --save-log
```

--project 选择工程依赖环境，不改变调用者目录；省略 --repo 仍根据当前 Git origin
和原有缓存识别仓库。新 ghwf 只下载已有 Run。原先触发普通 Workflow 的用法可另取名：

```powershell
function ghdispatch {
    uv run --project 'D:\workspace\engineering-ci' --locked python -m engineering_ci.execution.cli @args
}
```

底层模块保留原 Dispatch、inputs、缓存查询和 --resume 参数契约，有真实 POST 副作用。
其 --new-run 不属于公开 Release / Download 接口。
发布 engineering-ci 请使用 release.py。
