# Execution Graph v0.4 — Implementation Plan

**状态：候选实施计划，尚未实施。** [总览](README.md)

## 1. 本轮仅设计，不改现行运行状态
本专题要把既有 Plan、Dagger Check、Host 执行、Evidence 与 Provider 联系起来。不能把设计字段冒充已交付接口；不得为了设计验收启动完整 CI、再造 Checker 或按历史结果重复验证。已有有效 Pytest、Weaver、OTel Task-only 证据可复用，新增真实跨入口行为再定向验证。

## 2. 可审阅的切片与交付
| 切片 | 改动和实际权威 | 可独立验收 |
|---|---|---|
| A — Execution Inventory | 只读核对 `selection.plan`、`execution.execute`、Dagger `execute-plan`、`@check`、Host、Worktree/Tree 绑定、结果来源；明确 prerequisites 实际时序 | Check→输入→结果→产物→Gate 对照矩阵，指出重复调用与未接线 |
| B — DAG Mapping | 将实际可容器化的剩余 Check/数据产物映射 Dagger Function；Host 保持真实执行；保留已有 `_repository_tests` / `weaver_artifacts` | 独立 Check 原生并行、依赖产物有来源、失败报告和 Gate 消费一次原生结果 |
| C — Evidence/Identity | 对接 #63 真实受信 API；将拟议身份和 Native Result Ref 映射到现有版本化 Plan/Report；先核验证据再发 Dagger 计算 | 受信 PASS 短路、历史 blocked 不短路、Required 缺项不通过；A Verdict 复用而 B Directory 不断链 |
| D — Cache/Input | 按实际 Check 依赖核对 Directory.filter、Module Include、Function/Exec 失效；避免每次随机 ID 击穿缓存 | README/Provider 源码/真实工具输入变更分别观察实际计算执行与缓存，不按假设宣称全局失效 |
| E — Native OTel | 固定 Engine 导出入口/receiver、结果与 Trace 关联、缓存归因及三层 Root；保持 #52/#74/#75 原生职责 | 真实 Dagger Check Trace；Cache Hit 不计新独立运行；Task-only/Terminal 分层，不伪造 Root |
| F — Provider/Action | 本地/Agent、GitHub 和阶段性/最终动作判定；#61 Required/Release | 本地无 GitHub 运行；Required 缺项拒绝；后置 Terminal 不阻塞原 Run，自依赖拒绝 |
| G — RDF Interop | Knowledge #12 原生候选 → #10/#8 接纳/读回 → 可选 H-01 消费 + #18 impact；与 A～F 不强制串行 | 候选与已接纳区分；Knowledge 不存在/缺失时普通 Check 不受影响 |

**交付依赖：** A 为实际代码差距基线；B/D 与 E 的原生追踪部分可并行；C 的受信短路依赖 #63 已交付可用接口；F 的受保护 Required 联调依赖有效项目 Profile 和 #61；G 的正式读回依赖 Knowledge #10/#12/#8 对应已接纳 Profile，候选取证可更早进行。B/E 不因 G 尚未接纳而停工。

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

## 5. 提交和验收方法
先审阅设计/接口差异，再由对应 Issue 切片提交实现 PR。依据实际变更运行定位检查；复用既有有效证据，不因纯 Markdown/README 导航调整触发昂贵全量 Actions。设计文档提交不宣称任何接口、Native OTel 导出或 RDF 接纳已通过。

**已交付实现的正式操作与事实仍以 [现行技术架构](../../docs/architecture/README.md) 为准。**