# CLI 与 Workflow 调用边界

用户入口与 Workflow 内部操作分开。scripts 只保留 release.py 和 download.py，
当前用户命令与旧路径迁移见[使用说明](../../guides/README.md)。

```text
scripts/release.py publish → release/delivery.py（检查/提交/Push/PR/人工合并停点）→ execution Coordinator → release.yml
scripts/release.py status → 当前 Git/PR、本地结果与已有 execution checkpoint → GitHub GET
scripts/release.py validate → execution Coordinator（独立完整验证请求/等待）→ release.yml
scripts/release.py verify → release/verification.py → GitHub GET（已有 Run 与证据）
scripts/download.py → cli/download.py → execution Coordinator（已有 Run）→ ArtifactCollector
engineering-ci checks/version → checks.py / release/version.py
engineering-ci trace → cli/trace.py → tracing/（明确文件、宿主归档及只读回放）
engineering-ci mine → cli/mine.py → mining/（固定派生视图、Rust4PM 分析、比较、候选及显式 Handler 绑定）
engineering-ci checks --binding → cli/checks_binding.py（兼容桥）→ 原完整 pr
engineering-ci workflow → cli/workflow.py（组装 Catalog）→ workflow_core → workflow_adapters → 原检查/查询
GitHub Actions → workflow_actions.py → checks.py / release/ / tools/security.py
专项工具 → python -m engineering_ci.tools.<name>
```

发布的 job 依赖、Coverage、Matrix、Security 与证据收集仍由 YAML 定义。
检查版本、范围、参数与依赖集中在[独立 Pipeline 配置](../configuration/pipeline.md)，
checks.py 负责调用既有工具；Release Manifest 引用相同 CI step 名称。
本地交付层次化 FSM 管阶段准入，已有 execution FSM 管 Dispatch、等待和恢复；
两者不替代 Actions 的远端 DAG。

当前 `checks --binding` 为证据绑定原型的兼容桥。独立生成及工具编排由单独调用层组装，
原 checks/Release/Download 不反向依赖生成核心；实际入口和依赖方向见
[独立生成与编排](workflow-optimization/generated-workflows.md)。

## 公开调用契约

操作步骤、参数示例和退出码统一维护在[使用说明](../../guides/README.md)。
安装后的 `engineering-ci` 和两个脚本调用相同实现；模块运行使用
`python -m engineering_ci.cli`，需要已安装项目或设置 PYTHONPATH。

Release 交付模块读取当前 checkout 和 PR 事实，复用检查定义与结果身份校验，调用原生 Git/gh 完成提交和 PR 交付。
人工合并前输出停点、链接、SHA 和继续命令，不执行 Release Dispatch。合并后才交给已有 Coordinator 请求、等待。
status 使用相同层次化 FSM 从实际 Git / PR 事实识别停点；已有 Run 来自原 execution checkpoint。
本地阶段不新增状态文件，恢复时读取原生 Git / PR 证据；不建立任务注册表。
公开 verify 必须指定实际 Run，只读取远端状态与证据，不调用 Coordinator 或写 FSM。
GitHub Actions 负责完整检查和发布决定，JSON 生成或文件收集不能覆盖原始失败。
Release 不初始化下载后端或读取下载配置；只有 download 负责收集日志及 Artifact。
轨迹的来源与归档游标不进入 ExecutionContext 或发布门禁，归档回放不推进业务 FSM。
`trace status/inspect` 为只读；`init/import` 明确修改专用数据库对象；
`replay` 明确输出原始快照文件。详细契约见[轨迹架构](workflow-optimization/design.md)。

## 操作结果与恢复视图

`release/results.py` 的 `OperationResult` 是当前调用的只读结果视图。
它引用 FSM / Pipeline 使用的同一个 `ExecutionContext`，不增加持久化状态，不参与准入判断。
`status`、`validate`、`publish` 的最终文字摘要与 `--json` 使用同一视图。
原有进度和工具输出继续保留；JSON 模式把这些输出送到 stderr，stdout 只输出一个 JSON 对象。
子进程的未捕获输出也显式送到 stderr；用于工具探测的捕获输出保持原用途。

Schema 版本为 1，其顶层字段如下：

| 字段 | 定义 |
| --- | --- |
| `operation`、`repository`、`version`、`tag` | 本次显式请求身份；version 不含 v，tag 包含 v |
| `status`、`exit_code` | 调用完成、停止、失败或中断，以及原始 CLI 退出码；不是正式发布资格 |
| `delivery` | 当前 FSM 阶段、checkout / 分支 / SHA / 未提交文件 / PR 身份及已经形成的阶段证据 |
| `local_checks` | 既有检查报告的投影，包括是否当前通过、逐项有效性、执行/复用情况和报告位置；不复制环境变量或日志 |
| `review` | 实际 PR head、必需 CI 结果与可合并状态；未查询时为 null |
| `executions` | 原 execution checkpoint 的阶段、Run、恢复阶段和脱敏 Dispatch 诊断；服务器提供时包含 Run Attempt / SHA |
| `publication` | 未确认、Draft 或经过既有 Manifest 校验的正式 Release 及其原始验证 Run / Attempt |
| `operations` | 本次实际调用的检查、Commit、Push、PR 更新和 Workflow executor；调用 executor 不表示一定发出了 POST |
| `stop` | 停止原因代码及安全说明；没有停止原因为 null |
| `next_actions` | 动作种类、独立 argv 参数数组、人工操作标志及前置条件；空 argv 表示需要人工决定，不能执行 |

`status=completed` 只表示当前操作成功完成。例如 status 查询成功时 exit_code 为 0，
publication 仍可为 unconfirmed，stop 可为 human_review 或 pr_ci。
publish 等待审查与本地 incomplete 都可能退出 3，调用方必须同时查看 stop.code、review 和 local_checks。
环境缺失、失败、中断保留原有 2、非零工具退出码和 130，JSON 生成不覆盖它们。

checkpoint 的 binding 为 matched（远端 Run 满足已提供的版本 / 分支及 SHA 约束）、
historical（已确认不对应本次身份）或 unverified（尚未完成远端身份核对）。
binding 本身不是当前交付准入；status 只有 FSM 已确认 PUBLICATION_ready 才给出 publication Run 的恢复命令。
UNKNOWN 候选只列为候选，不能成为已确认 Run 或可执行的 resume 动作。
checkpoint 的 remote_conclusion 来自原执行记录，live_conclusion / status / run_attempt 来自本次远端查询，
不将重跑后的最新结果覆盖成原记录。FINISHED 的继续动作先核验同一 Run，不提示盲目重新 Dispatch。
历史 Run 不产生当前恢复建议；已经合并却仍有未交付文件时，需要人工决定后续 PR。
任何后续查询异常或中断都保留已完成阶段证据，并移除先前的合并建议，优先给出只读 status。
重新尝试必须先解决所报告的错误，并继续接受原有门禁校验。

next_actions 是提示，执行前仍经过原有身份核对和门禁，不能直接成为放行依据。
本次未执行或未查询的信息保持 not_run / not_inspected / null；旧检查是否有效仍由 checks.inspect 判定。
不读取终端文字来推断结果，不复制原始 API 响应、PR body、认证信息或旧状态中的任意错误字符串。
公开 `verify --json` 继续使用原有 Run 验证结构；本视图不合并本地检查 JSON 或 Release Manifest 的 Schema。

## 交互与非交互下载

显式 --interactive 后依次选择 Workflow、最近 30 个 Run 与未过期 Artifact 名称。0 收集所有名称，q
取消；不自动挑选默认值。已完成且无 Artifact 时收集日志，未完成时等待并收集最终输出。
同名 Artifact 保持原名称过滤语义，不按最大 ID 猜测最新 attempt。

Codex 使用 --run RUN_ID；ID 必须为实际正整数。真实 API 提供 Workflow ID 与 ref，
--workflow / --ref 仅作约束。无法确认身份时拒绝调用 Coordinator。
--resume RUN_ID 是别名；下载入口拒绝 --input、--new-run 和无 ID 的 --resume。
原生进度恢复、auto/aria2/native、Digest、ZIP 校验与输出目录沿用原有实现。

首次使用安装 gh 并完成认证。省略 repo 时按当前目录 origin 与缓存识别仓库。
交互需要终端；两种模式都只查询/恢复已有 Run。

## Workflow 内部调用

```sh
python3 -m engineering_ci.workflow_actions checks ruff-lint
python3 -m engineering_ci.workflow_actions checks tests --coverage
python3 -m engineering_ci.workflow_actions release prepare
python3 -m engineering_ci.workflow_actions manifest facts --directory .release-evidence --output release-facts/facts.json
```

Actions 设置 PYTHONPATH 为实际 checkout 的 src。内部 release prepare/verify/publish
读取 Actions 上下文、真实 SHA、Run Attempt 和必需证据，不用于本地请求发布。
这些操作不会触发另一轮 pytest 或 Dagger。内部 release verify 生成和校验 Manifest；
公开 release verify 只读已有 Run，不能将两者混作触发命令。

Security 默认 helper ref 仍为已发布的 v0.4.7，调用参数与消费者保持兼容。
匹配的新 helper 使用 workflow_actions security；旧 helper 继续调用其已发布的脚本。
入口只按文件是否存在选择；执行失败直接传播，不切换到另一入口重试。

普通 Workflow 的底层 Dispatch 参数保留在 engineering_ci.execution.cli，可非交互调用；
有真实 POST 副作用。旧 PowerShell 函数迁移与专项工具调用见[使用说明](../../guides/README.md)。

trace send 通过官方 OTLP/HTTP exporter 显式发送已有脱敏投影，不采集原始内容或推进业务 FSM；定义见 [P1 架构](workflow-optimization/design.md)。
mine candidates 复用已有分析和实际投影，仅产生候选与审查依据；不重跑分析、调用模型或执行候选。
调用与停止后的继续方式见 [候选审查](../../guides/cli-reference.md#工作流候选与人工审查)。

workflow 由 CLI 组装宿主 Catalog，调用独立 workflow_core。生成文件实际声明工具及步骤，
workflow_adapters 正向调用原 checks / Dagger / 报告检查。原模块不反向依赖 mining 或核心。
原型 --binding 的兼容处理在 cli/checks_binding.py，只有显式选择该旧参数才加载。
新调用及 Schema 见[独立生成工作流](workflow-optimization/generated-workflows.md)和
[操作说明](../../guides/generated-workflows.md)，不改变 scripts 的发布/下载责任。
