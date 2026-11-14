# Execution Graph v0.4 — Implementation Plan

**状态：候选实施计划，尚未实施。** [总览](README.md)

## 1. 本轮仅设计，不改现行运行状态
本专题要把既有 Plan、Dagger Check、Host 执行、Evidence 与 Provider 联系起来。不能把设计字段冒充已交付接口；不得为了设计验收启动完整 CI、再造 Checker 或按历史结果重复验证。已有有效 Pytest/Weaver 原生结果可按其**实际来源与适用范围**复用；OTel Task-only 仅复用已固定的执行事实、严格失败、候选输入及正反验证，不得描述为已完成 #52 A 的受信 E2E PASS。PR #82 旧真实 Capture 的严格 Native Check 失败；PR #84 新候选 Capture 在 PR #83 候选 Checker 上显示 Native PASS，但 #83/#84 尚未合并，且 #52 A 独立 Expected/Task 对账尚待验收。不得把不同 PR/Source 的候选结论合成为已交付结果。新增真实跨入口行为再定向验证。

## 2. 可审阅的切片与交付
| 切片 | 改动和实际权威 | 可独立验收 |
|---|---|---|
| A — Execution Inventory | 核对 #66 已有 `selection.plan`、`prerequisites[name]→tests[]`、`prerequisite_actions`、targeted 与 full/escalated 选择、Host `execution.execute` 实际时序，以及 Dagger `execute-plan`、`@check`、Source/Tree 绑定 | **A 的首项交付是逐真实配置实例矩阵（尚未填完）**：producer/config→selected_by(targeted/full/escalated)→consumer Check→Data/PASS 前置/后置/Policy→阶段→失败传播→独立 Check；标注 unresolved 项、重复调用与未接线 |
| B — DAG Mapping | 消费 #66 的实际 Source/Prerequisite 生产方，将剩余可容器化的 Check/数据产物映射 Dagger Function；Host 保持真实执行；复用 `_repository_tests` / `weaver_artifacts` | **仅验收同一次共享计算路径**：独立 Check 并行、真实前置失败使下游工具未启动但独立 Check 可运行、Bundle/Report/本次严格 Check/Enforce 不重复扫描；#61 最终 Required/Release 属于 F，不声称跨入口同一结果 |
| C — Evidence/Identity | 对接 #63 已实际交付的受信 API；按版本化 Plan/Report 引用实际 Native Result、Execution/Assessment；在该接口可用后再实现提前短路 | 受信 PASS 短路、blocked 不短路、Required 缺项拒绝、A Verdict 复用后 B 产物不断链；**跨 dagger check / Plan / Artifact 只有引用与来源可核验时才能宣称复用** |
| D — Cache/Input | 按实际 Check 依赖核对 Directory.filter、Module Include、Function/Exec 失效；避免每次随机 ID 击穿缓存 | README/Provider 源码/真实工具输入变更分别观察实际计算执行与缓存，不按假设宣称全局失效 |
| E — Native OTel | 固定 Engine 导出入口/receiver、结果与 Trace 关联、缓存归因及三层 Root；保持 #52/#74/#75 原生职责 | 真实 Dagger Check Trace；Cache Hit 不计新独立运行；Task-only/Terminal 分层，不伪造 Root |
| F — Provider/Action | 固定 Project/Org Profile→Expected→Plan→Assessment 交接与本地/Provider 作用域；#61 管最终 Required/Release，#63 管受信覆盖；按适用动作连接 #52 B/#74/#75 Terminal | **可先独立验收**本地无 GitHub、Profile/Expected 缺项拒绝；最终 Release/Promotion PASS 仅在该动作 Profile 实际要求的 Terminal/受信证据可用后验收，不以全部 #52/#75 Issue 关闭作统一前置 |
| G — RDF Interop | Knowledge #12 原生候选 → #10/#8 接纳/读回 → 可选 H-01 消费 + #18 impact；与 A～F 不强制串行 | 候选与已接纳区分；Knowledge 不存在/缺失时普通 Check 不受影响 |

**交付依赖：** A 的生产者/消费方映射字段在本设计内已固定，但**逐真实配置实例矩阵是 A 首项待交付成果**，不是当前 PR 已完成的事实。#66 负责原有 Source/Prerequisite 生产与其实际配置绑定，#52 保持既有 Check/Report，#63 负责 C，#61 负责 F，#52 B/#74/#75 保持 GitHub Terminal；不能因设计 A～G 再建立总包 Issue。B 的同次共享验收不等待 C；**跨入口相同结果声明依赖 C** 的真实 Result/Admission 接口。B/D 与 E 的 Dagger 原生追踪部分可并行；E 的 Dagger Trace/Native Result 绑定独立于 #75 GitHub Producer。F 的局部 Profile/缺项拒绝可先行；最终需要 Terminal 的动作才等待相应真实 #52 B/#75/#63/#61 交付。G 候选取证可早于正式 #10/#12/#8 接纳，B/E 不因 G 未完成而停工。

**实施 Issue 分配（提议，非已创建）：** 先用现有 #66/#52/#63/#61 消化其职责内差距；剩余 Plan→Dagger DAG/产物闭包/缓存输入可考虑狭义新 Issue（B 与 D 共同或分开，以真实执行依赖决定）；Dagger Native Trace→ECI 结果绑定可另列狭义 Issue，**不放到 #75**；RDF 只读 Adapter 只有在 Knowledge Profile/Readback 和具体消费者具备时另行立项。

### 2.1 A 阶段实际映射字段与验收基线

A 的矩阵不要求重新实现 Checker：至少包含 `prerequisite_name`、`config_source/revision`、`action_type/argv/commit`、`selected_by`（targeted: 选中测试与配置 tests[] 相交；full/escalated: `frozenset(config.prerequisites)` 全选）、`escalation_reason`、`consumer_check`、`dependency_kind`、`requires_pass/output_ref`、`execution_phase`、`failure_scope`、`independent_checks` 和 `unresolved_reason`。当前 `git_commit_present` 只核对 Commit 存在；`command` 不能仅凭名称判定是准备、前置验证还是后置 Gate。矩阵输出是对每个真实配置实例的接口映射任务，不是把整个现有 `prerequisites` 列表直接变成计算 DAG 边。

B 需对一条已明确的前置 PASS 关系演示：前置失败 → 依赖者未运行、独立检查运行；未声明为真实前置的现有后置政策检查不被错误提前。详见 [DAG 设计](dagger-execution.md)。

## 3. 需保持的现有能力
- `dagger check` 原生 Check 与 Plan-driven 入口分别可用；新的 #63 短路不能未经实际实现就宣称覆盖原生 `dagger check`。
- `engineering-ci select --mode worktree/index/commit/pr` 等真实 Source 身份不得合并为单个 HEAD SHA；PR Merge Tree 与工作区快照必须以真实输入为准。
- GitHub Workflow 既有 Job/Required/Release 约束不因“单一 Root”被强行合并或删除。
- ECI 公共核心不依赖 Knowledge 私有 RDF；Knowledge 消费端不复制 ECI Selector/Runner。
- Pants Python Test Impact 为独立技术选型候选，Dagger Execution DAG 不自称具备全部语言测试影响分析。

## 4. 首批关键反例（仅针对新增行为）
1. Expected 有 5 项，Selector 漏 1 项：最终 Required 不通过。
2. 旧受信 PASS 可复用：未发起多余 Dagger Check；旧 blocked/cancelled 不能短路。
3. A 已复用结论，B 必须消费 A 的 File/Directory：物化真实产物或阻断，不伪造输入。
4. Native Tool FAIL 但 Artifact 正常导出：原生 Check/Gate 失败，报告仍可用且不重新扫描。
5. Dagger Cache 部分命中：区分准备、工具计算及 OTel 实际范围；无独立新执行身份。
6. Dagger Trace 正常导出与关联；缺失 Trace 时只有相关 Required 属性无法满足，不伪造 OTel Root。
7. GitHub Attempt 无可信 Root：Task 有效事实保留，Terminal 需定向后置验证，不允许提前 Release。
8. Knowledge Candidate 关系完整但未正式接纳：不能被当成已采纳 Required 或受信来源。
9. 无 RDF 需求的本地 Check 不访问 RDF4J/GitHub。

### 4.1 Review 增补的定向反例
10. 同次 `_repository_tests`/Bundle/Check 消费可核验一个原生扫描；独立执行 `execute_plan` 的 Pytest 不自动计为同一结果。
11. Project Profile 修订/生效时间与 Source/Run/Attempt 不匹配：Required Gate 拒绝假 PASS；旧 Plan 不通过缺项/不适用伪装完整覆盖。
12. #10/#11/#12 候选 RDF 来自不同 DatasetRevision：不能拼接为同一次已接纳联合查询。
13. PR #82 的 Native FAIL 与 PR #83/#84 的候选 PASS 各自保持原始身份；不能计为已交付 #52 A E2E。

## 5. 提交和验收方法
先审阅设计/接口差异，再由对应 Issue 切片提交实现 PR。依据实际变更运行定位检查；复用既有有效证据，不因纯 Markdown/README 导航调整触发昂贵全量 Actions。设计文档提交不宣称任何接口、Native OTel 导出或 RDF 接纳已通过。

**已交付实现的正式操作与事实仍以 [现行技术架构](../../docs/architecture/README.md) 为准。**