# CLI 参考

本页说明实际保留的用户入口。准备环境见[开始使用](getting-started.md)，连续操作见[常用操作](common-tasks.md)。

## 用户入口

`engineering-ci` 提供 `version / release / download`。`scripts/` 只保留 `release.py` 与 `download.py`；
它们加载当前 checkout 的 `src/`。无参数或 `--help` 只显示帮助，不访问 GitHub。
模块内部文件不作为直接运行的脚本。检查使用原生 `dagger check`。

| 命令 | 用途 | 远端修改 |
| --- | --- | --- |
| `release.py publish --version VERSION` | 请求 main 上完整验证并正式发布；已知请求复用 Run，已有正式版本核验后返回 | 最多一次 Dispatch；发布由 Workflow 完成 |
| `release.py validate --version VERSION --ref BRANCH` | 显式请求独立完整验证 | 最多一次 Dispatch；不创建 Tag / Release |
| `release.py status --version VERSION --run ID` | 查询指定 Run 与发布证据 | 无，只有 GET |
| `release.py verify --version VERSION --run ID` | 与 status 相同的只读证据核验 | 无，只有 GET |
| `release.py wait --version VERSION --run ID` | 原生等待指定 Run，再核验实际证据 | 无 Dispatch |
| `download.py --run ID` | 下载指定已有 Run 的 Artifact；可选日志 | 无 Dispatch |
| `download.py --interactive` | 终端中显式选择 Workflow 和 Run 后下载 | 无 Dispatch |

## Release 参数与结果

所有操作必须给出 `--version`，接受 `0.7.0` 或 `v0.7.0`，不推测版本。
`--repo OWNER/REPO` 可选；省略时由 `gh repo view` 使用当前仓库上下文识别，没有自建缓存。
`status / verify / wait` 必须显式给出 `--run`；`publish / validate --run` 只附着该已有 Run，不再 POST。
`validate` 必须给 `--ref`；`publish` 只接受 main 和 pass 场景。
`--scenario pass|assertion` 用于非发布 fixture 验证；assertion 保留真实失败。
`--wait` 在请求后等待，`--timeout` 是等待上限秒数，默认 2700。
`--json` 将操作结果写入 stdout，原生等待进度写入 stderr。

JSON 的 `requested` 只表示请求受理，`validated` 表示同 Run 的验证证据通过，
`published` 才表示 Release、Tag、Manifest 与 Actions 原始证据一致。
`publication.status` 单独给出 not_published / draft / published / unconfirmed。
查询和报告不把远端失败转成成功；publish 不接受仅 validated 为发布成功。
字段与意图边界见[CLI 契约](../docs/architecture/cli-boundaries.md)。

不提供 Git 暂存、提交、Push 或 PR 操作参数，也不提供旧状态恢复、Backend 或强制重发选项。
Git/PR 直接使用原生命令；Release Notes 由既有 Workflow 使用 GitHub 自动生成。

## Download 参数与输出

`--repo` 同上。`--out` 默认 `workflow-artifacts`，相对于调用目录。
`--artifact NAME` 可重复；未给出时交给 gh 下载全部 Artifact。
`--save-log` 保存所确认 attempt 的日志；`--no-artifacts` 只取日志或查询，不可与 `--artifact` 同用。
`--wait / --timeout` 使用原生等待；非交互调用必须给 `--run`，菜单不可与 `--json` 同用。

日志在 `--out/RUN_ID/run.log`，拒绝覆盖。Artifact 在 `--out/RUN_ID/artifacts`；
gh 单个 Artifact 直接提取到该目录，多个按名称建子目录。没有字节续传或下载后端配置。
输出包含已知 Run URL、SHA、attempt、远端结论及继续命令；一般 Artifact 为 Run 范围，
不能将下载成功或输出文件存在当成发布证据通过。
日志下载可对失败 Run 成功返回，远端 `conclusion=failure` 保持原值。

## 退出状态

| 退出码 | 含义 |
| --- | --- |
| 0 | 帮助、请求受理、验证/发布核验或下载操作成功；含义由 result / publication / conclusion 区分 |
| 1 | 验证失败、不一致、缺失或本地操作失败 |
| 2 | 参数、工具或本地文件环境错误 |
| 3 | 指定 Run 仍运行或 Dispatch 未决，不能再次 POST |
| 124 / 130 | 超时 / 用户中断 |
| 原生 gh 非零 | 保留原工具退出码，不由报告解析覆盖 |

PowerShell 查看 `$LASTEXITCODE`，Linux 查看 `$?`。fine-grained PAT 不支持 gh watch 的 checks:read 时，
使用输出中的同一 Run 调用 status；不会自动切换凭据。见[官方说明](https://cli.github.com/manual/gh_run_watch)。

## 其他操作

| 功能 | 调用 |
| --- | --- |
| 原生检查 | `dagger check`；选择与报告见[本地检查](../docs/architecture/local-checks.md) |
| 版本 | `uv run --locked engineering-ci version show` / `check` / `set v0.7.0` |
| Actions 内部发布/证据 | `python -m engineering_ci.workflow_actions release ...` / `manifest ...` |
| 依赖安全 | `uv run --locked python -m engineering_ci.tools.security ...` |
| 性能采样 | `uv run --locked python -m engineering_ci.tools.performance ...` |
| 消费者同步 | `uv run --locked python -m engineering_ci.tools.consumer ...` |
| Dagger 原生遥测回执 | `uv run --locked python -m engineering_ci.dagger_telemetry --capture ... --output ...` |

Actions 内部操作依赖当前 Workflow 上下文，不是另一套本地发布入口。
consumer 默认只读，修改/提交/推送由显式参数控制。
