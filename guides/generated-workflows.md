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

`mine bind` / `checks --binding` 是 PR #25 早期原型的兼容桥，旧绑定文件不作为新的可执行定义。
新入口生成 `workflow.json` 与 `provenance.json`，宿主兼容桥保留在 CLI 层；原 checks 库
不导入 mining 或工作流核心。

## 当前边界

定义/schema/kind、工具契约及来源校验见[架构](../docs/architecture/workflow-optimization/generated-workflows.md)。
现阶段运行有限 DAG 和就绪批次，各批次之间顺序调用适配器；跨依赖批次可能再次准备原 Dagger
调用，尚未提供跨批次长驻 SDK 会话或任意并发策略。循环、动态扩图、嵌套子工作流明确拒绝。
数据引用的静态类型检查针对明确的 object/array/type Schema；复杂组合 Schema 的引用需要后续
显式扩展，实际输出及解析后的输入仍由 JSON Schema 校验。
通用结果不替代 PR CI 或正式 Release 门禁，现有消费者 Workflow 和发布生命周期继续独立。
