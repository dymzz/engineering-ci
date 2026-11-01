# 发布门禁、证据与恢复

当前定位为个人开发：本地获得完整反馈，GitHub 提供快速 PR 检查，
正式版本由人主动发起完整验收。无需团队审批环境、常驻服务或额外调度器。
功能 PR 的合并授权与阶段备份遵循[Agent 指令](../../../AGENTS.md)；
人或已授权 Agent 完成审查与合并，本地通过不能替代正式发布验收。
本文解释发布机制；用户命令、执行顺序和迁移方式统一维护在[使用说明](../../../guides/README.md)。
正式发布准备还需评估[Release Readiness](../../delivery/release-readiness.md)。
它定义公共交付验收，不新增 CI Job，不替代以下实际检查、证据或发布授权。

| 触发 | 实际执行 | 发布 |
|---|---|---|
| PR / push 到 main | 原生 Lint/Format、核心 pytest、四处版本检查、两份 uv.lock 完整性 | 不发布 |
| 手动 Release，publish=false | 上述检查加 Coverage 证据、完整 Dagger checks、既有质量与 Matrix fixture、真实根项目安全 enforce、报告下载核验 | 不创建 Tag/Release |
| 手动 Release，publish=true | 同一完整验收；main SHA 与版本检查通过后才发布 | 精确提交 |

PR 的五项原生 Dagger Checks 通过三个既有 Job 执行，不运行安全扫描或完整 Matrix。
这些额外发布策略仍在手动 Release 中显式执行。

## 检查定义与执行环境

本地与 `ci.yml` 使用同一组[原生 Dagger Checks](../local-checks.md)。
标准 Ruff 配置和锁定开发工具在根 `pyproject.toml` / `uv.lock`；没有宿主 Profile、
输入散列收据或另一套 Workflow YAML 校验器。三个既有 PR Job 名称保持不变。
根仓 Coverage 由原生 `release` 环境开启，严格检查与报告引用相同 pytest 节点。
发布 fixture 通过 `fixtures` 环境、`validation` 模块及四个明确的 fixture 检查名显式执行；
`validation:fixture-test` 由一次报告执行替代，质量/Matrix Check 仍实际执行。
GitHub Actions 继续拥有 Job DAG、权限、Artifact 上传和发布条件。

必需检查的唯一来源是 `src/engineering_ci/requirements.py`：每项记录键、
执行它的 Job 与 step、覆盖范围，以及是否是一个原生 Dagger 检查。
发布证据契约、CI Workflow 与漂移测试都读取该表，
新增或重新划定范围只需改一处，再由机器校验绑定关系，
而不是手工维护 Workflow、注册表与 Schema 三份副本。

## 必需检查与证据的关系

PR 与 main 分支的普通校验要求：Ruff Lint、Ruff Format、Workflow 分析、
核心 pytest、四处版本一致性与两份锁文件完整性；
Workflow 分析由原生 `workflow-analysis` 检查执行，
PowerShell 分析由 `windows-2025` Job 执行。
两者都在正式发布中经由 `core-tests` 复用同一 Job，不重复执行。

证据与执行是两件事：

- 执行结论来自原生结果。Workflow 分析读 actionlint 的退出状态与诊断，
  PowerShell 分析读 PSScriptAnalyzer 的诊断对象与已扫描文件数；
  覆盖不足、空范围与工具失败都不通过，原因分别记录。
- 证据结论来自可交付物。Artifact 上传失败不改变已经成立的执行结论，
  但发布证据不再完整，发布被拒绝。

## 发布 Run 的身份与执行范围

正式发布要求 workflow_dispatch、refs/heads/main、pass 场景，并核对实际提交及四处版本元数据。
完整检查、Manifest 与正式发布均关联同一个 Release Run 的原生执行证据；
PR 轻量通过和本地历史通过记录不能替代这些门禁。
必需检查失败、跳过、证据缺失或上传失败均不发布。

非发布模式运行同一完整检查，但不创建 Tag / Release。它是可选的独立验证，
不是默认发布的前置 Run。既有 Workflow 不跨 PR / Run 借用通过记录。
触发方式仍为手动；GitHub 页面、CLI 使用相同的 version / publish / pytest_scenario 输入，
不新增证据 ID 等必填参数。正常操作步骤只在[使用说明](../../../guides/common-tasks.md#默认发布流程)维护。

安全负向验证：候选分支使用 pytest_scenario=assertion、publish=false，
真实 pytest 退出 1，发布验收失败，已有 Artifact 保留，不创建 Tag/Release。
完整验收没有自动 push/PR 触发路径。

旧 ci.yml 的 pytest_probe_scenario / quality_probe_scenario / matrix_probe_scenario
不再作为 workflow_dispatch 输入。报告与负向门禁函数全部保留，可以本地直接调用
fixture-pytest-artifacts、fixture-quality-artifacts、fixture-matrix-artifacts
并传入原有 --scenario；发布路径统一使用上述 pass / assertion 输入。
[性能测量记录](../../verification/performance/measurements.md)中的 v0.4.7 数字是旧完整 CI 基线，不是当前 PR 工作负载。

## 保护与恢复

完整验收运行既有全部 Dagger checks，包括断言失败传播、13 个质量场景、
三版本 Matrix 的正负场景；Probe 被跳过不能代表通过。
单次 pytest bundle 只替换精确的 fixture-test，其余 check 不变。
核心 JUnit/Coverage、Dagger pytest、安全 Artifact 必须下载并核验；
成功生成报告不能把测试失败变成成功。

验证阶段只有 contents:read，最后 publish job 才有 contents:write。
必需 job 必须实际 success，skipped / cancelled / 缺失均拒绝。
main 前进、版本不一致、Tag 指向其他 SHA、报告缺失或检查失败均不发布。
发布 workflow 串行执行；Tag 仅使用 create-only API，禁止覆盖。

同 SHA 的 Tag 已创建、Release 未创建时，保留可恢复状态；
上传或 API 失败会留下可恢复的 Tag / Draft，不会提前转为正式版本。
已有同 SHA 的正式 Release 必须有一致的 Manifest，才返回原 URL，不重复创建。
已有正式版本缺少或冲突的证据时明确失败，不补写、覆盖历史版本。
祖先提交上的同版本 Tag 不能代替当前提交验证。注解 Tag 比较最终 commit SHA。

## 安全策略发布接入点

可信策略快照与接收契约见[安全策略快照](../security-policy.md)。
策略文件不是发布证据的替代：`publish` job 仍需先通过现有 Manifest 验证。
下载的 Manifest 尚无 `release_url`，此时不能直接执行 `policy publish`。
#53 的接入点在既有 `publish_release` 内：创建或恢复 Draft、准备包含实际 URL 的
Manifest → `policy publish` 生成 publication → 将策略及 publication 上传到同一
Draft 并读回 → 执行既有最终 `draft=false` 操作。恢复时保留已上传的 publication，
不能重新生成不同时间戳的记录冒充同一对象。
上传失败沿用本文的 Draft/Tag 恢复流程，不发布缺少 publication 的正式版本；
已发布对象不覆盖，策略内容变化必须提升 revision。本任务只交付该接入点和
失败恢复说明，未修改 Workflow、未执行正式发布。

## v0.4.9 发布证据

每次手动验证将 Actions 原始 job / step 结果写入
`release-manifest.json`，保存为 `release-manifest` Artifact（30 天）；
Job Summary 同时显示版本、SHA、验证 Run 链接与各检查状态。
机器可读结构见 [JSON Schema](manifest.schema.json)。
同一组输入产生相同核心 JSON；不复制日志、环境变量、Token 或临时下载 URL。

身份来自实际 checkout、四处版本元数据、GitHub Run API 与当前 attempt 的
Jobs API。Manifest 的 `result=passed` 表示必需验证结果与证据齐全，
不是进行中的整个 Workflow 已结束，也不是已经发布成功。
`mode=validation` 时 `release.release_url=null`：没有虚构 Release URL。
正式证据中的 `release_url` 使用 GitHub 实际返回的 Release API 资源 URL，
其 ID 在 Draft 转正式时保持稳定；Workflow Summary 提供正式页面链接。
失败验证可保存 `result=failed` 的 Manifest；API 不可用时保留失败 job 日志，
不能伪造缺失状态。检查失败、skipped、cancelled、timed_out、unavailable
都阻断发布。可选未启用项目记为 `not_enabled`。

Manifest 同时记录 `requirements`：它被验证时实际生效的必需检查集合，
含每项的键、Job、step、覆盖范围与 Dagger 检查名，并附一个摘要。
这使"结果仍然有效"与"结果不再覆盖当前要求"可以被区分：

- 候选验证把记录的要求集合与当前要求集合比较。新增、删除或重新划定范围
  都会被指名拒绝，历史结果不能被重新解读为当前要求的证明。
- 已发布 Release 按自己记录的要求集合核对原始 Actions 结果，
  因此要求集合演进之后，已发布版本仍然可以核验。

在记录该字段之前写出的 Manifest 仍然可用：它自身记录的 job 与 step
就是它被验证时的依据，但它不覆盖范围，候选验证不接受它。

Ruff、核心 pytest、版本锁一致性、Workflow 分析、PowerShell 分析、
安全 enforce、Dagger 与原始 pytest Gate 关联既有 job / step；
Coverage / Python Matrix 关联实际执行清单中的
`fixture-quality-gates` / `fixture-python-matrix`，不再执行一次。
它们是既有模块能力 fixture，不能当作根项目多版本兼容或根 Coverage 阈值。
根 Coverage 无阈值和 Smoke 专用负向 Probe 都明确未启用。

## 执行清单与覆盖

包住执行的 Job 成功，并不证明每一项必需操作都真的运行过。
可复用 Workflow 因此写出执行清单：模块提供的检查、被执行的检查，
以及被一次报告执行替代的那一项。`manifest facts` 核对清单与发现列表一致，
并要求必需集合被覆盖；缺一项或清单自相矛盾都会拒绝发布。
原先只要求两项 fixture 检查出现在清单中，其余可被漏掉；现在要求全部四项。

工具记录读取实际环境：根 Python / uv / pytest / pytest-cov、Ruff runner
的 uv / Ruff、既有 `dagger version`、已核验的 OSV Scanner 版本。
scope 区分执行环境；不把根工具版本当作 Dagger 容器内部的版本。
Artifact 条目仅保留原生 ID、可用 digest 和 API 元数据链接。
Artifact API 不提供 attempt，因此证据下载使用本次生产步骤的原生
`artifact-id`，经 job / reusable workflow outputs 传递；不按同名文件猜测，
不枚举整个 Run 后选最新条目。API 另行核对 Artifact ID、名称、Run 与 SHA，
Jobs API 核对当前 attempt 和 SHA。facts、Manifest 也用精确 ID 下载。
当前 attempt 缺少必需检查或原生输出时拒绝发布，不借用其他 attempt 的通过记录。
验证 Artifact 有保留期；正式 Release 的 Manifest Asset
提供持续追溯入口。重复发布还需要原始 Actions 元数据；历史记录删除导致
无法核验时明确拒绝，不修改已发布版本。

Schema 约束字段、固定检查来源、状态、工具 scope/name 和必需证据数量；
Python 校验进一步核对版本与 Tag、仓库与 URL、SHA 及原始 API 结果等
跨字段和远端事实。通过 Schema 不等于已有可信的发布验证。

正式发布沿用完整验证与 create-only Tag，然后用 GitHub 原生自动 Release Notes
创建 Draft。填入实际 Release URL，上传 Manifest，再下载并核验原始检查、
版本 / SHA 和上传内容，最后才将 Draft 转为正式 Release。
任一步失败均不覆盖 Tag 或已上传 Asset，也不伪造成功。
GitHub 上游失败可能留下 size=0、state=starter 的空 Asset；恢复仅在
核对同一 SHA / Tag / Draft，并读回确认仍是该空 Asset 后删除并重新上传。
正式 Release 或已上传文件不执行此清理。读回失败、权限不足、上传错误均
阻断转正式；HTTP 错误只保留安全状态码，不输出原始响应。
同 SHA 的 Draft 已有有效 Manifest 时保留原始验证 Run 的证据并核验后恢复；
已有正式版本的重复调用核对原有证据，不修改已有版本。
只有 publish job 有写权限；证据收集不新增 pytest / Dagger 调用。
完整检查与 Artifact 上传仍可能因外部服务失败而拒绝发布。

验证层次与剩余风险见 [v0.4.9 审查记录](../../verification/release-risk-review.md)。

GitHub 无法原子绑定 main 比较与 Release 创建。验证完成、创建 Tag 前、
创建 Release 前都会核对 main；最后 API 调用仍有极短竞争窗口。
发布期间 main 变化会使当前证据与发布目标不一致；不强推或自动撤销 Tag。

## 兼容性与状态检查

Smoke / OBridge 的共享 workflow 调用与 Dagger API 不变。
新增可选 verify_module_lock 默认 false，仅主仓库发布启用。
安全 helper 默认保留已发布 v0.4.7，本仓库发布显式使用候选 SHA。
Renovate 仍检测正式 Tag。公共接口范围和独立消费验收见[交付要求](../../delivery/README.md)；
Release 入口本身不执行 PR 合并，不由 Readiness 重建另一套发布门禁。

PR 保留 sync-tool-tests、version-metadata、python-ci / checks 三个真实状态名。
Workflow 分析作为 python-ci / checks 内的独立 step 执行，
PowerShell 分析使用独立的 powershell-analysis Job；
两者都不改变既有状态名。python-ci / dependency-security 移到手动发布；
若是 Required Check，需移除旧 PR 要求，不能提供假的成功占位。
此前私有仓库套餐的 branch protection / rulesets 返回 403，无法启用 Required Checks。
若目标仓库未启用服务端必需检查保护，执行合并的人或 Agent 必须核对当前 PR head 的三个状态，
不能宣称服务器已经阻断失败合并；实际权限及限制仍以本次 GitHub 操作结果为准。

三层结论互不替代：Dagger 的检查结果说明这项检查实际跑了什么；
Actions 的 Job 状态说明该 Job 的 step 是否成功；
GitHub 服务端的 Required Status 决定合并是否被阻断。
本仓只拥有前两层；第三层属于仓库管理设置，本次实现不改动它，
也不据此宣称服务端已经阻断失败合并。


## 本地请求与调用职责

Git/PR 使用原生工具完成；用户 `publish` 只请求 main 上已有的 Release Workflow。
具体 Dispatch 在 POST 前排他保存意图；未知响应停止并报告人工核验命令，绝不再次 POST。
`status / verify` 只读显式 Run，`wait` 使用原生 gh 等待后核验；不读取旧 FSM 或阶段收据。
一般 Artifact 下载交给 `gh run download`，日志交给 `gh run view --log`。
具体约束与结果见[CLI 边界](../cli-boundaries.md)。

Actions 通过 `engineering_ci.workflow_actions` 调用 `release prepare/verify/publish` 与
`manifest facts/tools`。内部动作核验真实 Actions 上下文和发布准入，不是用户请求的另一个入口。
原始 Manifest Schema、检查/工具定义与发布冲突检查保留；不重建本地交付 FSM 或远端 DAG 镜像。
操作与继续方式见[使用说明](../../../guides/common-tasks.md)。
