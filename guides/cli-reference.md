# CLI 参考

两个用户入口、输出、退出码和独立工具的参考。环境准备见[开始使用](getting-started.md)，操作顺序见[常用操作](common-tasks.md)。

v0.6.6 的 `checks`、`version`、`release`、`download` 基础实现保持独立。
`mine`、`workflow`、`checks --binding` 及 trace 的分析命令使用可选 Vesryn Harness；未安装时明确返回退出码 2。
trace 的 status/inspect/init/import/replay/sources/snapshots 直接使用 vesryn-log；
开发组安装日志基础包，PostgreSQL 归档需 trace extra；基础 no-dev 环境缺少日志包时只报告
记录 unavailable，不改变检查结果，也不要求轻量版本检查取得私有日志仓库凭据。
开发组安装它用于领域回归；基础环境可使用 `uv sync --locked --no-dev`，需要 Harness 时加 `--extra harness`。
原 trace/mining/telemetry extras 保留；trace 同时声明日志 archive SDK 与旧分析接口的依赖。
通用命令另有 `vesryn` CLI，CI 专用 `mine bind`、`mine workflow`、`export-xes --execution`
及能力批准/再审查继续通过 engineering-ci。详见[分仓契约](../docs/architecture/harness-integration/repository-split.md)。

## 两个用户入口

`scripts/` 只保留 `release.py` 和 `download.py`。脚本加载自身 checkout 的 `src/`，
实现分别位于 `engineering_ci.cli.release` 和 `engineering_ci.cli.download`。
GitHub Actions 调用 `engineering_ci.workflow_actions`，不调用这两个用户脚本。

| 入口 | 用途 | 是否创建 Run |
| --- | --- | --- |
| `release.py status` | 定位交付停点，查询 PR / 本地结果 / 已确认 Run / Release 并输出继续命令 | 否；只读 |
| `release.py validate` | 选择单独完整验证，不发布 | 是；恢复已有 Run 时除外 |
| `release.py verify` | 查询指定 Run 的状态与版本 / Manifest / Release 一致性 | 否；只有 GET |
| `release.py publish` | 功能分支上检查、提交、Push、交付 PR；确认实际合并后，由人请求完整验证与正式发布 | 仅在确认合并后；恢复已有 Run 或已发布时除外 |
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
| Release PR 交付 | 版本、分支、文件、提交、PR URL、CI 状态和审查合并停点 | 原有本地检查 JSON；其目录内的 PR 说明文本 |
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
| 3 | publish 已交付 PR，等待审查合并；本地检查未完整通过也保留其原有 3 和具体原因 |
| 130 | 用户中断 |

PowerShell 用 $LASTEXITCODE，Linux 用 $? 查看脚本退出码。报告或下载成功不能覆盖 Workflow 的失败。
status 返回 0 只表示查询完成，不表示 CI 或正式发布成功；输出中显示实际结果。
PR 合并权限与大阶段备份遵循[Agent 指令](../AGENTS.md)，操作见
[审查与合并](common-tasks.md#审查与合并)。入口仍返回 REVIEW.waiting；人或已授权 Agent 使用 gh
完成实际合并后才能接续，自动合并授权不包含正式发布。

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
| release_version.py | `uv run --locked engineering-ci version check` / show / set v0.6.0 |
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

新仓交互建仓使用 `uv run --locked python -m engineering_ci.tools.bootstrap`，不增加第三个
scripts 入口。`--plan` 只预览，`--repo OWNER/name --path ABSOLUTE_PATH --yes` 可非交互
生成本地骨架；显式 `--github` 才处理私有远端，新建远端只 Push README-only main。
已有远端交互选择连接/覆盖 main/取消；非交互使用 `--existing-remote connect|overwrite`，
覆盖还需实际完整 SHA 的 `--expected-main-sha`，`--yes` 不隐含覆盖。
`--resume ABSOLUTE_PATH` 恢复该工具自己的上下文，保留用户修改；远端创建结果不确定时
  必须核对实际仓库 ID，禁止自动再次创建。
  生成后交互选择继续到 Draft PR、只做本地准备/检查或保存退出；Commit、Push、Draft
  分别确认。非交互 `--setup` 请求依赖/SDK/Dagger 检查，`--deliver --yes -m '提交信息'`
  显式请求检查、Commit、普通 Push 和 Draft PR，停在审查，不合并/发布。
  `--yes` 本身仍只生成，不隐含这些后续操作。中断后用原目录 `--resume` 继续；
  原始检查失败、源码变化和 PR 未验收不会转为通过。完整动作与退出码见
[交互式建仓说明](new-repository.md#交互式建仓入口)。

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

## 本地任务日志

执行型入口自动记录现有调用边界，日志不经过 Vesryn observe-task 或另一次 Dagger 调用。
使用安装的 vesryn-log 写入 `.log/tasks/<UTC 日期>/<task-id>/`；不需要另开日志开关。
从最新成功指针定位本次记录，在 engineering-ci checkout 执行：

```powershell
$taskLog = Get-Content .log/latest-task.json -Raw | ConvertFrom-Json
$taskLogPath = Join-Path .log $taskLog.run_path
Get-Content (Join-Path $taskLogPath 'delivery.json') -Raw
Get-Content (Join-Path $taskLogPath 'events.jsonl') -Tail 20
```

`capture.json` 保留安全快照，`events.jsonl` 是同一组事件；默认不含 argv、工具输出、环境值
或异常正文。delivery.json 的 task_exit_code 是业务结果，observation_delivery 是记录结果，
二者分开判断。原命令返回失败时，日志写入成功仍是失败；日志失败只给独立诊断，可能留下
部分目录，不更新旧成功指针，也不自动重跑。旧 pending/可读分页不自动迁移或重投。
依赖来源与边界见[日志集成](../docs/architecture/integrations/tracing.md#vesryn-log)。

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

显式保留本轮 Mining 使用的准入 case 文件，供 Rust4PM/Propel 文件导入：

```powershell
uv run --locked --extra mining engineering-ci mine '.test-tools/otel-projection.json' --out '.test-tools/mining-new.json' --xes-out '.test-tools/cases-new.xes' --json
```

分析只执行一次。XES 写入失败时返回非零，已保存的分析仍保留，JSON 给出 analysis_saved、
analysis_output 与下一命令；原分析不可用仍是不可用。两份文件分别原子创建，并非跨文件事务。

已经有分析和它使用的原投影时，离线导出不需要数据库、OTel SDK 或 r4pm 安装，也不重跑算法：

```powershell
uv run --locked engineering-ci mine export-xes '.test-tools/mining-result.json' --projection '.test-tools/otel-projection.json' --out '.test-tools/cases.xes' --json
```

文件模式必须提供原 projection，核验原字节摘要、case 准入及已存统计。
可选登记模式为 `mine export-xes --analysis-result ANALYSIS_ID --out FILE.xes --json`，
只有该模式需要 trace extra 与 PostgreSQL；文件导入操作见[Propel 指南](propel-postgresql.md#使用离线-casesxes)。

已有注册工具执行使用 `mine export-xes --execution EXECUTION_DIR --out FILE.xes --json`；
该模式仅导出原回执，无需数据库或 mining extra。分析它使用
`mine workflow EXECUTION_DIR --out ANALYSIS.json [--xes-out FILE.xes] --json`，需要显式安装 mining extra。
两种来源不能混用；完整操作、Step/attempt Case 和字段说明见
[生成工作流使用说明](generated-workflows.md#分析已执行的注册工具并导出-xes)。
`cases.xes` 保留来源、完整性三态及排除数量，仅输出准入 case；失败/取消/未知不改为通过。
`time:timestamp` 使用原完成时间，另保留 start_ns/end_ns；没有因果或精确检查身份推断。
状态 exported 与退出码 0 表示文件成功导出，不表示采集完整或优化通过。已有文件拒绝覆盖。

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

## 可选遥测与结果比较

需要限制当前 Agent 收到的数据量时，先使用 `engineering-ci trace query summary`，
再按返回的分析及候选引用查询 pattern/candidate/evidence。Raw 需要显式 snapshot 与 allow-raw。
完整命令和预算见[分层查询](trace-queries.md)。

下面从已经完成 P0 的 `.test-tools/otel-projection.json` 开始；换成你实际生成的投影文件。
这些操作观察历史数据，不调用原 Agent、pytest、Dagger 检查或 Release Workflow。

```powershell
# 仅本地 Jaeger；UI 为 http://127.0.0.1:16686。
docker run --detach --name engineering-ci-jaeger --publish 127.0.0.1:16686:16686 --publish 127.0.0.1:4318:4318 cr.jaegertracing.io/jaegertracing/jaeger:2.22.0
uv run --locked --extra telemetry engineering-ci trace send '.test-tools/otel-projection.json' --endpoint 'http://127.0.0.1:4318/v1/traces' --out '.test-tools/otlp-receipt.json' --json
```

已有同名容器/端口时查看现有 Docker 配置，不覆盖其他服务。Linux 使用相同命令。
可选 Collector 转发使用仓库配置：

```powershell
docker network create engineering-ci-telemetry
docker network connect --alias jaeger engineering-ci-telemetry engineering-ci-jaeger
# 将转发端口单独映射到本机 4319；不用改动 Jaeger 4318。
docker run --detach --name engineering-ci-collector --network engineering-ci-telemetry --publish 127.0.0.1:4319:4318 --mount "type=bind,source=$((Get-Location).Path)/config/collector.yaml,target=/etc/otelcol/config.yaml,readonly" otel/opentelemetry-collector:0.162.0 --config=/etc/otelcol/config.yaml
uv run --locked --extra telemetry engineering-ci trace send '.test-tools/otel-projection.json' --endpoint 'http://127.0.0.1:4319/v1/traces' --out '.test-tools/collector-receipt.json' --json
```

该 PowerShell 挂载命令在 engineering-ci 根目录执行；Linux 将 source 替换为当前 checkout 的绝对路径。
receipt.trace_ids 可用于 Jaeger 原生 UI 查看对应 Trace。历史实录时间可能较早，按真实时间调整 UI 查询范围；
没有默认最近一小时结果不等于没有收到 Trace。数据库输入也可用已有的 --projection-result ID，另加 --extra trace。

accepted 表示当前 OTLP 接收节点确认；原工具失败、采集完整性与后端读回都单独保留。
partial、network_error、tls_error、rejected、unconfirmed 或 response_invalid 返回非零并保存实际 receipt；
无 Span 的 unavailable 返回 3，不记为一次成功发送。配置/依赖/输出错误返回 2。
结果写入失败时终端 JSON 仍保留已经取得的接收事实；不要把“文件未写出”理解为“没有发送”。
失败的 next_actions 带同一输入/目标和一个新的 receipt 文件名，可直接继续，不覆盖已有报告。
重发可能重复投递，但不重新执行历史工具。成功时没有自动重发建议。

比较已有结果无需安装 mining，也不重新计算算法。文件模式提供各自实际使用的原投影文件：

```powershell
uv run --locked engineering-ci mine compare '.test-tools/baseline-analysis.json' '.test-tools/candidate-analysis.json' --baseline-projection '.test-tools/baseline-projection.json' --candidate-projection '.test-tools/candidate-projection.json' --out '.test-tools/comparison.json' --json
# 已完成 register 的分析，可改用数据库 ID 关联，不需要手工拼原投影路径。
uv run --locked --extra trace engineering-ci mine compare --baseline-result BASELINE_ANALYSIS_ID --candidate-result CANDIDATE_ANALYSIS_ID --out '.test-tools/registered-comparison.json' --json
```

分析和输入摘要不符时拒绝；未提供原投影时仍可生成 not_comparable 报告，但不能认定可比。
不同快照/case/映射/完整性策略、不可用分析或缺失测量明确保留原因，not_comparable 返回 3。
compared 返回 0 表示比较操作完成；results_equivalent 才表示本范围内的输出是否一致。
质量门禁不由该命令判断。缺整体耗时、费用或跨运行证据时保持未知，optimization_verified=false。
当前支持固定输入的算法结果比较，未将发现的重复步骤转为可删除测试或可执行 Workflow。
完整配置和边界见 [遥测配置](../docs/configuration/tracing.md#可选-otlp-出口与本地-jaeger)。

## 工作流候选与人工审查

从上文已经生成的 mining-result.json 和实际输入 otel-projection.json 开始。
下面的命令复用文件，不重跑 SDK/Rust4PM，不调用模型、执行历史工具或修改 Pipeline：

```powershell
uv run --locked engineering-ci mine candidates '.test-tools/mining-result.json' --projection '.test-tools/otel-projection.json' --out '.test-tools/workflow-candidates.json' --json
```

已登记的分析可直接使用此前 mine --record 或 trace results 返回的分析 ID：

```powershell
uv run --locked --extra trace engineering-ci trace results --kind analysis --limit 10 --json
uv run --locked --extra trace engineering-ci mine candidates --analysis-result ANALYSIS_ID --out '.test-tools/registered-candidates.json' --json
```

ANALYSIS_ID 来自所选记录的 result_id；该模式检查既有分析→投影→快照关联，
不需要导出原始 rollout 或重新计算。--config 仅用于 ID 模式的非敏感归档配置。
文件模式不需要 PostgreSQL、OTel SDK 或 mining extra；其 --projection 必须是分析当时使用的实际字节。

报告中的 source 给出快照/投影/分析 ID、输入及内容摘要、origin 和原工具版本。
coverage 分别记录全部、准入和排除操作及失败/未知统计；排除数据不会被称为成功。
三种 kind 分别表示相邻同类活动、多次出现的完整变体和不同观察后继。
相邻对可能重叠；次数不等于可删除调用数，观察后继不等于因果依赖或执行条件。

每个候选含 occurrences/case_count 计数、Turn/event 引用、原结果与实际时间。
--limit 默认 20（1–100），--evidence-limit 默认 3（1–20）；上限不改变全量统计。
omitted_candidates/omitted_witnesses 明确省略数量；每个见证和长变体最多展示 8 个操作/活动，
另保留完整数量、序列摘要及 omitted_operations/omitted_activities。
分支另为每个后继保留一个 successor_examples 见证，避免少量示例掩盖另一条观察路径。
排序按种类及活动名称确定，不表示收益排名。需要更多见证时增加上限并选择新 --out 路径。

status=reported 与退出码 0 仅表示报告生成完成，即使没有候选也不代表优化评估通过。
缺原输入关联或没有准入 case 时保存 unavailable 报告并返回 3；
配置/文件/关联/模型不一致或写入错误返回 2。已有输出拒绝覆盖，且不会为此连接数据库。
输出中断保留已获得的 report_id、analysis_id 和 next_actions，继续命令使用新文件名；
无网络副作用，也不重新运行发现算法。写入失败的 report_saved=false 不能视为报告已保存。

打开实际输出文件，以 candidate_id 为审查引用，按 required_evidence 核对：

1. 活动对应哪项具体检查或动作，参数、文件输入摘要及工具环境是否相同。
2. 重复是否属于修复、失败恢复、不同 Attempt、负向测试或不同输入。
3. 候选目标 Handler 的输入输出、权限、副作用和准入条件是否明确。
4. 原质量门禁与基线/候选执行证据是否足以验证改动。

当前均为 needs_review、executable=false、handler=null，报告没有自动批准或改写检查策略的命令。
候选报告自身不启用执行，llm_used=false、quality_gate=not_evaluated、optimization_verified=false。
显式选择目标后，可按下一节绑定已支持的 Handler；候选报告保持原审查状态。流程边界见
[实施方案](https://github.com/dymzz/vesryn/blob/7ac74b83c4a206446b9c8b52cc88c906a13ad2f5/docs/architecture/harness/analysis.md#p2候选报告与人工审查依据v057)。

## 绑定并调用 PR 检查 Handler

先生成上一节的候选报告，选择 `kind=adjacent_activity_repeat` 且
`pattern.activities=["command_execution","command_execution"]` 的 candidate_id。
下面的变量来自实际报告，不需要手工寻找或填写摘要：

```powershell
$report = Get-Content '.test-tools/workflow-candidates.json' -Raw | ConvertFrom-Json
$candidate = @($report.candidates | Where-Object {
    $_.kind -eq 'adjacent_activity_repeat' -and
    ($_.pattern.activities -join ',') -eq 'command_execution,command_execution'
})
if ($candidate.Count -ne 1) { throw '请选择报告中实际存在的连续 command 候选' }
uv run --locked engineering-ci mine bind '.test-tools/mining-result.json' --projection '.test-tools/otel-projection.json' --candidate $candidate[0].candidate_id --profile pr --out '.test-tools/pr-check-reuse.json' --json
```

登记模式使用同一分析记录的 result_id 替换文件和 `--projection`：
`mine bind --analysis-result ANALYSIS_ID --candidate CANDIDATE_ID --profile pr --out FILE --json`。
只有登记模式需要 trace extra、PostgreSQL 和 VESRYN_DATABASE；文件模式及执行已绑定操作均不需要。
绑定只创建新的 JSON 文件，不启动检查、重新挖掘或执行历史命令。
打开该文件审查 source、target 和摘要；当前目标只支持完整 PR 检查，没有任意命令或其他 Profile。

审查后使用绑定输出 `next_actions` 中的完整调用；也可以明确选择现有报告位置：

```powershell
uv run --locked engineering-ci checks --binding '.test-tools/pr-check-reuse.json' --reuse --json '.test-tools/checks/pr.json'
uv run --locked engineering-ci checks inspect '.test-tools/checks/pr.json'
```

已有 `checks pr --reuse` 继续有效，两者调用同一实现。首次运行核验报告，执行缺失或不匹配项；
再次运行复用同输入、工具、参数和环境的事实。失败和 incomplete 仍阻断，修复后重新执行受影响检查。
可以继续给出原 `--coverage`、`--junit`、`--executor` 参数；参数变化参与原检查身份。
`--binding` 必须与 `--reuse --json` 一起使用，不能同时传入其他检查选择，也不能让输出覆盖绑定。

绑定创建成功为 status=bound、退出 0，表示显式目标可调用；不表示检查或优化已通过。
创建失败退出 2，已有文件拒绝覆盖；next_actions 保留新文件名和原输入，binding_saved=false 表示未保存。
绑定过期或无效时，检查入口在调用工具前返回 3；用原分析/投影和候选重新 bind 到新文件并审查。
实际执行继续传播原工具退出码。检查 JSON 的 handler_binding 只关联来源，不替代原检查事实。
终止后重复同一 checks 调用，原始 Dagger 文件缺失/损坏或当前输入变化会使相关证据失效。
最终关联保存失败会保留原检查文件；成功执行返回 3，已有失败保留原非零码。

历史投影没有具体检查身份，绑定是用户显式选择的新目标，不证明历史重复命令就是 PR 检查，
也不批准删检查、自动发布或扩展远端权限。详细契约见
[Handler 固化](../docs/architecture/harness-integration/legacy-binding.md#首个-handler-固化基础-pr-检查复用v061)。

以上调用是早期证据绑定原型的兼容入口。新配置执行使用 `engineering-ci workflow`，见
[完整操作说明](generated-workflows.md)，不要将旧绑定文件当作新的 workflow.json。

## 独立工作流入口

`engineering-ci workflow` 提供 tools、schemas、generate、validate、run、status、resume、evidence、compare，
以及 approve、activate、deactivate、capabilities、select、call、feedback、feedback-query、feedback-summary、feedback-review。
`workflow compare BASELINE_DIR CANDIDATE_DIR --out FILE.json [--json ACTION.json]` 只读比较
完整 PR 检查的同输入复用证据。它与 `mine compare` 的固定分析结果比较是不同用途；
原质量未通过或输入不可比时仍保存报告并非零。连贯调用见
[受控比较操作](generated-workflows.md#完整-pr-检查的同输入受控比较)。
generate 接受独立 Recipe 或显式 Profile，每次创建独立定义及来源；run 创建独立执行；
resume 继续同一检查点，status 只读已记录事实。每个子命令支持 `--json FILE`。
所有工具使用已有 console-script 入口；工具注册及实际命令不放入 pyproject 的 scripts 表。
参数、连贯 PowerShell 示例、输入/输出引用和中断后的继续方式见[独立工作流](generated-workflows.md)。

P2 参数如下，版本指能力版本，与项目发行版本独立：

| 命令 | 参数与职责 |
| --- | --- |
| approve | `COMPARISON BASELINE_DIR CANDIDATE_DIR --name NAME --version VERSION`；独立批准，不启用 |
| activate | `--name NAME --version VERSION`；核验后显式选择 active |
| deactivate | `--name NAME`；阻止后续选择，保留历史/已准入执行 |
| capabilities | `[--name NAME] [--limit N] [--max-bytes N] [--after TOKEN]`；有界发现 |
| select | `--name NAME [--version VERSION]`；只查询启用版本与证据准入 |
| call | `--name NAME [--version VERSION] --out DIR`；调用原 Runtime 和检查 |
| feedback | `EXECUTION_DIR [--archive] [--config FILE]`；本地补写/显式 PostgreSQL 归档，不执行检查 |

除 feedback 从冻结调用读取 store，以上命令支持 `--store DIR`，默认
`.test-tools/workflow-capabilities`；所有命令支持独立操作回执 `--json FILE`。
call 返回原执行退出码，反馈失败单列；feedback --archive 成功只代表归档成功。
操作与恢复见[能力调用](generated-workflows.md#批准启用选择与调用能力)。

`workflow feedback-query` 单独只读 PostgreSQL 安全反馈，保留 feedback 保存/归档语义。
支持 `--feedback-id`、`--approval-id`、`--execution-id`、`--name`、`--version`、
`--result-status`、`--completeness`、`--limit`、`--max-bytes`、`--after`、`--config`、`--json`。
ID 来自实际反馈记录；能力版本非项目版本，原状态过滤不是查询成功状态。
分页可有空匹配页，保留过滤条件沿 next_after 继续；不汇总累计快照。
命令与停止处理见[反馈查询](generated-workflows.md#查询已归档反馈)。

`workflow feedback-summary --feedback-id ID [--feedback-id ID ...]` 只读明确选择的
1–32 份归档。支持 `--max-bytes`、`--config` 和 `--json`，不接受含糊的“所有历史”或
以分页结果直接累加。重复 ID 去重；v2 索引核验覆盖/Attempt 身份，v1 与冲突保持未知。
完整命令、数据选择和非零恢复见[反馈汇总](generated-workflows.md#核验覆盖并汇总反馈)。

`workflow feedback-review --summary FILE --store DIR [--execution CALL_DIR ...]` 消费上一阶段
成功汇总，并从同一归档重算校验后关联原批准/比较与 call 回执。支持 `--config`、
`--max-bytes`、`--json`；执行目录最多 32 个，未提供的原证据保持不可核验，不扫描或猜 ID。
报告只提供人工保留、调查或新受控试验依据，不启用能力、不运行工具。
命令与停点处理见[再审查](generated-workflows.md#关联原比较与执行证据进行再审查)。
