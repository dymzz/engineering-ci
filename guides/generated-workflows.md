# 生成、执行与恢复独立工作流

本入口调用现有检查，不改变 PR、Release Workflow 或发布权限。`scripts/` 仍只保留
`release.py` 与 `download.py`。下面所有命令从主 checkout 执行。

## 安装和发现工具

```powershell
uv sync --locked --group dev
uv run --locked engineering-ci workflow tools
uv run --locked engineering-ci workflow schemas --json .test-tools/workflow-schemas.json
```

生成与执行需要锁定的 `jsonschema==4.26.0`、`transitions==0.9.3` 和 `filelock==3.20.3`。
只执行生成文件无需安装 trace/mining extra，也不连接 PostgreSQL。
工具从宿主 Catalog 注册，CLI console-script 仍是已有的 `engineering-ci`，不在
`pyproject.toml` 为每个工具添加入口。未安装项目时可以使用 `python -m engineering_ci.cli`
并将主 checkout 的 `src` 加入 PYTHONPATH。

`workflow tools` 返回实际注册的五项检查及 `engineering-ci.check-inspect` 的输入/输出
Schema、契约摘要、副作用、恢复方式和支持的环境。原命令、版本和范围仍来自原 Pipeline。
Windows 的 `auto` 使用原 Dagger/Linux 执行器；Linux 使用原 native 执行器。
Dagger 仍需匹配 `dagger.json` 的版本，环境缺失或版本不一致会明确失败。

## 完整 PR 检查：生成、审查、运行

```powershell
uv run --locked engineering-ci workflow generate --profile pr --out .test-tools/workflow-generated --json .test-tools/latest-generation.json
if ($LASTEXITCODE -ne 0) { throw '生成失败，查看命令诊断' }
$generation = Get-Content .test-tools/latest-generation.json -Raw | ConvertFrom-Json
$workflow = $generation.output

uv run --locked engineering-ci workflow validate $workflow
if ($LASTEXITCODE -ne 0) { throw '定义校验失败，不执行' }
Get-Content $workflow
Get-Content (Join-Path (Split-Path $workflow) provenance.json)

uv run --locked engineering-ci workflow run $workflow --out .test-tools/workflow-runs --json .test-tools/latest-workflow-run.json
$run = Get-Content .test-tools/latest-workflow-run.json -Raw | ConvertFrom-Json
$execution = $run.execution
uv run --locked engineering-ci workflow status $execution
```

`--out` 是父目录，每次生成、每次新运行各分配新子目录，旧文件不覆盖。
`--profile` 将当时的现有 Profile 展开为具体工具、参数及步骤，执行阶段读取这些步骤，
不把所有生成定义重新路由到硬编码 `pr`。`--executor`、`--coverage`、`--junit` 可用于 Profile
生成，实际参数照常参与原检查身份。

相同环境和参数的就绪检查交给同一原执行器批次。Dagger 的五项 PR 检查共用原准备和
Artifact 请求，报告保留各子检查；原有 JUnit/Coverage 输出习惯保持有效。
原工具输出仍可见，`--json FILE` 单独保存命令结果；执行目录的 `state.json` 和 `result.json`
始终是独立 JSON，不需要解析终端日志。

## 自己编排两个工具及数据引用

仓库提供 [workflow.example.json](../config/workflow.example.json)：lint 输出的报告资源通过
`$from` 和 JSON Pointer `/report` 成为 inspect 的实际输入。修改自己的 Recipe 再生成，
不要原地修改已生成文件并保留旧摘要。

```powershell
uv run --locked engineering-ci workflow generate --recipe config/workflow.example.json --out .test-tools/workflow-generated --json .test-tools/latest-generation.json
$generation = Get-Content .test-tools/latest-generation.json -Raw | ConvertFrom-Json
uv run --locked engineering-ci workflow validate $generation.output
uv run --locked engineering-ci workflow run $generation.output --out .test-tools/workflow-runs --json .test-tools/latest-workflow-run.json
```

Recipe 的参数由文件拥有，不能再传 Profile 参数覆盖它。`needs` 表示控制依赖，数据引用
自动形成依赖。`when=success` 是默认条件；`always` 可用于检查失败后的诊断；`failure` 需要
前驱失败。必需步骤 skipped、缺少输出、不完整或未执行都不能使工作流通过。
inspect 只查询原报告的当前有效性，不重新跑检查；前驱失败不会因诊断报告生成成功而消失。

## 复用与意外停止

恢复同一次执行：

```powershell
uv run --locked engineering-ci workflow resume $execution --json .test-tools/latest-workflow-resume.json
```

新建独立执行并引用上一执行的证据：

```powershell
uv run --locked engineering-ci workflow run $workflow --out .test-tools/workflow-runs --reuse-from $execution --json .test-tools/latest-workflow-run.json
```

两次独立生成的语义相同，也可使用 `--reuse-from`。目录或 ID 变化不会触发检查重跑，
工具逐项核验原报告、资源摘要、当前输入、参数及实际环境。失败结果复用后仍非零；
输入变化只使相关结果失效。不同语义定义不能直接引用为同一执行。

执行前会输出检查点完整路径。强杀进程导致没有最终命令 JSON 时，用当时打印的路径执行
`workflow status`，再执行 `workflow resume`。status 只读历史事实，`current_input_validity=not_checked`
不表示当前代码已通过。resume 保持同一 execution_id，attempt 增加，重新接受工具准入。
同一执行有进程锁。检查点损坏或工具契约变化时拒绝继续，不猜测成功。

检查适配器在调用前保存意图，在结果形成后保存每项回执。已导出的 Dagger 原始证据在
主进程接收前中断，也能再次核验。native pytest 若在完成后、冻结 JUnit 回执前终止，
无法证明输出完整，将重跑这一项；其他具有完整回执的检查继续复用。
UNKNOWN 写操作只允许查询及人工/domain 核验，不提供盲目重做的继续命令。
已知拒绝保留原失败，不能因为尚未提供领域恢复接口就变成 UNKNOWN；该情况为 PAUSED/blocked，
结果明确给出 decision_required，不给出盲目重做的命令。
本轮 Catalog 没有注册 Git Commit、Dispatch、Merge 或 Release 工具。

## 关联已经审查的真实候选

只有生成步骤读取候选来源，执行层不依赖矿工或数据库：

```powershell
uv run --locked engineering-ci workflow generate --profile pr --analysis-result $analysisId --candidate $candidateId --out .test-tools/workflow-generated --json .test-tools/latest-generation.json
```

`$analysisId` 与 `$candidateId` 来自实际分析/候选报告。登记模式需要已有 trace 安装及
VESRYN_DATABASE；文件模式用 `--analysis ANALYSIS.json --projection PROJECTION.json` 替代
`--analysis-result`，核验原始投影摘要及候选归属。未提供候选时为人工编排，provenance.source=null。
当前粗粒度实录不含具体检查身份，所以来源保留 `historical_check_identity=unavailable`、
`optimization_verified=false`。生成不代表证明历史重复的工具可删，也不批准发布。

关联候选时必须保留完整 PR Profile，provenance 同时保存目标策略与逐项 step/tool 映射。
候选 ID 含来源模式：文件与登记模式分别使用各自 candidates/query 返回的 ID，不能混用；
analysis/projection 的原内容身份仍保留，显式映射不倒填历史检查身份。
目标 Pipeline 改变后，validate/run/resume 拒绝旧绑定，重新生成并审查；原结果保留。
没有 target 字段的旧文件仍可读取，但目标策略状态为 unavailable，不自动补填历史身份。

`mine bind` / `checks --binding` 是 PR #25 早期原型的兼容桥，旧绑定文件不作为新的可执行定义。
新入口生成 `workflow.json` 与 `provenance.json`，宿主兼容桥保留在 CLI 层；原 checks 库
不导入 mining 或工作流核心。

## 查询实际尝试与导出标准 Span

上面的 run/resume JSON 提供 `execution` 与 `observations.next_action`，不需要再查 ID：

```powershell
$run = Get-Content .test-tools/latest-workflow-run.json -Raw | ConvertFrom-Json
$execution = $run.execution
uv run --locked engineering-ci workflow evidence $execution --limit 5 --max-bytes 16384 --json .test-tools/workflow-evidence.json
$proof = Get-Content .test-tools/workflow-evidence.json -Raw | ConvertFrom-Json
$proof.items | Select-Object step_id, attempt, invocation_started, result, check, reuse_source

if ($proof.page.next_after) {
    uv run --locked engineering-ci workflow evidence $execution --limit 5 --max-bytes 16384 --after $proof.page.next_after
}
```

查询读取指定执行的来源、生成定义、观测以及经摘要核验的原回执，不重新执行工具或 Mining。
`recorded_result` 是当时结果，`current_input_validity=not_checked`；资源损坏时该项证据 unavailable。
`check.actual_check_executed` 区分真正执行与 `check.evidence_reused`，未知时为 null；
`reuse_source` 给出已关联的原 execution/generation/step/attempt，不代表这些证据至今仍有效。
`approved=false`、`optimization_verified=false`，观察候选与原报告均不自动成为批准记录。

恢复后观测追加新会话和 attempt，旧记录不覆盖。完整性 complete 仅覆盖这个本地观测协议；
强杀或丢失记录为 incomplete，旧检查点不支持判断完整历史时为 unknown。
观测保存失败单独报告 `write_errors`，原失败退出码保持。输出预算按 stdout 紧凑 UTF-8 JSON
含换行计算，复用原查询的书签语义；执行变化使旧书签失效。单个执行读取上限为 10000 记录/16 MiB。
`--json` 放在执行目录外，不能覆盖原回执。

需要标准 Span JSON 时显式安装已有 trace extra 并指定新文件：

```powershell
uv run --locked --extra trace engineering-ci workflow evidence $execution --otel-json .test-tools/workflow-spans.json
```

使用官方 OTel SDK；缺 SDK 或文件写入失败使本次导出命令非零，不改变原工作流结果。
文件已存在时不覆盖，换一个输出文件名。Span 衡量适配器边界，不能当成单项测试的内部耗时；
Token/费用、Dagger 缓存命中保持未知。当前不自动上传 Collector/PostgreSQL，原 Codex 投影和
OTLP 入口继续保留原契约，不能用此文件冒充 Codex 原生投影。

## 分析已执行的注册工具并导出 XES

沿用上面 run/resume 输出的 `$execution`；这些命令读取已存在的回执，不再执行 pytest 或 Dagger。
先查安全事实：

```powershell
uv run --locked engineering-ci workflow evidence $execution --limit 5 --max-bytes 32768 --json .test-tools/workflow-evidence-details.json
$proof = Get-Content .test-tools/workflow-evidence-details.json -Raw | ConvertFrom-Json
$proof.items | Select-Object step_id, attempt, batch, time_scope, check
```

`check.details` 包括原报告中的实际版本、允许公开的参数、环境和测试统计。
字段缺失不表示零；`batch` 为 Adapter 调用组，不能证明每项检查同时运行。

只导出，交给 Propel 文件导入或其他 XES 工具：

```powershell
uv run --locked engineering-ci mine export-xes --execution $execution --out .test-tools/workflow-cases.xes --json
```

本地分析并保存同一组 Case：

```powershell
uv run --locked --extra mining engineering-ci mine workflow $execution --out .test-tools/workflow-analysis.json --xes-out .test-tools/workflow-analysis-cases.xes --json
$analysis = Get-Content .test-tools/workflow-analysis.json -Raw | ConvertFrom-Json
$analysis | Select-Object analysis_id, case_scope, completeness, activity_counts, check_execution_counts, recorded_result
```

每个 Case 对应一个 Step，事件为它的历次 attempt，活动名称是实际 tool_id。五个独立检查
形成五个 Case，不伪造串行流程；重跑同一执行后，各 Case 会出现新的 attempt。
跨 Step 的原依赖见 `workflow_dependencies`，DFG 不替代原 DAG。

完整性 incomplete 默认拒绝；需要分析已知不完整观测时显式加 `--allow-incomplete`，缺起止或
证据损坏的 Case 仍被排除。分析 `status=passed` 与 `recorded_result` 是两种事实；原失败保持失败。
源码当前有效性未检查，缓存命中、费用及单项检查内部耗时仍未知。

输出使用新文件名，不覆盖旧产物。分析已保存而 XES 失败时，按返回的 `next_actions` 继续导出，
其中 `--event-log-id` 确保仍是同一证据；不要重新运行检查来补一份 XES。
新 workflow-analysis 暂不登记 PostgreSQL 或用于旧 Codex candidates/bind。

## 完整 PR 检查的同输入受控比较

比较入口只读取两个已有执行。先沿用前文完整 PR Profile 的基线 run 回执：

```powershell
$baselineRun = Get-Content .test-tools/latest-workflow-run.json -Raw | ConvertFrom-Json
$baseline = $baselineRun.execution
$workflow = Join-Path $baseline 'workflow.json'
uv run --locked engineering-ci workflow run $workflow --out .test-tools/workflow-runs --reuse-from $baseline --json .test-tools/candidate-run.json
$candidateRun = Get-Content .test-tools/candidate-run.json -Raw | ConvertFrom-Json
uv run --locked engineering-ci workflow compare $baseline $candidateRun.execution --out .test-tools/pr-reuse-comparison.json --json .test-tools/comparison-action.json
$comparison = Get-Content .test-tools/pr-reuse-comparison.json -Raw | ConvertFrom-Json
$comparison | Select-Object status, recorded_quality_gate, check_submissions_avoided, optimization_verified, verified_scope, reasons
$comparison.checks | Select-Object step_id, status, reasons, baseline_gate_passed, candidate_gate_passed
```

候选 run 会重新接受原工具的证据准入；匹配项复用，变化项才提交检查。compare 本身不重新
执行任何检查，不要求 trace/mining extra 或 PostgreSQL。基线必须完整、每项一次且未复用；
两侧仍运行同一份完整 PR 定义，原五个必需检查都保留。

已保存的两次执行可以直接从 compare 那一步进入。--out 每次使用新文件名，避免覆盖独立试验；
--json 只是可更新的操作回执，里面有输出路径、两份执行和 next_actions。进程停止后先用原
workflow status/resume 查看或恢复同一执行，再决定是否已有符合控制条件的样本。
多次 attempt 不会自动压缩成一份有利样本，UNKNOWN 操作不能盲目重做。

报告逐项列出输入、工具、参数、环境或 Executor 差异。不匹配时保留所有匹配项和历史证据，
整体 status=not_comparable，退出 3；它不会为了生成报告自动重跑基线。缺回执或原质量失败也非零。
若源码或检查配置已经变化，旧通过记录仍是历史事实，但不能代替当前输入；如确需新的控制样本，
按前文重新生成当前定义并使用 run，而非删除旧报告或用它强行放行。

两个已比较的原通过结果，只有减少检查提交时才标记该范围的收益；没有减少时退出也可以是 0，
但 optimization_verified=false。原失败即使能复用，仍保持原失败且比较不认证收益。
计数来自原报告的 executed_this_invocation；Dagger 内部缓存、费用和速度仍未测量。
该报告不是当前代码、PR 或 Release 的通过凭据，也不批准某个候选。

## 批准、启用、选择与调用能力

使用上节已经产生的 `$baseline`、`$candidateRun.execution` 和比较文件；定义须来自前文
“关联已经审查的真实候选”。人工编排 source=null 的试验仍可运行/比较，但不能冒充有来源
的 Harness 能力。先审查定义、来源及比较逐项结果，再显式批准一个能力版本：

```powershell
$store = '.test-tools/workflow-capabilities'
uv run --locked engineering-ci workflow approve .test-tools/pr-reuse-comparison.json $baseline $candidateRun.execution --name pr-check-reuse --version 1.0.0 --store $store --json .test-tools/capability-approval-action.json
if ($LASTEXITCODE -ne 0) { throw '批准未完成，按输出保留原证据并处理问题' }
uv run --locked engineering-ci workflow capabilities --name pr-check-reuse --store $store --limit 10 --max-bytes 16384

# 批准没有启用；启用这一份明确的能力版本。
uv run --locked engineering-ci workflow activate --name pr-check-reuse --version 1.0.0 --store $store
if ($LASTEXITCODE -ne 0) { throw '启用准入失败，不调用' }
uv run --locked engineering-ci workflow select --name pr-check-reuse --version 1.0.0 --store $store

uv run --locked engineering-ci workflow call --name pr-check-reuse --version 1.0.0 --store $store --out .test-tools/workflow-runs --json .test-tools/capability-call-action.json
$callExit = $LASTEXITCODE
$call = Get-Content .test-tools/capability-call-action.json -Raw | ConvertFrom-Json
$call | Select-Object status, exit_code, execution, capability, feedback, next_actions
```

1.0.0 是这份能力的版本，不是 engineering-ci 的发布版本。每个版本拥有独立文件；不会
写入 pipeline.toml 或 pyproject.toml。默认 store 被 Git 忽略，跨机器保存须同时保留对应原
定义/执行/比较证据；这不是签名或账户认证系统。discovery 的 admission=not_checked 只列
索引；select 才复核启用与证据，call 再让原工具检查当前输入。

call 默认核验并复用已批准候选的原结果，当前变化项才重跑。`$callExit` 是原检查退出码，
即使 feedback.status=saved 也不能把失败当通过。能力名输入就能调用完整定义，Agent
无需再次展开五个内部步骤。现有 tools 仍是原 Catalog，不生成新 console-script。

停用以及明确切回仍有效的旧能力版本：

```powershell
uv run --locked engineering-ci workflow deactivate --name pr-check-reuse --store $store
# 仅在决定重新启用已审查版本时执行。
uv run --locked engineering-ci workflow activate --name pr-check-reuse --version 1.0.0 --store $store
```

不同版本不会因版本号较新而自动替换 active。原来源、契约或目标策略变化后，准入失败，
按真实状态重新生成/试验/审查新版本；旧文件与失败结果保留。

## 能力调用中断及反馈归档

call 在执行前打印完整检查点。没有命令 JSON 时直接使用这个路径；有 JSON 时沿用 `$call`：

```powershell
uv run --locked engineering-ci workflow status $call.execution
uv run --locked engineering-ci workflow resume $call.execution --json .test-tools/capability-resume-action.json
uv run --locked engineering-ci workflow feedback $call.execution --json .test-tools/capability-feedback-action.json
```

恢复同一冻结能力版本，即使之后被停用，也不换成另一版本。feedback 默认只保存有界安全
事实，不连接数据库、不执行测试；执行失败和反馈保存状态分别记录。写盘失败可重复该
命令，原退出码保持不变。

需要归档到已配置的宿主 PostgreSQL 时，先只读检查，再显式升级已确认属于本组件的 schema：

```powershell
uv run --locked --extra trace engineering-ci trace status --json
# 已确认连接与组件 schema，需要从 1/2 升级到 3 时执行一次；重复执行幂等。
uv run --locked --extra trace engineering-ci trace init --json
uv run --locked --extra trace engineering-ci workflow feedback $call.execution --archive --json .test-tools/capability-feedback-archive-action.json
```

配置仍读取已有 tracing.toml 和 VESRYN_DATABASE，不发送连接串。自定义 tracing 配置同时
给 trace 命令与 feedback --archive 传 `--config FILE`。feedback 的能力 store 取自冻结调用
绑定，没有另一个 --store 覆盖。数据库失败时返回非零和本地反馈路径；修复对应连接/权限/
迁移问题后，重复同一 feedback --archive，不重跑检查。数据库读回核验只证明归档内容一致，
不把模拟工具/测试数据或历史通过记录当成正式发布证据。

## 当前边界

定义/schema/kind、工具契约及来源校验见[架构](../docs/architecture/workflow-optimization/generated-workflows.md)。
现阶段运行有限 DAG 和就绪批次，各批次之间顺序调用适配器；跨依赖批次可能再次准备原 Dagger
调用，尚未提供跨批次长驻 SDK 会话或任意并发策略。循环、动态扩图、嵌套子工作流明确拒绝。
数据引用的静态类型检查针对明确的 object/array/type Schema；复杂组合 Schema 的引用需要后续
显式扩展，实际输出及解析后的输入仍由 JSON Schema 校验。
通用结果不替代 PR CI 或正式 Release 门禁，现有消费者 Workflow 和发布生命周期继续独立。
