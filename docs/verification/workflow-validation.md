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
