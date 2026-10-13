# 独立工作流验收

本记录对应 PR #25 的独立生成/执行实现，区别于早期 `registered-pr-check-reuse/1` 绑定原型。
验证在主 checkout 完成，没有替代工作区、数据库迁移、Workflow Dispatch、合并或正式发布。
操作说明见[生成工作流](../../guides/generated-workflows.md)，契约见
[架构](../architecture/workflow-optimization/generated-workflows.md)。

## 替身及本地核心测试

新增核心与适配器测试共 48 项通过，覆盖独立生成、规范 Schema、实际参数/依赖、JSON Pointer、
类型不匹配、缺失工具/输出、未知能力、环、资源损坏、报告保存失败、旧 CLI 兼容、状态及复用。
隔离测试把核心作为单独 Python 包导入，不加载 engineering-ci、Dagger 或 PostgreSQL。
替换同一契约的适配器不改核心。真实子进程 `os._exit(91)` 验证落盘意图和文件锁释放；
读/检查恢复，未知写入不重做。已知写入拒绝保留原失败，在缺少领域验证时暂停。
共享上下文测试核对 FSM 与调用对象相同，前置视图不能修改权威结果。

报告保存失败采用注入 OSError；原 pytest 退出 1 保留，其他报告不可用不掩盖该失败。
这些替身结果不是 GitHub 发布验证，也不表示已经接入真实写工具。

## 真实 Dagger / Linux

使用仓库固定 Dagger 1.0.0-beta.16 及原 repository-check 执行器，完整检查实际运行结果：

| 验证 | 结果 |
| --- | --- |
| 完整 PR 工具工作流 | 五项通过；1141 tests + 105 subtests，JUnit 共 1246，failure/error/skipped 均为 0 |
| 第二个生成文件的新执行，显式 reuse-from | 五项均复用，未提交新的检查 Artifact 执行 |
| 临时真实 `assert 1 == 2` 测试 | pytest 和工作流均退出 1，原失败日志/JUnit 保存 |
| 同一负向执行 resume | 五项事实均复用，pytest 和工作流仍退出 1 |
| 移除自建临时测试，恢复原输入 | 五项原通过事实复用，不再提交 pytest |
| Ruff → report-inspect | 原报告资源实际传递；接收步骤 input_sha256 等于解析后资源输入摘要，原 freshness 检查通过 |

同参数就绪检查通过一个原执行器批次运行，不按工具数重复初始化 Dagger。
现有 JUnit、原容器报告、环境与文件摘要均通过 `dagger_checks.read_evidence` 读回。
不能识别 Dagger 内部缓存命中时不把它写成成本收益；“未提交新执行”指检查证据复用，
不是仅根据显示时间或工具调用完成推断的结论。

## 实际来源及独立产物

两次生成使用不同 generation_id，具有相同 definition_id：

- 人工展开完整 PR Profile。
- 已登记 Rust4PM 分析和实际投影关联核验后，用户显式选择同一 PR 目标。

登记分析 ID 为 `1a730b2a683b402ef06ce4eb1073505886ec4a4183413f94b81d5b217b6a66d0`，
候选 ID 为 `5de6ca846d52575adae5313249a8ad45f57e03fd34b630563b901de91c098cb4`。
仅查询既有派生记录，不读取原始实录、不重跑 mining、不写数据库。
来源完整性 unknown、历史检查身份 unavailable、optimization_verified=false 保持有效。

本地集中验收文件为 `.test-tools/workflow-v061/acceptance-review.json`，
汇总生成/来源关联、逐项真实回执、失败/复用以及实际数据输入摘要；
原运行文件各在 `.test-tools/workflow-runs/<execution-id>/`，测试日志与命令 JSON
在 `.test-tools/workflow-v061/`。本地文件不作为远端 CI 或发布门禁。

## 交付边界

继续在[现有 Draft PR #25](https://github.com/dymzz/engineering-ci/pull/25)交付。
GitHub PR CI 通过 Push 后的实际 Workflow 记录验收，正式 Release 仍须人发起，未在本轮运行。
原 CI Job、发布门禁、消费者调用及两个 scripts 入口保持原责任。

当前只注册检查和报告查询；没有 Git、Dispatch、Merge 或 Release 执行工具。
跨依赖批次仍调用原 Dagger 入口，长驻 SDK 会话、任意并发、嵌套子流程和动态循环未实现。
native pytest 在完整 JUnit 回执形成前强杀时不能安全复用，将只重新执行证据不完整项。
复杂 Schema 的静态引用推导限于明确的 object/array/type；运行值仍由正式 JSON Schema 校验。
这些边界不能被生成配置当作已经提供的能力。

## Harness P0 与离线 XES（v0.6.2）

本轮在 PR #26 接入真实尝试观测、显式候选/目标/执行关联，以及用户要求的离线 XES 出口。
沿用原五项检查和 Runtime，版本不另分 PR；未创建 Tag、Release 或手动 GitHub Run。

专项回归分层记录，替身与真实运行的作用不同：

- 原核心、适配器、有界查询、Handler 兼容及本轮操作观测专项共 **126 passed**。
  包括真实进程 os._exit(91)、真实 pytest 成功/断言失败/异常失败；错误仍非零，观测文件不含错误正文。
- XES 来源/失败保存及真实 Rust4PM 读回相关回归 **79 passed**；随后新增不可用分析语义及组合回归 **46 passed**。
  组合命令：`pytest -q tests/test_trace_mining.py tests/test_workflow_observations.py tests/integration/trace_mining.py tests/integration/workflow_observations.py`。
- 官方 OTel SDK 1.45.1 实测 Span 生成，失败状态/退出码保留；无 GenAI 冒充、无 Token/费用推断。
  不发送 Collector，也不把缺结束/时钟逆序补为 Span。
- 观测 OSError、旧检查点、整个旧会话丢失、过期分页书签、源资源损坏、目标策略变化与并发锁均有测试。
  这些故障测试不替代 GitHub 发布验证，当前仍无真实写工具。

真实 Dagger/Linux 最终输入检查：**1197 tests + 105 subtests**（JUnit 1302），
failure/error/skipped 均为 0；Ruff Lint/Format、Version、Lockfile 全部 passed。
该执行为 `.test-tools/harness-p0/final-run.json`，execution_id=`d37067b3da484240ad961adb704fa0b7`。
XES 改动使原 Ruff/pytest 证据过期，实际重新执行这三项；版本/锁文件输入未变，继续复用原证据。
这次执行不能与改动前的本地通过记录混用。
最终输入再次调用，execution_id=`2f246930c2264497aa52faa4db27d9f5`：
五项 actual_check_executed=false、evidence_reused=true，均引用上述原执行回执，
没有提交新的 pytest/检查 Artifact 执行；原 Dagger 仍做真实环境和证据准入。
读回的五项观测和原回执关联均通过，OTel 投影各有五个 Span，Dagger 内部缓存命中保持 unknown。
本地汇总 `.test-tools/harness-p0/acceptance.json` 与 evidence-reuse.json 保存这些实际事实。

本轮曾只读读取已登记的实际分析与投影，随后导出原字节到 analysis.json/projection.json。
移除本进程的 VESRYN_DATABASE 后，`mine export-xes` 完全离线成功：
6 个准入 cases、40 个事件、56 个排除 cases，completeness=unknown；失败与未知结果保留。
真实 Rust4PM 0.6.2 导入后变体与原分析相同，局部 Rust 注册对象均清理。
导出文件为 `.test-tools/harness-p0/cases.xes`，SHA-256：
`2052b710c8c0e9379de353ed24b035d3b16942793584d0b26e099dd83292e6fa`。
未读取原始 rollout、重新执行 Mining、写数据库或恢复 Propel 连接排障。
Propel 桌面 UI 文件导入仍由用户人工确认。

文件模式候选 ID 与登记模式按现有 origin 规则分别产生，不能互相冒充。
来源完整性 unknown、历史检查身份 unavailable、approved=false、optimization_verified=false 均保持。
本轮证明已注册调用的本地观测及来源/目标关联；没有宣称完整 Agent 轨迹、优化收益或反馈入库。

## 注册操作事实与 Step Event Log（v0.6.3）

在原 checkout 从已合并 v0.6.2 main 开发。相关单元、旧来源/查询/候选回归及实际
Rust4PM/OTel SDK 集成共 194 项通过；随后新增的未执行结果测试所在文件 13 项通过。
测试覆盖安全字段、执行/复用区分、工具版本变化、缺边界、坏时钟、资源损坏、批次不一致、
旧观测、输出保护和分析/XES 部分保存。集成夹具的领域工具使用替身，Rust4PM 与 SDK 使用真实安装。
既有真实 pytest 成功、assert 失败、异常及强杀恢复测试仍通过，不用模拟成功代替发布结果。

本轮真实 Dagger 1.0.0-beta.16/Linux 完整 PR Profile 五项全部通过，JUnit 1315 个测试结果，
failures/errors/skipped 均为 0；原报告保留 actual tool versions 与 Linux/x86_64 环境。
执行 `c4023dd40c664876890371785edaccc7`，生成 `5913cc09b92e45a1ad06f8588ecf7021`，
原 Codex 候选继续通过显式来源关联；候选历史来源 unknown 与本地观测 complete 分别保留。

`mine workflow` 只读该执行，真实 Rust4PM 0.6.2 生成 5 个 Step Case/5 个事件，0 个排除项。
各事件有 35–47 个允许公开的属性，包括实际工具身份、批次、参数、实际版本、环境、结果和摘要。
Case 内没有跨 Step 的伪造后继边；原 DAG 另保留。OTel SDK 1.45.1 导出 5 个 Span，明确为
adapter_invocation；检查内部耗时、成本与 Dagger 缓存命中仍未知。

Event Log 身份 `39740bc902b76d090019b3cd56a8f2b26a258a6cd0a56d9f7f7bb39639d457cb`；
分析身份 `ef08f29401cbf6e8157f9f1c0c54f135c7ac7242ac775d641e5b6808547d0536`。
移除本次进程 VESRYN_DATABASE 后独立 XES 导出成功，与分析时导出的字节相同。
XES SHA-256 `9fffe2a06e4896e025fd5844b95410075f47e13217a6de25aa06e1bde148cb51`，
真实 Rust4PM 读回变体与分析一致，局部注册对象清理。

导出前后原执行目录文件摘要一致（互斥锁文件除外），新增检查 Attempt 为 0。
本地证据在 `.test-tools/harness-v063/acceptance.json`、analysis.json、cases.xes 与原工具回执。
输出可再导出，不需要再次执行 pytest/Dagger；CLI 的失败恢复命令绑定 Event Log 身份。
受影响文档的 29 个内部链接/锚点通过核对。

本轮不证明优化收益或完整 Agent 决策流，不写 PostgreSQL，也没有重新排查 Propel。
Propel 桌面文件导入与正式 Release 仍未作为本轮真实验收结果；原 CI/Release 门禁保持独立。

## 首个受控比较：PR 检查提交复用（v0.6.3 P1）

继续同一开放 PR #27 和原 checkout，四处版本仍为 0.6.3。相关比较、查询、FSM、
旧挖掘比较及真实 Rust4PM/SDK 回归 122 项通过。后续兼容/资源校正的最终比较测试 28 项通过。
其中真实 pytest 成功与 assert 失败各只执行一次，候选复用原输出，退出分别保持 0 和 1；
其余四个工具在这两个负向/正向夹具中为显式替身，不把夹具当成全量 Dagger 验证。

真实 Dagger 1.0.0-beta.16/Linux 控制基线执行为
`c03b6ef7a7b54da5a22a5fe0b078aa11`；候选为 `df325f39c95a468ebb262f1aeee47027`。
二者读取同一生成 `b6b47bbae66341978c95401d637d2e8e` 的完整 PR 定义；
基线五项通过，真实 pytest 为 1236 tests + 105 subtests，JUnit 1341、零失败/错误/跳过。
候选按原 --reuse-from 准入，五项全部复用；两侧逐项身份、原门禁及冻结来源均匹配。

| 观测量 | 基线 | 候选 |
| --- | --- | --- |
| 原必需检查 | 5 | 5 |
| 实际观测的调用 Attempt | 5 | 5 |
| 原报告标记的检查提交 | 5 | 0 |
| 原证据复用 | 0 | 5 |
| Runtime Adapter 调用批次 | 1 | 1 |

这里的提交是原 executed_this_invocation 标记，不是进程数或 Dagger 缓存未命中。
两侧均保留原完整通过结果和全部必需检查；没有省略操作、改变质量阈值或清空后端缓存。
cache_policy=backend-managed，缓存命中、检查内部耗时、费用与速度改善未测量。

实际整合暴露并修复两个本项目问题：新比较目录参数与旧生成候选 ID 内部名称冲突，
在任何检查前停止；已分开命名并增加兼容测试。Dagger 原始证据与 JSON 读回的资源列表
顺序不同，首次比较误拒绝；路径/摘要完全一致，修复后按完整资源引用比较，仍验证数量、
路径与摘要，不去重或忽略损坏。旧误拒绝报告 comparison.json 保留；修订规则为
pr-check-submission-reuse/2，ID 独立，不覆盖历史结论。

最终比较只读这两份已完成执行，没有再次提交 pytest 或 Dagger 检查。
comparison-final.json 的 ID 为 `dcc9edde5fafbf27947af5954fd37b24517c470c199d55833fa9738a680fd267`，
status=compared、recorded_quality_gate=passed、check_submissions_avoided=5。
optimization_verified=true 仅限 verified_scope=check-submission-reuse；approved=false，
current_input_validity=not_checked。原检查点摘要与 run 回执一致，比较新增 Attempt 为 0。
本地证据在 `.test-tools/harness-p1/acceptance.json`、两份 run/原回执和独立比较报告。

这是一组匹配的历史控制样本。资源排序校正及规则版本修订后的最终代码由相关测试和当前
PR CI 验证，不能用校正前基线的通过记录声称当前输入已通过。报告也不替代 PR/Release 门禁。
本轮没有认证整体 Agent 决策、速度、费用、故障率或发布资格；P2 批准/激活/反馈尚未实施。

## P2：已批准能力的选择、调用和反馈（v0.6.4）

本轮基于已 Squash 合并的 PR #27，使用原 checkout 的 codex/workflow-capabilities。
四处项目版本为 0.6.4；能力 pr-check-reuse 的版本为 1.0.0，两者身份独立。
规范和操作见[能力契约](../architecture/workflow-optimization/generated-workflows.md#能力批准版本选择与执行反馈)
及[用户指南](../../guides/generated-workflows.md#批准启用选择与调用能力)。

相关回归 200 项通过；新增能力最终测试 23 项通过，包括真实 pytest assert 失败退出 1、
批准不启用、显式切换/停用、拒绝损坏/修改证据、部分写入恢复、锁、冻结上下文、输入改变
只重跑相关项、操作 JSON 不覆盖目录索引、预算及反馈失败不改原退出码。其他原检查替身
按测试注明，没有把模拟五项检查计为真实 Dagger 执行。

宿主 PostgreSQL 显式集成 12 项通过：组件测试 schema 的 1/2→3 迁移、旧字节保持、原
projection/analysis 目录兼容、反馈真实写入与读回、同身份重复写入、内容冲突、非法敏感
字段拒绝。默认 CI 不连接宿主数据库，不跳过必需核心测试；手动入口为：

```powershell
uv run --locked --extra trace --extra mining pytest -q tests/integration/workflow_feedback.py tests/integration/trace_catalog.py
```

### 实际 CLI、Dagger/Linux 与反馈链

实际控制样本保留在 `.test-tools/harness-p2/`：

| 事实 | 实测结果 |
| --- | --- |
| 完整 PR 基线 | 五项原门禁通过；JUnit 1366 个用例，无失败/错误/跳过 |
| 受控候选 | 同输入五项通过，原检查提交 0、复用 5 |
| 批准/启用/发现/选择 | 各自返回独立动作；未自动启用或选择较新版本 |
| 能力 call | 原五项通过、提交 0、复用 5，生成真实安全反馈 |
| 停用后新 call | 退出 3，没有开始新检查 |
| 停用后恢复原 call | 同一 execution/approval，退出 0；十条累计观察、提交仍 0 |
| 实际调用反馈归档 | 组件测试 PostgreSQL schema 3；两次写入只有一条，读回原字节与摘要一致 |

实际 definition 为 `97bcfa370aeb680dca4933c9f27b9d63d61797dbd65ae0f9a657ef60c181959d`；
baseline `ff810223f52643ac8b1259fb7ab1fb82`，candidate `392f78c101394a58a078a6ec1e72ec75`，
call `88051221bb734e0887987fac7d87d777`。比较 ID
`d35ae9a14b81bb177b8d733d8bf7e91a371cfa145c88622c3575cc1fef81c927`，仍只认证
check-submission-reuse，原 P1 文件 approved=false。

显式验收 store 为 `.test-tools/harness-p2/final-capabilities`；approval
`f02d6dd9f6c43fee9f650ae8840f9e33583ec1071d9c07fa38e47e771f393d0d`。
它记录验收命令，不声称经过额外人工批准或签名认证。反馈
`87f03cdc50c6668ba20f53ace6b7dd08f3293adfd89ab6b394d0da74e368a2e8` 已在真实宿主的
唯一组件测试 schema 归档并读回；测试 namespace 清理，生产归档 schema 未迁移。
原基线与候选状态摘要未改变，反馈操作新增检查提交为 0。

`acceptance.json` 汇总实际身份、逐项指标和 PostgreSQL 读回事实；原命令 JSON、日志和
完整回执仍独立保留。中间失败验证也保留：SQL 模板量词转义/夹具/可选 SDK 安装问题，
以及新测试误读原分页返回字段；按实际失败修正，没有删除负向用例或改原失败传播。
审查补齐了批准后到 Runtime 冻结源之间的竞态核验，以及新命令在编码预算前的身份设置。

这些是当时相同输入的控制样本，不是后续代码的无条件通过记录。最后预算修订由当前
能力测试与 PR CI 验证；旧样本不能替代当前输入、PR SHA 或正式 Release 门禁。
没有认证速度、费用、整体 Agent 效果或任意任务成功。自动调优/跨版本试验、远端能力
分发、签名身份和 PostgreSQL 反馈接入旧 trace query 尚未实施。旧 schema 2 客户端不能
读取 schema 3，切换 Git 分支不会回退数据库，正式归档迁移需显式操作。

## P3-1：已归档反馈的只读检索（v0.6.5）

基于已合并 PR #28 的 main，在原 checkout 的 codex/feedback-retrieval 开发。
实现 `workflow feedback-query`，复用原 FEEDBACK Schema、schema 3 表和分页预算机制；
不构建 Runtime、不执行检查、不在查询中迁移或写入。规范见
[反馈检索契约](../architecture/workflow-optimization/retrieval.md#已归档能力反馈的只读检索p3-1)，
操作见[查询指南](../../guides/generated-workflows.md#查询已归档反馈)。

新查询单元测试最终 39 项通过；查询、能力、比较、旧 Trace 检索/结果与统一 CLI 的相关
回归 216 项通过。覆盖原失败与查询成功分离、参数拒绝、只读事务、损坏身份/摘要/字节、
游标源/过滤绑定、稀疏空页继续、完整 UTF-8 编码预算和输出保护。替身只验证协议行为，
不计为真实 PostgreSQL 或工具执行。

实际宿主 PostgreSQL 的查询及原反馈归档回归 10 项通过，最终查询集成 6 项通过。
使用唯一组件测试 namespace 并清理，包含原失败事实、同一执行累计 5/10 的两份快照、
106 条记录的稀疏分页、字节预算、损坏拒绝和旧 schema 不自动升级。实际 CLI 使用已有
TOML 查询并写安全 JSON，没有构建本地能力目录或运行原检查。

### 默认宿主归档升级与查询

P2 合并了 migration 003，但当时只升级组件测试 namespace；默认 engineering_ci_trace
仍为 schema 2。本轮第一次只读查询明确返回 migration_required/退出 3，没有隐式迁移。
2026-10-14 用户明确要求完成宿主 schema 3 后，使用原 ArchiveStore.initialize() 升级
同一个已验证属于本组件的 schema；没有新建数据库、替换连接或修改迁移实现。

| 原有表 | 迁移前 | 迁移后 |
| --- | --- | --- |
| sources | 1 | 1 |
| events | 20604 | 20604 |
| snapshots | 1 | 1 |
| snapshot_members | 20604 | 20604 |
| derived_results | 3 | 3 |

各表原身份/摘要元数据指纹一致，没有读取原 payload 或完整分析文档；这证明迁移保留原
记录及其已存摘要，不冒充原始 Trace 全量内容校验。升级后 schema_version=3，新增反馈表
为空。默认 CLI 查询 status=returned、exit_code=0、items=[]、next_after=null；这是有效
空库查询，不是能力执行成功。实际正向/负向反馈查询由上述独立 PostgreSQL 夹具验证。

本地证据保留在 `.test-tools/harness-p3-1/`：query-unit-junit.xml、related-junit.xml、
postgres-regression-junit.xml、query-postgres-final-junit.xml、host-schema-upgrade.json、
live-query-after-upgrade.json。一次 Windows 临时目录权限错误保留在执行记录中，获准使用
标准临时目录后最终 6 项通过；没有跳过该测试或变更项目位置。

最终实际 Dagger 1.0.0-beta.16/Linux PR Profile 五项通过，pytest 为 1300 tests + 105
subtests；没有失败、错误或跳过。第一轮期间补了空游标的连接前拒绝，原报告将受影响的
Ruff/pytest 结果标记 stale，整体退出 3；没有沿用该通过记录。最终 --reuse 只复用输入、
工具及环境仍匹配的 version/lock，Ruff 和 pytest 重新执行。pr-checks.json 与
pr-checks-final-console.log 保存当前结果，pr-checks-before-cursor-fix.json 保存过期证据。
文档 55 个内部链接/锚点核对通过，git diff --check 通过；未修改 GitHub Workflow/Dagger
实现或消费者仓库，也未发起正式发布。

分页是每页只读快照上的实时 feedback ID 书签，不是固定长事务或执行顺序；并发插入且
ID 在已读位置前时须从头新查。每页最多核验 100 条源记录，过滤稀疏时空页仍可有游标。
返回对象仍是原累计反馈快照，collection_coverage=unknown；未做 P3-2 去重/覆盖聚合或
P3-3 再审查依据，不推断当前源码有效、能力仍启用、整体成功率或优化收益。

## P3-2：显式反馈集合的覆盖核验与汇总（v0.6.5）

PR #29 尚开放，继续原 checkout/分支和同一个版本 PR，没有为阶段新增平行 PR。新反馈
文档 v2 提取已有安全 Attempt 索引；原反馈 v1 继续校验/查询/归档，数据库仍为 schema 3，
没有新迁移。契约见[覆盖汇总](../architecture/workflow-optimization/retrieval.md#已归档反馈的覆盖与汇总p3-2)，
操作见[用户指南](../../guides/generated-workflows.md#核验覆盖并汇总反馈)。

模型/旧能力/查询兼容测试 69 项通过；最终查询、汇总、能力、比较、观测及归档相关
回归 142 项通过。覆盖重复输入、相等/部分重叠/覆盖集合、不同执行不混并、原失败保持、
旧 v1 未知、缺测量、同 Attempt 证据变化、绑定冲突、坏索引/摘要、非法参数和完整输出
预算。极大 Attempt 编号的缺口校验按索引长度计算，不分配到该编号的范围。

宿主 PostgreSQL 实际集成 15 项通过，最终汇总集成 5 项通过，使用唯一组件测试 namespace
并清理。实际 Runtime 与反馈 writer 产生同一执行 5/10 两份索引，真实归档/CLI 只读汇总
为 10 个唯一 Attempt、去除 5 个重复引用、提交 0；再次汇总不调用 Runtime 或迁移。
这里原五个领域工具使用注明的进程替身，不冒充真实五项质量检查。

### 已有真实 Dagger 来源的只读验收

另读 P2 保留的实际 Dagger/Linux call `88051221bb734e0887987fac7d87d777`，从已有原资源
提取 v2，没有 resume 或重新提交检查。原 v1
`87f03cdc50c6668ba20f53ace6b7dd08f3293adfd89ab6b394d0da74e368a2e8` 保持不变；新 v2
`4958425b629b2052885acc13dbc0604637899159d66f8ab9ec3506c3eb256ac5` 单独保存。

通过实际 CLI 读取这两个归档反馈（重复传入 v2 ID 仍只读取一次），返回 10 个已知
Attempt、已知复用 10、已知提交 0，而非把两个累计数相加。旧 v1 没有自己的索引，仍
partial/legacy_unverifiable；没有用较大计数或同一执行 ID 补造历史覆盖关系。
原 state/capability/observations 文件摘要前后一致，新增 Runtime resume 和检查提交均为 0。
只在唯一组件测试 schema 写入这份验收数据并清理，没有写生产反馈表。

summary_id 为 `47cac07b710f3a2d07f1c1a9ea4e014f0fefa8015de364284ad6d434bd064264`，
本地 `.test-tools/harness-p3-2/acceptance.json`、actual-summary.json、各 JUnit 保存结果。
该 ID 包含当时临时数据库 namespace 身份；不将其配置路径视为清理后的生产连接入口。
此来源只证明历史反馈读取/索引核验，current_input_validity=not_checked，不能替代当前
PR/Release 门禁，也未宣称新的速度、成本或整体优化收益。

最终当前代码实际 Dagger 1.0.0-beta.16/Linux PR Profile 五项通过：1326 tests + 105
subtests，JUnit 1431、无失败/错误/跳过；四处版本仍为 0.6.5，Pipeline/Workflow 一致性
与锁文件检查通过。原 Workflow、Dagger 实现和消费者未修改。pr-checks.json 与
pr-checks-console.log 保留实际输入/工具/环境身份。

剩余边界：选中集合不是全库，整体覆盖 unknown；旧 v1、证据变化及部分边界补全不猜
演进链。汇总报告成功不改变原失败，不认证当前输入、能力启用或原执行文件仍有效。
P3-3 再审查依据及自动调优没有进入本轮。
