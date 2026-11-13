# #52-A / #66-B/C 设计审阅与修订记录（非规范性索引）

> 本文件仅记录设计问题、修订来源和 GitHub Issue/PR 的实施差分。**不得作为独立的 Check、Profile、Expected、Required 或 Coverage 规范来源。** 详细目标设计的规范性接口由 [Execution Graph / Provider and Gates §1.1–§1.2](../../design/execution-graph/provider-and-gates.md) 维护；Evidence/Identity 与 Dagger 分工分别见对应专题。现有已发布执行事实仍以当前代码及版本化 API 为准。

## 背景及真实问题

- [PR #93](https://github.com/dymzz/engineering-ci/pull/93) 已形成局部稳定 Check ID、Native Result、Host 校验/Report 及 Source 等级字段，修复了 Result 构造/校验不一致等实际缺陷。但它的 `resolve_definitions(requested_names)` **只解析给定 Check 名称**，其 `validate_completeness_receipt()` 只能检查若干字符串结构，不能证明有权工程 Profile 对某次 Event/Ref 的 Expected 真正完整。
- 此前 [Issue #52](https://github.com/dymzz/engineering-ci/issues/52) 和 [Issue #66](https://github.com/dymzz/engineering-ci/issues/66) 被追加“CheckDefinition 域权威”“ExpectedSet 完整性权威”等优先解释条款。在没有先核对已存在的有效 Profile→Expected Requirements→Plan→Assessment 设计前，容易把**事实实现职责**提升为新的**策略规范权威**；该新增设计仍是待审提案，不自动高于已有 Issue。
- 早期 Review 将消费者合法空集、#66-P0 Source 字节证明、#66-C 实际 Job 路由、#63 来源受信和 #61 最终 Gate 全部作为 PR #93 首切片的单独阻断，是不正确的跨组件验收归属；反过来，首切片可独立验收也**不表示**这些能力可从全系统需求中删除。

## 本 PR 实际修订

| 修改目标 | 变更性质 | 验证边界 |
| --- | --- | --- |
| [Provider and Gates §1.2](../../design/execution-graph/provider-and-gates.md) | **既有架构交接的修订提案（待审）**：延续 §1.1 的有效 Profile 及 Required 来源；明确 Expected/Native Result/覆盖与受信的接口，不宣称已接纳或已实施 | 目标设计；不是已交付 Profile→Expected API |
| 本文件 | 只保留审阅事实、差分、出处、受影响任务和实施顺序 | **不得**在此维护平行字段表或合法空集规则 |
| PR #92 描述 | 保留设计修订记录；不把已直接修改的 Issue 正文冒充 PR Git Diff | 不启动代码实现 |
| Issue #52/#66（**未来经独立审阅后**原位修订） | **按下方逐项处置表**区分保留、修正、移交；不能以“优先解释”标题直接整段删除或回退有效契约 | 当前 PR 不直接编辑 Issue；实施任务不是第二 Required/Profile 策略来源 |
| PR #93（下一步定向修复） | 保留已修复 Host/Native Result；不以任意 receipt 判消费者全集；避免 #52 维护第二个完整 Expected Schema | 实际可合并范围依据自身 API/测试，不要求已完成整个后续系统 |

## 未来 #52/#66 原位修订的定向处置表（非规范性审计索引）

**注意：** 下表是后续修订的范围/去向清单，而非第二套规范性字段或权限定义；实施时以 [Provider §1.1–§1.2](../../design/execution-graph/provider-and-gates.md)、各专题设计、Issue 当前正文和真实代码版本共同核对。**不得根据章节标题直接删除整段，也不得把这份索引当作 Issue 已修改或接口已验收。**

| 当前位置/争议 | 处置 | 应保留的有效内容 / 实际落点 |
| --- | --- | --- |
| [#52](https://github.com/dymzz/engineering-ci/issues/52) 的“责任再裁决 / 优先解释”及 Check/Profile/策略引用 | **修正权威措辞，保留接口内容** | 区分 CheckDefinition 的 `profile_ref` 元数据关联与有权生效 Engineering Profile 的 Required 策略来源；#52 保留稳定 Check ID/版本、Native Rule/Tool/输入/Result 归因，**不**生产消费者 Required Policy 或 Expected 全集 |
| #52 的 CheckDefinition 定义域完整性、局部 `resolve_definitions` 与多 Tool 组合 | **保留，限定声明作用域** | 保留局部请求名解析的已知局限、定义缺失/冲突/部分加载/未知、比较基线依赖、all-required / alternative / prerequisite；不能将局部七项或空请求当成消费者完整域，也不因整改策略权威而删除这些长期合同 |
| [#66](https://github.com/dymzz/engineering-ci/issues/66) “ExpectedSet 完整性权威 / 优先于旧版”措辞 | **修正为派生完整性责任** | #66-B 仅从**有权生效 Profile、项目适用规则、Context×Action×Target、CheckDefinition 与真实 Source**派生 Expected Checks、作用域完整性和 `expected_set_ref`；不另行制定 Required Obligations；保留旧条款中真实 Source P0 与 Plan 生产要求 |
| #66-B 的合法空集、Required/Advisory、未知和定义缺失处理 | **保留，补全与 Obligations 的关联** | 空 Required 不推出空 Expected；空 Expected 只有检查域完整才合法；缺 Profile/规则/Source 明确未知或拒绝；**完整 Required Obligations 与 Expected Check 投影不可互相替代**，无 Native Check 的义务必须仍由 #61 评估 |
| #66-C 的 Check↔Job/Result 路由覆盖、跨 Ref/Source 复用、多个 Result 满足 Check | **保留并按职责交接** | Expected 不从 Job/Result 倒推，缺 Job 不删除 Expected；多对多、真实原生结果、比较基线、输入闭包、失效/未执行理由均保留，#86 管真实 Dagger DAG、#63 受信、#61 最终授权 |
| [PR #93](https://github.com/dymzz/engineering-ci/pull/93) `validate_completeness_receipt()` / `validate_expected_coverage()` | **另行定向代码审阅/修正** | 保留可证明有效的 Check ID、Host Native Result、Report/validator 一致性和有限 Source 等级；非空字符串 receipt 只能结构验证，不能授予全域完整性；本 PR 不修改 PR #93 的运行时代码 |
| PR #92 相对 `main` 的版本和历史编辑记录 | **分开记录** | 当前 PR 的 Files Changed 是两个文件的新增差分；审阅索引此前分支版本 §1–§6 的裁剪属于**分支编辑历史**，并非相对 `main` 的对应删除；相关历史仍可在 GitHub 提交/Review 中追溯 |

**处置验收：** 后续每次原位修订必须确认公共 Profile 策略、CheckDefinition 身份、Expected 检查预期、完整 Obligations、Source/Native Result 和最终 Assessment 的引用不断链。对已有有效能力只保留/引用，不因消除重复而收缩跨生态 Tool/Hook、Dagger、Required 或真实受信覆盖的设计范围；真实新增风险才加定向反例。

## 参考的现有权威与实装状态

1. [Knowledge 总设计 §4、§6.3](https://github.com/dymzz/engineering-knowledge/blob/main/design/software-engineering-knowledge-architecture.md)：规范与工具原始语义、项目有权策略、Knowledge 应用/适用性、原生执行、质量门禁不得混为一个决定。
2. [Engineering Verification Overall Design §2、§4](https://github.com/dymzz/engineering-knowledge/blob/main/design/engineering-verification/overall-design.md)：ECI 的公共接口、原生工具优先、Knowledge 作为消费者不得复制实现、ECI 不运行时依赖 Knowledge。
3. [ECI Execution Graph 总览](../../design/execution-graph/README.md)及 [Provider §1.1–§1.2](../../design/execution-graph/provider-and-gates.md)：同一生效 Profile → Expected Requirements → Plan → Assessment；#63 受信覆盖，#61 最终 Gate。
4. [Evidence and Identity](../../design/execution-graph/evidence-and-identity.md) 与 [Dagger Execution](../../design/execution-graph/dagger-execution.md)：Check Key、Invocation、Origin Execution、Native Result、Assessment、真实 DAG 互不混淆。
5. [#52](https://github.com/dymzz/engineering-ci/issues/52) / [#66](https://github.com/dymzz/engineering-ci/issues/66) / [#63](https://github.com/dymzz/engineering-ci/issues/63) / [#61](https://github.com/dymzz/engineering-ci/issues/61) / [#86](https://github.com/dymzz/engineering-ci/issues/86)：实际代码与交付按原有职责实施，不能通过本审阅索引重新规定其领域语义。

## 实施衔接与审阅条件

- **#52-A / PR #93**：保留稳定 Check ID、当前已支持的 Check 定义、真实 Native Result 与同次 Report/validator 的一致性。后续领域/多 Tool/外部适配需如实标明未交付；没有 Profile 全集/Source 全闭包时不假称已通过整体覆盖。#52 的结构性 matcher 不取代 #66 的 Expected+Coverage 计算。
- **#66-P0**：目标 Git Index/Worktree/Commit/PR/Push 实际被检字节和闭包；可与 #52-A 并行。
- **#66-B**：从项目/组织有效 Profile、项目适用规则、Context×Action×Target、#52 CheckDefinition 与 #66-P0 已知 Source 派生本次 Expected Checks / Required 子集和 `expected_set_ref`；保留每项**完整 Required Obligation** 的来源及其与多个 Check/非 Check 必要证据的关系，不将 Expected Check 子集视为授权义务全集。Required 为空不自动删除 Advisory，原生输入闭包尚未知时可保留 Expected 及未知范围，不必等待工具执行。
- **#66-C / PR #72**：真实 Job Routing 与 Result 多对多关联，对账未路由/未执行/不等价/输入漂移；不能按 Job 清单反推 Required/Expected。
- **#63（双时点）**：执行前评估受信历史结果并计算 Remaining Work；执行后复核同次 Native Result、已接纳复用与覆盖缺项；历史 PASS 不替代缺失的 Dagger 产物闭包。
- **#86/#61**：Dagger Engine/#86 负责编排真实计算图和必要产物；#61 按同一有权生效 Profile 的**完整 Required Obligations**、Expected/非 Check 证据关联和 #63 受信结论完成阶段性授权与 Release，不以 Check 全部 PASS 替代完整义务验收，不另建 Required 规则。
- **Knowledge #68**：维护项目领域规则和 Profile 配置、Workflow/Hook 消费行为，不实现 ECI 的公共选择和检查器。

**定向验收**：Native Checks 全部 PASS 但上一 Run 的 Required Terminal 见证缺失时 Release 拒绝；Required Obligations 不为空但 Expected Checks 合法为空时不能获得 Required PASS；Required 子集为空但 Advisory 适用；Profile 实际合法空 Expected vs 未加载/未批准；已 Required 但 Job 缺失；相同目标 Tree、不同 old→new 的差分型 Check 不得错误复用；Index B vs Worktree C；两个必须的 Native Result 少一个不能通过；真实 Result 结构有效但 #63 来源不受信；合格历史 PASS 可在输入等价时复用。将验收分配给真正的接口生产/消费方；不为文档修订重跑原生扫描。

## 历史说明

本文件**分支历史**此前版本在 §1–§6 重复叙述 Check/Expected/Profile 的规范条款，并把“以后创建的 #52-A PR”作为未来事项。实际 [PR #93](https://github.com/dymzz/engineering-ci/pull/93) 已创建且在 Draft；旧章节在分支编辑过程中被收敛为非规范性审计索引。**相对 `main` 的当前 PR Diff 并没有对应的整段删除；不得将分支历史删除误记为本 PR 的 Files Changed 删除。**此前 Issue 中直接修改的事实和 Review 记录仍可通过 GitHub 历史查询。本 PR 合并不等于 Issue 或运行时代码已按设计完成。
