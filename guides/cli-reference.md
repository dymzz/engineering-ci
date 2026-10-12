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

默认输出终端文本，通过退出码报告结果。status / validate / publish 支持 `--json`：
stdout 输出一个操作结果对象，原有进度及工具输出写到 stderr；非零退出码仍保留。
verify --json 的既有 Run 验证结构保持兼容。
字段、停止原因与证据边界见[操作结果契约](../docs/architecture/cli-boundaries.md#操作结果与恢复视图)。

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
逐项复用匹配结果，不因其他检查失败而重跑已完成检查；相同输入的失败和 incomplete 保留原状态。
只有四处一致的本项目版本值变化时，Ruff / pytest 证据可以延续，版本和锁文件结果需对应当前元数据。
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
| release_version.py | `uv run --locked engineering-ci version check` / show / set v0.5.4 |
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
`checks --executor auto/native/dagger` 选择检查执行环境；默认读取 Pipeline，Windows
自动使用 Dagger Linux 容器，Linux CI 保留原生执行。`--json` 保存失败或成功证据，
`--reuse` 核验后逐项复用；`inspect REPORT` 查询时不重跑 pytest。
发布交付使用同一配置，无需手工复制容器 JSON 或更换 checkout。
检查和 Profile 必须显式选择；空请求拒绝，不记为通过。修改 TOML 后启动新命令即可生效，
字段、已注册操作和 Dagger 环境准备的对应关系见[Pipeline 配置](../docs/configuration/pipeline.md#操作契约与编排边界)。
`version check` 除四处版本元数据外，还校验既有 Pipeline / Workflow 绑定；
使用 `uv run --locked` 安装锁定的 YAML 解析依赖，配置不一致返回非零。
绑定约束见[配置说明](../docs/configuration/pipeline.md#github-actions-一致性)。

## 原始轨迹

轨迹命令独立于 release/download，不创建 Workflow，不改变检查或发布状态。
安装和连接配置见[归档配置](../docs/configuration/tracing.md)。

```powershell
# 只读确认连接；不自动初始化。
uv run --locked --extra trace engineering-ci trace status --json
# 只读检查明确选择的一份 rollout；无需数据库。
uv run --locked engineering-ci trace inspect 'C:/path/to/rollout.jsonl' --json
# 明确创建专用归档 schema，不接管同名其他应用对象。
uv run --locked --extra trace engineering-ci trace init --json
# 归档并返回 snapshot_id、source、字节摘要、游标与完整性。
uv run --locked --extra trace engineering-ci trace import 'C:/path/to/rollout.jsonl' --json
```

Linux 使用同样命令及本机文件路径。`--config` 可指定非敏感 TOML；相对配置路径从项目定位，
输入文件路径从调用目录定位。仅接受单个原生 rollout，不扫描 .codex 目录；
`codex exec --json` 用于新执行的 stdout 事件流，不是导出现有桌面会话的命令，其格式单独处理。
目前原生来源核对版本为 0.162.0-alpha.2；其他版本可以保真归档，但不声称语义投影已经兼容。

中断后重复同一条 import 命令，读取数据库的已提交游标，核对之前归档的文件前缀，
继续新增完整行。重复事件核对内容和属性，冲突拒绝覆盖。文件改写/缩短拒绝继续；
移动或复制相同文件不改变来源身份。截断尾片段保留为独立记录，游标停在它之前，
完成后建立新快照，旧快照不改变。活跃文件只捕获命令开始时的固定字节前缀。

回放必须明确提供 import 返回的 snapshot_id 和新输出路径：

```powershell
uv run --locked --extra trace engineering-ci trace replay SNAPSHOT_ID --out '.test-tools/trace-replay.jsonl' --json
```

将 SNAPSHOT_ID 替换为该次结果中的值。回放只读数据库，不执行历史工具；
内容校验全部通过后才创建目标文件，已有文件拒绝覆盖。输出含完整私密原始内容，
不会自动上传、加入 Git 或写到终端。trace 操作的 `--json` 输出一个脱敏结果对象，
退出码 0 表示该操作完成，2 表示配置、来源、数据库或输出错误；
`completeness=unknown/incomplete`、归档成功和原任务成功是不同事实。

## 标准化与离线分析

使用 import 返回的固定快照 ID，生成不包含命令、参数、prompt、输出及推理内容的派生视图：

```powershell
uv run --locked --extra trace engineering-ci trace project SNAPSHOT_ID --out '.test-tools/otel-projection.json' --json
uv run --locked --extra mining engineering-ci mine '.test-tools/otel-projection.json' --out '.test-tools/mining-result.json' --json
```

project 输出官方 OTel SDK Span JSON；这是离线 SDK 视图，不是 OTLP JSON 或已发送的遥测。
只使用已核对 rollout 版本的 item_completed 结构化时间、状态和退出码，缺时间不生成 Span；
外层工具包装记录不再计为第二次操作。未知退出码保持 unknown，不根据终端日志推测。

mine 只运行 Rust4PM 非 LLM 算法，不需要数据库或 OTel SDK。输入 JSON 全部字节摘要、
投影版本、case 范围、算法和依赖版本组成分析身份；同一输入的核心结果确定。
case 为 Turn，活动为 command_execution/file_change/mcp_tool_call，
尚不能区分命令内部具体 pytest/check 身份。结果中的操作引用可以定位回原快照。

没有可验证的全局序列时，unknown 完整性可参与观察性分析，并原样标注。
incomplete 输入需显式 --allow-incomplete。缺少 Turn 边界、时间缺失、并发重叠及时间并列的
case 保留排除原因，不用稳定排序冒充实际执行顺序。失败/取消也保留，不当作成功。
没有合格顺序片段时仍保存 unavailable 结果并返回 3；依赖、输入或输出错误返回 2。
结果文件拒绝覆盖。原始业务退出码、分析运行成功和发现优化效果是不同事实。
不执行工作流候选，不调用付费模型；Rust4PM 安装与历史 PM4Py 结果兼容见[配置说明](../docs/configuration/tracing.md#可选离线挖掘)。

## 归档查询与结果恢复

连接预检仍是 `trace status`；带选择器时查询已归档证据，不导入或重算。
source cursor 与 snapshot 分开显示，前缀校验标为未执行。status --file 只读 session header。

```powershell
uv run --locked --extra trace engineering-ci trace sources --limit 20 --json
uv run --locked --extra trace engineering-ci trace status --file 'C:/path/to/rollout.jsonl' --json
uv run --locked --extra trace engineering-ci trace snapshots --source SOURCE_URI --limit 20 --json
uv run --locked --extra trace engineering-ci trace status --snapshot SNAPSHOT_ID --json
# next_after 非空时，将值传给同一查询的 --after；不猜测 offset 或重读实录。
```

选择同一固定快照，显式建立完整结果链：

```powershell
uv run --locked --extra trace engineering-ci trace init --json
uv run --locked --extra trace engineering-ci trace project SNAPSHOT_ID --record --out '.test-tools/projection-new.json' --json
uv run --locked --extra trace engineering-ci trace results --snapshot SNAPSHOT_ID --kind projection --json
uv run --locked --extra trace --extra mining engineering-ci mine --projection-result PROJECTION_ID --record --out '.test-tools/analysis-new.json' --json
uv run --locked --extra trace engineering-ci trace status --result ANALYSIS_ID --json
```

source/snapshot/projection/analysis ID 都来自对应查询或命令结果。源 URI 可以用 status --file 取得。
`--projection-result` 与位置文件参数互斥。读取 ID 要数据库；普通 mine 文件模式仍无需数据库或 OTel。
无合格 case 的 unavailable 结果同样可登记，mine 保持退出码 3，不变为通过。

已有投影/PM4Py 分析文件可补登记而不重跑。分析的 --input 必须是它实际使用的原投影文件，字节摘要需匹配：

```powershell
uv run --locked --extra trace engineering-ci trace register '.test-tools/otel-projection.json' --json
uv run --locked --extra trace engineering-ci trace register '.test-tools/mining-result.json' --input '.test-tools/otel-projection.json' --json
```

登记检查身份和父结果，不证明历史算法执行；origin=file 在查询中保留。
project/mine --record 在输出文件创建前确认数据库登记。导出失败会返回非零，
同时给出 registered_result 和 next_actions argv；选新路径执行 export，无需再运行算法：

```powershell
uv run --locked --extra trace engineering-ci trace export --result RESULT_ID --out '.test-tools/recovered-new.json' --json
```

已有目标拒绝覆盖。result ID 已知但提交未确认时，先查 status --result；不存在就明确报 result_unavailable。
--config 是相对项目定位的非敏感配置；继续命令保留实际配置路径。
JSON 字段 status 表示当前操作，analysis.summary.status 才是分析结果，二者均不代表原任务全部成功。
