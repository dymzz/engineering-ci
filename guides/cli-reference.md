# CLI 参考

两个用户入口、输出、退出码和独立工具的参考。环境准备见[开始使用](getting-started.md)，操作顺序见[常用操作](common-tasks.md)。

## 两个用户入口

`scripts/` 只保留 `release.py` 和 `download.py`。脚本加载自身 checkout 的 `src/`，
实现分别位于 `engineering_ci.cli.release` 和 `engineering_ci.cli.download`。
GitHub Actions 调用 `engineering_ci.workflow_actions`，不调用这两个用户脚本。

| 入口 | 用途 | 是否创建 Run |
| --- | --- | --- |
| `release.py status` | 定位交付停点，查询 PR / 本地结果 / 已确认 Run / Release 并输出继续命令 | 否；只读 |
| `release.py validate` | 选择单独完整验证，不发布 | 是；恢复已有 Run 时除外 |
| `release.py verify` | 查询指定 Run 的状态与版本 / Manifest / Release 一致性 | 否；只有 GET |
| `release.py publish` | 功能分支上检查、提交、Push、交付 PR；人工合并后请求完整验证与正式发布 | 仅在确认合并后；恢复已有 Run 或已发布时除外 |
| `download.py` | 选择、等待、恢复已有 Run，下载日志及 Artifact | 否 |

## 无参数、输出与退出码

```powershell
uv run scripts/download.py
uv run scripts/release.py
```

这两条无参数命令只打印帮助和示例，退出码为 0，不查询 GitHub、不创建 Run、不下载文件。
下载的菜单选择必须显式提供 --interactive；Release 必须显式选择子命令及 --version。
verify 还必须提供已有 --run RUN_ID；读取元数据仅校验版本，不替用户选择版本。
--help 同样只显示帮助。

默认输出终端文本，通过退出码报告结果。verify --json 输出结构化状态，不包含日志或凭据：

| 操作 | 终端输出 | 本地文件 |
| --- | --- | --- |
| Release status | 改动文件、PR / CI、检查结果有效性、已有 Run / Release、完整继续命令 | 不修改 Git / FSM 或结果文件 |
| Release PR 交付 | 版本、分支、文件、提交、PR URL、CI 状态和人工合并停点 | 原有本地检查 JSON；其目录内的 PR 说明文本 |
| Release 请求 / 恢复 | Run ID、Run URL、Workflow 最终结论、状态文件位置 | 仅 FSM 状态缓存；不下载日志或 Artifact |
| Release verify | 指定 Run 的真实状态、SHA、验证及发布结果；可选 JSON | 不写 FSM / 结果文件 |
| 下载已有 Run | Run 身份、下载后端、保存位置、最终结论和状态文件位置 | --out/RUN_ID 下的日志及所选 Artifact |

Release 不接受 --save-log、--artifact、--out 或下载后端参数。发布后需要取回文件时，
将输出的真实 Run ID 传给 download.py。下载开启 --save-log 时生成 run.log；
选择单个 Artifact 时解压到 Run 目录，收集全部 Artifact 时按名称分别建立目录。
FSM 缓存仍位于现有 gh-workflow-artifacts 用户缓存目录，不在项目中创建另一个 checkout。

| 退出码 | 含义 |
| --- | --- |
| 0 | 帮助正常显示，或 Workflow 成功且当前操作完成 |
| 1 | Workflow 失败 / 取消，验证运行中 / 不可用 / 不一致，或暂停、UNKNOWN、API / 下载错误 |
| 2 | 参数错误或缺少必需运行环境 |
| 3 | publish 已交付 PR，等待人工审查合并；本地检查未完整通过也保留其原有 3 和具体原因 |
| 130 | 用户中断 |

PowerShell 用 $LASTEXITCODE，Linux 用 $? 查看脚本退出码。报告或下载成功不能覆盖 Workflow 的失败。
status 返回 0 只表示查询完成，不表示 CI 或正式发布成功；输出中显示实际结果。

publish 的 `--all` 对应 `git add --all`，包括列出的未跟踪文件；`-m` / `--message` 对应 Git 提交信息。
有未提交改动时必须同时提供这两个参数。新 PR 标题来自实际提交首行，PR 说明包含完整提交信息；
已有 PR 保留人工标题和说明。正式 Release Notes 沿用 GitHub 对已合并 PR 的自动生成，无需另填发布信息。
--checks-report 可指定完整 PR JSON，默认 .test-tools/checks/pr.json；复用前校验当前输入与工具身份。
status --diff 显示已跟踪文件补丁；不执行检查、Commit、Push、合并、Dispatch 或下载。
状态查询使用原有 execution 缓存恢复已确认 Run；UNKNOWN 候选不能自动认定为原请求。
交付输出包含主阶段和内部操作，例如 DELIVERY.checking、REVIEW.waiting、BLOCKED.merged；
程序显示使用下划线分隔的状态名。准入失败保留已完成操作和共享上下文，不把跳过、过期或旧 Run 当作通过。

直接运行 Python 也可以，但有实际操作时必须使用安装了项目依赖的解释器：

```powershell
.\.venv\Scripts\python.exe scripts/download.py --help
.\.venv\Scripts\python.exe scripts/release.py --help
```

Linux 对应 .venv/bin/python。系统 python 若没有 transitions / filelock，会明确提示使用
uv run --locked python 或项目 .venv，返回 2；不自动向系统 Python 安装依赖。
源码模块也支持直接运行，但公开使用入口仍为 scripts 下的两个脚本或已安装的 engineering-ci 命令。

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
Security Workflow 兼容已发布旧 helper 的脚本；默认 ref 和消费者参数不变，
不在当前 scripts 目录恢复第三个入口，也不会在执行失败后换入口重试。
consumer 默认只读；修改、提交、推送仍需显式参数。
E2E 默认要求已有 Run；只有显式 --allow-dispatch 才允许新 POST。
本地结果有效性、平台限制和 JSON 退出状态见[检查说明](../docs/architecture/local-checks.md)。
