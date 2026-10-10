# 两个用户入口

`scripts/` 只保留 `release.py` 和 `download.py`。脚本加载自身 checkout 的 `src/`，
实现分别位于 `engineering_ci.cli.release` 和 `engineering_ci.cli.download`。
GitHub Actions 调用 `engineering_ci.workflow_actions`，不调用这两个用户脚本。

| 入口 | 用途 | 是否创建 Run |
| --- | --- | --- |
| `release.py verify` | 请求已有 release.yml 完整验证候选版本 | 是；恢复已有 Run 时除外 |
| `release.py publish` | 人工请求 main 的完整验证、Tag 和 Release 发布 | 是；发布由 Workflow 门禁决定 |
| `download.py` | 选择、等待、恢复已有 Run，下载日志及 Artifact | 否 |

## 环境与目录

在 `D:\workspace\engineering-ci` 使用当前分支开发，不需要额外 worktree。
安装项目锁定环境、GitHub CLI，并确认 `gh auth status` 和目标仓库权限：

```powershell
Set-Location D:\workspace\engineering-ci
uv sync --locked --group dev
uv run --locked python scripts/release.py --help
uv run --locked python scripts/download.py --help
```

Linux 使用相同的 uv 命令。aria2c 可选，auto 缺失时使用标准库 HTTP；
具体配置见[下载配置](../docs/ghwf-downloads.md)。
输出 --out 相对于调用者目录；机器配置相对于 engineering-ci checkout。

## Release Workflow

```powershell
# 候选分支验证，不创建 Tag 或 Release；这条命令会请求真实 Workflow。
uv run --locked python scripts/release.py verify --version v0.4.11 --ref codex/local-verification-v0.4.10
# 已知 Run 恢复：先核对它属于相同版本、模式和分支。
uv run --locked python scripts/release.py verify --version v0.4.11 --ref codex/local-verification-v0.4.10 --resume RUN_ID
# 人工审查合并、版本核对完成后，才执行正式发布。
uv run --locked python scripts/release.py publish --version v0.4.11
```

RUN_ID 替换为实际正整数。publish 只接受 main 和 pass 场景。
verify --scenario assertion 可请求现有负向验证。版本必须显式提供，不猜测远端版本。
既有 FSM 保留单次 POST 与 UNKNOWN 处理；UNKNOWN 要先人工核对远端 Run，再提供确认过的 ID，
不能用 --new-run 盲目重发。--resume 无 ID 恢复同一请求的缓存；--new-run 明确放弃旧任务，
可能产生重复 Run。两个命令返回实际 Workflow/收集失败状态。
正式 Release 的最终证据以 Workflow 和 Release Asset 为准，详见[发布说明](../docs/release.md)。

## 下载已有 Run

```powershell
uv run --locked python scripts/download.py --interactive --repo dymzz/engineering-ci
uv run --locked python scripts/download.py --repo dymzz/engineering-ci --run RUN_ID --save-log --out workflow-artifacts
uv run --locked python scripts/download.py --run RUN_ID --artifact release-manifest --download-backend native
```

非交互必须提供实际 ID；--resume RUN_ID 是 --run RUN_ID 的别名。
Workflow 与 ref 从真实 Run 获取；--workflow、--ref 是额外身份约束，冲突时拒绝。
--input、--new-run、无 ID 的 --resume 在下载入口不可用。
失败 Run 仍返回失败，收集到日志不代表 CI 通过。目录、缓存、完整性校验和安全解压
继续使用原实现，详见[执行与恢复](../docs/ghwf-execution.md)。

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
发布 engineering-ci 请使用 release.py。

## 检查与专项工具

这些操作按需独立调用，不作为 scripts 入口：

| 原脚本 | 当前调用 |
| --- | --- |
| checks.py | `uv run --locked engineering-ci checks pr`，支持 ruff/tests/version/lock/inspect |
| release_version.py | `uv run --locked engineering-ci version check` / show / set v0.4.11 |
| manual_release.py | Workflow 内部 `python -m engineering_ci.workflow_actions release ...` |
| release_manifest.py | Workflow 内部 `python -m engineering_ci.workflow_actions manifest ...` |
| dependency_security.py | `uv run --locked python -m engineering_ci.tools.security ...` |
| ci_performance.py | `uv run --locked python -m engineering_ci.tools.performance ...` |
| sync_consumer.py | `uv run --locked python -m engineering_ci.tools.consumer ...` |
| ghwf_e2e.py | `uv run --locked python -m engineering_ci.tools.workflow_e2e ...` |

内部发布操作依赖 Actions 上下文，不是本地发起发布的命令。
consumer 默认只读；修改、提交、推送仍需显式参数。
E2E 默认要求已有 Run；只有显式 --allow-dispatch 才允许新 POST。
本地结果有效性、平台限制和 JSON 退出状态见[检查说明](../docs/local-verification.md)。
