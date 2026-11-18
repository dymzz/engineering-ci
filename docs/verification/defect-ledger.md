# Engineering CI — 统一缺陷发现台账

> **审计状态：持续进行，当前发现不是全仓缺陷全集。** 本文件集中记录 ECI 的源码观察、原始证据、反例、反证、依赖和结论演进；它既不提供运行时授权，也不承担对未来实现方案的批准或限制职能。
>
> 初始版本：2026-11-18。基线：`main@fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83`（[#125-D / PR #128](https://github.com/dymzz/engineering-ci/pull/128) 已合并）。旧候选仍单独记作 `#124@4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5`，**不是 main 的一部分**。
>
> **审计范围是 `dymzz/engineering-ci` 全仓。** 包括原始工程目标、架构与成熟工具、重复建设、Hooks/Lefthook、Dagger/Actions、证据/权限、Tool Runtime、OTel/Weaver、Release/GitHub 与历史需求和 Issue/PR。[#125](https://github.com/dymzz/engineering-ci/issues/125) 是授权链调查切面，不构成审计范围上限；未调查的其他领域保留为未知。其他仓库、规范和成熟工具源码只作为核实 ECI 设计选择的证据。

> **2026-11-18 表述纠偏说明：** 保留历史发现 ID、源码/测试链接及既有证据；原 NEW 候选的预估 P1/P2 统一改为“优先级待评估”。代码观察、合同违例、生产可达性和修复方式分开裁决；历史判断仍可由 PR #129 的先前提交追溯。

## 0. 缺陷编号、时间轴、依赖和影响

### 0.1 统一编号与记录时序

**稳定编号与历史可追溯：** `D-Fxx` 为 PR #128 受控 API 已复现的合同问题（D-F01 位于未合并 #124 候选；D-F02 位于 main 的跨模块交接）；`NEW-xxx` 是 PR #128 以后发现、有直接源码风险但待差异 API 核实的问题；`HYP-xxx` 为待语义裁决疑点；`D-Gxx` 为缺少实际生产验证的边界（不能计作 BUG）。后续新发现沿已有序列递增，重复根因链接既有 ID 而不新增编号。

时间分别记录 **发现/证据采集（审计时间）**、**业务事件/评估/有效期（被测时序）**、**代码引入/修复/合并时间（工程时间）**，不能用某一时间冒充另一时间。无原始时间戳时只标日期和来源，不捏造精确时刻。

| 日期或原始节点 | 工程/审计事件 | 建立的证据或仍未决事实 |
| --- | --- | --- |
| 2026-11-17：PR #126 合并 | Issue #125 A/B；固定 main 与 #124 的源码、出口、依赖关系 | 93 个节点、86 条边，24 条未解析动态/外部边；建立 **发现边界**，不是 BUG 全集 |
| 2026-11-18：PR #127 合并 | C 逐边来源/合同验证等级 | 13 条受控交接合同、5 条已知缺口、68 条未调查；**未调查不能推导没问题** |
| 2026-11-18：PR #128 合并 | D 独立三视角、82 个 API 案例 | D-F01、D-F02 两个根因（5 个反例）；D-G01～D-G05 为未验证生产接线 |
| 2026-11-18：PR #129 初始提交 | 统一 Markdown 台账首次登记 | NEW-001、HYP-001，**仅静态查阅，尚未执行各自新反例** |
| 2026-11-18：PR #129 本次续审 | 复核 R1/R5/Release/Publisher/Hook 的实际调用与测试 | 新增 NEW-002、HYP-002；与旧根因去重，**不把在此之后才观察的风险倒记为 PR #128 已发现** |
| 2026-11-18：PR #129 四项新增排查（本轮） | 逐项对照 R4 `require_reconciliation_manifest`、`check_bindings`、Release/Assessment 时序与 R1 Adjudication 选择 | 新增 NEW-003～NEW-006；均有直接源码/合同条件依据，**尚未运行这些新组合的原生 API 反例；不宣称四个生产漏洞已复现** |
| 2026-11-18：PR #129 同轮第二批 | 深挖 R4 空映射、R1 裁决类型/时序、Hook Exit 与 Native Result | 新增 NEW-007～NEW-009、HYP-003。代码证据可复核，**未将业务合同待裁决点提升为已复现缺陷** |
| 2026-11-18：PR #129 继续审阅（本批四项） | #61 G2 发布数据来源、Assessment→GitHub/Release 跨 Scope、R5 Trigger 身份 | 新增 **NEW-010～NEW-013**。四项均定位到消费边界及具体可变输入，尚未执行差异 API，也未确认任何已部署生产链被绕过 |
| 2026-11-18：PR #129 继续审阅（本批四项） | #63 T1-a 的 Report 失败隔离、#66-C 重复 Result/空 Observation、#63 T2 历史候选排序 | 新增 **NEW-014～NEW-017**。其中 014/015 的相关行为已有既有测试，016/017 是确定代码路径；四项的**违规合同与生产后果仍待独立反例** |
| 2026-11-18：PR #129 继续审阅（本批四项） | #66 Hook 同 Job 重复激活、Job Receipt 事件与 Scope 签发、Native Result Attempt 字符串身份、multi-ref Final Reconciliation 输入全集 | 新增 **NEW-018～NEW-021**；源码给出明确可达条件，尚未运行原生组合反例或确证生产权限绕过 |
| 2026-11-18：PR #129 继续审阅（本批四项） | #52 Report 全局终态、#66-C Check/Plan admissibility、Attempt Qualification 来源、Hook Final 退出码 | 新增 **NEW-022～NEW-025**。逐项区分已证源码分支与真实来源/可利用性，未运行新的实际组合反例，也未将独立 Check PASS 等同整个 Job PASS |
| 2026-11-18：PR #129 继续审阅（本批四项） | R1 序列化 Assessment 时间/样本校验、R5 Reevaluation 时序、G2 Publisher Approval 时钟与 R5 逾期证据授权关联；复核 #63 原始 Issuer Context | 新增 **NEW-026～NEW-029**；其中 026/027/029 是明示的局部校验缺口，028 是调用方时间注入风险。**未执行本轮新增完整 API 反例、未证明生产绕过**；同时给 NEW-019 增加 #63 消费证据 |
| 2026-11-18：PR #129 继续审阅（本批四项） | R1 非 Check Verifier 来源认证、Universe Evaluation 与 Assessment 时钟绑定、Profile ApprovedAt 真实性、G2 实际 Evaluator Revision 身份 | 新增 **NEW-030～NEW-033**。核对 #61 设计原文与实际源码/既有测试；四项分别落在不同 Producer→Consumer 关系，**代码条件确认但尚未运行新增原生 API 差异/生产认证测试** |
| 2026-11-18：PR #129 Release 原生证据审阅 | 对照 Manifest Dagger Policy Check / Tool Version 来源、首次发布过期 Artifact、缺失 Digest 的 Provider 条件及 #61 Release 合同 | NEW-034～NEW-037 为源码差异与合同待确认点；已存在受信 Manifest Artifact 交接，重复验证必要性及异常可达性尚未证明 |
| 2026-11-18：全仓 Hook/Lefthook 接线复核 | `main@fc0df2a` 文件树、Hook Coordinator/CLI、Reconciliation、Issue #66/PR #72 及既有模块正例 | **NEW-038**：Routing 默认把 Lefthook 2.2.2/固定配置标签写为调度来源，但无本次 Lefthook/合成配置真实证据。仅证结构性声明差异；Receipt `trusted=False`，未证生产权限绕过。实际 Hook 配置/安装缺口已在 #66 登记，不重复记作新 BUG。 |
| 2026-11-18：Host/Dagger 依赖执行与缓存复审 | Host 前置命令 Runner 参数适配、Dagger 两路径执行、Workflow Analysis 共享缓存/Lock、实际 Plan/测试 | 新增 **NEW-039**：前置原生命令 Runner 的内部 TypeError 会被当作签名不匹配后重新调用；源码条件确认，真实内置 Runner 生产发生率和后果未证。确认同次 PlanExecution 二选一执行分支，Actionlint 共享缓存已有锁，均不据此立重复建设 BUG。 |
| 2026-11-18：Reusable Dagger 报告与私有 Token 边界审计 | Workflow 可选 Secret→Host Git Credential Store→Dagger 函数导出 `ci.py`→Runner 执行 `plan/gate`；核对 ECI 当前 Release 调用、可信 `pytest_ci.py` 及 GitHub Secrets/Checks 原生机制 | **NEW-040**：导出脚本的可执行来源没有独立认证；可选 Secret 在 Host 运行期可用。源码交接路径成立，但真实受检 PR 是否控制导出脚本、调用方是否传入 Secret 均未证；本仓 Release 未传 Secret、fork PR 默认拿不到普通 Secret。G2 本身有可复用 GitHub App 形式的 `GITHUB_TOKEN`，不必先造新 App。 |
| 2026-11-18：NEW-040 消费者及真实 PR Run 复审 | 核对 `vesryn` 的实际 `workflow_call`、Dagger Module/Lock、固定旧版 ECI 0f74b2f Workflow，及 PR #32/#29 对应的成功 Actions `harness / checks`；两个原始 PR Head 均确认相同调用契约 | **实际消费者和真实运行已证**：共享 ECI 依赖固定，但消费者本地 `test_results` 来源于受检仓库；Workflow 显式声明传入可选 Secret。仍未证明 Secret 非空、非授权改动、泄漏或门禁绕过；沿 NEW-040 记录，不重复编号。 |

审计快照始终保留 commit。`#124@4a41b4e` 独立于 `main@fc0df2a`，不是可在同一生产执行中直接拼接的身份。

### 0.2 授权链调查切面（不是全仓范围上限）

依赖方向表示 **需要核查的事实流与合同**，不是所有箭头都已在生产环境接通。事件时间至少应区分：`Profile approval/effective/revoke` → `Expected & Source closure (pre-execution)` → `Run/Attempt/Native Result` → `Receipt/Admission` → `R1 evaluated_at` → `GitHub Check/Release` → `post-stage Trigger/Due/evaluated_at`；历史复用单独需要旧事实的来源与**当前**授权。不同 Stage 不允许用后置结果倒填前置 Expected。

| 序位 | Producer / 关键事实 | Validator → Consumer | 涉及的发现 | 已证状态 / 待核实 |
| --- | --- | --- | --- | --- |
| T0 | 有权 EffectiveProfile、治理/委托闭包、Action×Target×Phase×Ref、Obligations Universe；输入 Source 闭包 | #66-B2/P0 → Expected v2/selection；R1 检查前置权威 | D-F01（共享 Producer）、D-G01、**NEW-031、NEW-032** | 除 Domain/Phase 真实性，还须核对 Universe 与 R1 的求值时点及 Approval 签发先后；#124 Binder 的错误授权并不等于 R1 已错 |
| T1 | Execution Run/Attempt、原生执行事实与历史证据/当前授权 | #52/#66-C/#63 T1/T2 → Admission/Reuse Gate 输入 | D-F02、D-G02、NEW-002、**NEW-014～NEW-017、NEW-022～NEW-024** | 进一步核对 Report-wide 终态、Check-level 可采纳性、Plan 一致性与 Attempt Qualification 的来源；不能从局部 PASS 或自声明资格推导当前授权 |
| T2 | 归一化 Admission、Scope/Instance、Failure、Adjudication、non-check evidence | #61-R1 → 阶段 Required Assessment | D-F02、HYP-001、HYP-002、**NEW-006、NEW-026、NEW-030** | 新增 Non-Check Verifier 真实签发事实缺少独立核验的问题；不能以相等的 Verifier/Rule Ref 直接当有权真实 Evidence |
| T3 | 经 R1 认可的阶段判定 + Approved Workflow/Execution identity | #61-G2/G3 → GitHub Check → 服务端 Required | D-G03、**NEW-010、NEW-011、NEW-028、NEW-033** | 审批有效期时钟与真实执行的 Evaluator/Policy 身份是不同证明对象；相同 Workflow SHA 不等于实际评估器代码已受信 |
| T4 | R1、当前 Policy、Manifest/Artifact、Provider/Handoff、Run/Attempt | #61-R4 → 真实 Release 发布消费方 | D-G04、**NEW-003～NEW-005、NEW-012、NEW-034～NEW-037** | 尚待核对 Assessment 原 Target/Action/Ref 与 Release 作用域、Manifest 来源及真实消费边界 |
| T5 | 已完成 Run、有效 Trigger/Due、相同 Candidate/Phase/Policy、前后 R1 Assessment | #61-R5 → Post-stage disposition/后续消费 | NEW-001、**NEW-013、NEW-027、NEW-029**、D-G04 | 重新评估的时间箭头和逾期证据批准的 Assessment 对象身份须独立核实；不代表全生命周期 PASS |
| TX | Tool/Hook 执行权限、批准锚点、真实 Policy revision 和持久状态 | Native Tool Runtime / ECI Security Policy / Hook 路径 | D-G05、**NEW-018～NEW-021、NEW-025** | 除激活/签发/实例/Scope 闭包外，结构性 Receipt 能否作为真正的 Git Hook 进程成功依据仍须审计真实输入链和已安装 Hook 配置 |

### 0.3 源码事实、条件性影响和反证

区分已观察的源码/测试事实、条件性影响与尚缺的合同或原生证据。未知消费者与假设不构成生产错误证明；这里记录调查条件，不规定实现选择。

| ID | 已观察的边界 / 时间条件 | 源码或受控测试事实 | 条件性影响及未知消费者 | 合同不确定性／反证方向 |
| --- | --- | --- | --- | --- |
| D-F01 | #124 Binder 评估时，原 Producer Domain 不闭合且 Terminal/Phase Witness 有效 | Binder 可返回 `authorized=True`、清空 `reasons`；候选实现局部错误放行 | #66 Expected Validator、执行消费者、#63/#61 是否实际消费该候选输出 | 候选 Binder 已证错误授权；main、其他消费者及合法单域/闭域路径的影响仍待独立核验。 |
| D-F02 | #63 T1/T2 完成受控 Admission/Reuse 后，交接 R1 同一 Scope | R1 抛 `admission_phase_invalid` / `admission_kind_invalid`，阻断合法互通 | G2、Release 依赖 R1 的后续正例；失败/冲突/复用语义交接 | T1/T2 到 R1 的合法 Gate Projection、来源认证、失败/历史复用及其他消费者仍需对照。 |
| NEW-001 | R5 prior `contract_sample=False`、post `True`、显式参数 `False` | 源码能确认 post 样本未纳入汇总；**实际 `verified` 尚未 API 复现** | 若任何后置消费者以 `verified` 当受信事实，会受风险影响 | post-only 样本输入的原生 R5 API 结果和实际后置消费者待核验。 |
| NEW-002 | R1 `failure.issued_at > assessed_at`；历史/当前评估的时间切面待确定 | 代码仅解析 `issued_at`，未在 `_in_scope_failures` 按 `evaluated_at` 过滤；**返回值尚未 API 复现** | R1 误阻断较早阶段；后续 G2/Release 的拒绝与证据有效期解释 | Failure issued_at 的业务时刻与严格 as-of 或当前再评估语义未裁决。 |
| HYP-001 | Candidate `commit/tree/artifact_digest/source_digest` 同值但字段类型不同 | 使用值集合做匹配这一实现事实成立；是否违规未知 | R1 Candidate Scope、Failure in-scope、Universe membership | 候选 Digest 的对象类型和允许的通用身份绑定语义待明确。 |
| HYP-002 | Admission `all_observations_reconciled=False` 且局部受信 Coverage 足够 | R1 验证该布尔字段的**类型**，但未在阶段 Required 判断中直接使用；实际风险未知 | 未关联的 Raw Observation 是否影响 Required 的完整性，需与 #66-C/#63 校验边界联查 | 未关联 Observation 对当前 Required 完整性是否有权威影响待确认。 |
| NEW-003 | 同一 Manifest 与一个合法但 `release_authorized=False` 的 R4 Envelope | 函数主体没有验证授权位，只比较 Manifest identity；按静态分支可返回 identity | 若未来/现有真实 Publisher 把它当“放行”接口，将跳过独立授权判断；**生产消费者未证** | 该公开 API 是身份绑定还是实际发布许可检查，以及具体调用方待核验。 |
| NEW-004 | 明示绑定 `core_coverage_threshold` 或 `consumer_failure_probe`（v1 schema 固定 `required=False,status=not_enabled`）与 R1 satisfied Check | R4 的绑定循环忽略 Manifest `status/required`，只判断 R1 Row；不能证明该绑定代表已执行 Check | Release 对外报告的 `check_bindings` 可能产生无执行见证的“关联”；实际发布绕过未证 | Binding 是执行证明还是描述性索引；optional Check 的真实使用者待确认。 |
| NEW-005 | `assessment.evaluated_at` 晚于 `publish_context.evaluated_at`，而受控 Policy/Provider/Handoff 接受 | R4 只比较 Phase/commit/run，并将两个时间传递给 callback；本地没有同向先后断言 | 先发生的 Release 可能消费“未来评估的 PASS”，影响撤销、发布链审计与签发时间解释 | 历史 Assessment 与发布事件的允许因果关系、上游 Policy 校验待确认。 |
| NEW-006 | 同 Result 两份有效且不同 Authority/Policy 或 Disposition 的 Adjudication，改变输入顺序 | `_validate_admission` 原样收集列表，`_effective_adjudication` 返回首条，选择的来源归属与输入顺序相关 | R1 失败解除的决策依据可能不确定；外部 Issuer/冲突/先后决策规则未证 | 受信裁决源的唯一性、优先级、替代与多签并存模型仍未知。 |
| NEW-007 | R4 合法 PASS Assessment 与 Manifest，未提供 Check Binding | 代码可在空绑定时返回 `release_authorized=True`；这属于映射缺失事实，而非既有必需 Check 失败 | R1/Manifest 的 Check 身份是否对应当前 Profile 尚未证明 | 零映射下 R1/Manifest 是否按有权合同独立完成全量覆盖待确认。 |
| NEW-008 | 同一 Admission 混合 native_failure/conflict，由单个复合裁决覆盖 | 现有测试直接断言该组合 `status=passed` | #63 签发范围/不同失败类别的独立撤销语义尚待证实 | 受信 Adjudication 对 Native Failure/Conflict 复合解除的权限语义待查。 |
| NEW-009 | 同 Result 的 Adjudication 先于 Failure issued_at | 时间关系未在 R1 过滤条件中被比较 | R1 失败解除的真实因果链、历史/当前接纳时间 | 提前签发裁决能否合法引用尚未观察的 Result ID，取决于真实 Issuer 合同。 |
| HYP-003 | 同 Invocation 有 Failed 与 Passed 组合 Native Result | Hook Exit 只检查 binding/status，没有显式 Failed 集合否决 | 未授权失败是否能够在成功选取后被覆盖，需按 Check/Attempt/重试合同取证 | 最终 Witness 的合法重试、历史失败及同 Attempt 归属模型待确认。 |
| NEW-010 | Serialized Check Publication `conclusion=success`，将 `assessment_status` 改为 `None`/任意非 blocked 值并重新计算公开摘要 | Validator 未要求成功必须对应 passed；Publisher 的授权层不会取回或校验 R1 原件 | 如果实际入口接收不受信 Publication，可错误发布 Required SUCCESS；目前未证明该入口可被非受信方控制 | Publication 的实际受信生产入口、R1 PASS 原件及 Publisher Authority 证明待查。 |
| NEW-011 | R1 Assessment 同 SHA，但 `refs/action/target/phase` 与 GitHub Check 目标不同 | G2 的绑定逻辑未读取这些 Assessment 字段，仅从 Summary 展示 | GitHub Required 上下文可能被错 Scope 的 PASS 覆盖；受信工作流是否附加该绑定未知 | GitHub 事件和 Assessment Action×Target×Phase×Refs 的有权对应关系未证。 |
| NEW-012 | R1 Assessment Commit/Phase 正确，但 Target/Action/Ref 不符合 Release | R4 只比较 Commit/Phase；Profile snapshot 传入 Policy callback 不含 Action/Target/Refs | Release 可能接纳不适用的 R1 PASS；真实 Policy callback 的查证能力和权威结果未证 | Release Policy callback 能否独立核验 R1 Scope 及实际发布调用仍待确认。 |
| NEW-013 | `artifact_available` Trigger Artifact Digest 或 Run Attempt 与候选/真实 Post Evidence 不同 | R5 仅解析 Artifact Digest，回调可见完整 Trigger 而本地未比较；实际 Authority 可能负责验证 | 后置 verified 对应错误 Trigger 的风险，影响后置 disposition 和追溯 | Trigger 与 Artifact/Run 的类型化归属关系及真实 Issuer 待查。 |
| NEW-014 | 同 Job/Run/Attempt，选中 PASS 与另一独立 Report/Receipt 中的 Verified Failed | 现有 T1-a 测试得到 `admitted=True` 且 Failed 留在 `observed_diagnostics` | 若后续 R1 投影只消费 Coverage PASS，则可能丢失同执行失败；是否属于不同受信 Attempt/独立 Check 待查 | 相同 Job/Attempt 的多 Report 合法性、最终状态与失败传播规则待核实。 |
| NEW-015 | 两个 Job Observation 重复相同 Result ID，均合法参与当前 Scope | 已有 Reconciliation 测试证明 `binding_status=matched` 且 `duplicate` 非空，聚合不计重复为 `unreconciled` | 若重复是错误执行归属或独立 Witness 伪造，Hook `exit=0` 的证据资格可能受影响 | 相同不可变 Result 跨 Job 复用的授权范围、来源及 Qualification 待查。 |
| NEW-016 | 同一 Invocation 外多余未路由 Job 产生零 Native Result 的有效失败 Report | `_unexpected_observations` 只逐 Result 记录；空列表不会生成 `unexpected` | Hook 对失败 Job 的遗漏、`all_observations_reconciled` 语义不完整；仍需看 Report 状态/Job 资格 | 失败但零 Native 的 Job 是否已激活、是否属于当前 Required 待核实。 |
| NEW-017 | 两条不同成功历史候选共享 `historical_scope_id` 和 `historical_expected_check_ref` | T2 对 admitted 列表按仅这两字段排序，平键依赖初始顺序选取 `result_ids` | 相同历史输入集合变更顺序可产生不同 Evidence Identity/Cache provenance，难以稳定追溯 | Historical Candidate Provider 的唯一性及输入顺序是否影响权限待证。 |
| NEW-018 | 同一 `run_ref/attempt_ref/job_ref` 再次 `record_job`，尤其首次 Report Delivered 后 | `jobs[job_ref]=entry` 丢失前次 Production Ref/Delivery，历史激活记录在对象内不可追溯 | Final Reconciliation/Attempt Qualification 可能引用新身份而掩盖旧运行事实 | 真实调度中同一 Job 二次激活的合法性及结果保留机制待核实。 |
| NEW-019 | Invocation A 已收集真实 Report，却以 Event/Expected B 的声明调用 Receipt 签发 | 本地仍可将未对账字段写入 `upstream_verified` Receipt | 若后续消费仅比较 Receipt 和 Expected B，会发生来源错归属；外部权威是否防止未知 | #63 外部 Issuer 可能凭 Receipt Ref 查原件；完整 Scope 认证是否已接通未知。 |
| NEW-020 | 两个同 Run/Attempt Job Ref 一个是另一个字符串前缀 | Attempt 前缀检查不能区分 `job-1` 与 `job-1-extra` 的 `result_instance` | 如错误 Result 进入有权 Delivered Report，Collect 的本地 Job 实例判断可错接 | Result Instance 字符串前缀歧义与原始 Delivered Report 防护的可达性待查。 |
| NEW-021 | 多 Ref 真实 Expected 完全覆盖 Invocation，但 `observations_by_scope` 多一个未知 Scope | Extra Scope 从未送入 `reconcile_expected_results`，不影响已生成 Receipt 与 Exit | 当前 Invocation 的实际失败若被错标 Scope，可能被 Final 静默丢弃 | 未知 Scoped 输入是否属于当前 Invocation 的真实执行事实待查。 |
| NEW-022 | 同一完整 Execution Report 含合法匹配 PASS Native，同时 Report 因其他必需 Check/Prerequisite/Artifact Delivery 为 `failed/blocked` | `_execution_report` 对全局状态无一般失败拒绝；选中 PASS 只按单 Check admissibility 进入 `matched`，是否最终 Hook PASS 取决于 Expected 枚举 | 整体 Job/Producer 失败可能未通过 Report→Receipt 传播到需要该前置事实的消费者 | 局部 Check PASS 与完整 Report 失败的责任边界和 Required 前置依赖待定。 |
| NEW-023 | ECI 样式 Report 保留 `plan_authorized=True/check_contract.valid`，但删除或设空 `plan_declaration_consistent` | 原生 Reconciliation Report 校验只拒绝显式 `False`，缺失值不进入 `report_reason` | Plan Source/选择一致性无证明时，若外部 Raw Report 被误当受信，可获得结构性匹配 | 真实 Runner 是否强制输出 Plan 一致性标志及认证原件待查。 |
| NEW-024 | 构造 `attempt_eligibility=eligible` 与形状合法、但不属于当前 Run/Attempt 的 `qualification_ref` | `_job_instance` 未核验 Qualification 绑定，Reconciliation 可将该实例列为 Eligible | 结构性 Witness Selection 可使用无资格证据的 Attempt；#63 外部签发是否拒绝尚未知 | 组件为结构性 Witness；受信资格的真正生产和验证责任待确认。 |
| NEW-025 | Hook Final 消费完整结构性 `matched` Receipt，而 #63/R1 尚未执行 | Hook 仅检查 `status`、`all_observations_reconciled`、Expected completeness，成功则返回进程 0 | 如果此进程零退出被 Lefthook 作为唯一准入，未认证 Report/Qualification 的结构性通过可能误放行 | 本地 Lefthook 进程出口与 Required/Release 许可的不同目标及输入来源待确认。 |
| NEW-026 | 将 PASS Assessment 的 `inputs.authority.evaluated_at/effective_at/valid_from` 改为未来（或移除 contract_sample 标记），重新计算 `assessment_ref` | R1 消费端 `validate_assessment` 未重验与原计算器 `_authority_fact` 相同的时间/样本事实；摘要本身可由调用方重算 | G2/R4/R5 只看到验证通过的序列化 PASS 时，来源或时间可能被错归属；真实上游签发/存储未证 | Assessment 原始签发、内容摘要与实际消费端的独立验证责任待查。 |
| NEW-027 | 同 Phase 的授权 Reevaluation，`post_at` 比 `prior_at` 早，或 Reevaluation Trigger 发生在 `post_at` 之后 | R5 `_bind_temporal` 跳过该 Trigger 的时间单调与事件→后证据因果断言 | 较旧的 Stage 判定可能被重新命名为较新的验证结论；真实 Authority 对回溯式评估是否允许尚未知 | 历史追溯式重评估和新阶段授权的时间语义待裁决。 |
| NEW-028 | 真实 Approval 已过 `valid_to`，调用 `authorize_and_publish(...,now=<expiry 前时间>)` | 该组件用注入时钟判断窗口，外部 Approval Verifier 仅在返回 True 时被采纳 | 如果真实发布入口允许自定测试时间且外部 Authority 不复核，过期身份可取得 Required Check 发布资格 | 测试可注入时钟与生产受信时间来源、外部 Authority 的责任待证。 |
| NEW-029 | Post Assessment A/B 同 Scope、`evaluated_at`，但内容/Ref 不同；两者都 Overdue | Due Authority 不见 `post_assessment_ref`，因此同一时间元数据下无法仅凭入参区分被批准的迟到证据 | 逾期例外可能从 A 传播到 B，影响后置 verified/disposition；真实 Issuer/Callback 未证 | Due Window 例外是否应逐 Post Assessment 授权，取决于原策略合同。 |
| NEW-030 | Non-Check Evidence 声明 `result=satisfied`，Verifier/Authorization/Rule Ref 与 Obligation 匹配；无独立 Verifier 签发事实 | R1 结构性算法可将对应 Non-Check Obligation 标记为 `satisfied`；现有正例测试使用自建 Evidence Document 即产生阶段 PASS | 当前 Phase/Release/Check 可能引用来源尚未经认证的领域 PASS；若真实上游确有签发验收，风险会降低 | Non-Check Verifier 真正签发者及 R1 前是否完成可信接纳待查。 |
| NEW-031 | Universe 自报 `evaluation.evaluated_at=t2`、R1 传 `evaluated_at=t1`，`t1!=t2` | Universe 只存时间字段并校验格式；R1 判定基于 t1，跨时点一致性无显式要求 | 撤销、策略修订或后置义务变化可能被错误套用到另一个时点；生产有权 Universe 签发未验 | Universe As-of 与 R1 评估时间合法复用和重评语义待确认。 |
| NEW-032 | `approved_at=t2>assessed_at=t1` 但 `effective_at<=t1`、Authority 仍 `verified` | R1 没有 Approval-issued-at 与 Assessment 的因果比较 | 若不允许追认，尚未发生的批准可能支持较早的 PASS；真实 Issuer 的授权窗口/回溯规则未证 | Profile 生效、批准时刻与追认授权的实际治理规则未核实。 |
| NEW-033 | 恶意或错误的实际 Evaluator/Policy Version，却将 `execution_identity` 自报为受批准 Ref | G2 只校验自报的 Ref 与批准及 Publication 一致，Workflow SHA 与 GitHub Runtime 匹配 | 有发布权限但未运行批准评估器的 Workflow 可能发布 Required SUCCESS；Approval Authority 是否验证实际执行版本未知 | Workflow SHA 与实际 Evaluator/Policy 版本签发证据是否独立关联待核查。 |
| NEW-034 | 序列化 Manifest 派生 Dagger 子 Policy Check 从 unavailable 改 passed，而原 Dagger Job 仍 success | 验证器核对成功 Job/Step 字段，未重取独立 Dagger Policy Check 原始证明 | Release 固定 Required 的子 Check 可被未经验证的 Manifest 声明替代；生产篡改来源未证 | 生成时的 Dagger 子检查证明是否已随受信 Manifest 交付，以及篡改入口是否可达。 |
| NEW-035 | Manifest tools 版本字符串被替换为另一合法版本，而原工具文件和成功 Run 未变 | clean_tools 只验证格式/集合；Verification/Publishing 不复核原始 Tool Version 输出 | 工具版本 provenance 可能漂移，是否影响已签发正式 Manifest 待证 | Tool Version 是审计属性或当前授权前提、原始 Facts 是否已由 Manifest 充分绑定待查。 |
| NEW-036 | 原 Manifest 在 t0 合格，首次发布前其 Actions Artifact 在 t1 过期 | 新建 Manifest 会拒绝过期项，但首次 publish_release 使用允许过期元数据的 published_evidence | 若发布时要求源 Artifact 仍可取，过期证据可能支持首次发布；合法历史复用另论 | 首次发行与历史复用对 Artifact 可获取性要求是否相同待明确。 |
| NEW-037 | GitHub Artifact 元数据不提供 digest，但相同 Run/ID/名称均合法 | passed Manifest 接受 digest=None，Provider 仅在声明 Digest 时比较 | 无法仅凭当前 Manifest 完成字节 Digest 证明；API 可达性/当前义务未实测 | GitHub API 缺 Digest 的真实可达性及当期 Profile 对字节完整性的适用性待验证。 |
| NEW-038 | 未执行 Lefthook 调度，但调用本地 `record_job` 与默认 `build_routing` | Routing 中写入固定 Lefthook 调度器身份与 `hook-job:1` 配置标签，`_normalize_routing` 只验证字段为字典并在 Receipt 中保留 | 若有消费者将此标签解释为实际调度、过滤或生效配置证明，可能发生来源错归属；当前没有全链受信消费证据 | 真实 Scheduler activation / effective merged config / Event 关联、下游受信规则待核实；NEW-024/025 涉及其他属性 |
| NEW-039 | 指定具有合法二参/三参 Runner，当 Runner 开始执行副作用后抛出 `TypeError` | `_run_command()` 会在 `except TypeError` 里以三个位置参数重新调用同一个 Runner；真实错误上下文可能被覆盖，命令可能被第二次执行 | Pre-execution Command 的生成产物、远端副作用或 Report 准确性受影响；现有内置 `_run` 及真实 Host/Dagger 中是否可达此后置异常未证 | 需要区分签名不兼容与 Runner 内部异常；用带调用次数/副作用的受控二参/三参 Runner、实际 PREREQ Provider 验证 |
| NEW-040 | `vesryn` 实际 Consumer 的 PR Workflow 使用受检仓库本地 Dagger Module、固定 ECI Reusable 旧版，并显式传私有 Secret 表达式；若配置非空且本地函数被未经授权修改 | ECI Reusable 在 Host 执行 Dagger 报告 `ci.py plan/gate`，Git Credential Store 可在执行期间使用；两次真实 PR Run 的 `harness / checks` 与 Gate 成功 | 满足未知的 Secret/不可信代码组合后可能误选 Check 或访问 Host 凭证；真实凭证存在性与越权行为尚无证据 | 已验证 PR #32/#29 Head 配置与 Actions Run，后续查 Secret 元数据、Caller 信任模型及受保护 Module 来源 |

### 0.4 调查依赖（可并行，不是修改门槛）

此处的顺序仅描述 Producer→Validator→Consumer 的事实依赖；并不要求全仓审计或代码修订按统一阶段排队。已有充分证据的独立工程问题可在其原归属模块实施、审阅和验收，持续发现与修复进度分别记账。

| 调查面 | 既有基线 | 仍需辨别的事实 |
| --- | --- | --- |
| 有权 Profile、治理域、Phase | D-G01、D-F01、#66-B2 | 合法闭域与复合未知、并存原因、真实 Producer 和消费者 |
| 原生证据、Admission 与 R1 | D-F02、#52/#63/#61 | Gate Projection、受信来源、失败/冲突、真实历史复用 |
| Scope、身份、时间 | NEW-002/005/006/009/011/012/026/031/032 | 时间基准、重评估、类型身份及消费者契约 |
| GitHub/Release/Post 出口 | G2/G3、R4/R5、D-G03/D-G04 | 实际授权来源、原生发布入口、服务端拒绝和后置条件 |
| 其余全仓工程设计 | Hooks/Lefthook、Dagger/Actions、Tool Runtime、Weaver 等 | 原始需求、成熟机制覆盖、重复实现与实际工程收益 |

公共根因可能影响多个接口；保留每个已观察差异的来源引用，根据新增证据决定关联、合并或拆分，不预设最终技术方案。

## 证据等级、优先级与记录口径

- **已复现合同错误**：固定代码版本上的实际 API/受控输入已经展示违反既定合同的结果，并标出其适用范围。**源码行为已定位**：具体代码分支和输入条件已观察，但合法合同解释、原生组合结果或授权可达性未完成确认。**来源／生产待核验**：真实 Issuer、原件和外部服务端证据不足。**合法或已排除**：反证成立时保留编号和裁决依据。
- **严重性与证据分开**：D-F01/D-F02 的受控 P1 结论保留；既有 NEW 的 P1/P2 是审计中形成的风险猜测，现统一标记“待评估”，其历史标签仍留在 Git 提交记录中。真正的严重性根据用户目标、有效合同、实际消费路径、可达条件和影响决定。
- **发现记录**：保留稳定 ID、原始目标、Commit/SHA、源码或测试事实、反例与合法对照、反证、依赖、实际影响和未知影响、根因关系、原归属及调查/修复状态。复用已有有效证据，核验实际变更和风险；旧审计结论允许被新证据推翻。
- **实现与调查职责**：台账提供事实与设计比较依据，不预先排除组件复用、重构、替换、扩展或必要的新机制；技术选择按成熟方案的真实能力、原始需求、工程收益与接口职责裁决。独立可修订的问题不因其他未调查模块而冻结；共同上游问题按实际依赖协调修改。
- **保留原始材料**：[PR #128 原始发现](authorization-closure/independent-review/findings.json)、[受控测试案例](authorization-closure/independent-review/cases.json)、[PR #127 来源/覆盖](authorization-closure/coverage/README.md) 和原有领域 Issue/PR 仍是历史可复核来源。文档状态不是代码已修复、门禁已部署或服务端已启用的证明。

## A. 已复现合同错误与待裁决的源码观察

| ID | 风险优先级 | 证据／合同状态 | 源码快照及原归属 | 已观察事实与条件 | 调查／修复状态 |
| --- | --- | --- | --- | --- | --- |
| **D-F01** | P1 | **已复现（受控 API）** | `#124@4a41b4e`；#66-B2、#52-D4/#124 | Domain 未闭合仍被 Terminal/Phase Binder 清除复合缺口，返回 `bound/authorized=True` | 未修复；待统一裁决 |
| **D-F02** | P1 | **已复现（受控 API）** | `main@adeeb4b`；#63 T1/T2、#61 R1 | 实际 Admission/Reuse 输出直接输入 R1，分别触发 `admission_phase_invalid` / `admission_kind_invalid`；为合法交接缺口，不是越权通过 | 未修复；待统一裁决 |
| **NEW-001** | 待评估 | **源码行为已定位；差异反例与合同待核验** | `main@fc0df2a`；#61-R5 | R5 的 `contract_sample` 汇总检查只看显式参数和 prior Assessment，**漏查 post Assessment 的样本位**；可能将后置样本标记为非样本且返回 `verified` | 待裁决；须独立正反例确认 |
| **HYP-001** | 待裁决 | **待证实疑点** | `main@fc0df2a`；#61-R1 | `candidate_digest` 关联使用 Candidate 字段值集合，可能将不同类型的 Candidate 身份值混同；先核对合同是否授权这种无类型匹配，再构造区分字段的反例 | 待裁决；尚无确诊的实现缺陷 |
| **NEW-002** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#61-R1 | `failure.issued_at` 仅做格式验证，没有参与评估时间过滤；需要证明过去 Assessment 是否被未来 Native Failure 改写 | 待裁决；待 as-of 合同核查 |
| **HYP-002** | 待裁决 | **待证实疑点** | `main@fc0df2a`；#61-R1/#63/#66-C | R1 虽接收 `all_observations_reconciled` 却只校验类型，未用于阶段判定；是否需要该事实取决于 Required 完整性与附加 Observation 的合同语义 | 未修复，不视作确诊 |
| **NEW-003** | 待评估 | **源码行为已定位；差异反例与合同待核验** | `main@fc0df2a`；#61-R4 | `require_reconciliation_manifest()` 名为需检验“授权 Manifest”，实际只验证 Envelope 结构与 Manifest 身份，允许 `release_authorized=False` 的验证阶段 Envelope 通过身份检查 | 待裁决；真实生产调用方未证明 |
| **NEW-004** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#61-R4 / Release Manifest v1 | 显式 `check_bindings` 可指向永远 `not_enabled` 的 optional Check；R4 只判断关联 R1 Check 的状态，不判断被绑定的 Manifest Check 状态或 `required` | 待裁决；若绑定仅是附加说明则须明确定义，不宣称发布绕过 |
| **NEW-005** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#61-R4 | R4 没有要求 `assessment.evaluated_at <= publish_context.evaluated_at`；即使两者逆序，也可能在外部 Policy/Provider 回调接受时 `release_authorized=True` | 待裁决；真实外部 Authority 是否自行约束尚未知 |
| **NEW-006** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#61-R1、#63 adjudications | 同一 Result 多条同时有效的不同 Adjudication 允许并存，`_effective_adjudication()` 按输入顺序选第一条；缺少同一结果多裁决优先级/冲突判定 | 待裁决；已确认来源选择受顺序影响，不等同证实错误 PASS |
| **NEW-007** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#61-R4 | `check_bindings=None` 不执行 Assessment Check↔Manifest Check 双向证明；控制测试允许 R4 `release_authorized=True` | 先核对映射是否为强制合同，与 NEW-004 可能同根 |
| **NEW-008** | 待评估 | **既有测试已覆盖行为；合同是否违反待裁决** | `main@fc0df2a`；#61-R1/#63 | 单种 `failure_superseded` 或 `conflict_resolved` 能解除 Native Failure+Conflict 混合结果 | 需上游授权规则裁决，不能直接称错误 PASS |
| **NEW-009** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#61-R1/#63 | Adjudication 可以早于它宣称解除的 Failure 的 `issued_at`，R1 本地无先后验证 | 待实测及 `issued_at` 语义裁决 |
| **HYP-003** | 待裁决 | **静态发现，待组合反例** | `main@fc0df2a`；#66 Hook/#52/#66-C | Hook Exit 只检查 Receipt matched，不直接读取未选中的 Failed Native Result；可能涉及合法重试与无效忽略的边界 | 待同 Invocation 多结果验证 |
| **NEW-010** | 待评估 | **源码边界已定位；来源与生产可达性待核验** | `main@fc0df2a`；#61-G2 / GitHub Check Publisher | `validate_publication()` 只禁止 `assessment_status="blocked"` 与 `conclusion="success"`；不要求成功对应 `assessment_status="passed"`，正式 Publisher 的授权仅对发布者/执行身份作证，未重新取得实际 R1 PASS | 待裁决；需要核查实际 Publication 来源/可信边界 |
| **NEW-011** | 待评估 | **源码边界已定位；来源与生产可达性待核验** | `main@fc0df2a`；#61-G2/R2 | `build_check_publication()` 对 Assessment 与 GitHub target 只绑定 `candidate.commit`（缺省时可用 event_head），未检查 Assessment `action/target/refs/phase` 是否与被发布的受保护 Scope 对应 | 待裁决；同 SHA 跨目标消费者仍待验证 |
| **NEW-012** | 待评估 | **源码边界已定位；来源与生产可达性待核验** | `main@fc0df2a`；#61-R4/Release | `reconcile_release_assessment()` 只比较 Assessment Commit 与 Phase，没有在 R4 本地核对 Assessment `action/target/refs` 与 Release repository/tag/target 的适用性；Policy 回调所收快照也未包含这些作用域字段 | 待裁决；与 NEW-011 同属跨消费者 Scope 漏绑定组，勿重复开发 |
| **NEW-013** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#61-R5 | R5 Trigger 的 `artifact_digest` 仅格式化后传入 Authority 回调，`run_id/run_attempt` 也没有在本地与 Assessment 所引用的原始 Run 证据关联；若实际 Authority 只核验事件存在，可能把错 Artifact/Attempt 当作合法 Trigger | 待裁决；必须先确认 Trigger Issuer/Artifact 归属及 Auth 回调真实实现 |
| **NEW-014** | 待评估 | **既有测试已覆盖行为；合同是否违反待裁决** | `main@fc0df2a`；#63 T1-a / #66-C | 同一 Job/Run/Attempt 标签下，跨独立 Report/Receipt 的已认证 Failed Result 保留诊断但 Selected PASS 仍被 `admitted=True` | 待裁决；须查是否为同一有效 Attempt 及失败传播规则 |
| **NEW-015** | 待评估 | **既有测试已覆盖行为；合同是否违反待裁决** | `main@fc0df2a`；#66-C / Hook Exit | 相同不可变 Result ID 在两个 Job Observation 出现时，Receipt `duplicate` 记账却不计入 `unreconciled`；可能保持 `matched` 并由 Hook Exit 放行 | 待裁决；须查重复计算复用是否受信且合法 |
| **NEW-016** | 待评估 | **源码行为已定位；差异反例与合同待核验** | `main@fc0df2a`；#66-C / Hook | 未路由的额外 Job Observation 若不含 Native Result，`_unexpected_observations()` 不生成记录；失败但零 Native 输出的 Job 可能不影响 `all_observations_reconciled` | 待裁决；需独立失败 Report/Job Receipt 的正反例 |
| **NEW-017** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#63 T2 | 多个合法 `historical_candidates` 具有相同历史 Scope/Expected Ref 但不同 Result 时，`admitted` 排序只有两项相同键，稳定排序按调用方原始顺序选出不同见证 | 待裁决；需确认候选唯一性要求与历史身份归属 |
| **NEW-018** | 待评估 | **源码行为已定位；差异反例与合同待核验** | `main@fc0df2a`；#66 Hook Dispatch | `record_job` 不拒绝重复 `job_ref`，第二次直接覆盖前次 Eligibility/Production Ref/Delivery 状态 | 待裁决；查合法 Retry 与唯一 Job 身份 |
| **NEW-019** | 待评估 | **源码边界已定位；来源与生产可达性待核验** | `main@fc0df2a`；#66 Hook Producer/#63 T1-a | `issue_job_receipt` 从调用方接收 Event/Scope/Expected Set，仅做形状/前缀检查便写 `upstream_verified`，未与本 Invocation 实际 Event/Expected 对照 | 待裁决；查原始 Issuer Authority 是否已独立阻断 |
| **NEW-020** | 待评估 | **源码行为已定位；差异反例与合同待核验** | `main@fc0df2a`；#52 Native/#66 Hook Collect | `_require_attempt_bound_natives` 用 `startswith` 匹配实例，`job-1-extra` 文字会满足 `job-1` 的前缀 | 待裁决；实际 Deliver/Production Ref 另有保护 |
| **NEW-021** | 待评估 | **源码行为已定位；差异反例与合同待核验** | `main@fc0df2a`；#66 Hook/#66-C | `finalize_invocation` 不拒绝输入字典的未知 Scope key，额外 Observation 直接被忽略，Exit 仅依赖 Expected Scope Receipts | 待裁决；先确认额外数据属于本 Invocation |
| **NEW-022** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#52→#66-C/#63 | `_execution_report` 只全局拒绝 `status=not_executed`/显式 Plan 不一致，却未对 `status=failed/blocked`、Report 级 Artifact Delivery/Prerequisite 失败传递否决；符合局部 Check 的 PASS 仍可被选为 matched | 待裁决；须区分独立 Check 可复用与整个 Job 的 Required FAIL |
| **NEW-023** | 待评估 | **源码边界已定位；来源与生产可达性待核验** | `main@fc0df2a`；#52→#66-C | ECI Report 仅 `plan_declaration_consistent is False` 会被整体拒绝，缺字段/None 不会触发拒绝；同一 Report 内 Checked Native Result 可获 matched | 待裁决；实际 Runner 产物通常显式带 True；查是否强制受信原件 |
| **NEW-024** | 待评估 | **源码边界已定位；来源与生产可达性待核验** | `main@fc0df2a`；#66-C | `_job_instance` 只要求 eligible 时存在一个合法 `qualification_ref` 对象，不验证其 SHA 与 Job/Run/Attempt/Invocation 内容或有权资格签发；可独立自报 eligible | 待裁决；这一级自称 structural，不可推断 #63 已承认来源 |
| **NEW-025** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#66 Hook Final/#66-C/#63 | `hook_exit_code` 将结构性 Receipt `matched` + 闭包标志直接映射进程成功，即使 `required_gate=not_evaluated`、`trusted_evidence_not_admitted`；若外部 Scoped JSON 并非可信 Dispatch 产物，可能错放行 | 待裁决；须查实际 Lefthook 调用、输入来源和本地 Gate 目标 |
| **NEW-026** | 待评估 | **源码行为已定位；差异反例与合同待核验** | `main@fc0df2a`；#61-R1→G2/R4/R5 | `validate_assessment` 不重检 authority `effective_at/valid_from/evaluated_at` 相对 Assessment 的时间条件，也不校验样本标记与其来源事实/限制语义；重新摘要的通过 Assessment 可能在下游继续被当 PASS | 待裁决；需原生重签摘要差异反例及受信来源裁决 |
| **NEW-027** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#61-R5 | `_bind_temporal` 当 Trigger=`authorized_reevaluation` 时允许 `post.evaluated_at < prior.evaluated_at`，也不要求 Post Evidence 不早于 Reevaluation 事件 | 待裁决；须明确追溯型重算与新判定的时间语义 |
| **NEW-028** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#61-G2 | `normalize_publisher_approval(...,now=...)` 以参数时间判断过期；`authorize_and_publish/workflow_publish_authority` 原样透传该时间。若独立 Approval Authority 只认证签发而不验证当前时钟，过期审批可能通过 | 待裁决；真实接线和外部 Authority 是否重验时钟未证 |
| **NEW-029** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#61-R5 | `due_window_authority` 可签出 `late_evidence_accepted=True`，其 Context 只有时间、Due Window 与 Lifecycle identity，没有 `post_assessment_ref` 或证据内容引用；难以对具体迟到证据做逐件授权 | 待裁决；需同一 Scope/时间不同 Post Assessment 的差异反例 |
| **NEW-030** | 待评估 | **源码边界已定位；来源与生产可达性待核验** | `main@fc0df2a`；#61-R1 Non-Check / D-G05 | `non_check_evidence` 的 `result=satisfied` 与 Obligation 的 Verifier/Authorization/Rule Ref 匹配即可记 `satisfied`；代码只验证引用形状/相等、时间、Candidate Subject，不核实真实 Verifier 是否签发了该 Result | 待裁决；查领域 Producer 是否在 R1 前保证受信交接，不能把内容哈希当证明 |
| **NEW-031** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#66-B2→#61-R1 | `universe.evaluation.evaluated_at` 已作为完整全集求值输入并入摘要，但 `assess_required_obligations(evaluated_at=...)` 独立控制阶段 Assessment；两时刻不比较，可能拿后来求值的 Obligations Universe 评价较早时点 | 待裁决；需同源、先后与 As-of 反例及有权 Universe 来源 |
| **NEW-032** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | `main@fc0df2a`；#66-B2→#61-R1 | R1 只解析 `authority.approved_at`，`_authority_fact` 核验状态、生效和有效期，但未比较 `approved_at <= assessed_at`；若政策不允许追认，未来才批准的 Profile 可参与过去 Assessment PASS | 待裁决；须核对回溯授权与 Approval 有效时间语义 |
| **NEW-033** | 待评估 | **源码边界已定位；来源与生产可达性待核验** | `main@fc0df2a`；#61-G2/G3 | `execution_identity` 的 `workflow_sha` 与 GitHub Runtime 比较，但 Evaluator/Policy Ref 只与 Approval/Publication 的声明比对，没有内置独立执行产物签发证明；一致的自声明仍可能获得发布 Authority | 待裁决；查实际运行身份的有权 Producer、Approval Authority 及 Check 发布来源 |
| **NEW-034** | 待评估 | **源码边界已定位；来源与生产可达性待核验** | main@fc0df2a；Release Manifest/Verification/Publisher | 生成器要求 Dagger 原始 Check 名称，序列化验证和真实 Publisher 仅重查基础 CHECKS Job/Step | 待裁决；需原生差异 API 和真实来源边界 |
| **NEW-035** | 待评估 | **源码边界已定位；来源与生产可达性待核验** | main@fc0df2a；Release Manifest/Provider | 版本仅做合法格式、枚举、唯一性检查；未从原生 Producer 复核取值 | 待裁决；034/035 同源验证族 |
| **NEW-036** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | main@fc0df2a；Release Publishing | allow_expired=True 不区分历史已发布核验与首次 publish_release | 待裁决；依赖当前 Profile 发布时可取用合同 |
| **NEW-037** | 待评估 | **源码行为已定位；合法合同与影响待裁决** | main@fc0df2a；Manifest/Publishing | Digest=None 可通过固定 Artifact 名称/ID/Run 的 Manifest 验证，Provider 不核对字节摘要 | 待裁决；真实 API 及字节义务待验证 |
| **NEW-038** | 待评估 | **源码声明行为已确认；真实调度来源与消费者待核验** | `main@fc0df2a`；#66 Hook/Lefthook → #66-C Routing | 未调用 Lefthook 的模块级执行和对账中，`scheduler_ref` 自动写 Lefthook 2.2.2，`effective_config_ref` 自动写 `hook-job:1` | 原生来源/生产授权影响未证；与 NEW-024/025 关联 |
| **NEW-039** | 待评估 | **源码级重调条件确认；原生 Runner 异常复现与生产可达性待验证** | `main@fc0df2a`；#86 Host Prerequisite / Dagger Module-local command | `_run_command` 把 Runner 内部抛出的 `TypeError` 当作参数签名失败，可能重复调用已开始执行的原生命令 | 没有提交原生复现、没有证明真实受保护发布/门禁错误 |
| **NEW-040** | 待评估 | **已证外部 Consumer 与真实 PR Run；Secret 是否存在/越权未证** | ECI `main@fc0df2a` + 旧版 Workflow `@0f74b2f`；vesryn PR #32/#29 | Consumer 的本地 Dagger Module 可由其 PR 代码修改，`workflow_call` 显式传可选 Secret；ECI 固定旧版在 Host 执行它导出的 `ci.py`；两个真实 Run 均完成 Gate | 代码交接路径真实运行，但未证明凭证实际非空、发生泄漏或 Consumer 非授权执行 |

### D-F01 — Terminal/Phase 误清除未闭合 Domain

- **证据与输入**：[#125-D assessment](authorization-closure/independent-review/assessment.md)、[findings.json](authorization-closure/independent-review/findings.json)（`D-F01`）；原始案例 `terminal.compound_open_domain` 与 `terminal.compound_original_declared_domain`，详见 [cases.json](authorization-closure/independent-review/cases.json) 与保存的 `pr124-semantic-results.json.gz`。
- **实际结果**：`selection.plan()` 真正导出 `status=unknown`、`effective_profile.domain_complete=False`、`definitions_status=requested_names_complete`，与合法 Terminal/Phase 见证同时出现时，Binder 返回 `bound/authorized=True` 且 `reasons=[]`。合法闭域对照 `terminal.compound_closed_domain` 也应允许 `bound`。
- **定位**：[Producer `expected_checks._derive_v2_from_profile`](https://github.com/dymzz/engineering-ci/blob/adeeb4b15e47072c5091a31c048253f60471ff1b/src/engineering_ci/expected_checks.py#L1770)、[#124 `terminal_expected._terminal_applicability_is_sole_unknown`](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L261) 与 `_partial_is_phase_only`。现有 Producer 已保存部分并存事实，B2 Schema 是否需要调整尚无充分证据。
- **影响**：已证明仅候选 Binder 的错误授权；共享上游 Expected v2 其他消费者需要查受影响子图，不能直接外推“生产 Gate 已放行”。外部 Issuer/Completed Run/Phase 回调仍为受控替身。
- **未结证据**：governance/check-domain、definition/rule、applicability、Phase、authority、source、time、Ref 的并存事实，以及合法单域、空域、Phase-only、复合缺口案例与各真实消费者。I01 的改动位置和合同范围仍依赖这组证据。

### D-F02 — T1/T2 Producer → R1 接线合同断裂

- **证据与输入**：[D-F02](authorization-closure/independent-review/findings.json)；`interop.t1_pass_to_r1`、`interop.t1_fail_to_r1`、`interop.t2_pass_to_r1`。真实 T1/T2 模块的 API 被调用，原生 Result 是受控模型，Issuer/verifier 回调为替身。
- **实际结果**：T1-a admitted → R1 `admission_phase_invalid`；T2 admitted → R1 `admission_kind_invalid`；认证失败的 T1 也卡在输入形状，**未观察到 Native FAIL 被错误转成 PASS**。
- **定位**：[#63 `admission.admit_evidence`](https://github.com/dymzz/engineering-ci/blob/adeeb4b15e47072c5091a31c048253f60471ff1b/src/engineering_ci/admission.py#L779)、`admit_historical_reuse` 与 [#61 `_validate_admission`](https://github.com/dymzz/engineering-ci/blob/adeeb4b15e47072c5091a31c048253f60471ff1b/src/engineering_ci/required_assessment.py#L1341) 的 Gate 投影不同。
- **待核实接口关系**：R1/#63 的原生产对象、结构 Receipt、受信 Gate Projection、Non-Check、历史身份、失败/冲突/Remaining Work 对应关系。当前已复现 kind/phase 形状不兼容；其他类型化身份、来源认证和 T2 合法复用仍属于必要的合同区别。

### NEW-001 — R5 对 post-only 合同样本的漏检（新发现，待 API 复现）

- **静态证据**：[R5 `reconcile_post_stage`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L582-L634) 的赋值与理由判断只读 `contract_sample`、`prior.get("inputs", {}).get("contract_sample")`；并未读 `post.get("inputs", {}).get("contract_sample")`。后续 [`_envelope_status`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L470) 与 [`_consistency`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L506) 只消费已汇总的样本位。R1 `_assemble` 能在局部四事实为真时令样本 Assessment 的 `status=passed`，同时保留 `inputs.contract_sample=True`。
- **测试盲区**：[现有 R5 测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment_post.py#L442) 只检查“prior 和 post 都是 sample，且显式 sample=True”；没有测试 **prior=False、post=True、显式=False**。
- **待运行反例**：用测试中现成 `prior_passed(contract_sample=False)`、`post_assessment(passed=True, contract_sample=True)`、合法 Trigger/PhaseOrder/DueWindow 与受控 authority，调用真实 `reconcile_post_stage(..., contract_sample=False)`；验证是否返回 `status=verified,contract_sample=False`。同时测试 prior-only 与 non-sample 的正例，不扩成全生命周期 PASS。
- **注意**：截至本条记录，API 组合**未执行**；此项只能称“由源码可推导的错误路径”，不能称“已复现生产绕过”。

### HYP-001 — Candidate 身份的无类型值匹配（待语义裁决）

- [`required_assessment._applicable_to_matches`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1188) 使用 `set((universe["evaluation"].get("candidate") or {}).values())` 比对单个 `applicable_to.candidate_digest`；[`_cross_check_universe`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L936) 也对 Obligation candidate digest 做值集合检查。候选类型允许 `commit/tree/artifact_digest/source_digest`。
- **未知**：`candidate_digest` 合同是否有意表示“任一已授权候选身份”，还是应该绑定明确的 digest 类型、对象与作用域。现有 version/tag 错配用例不覆盖 artifact/source 摘要碰巧相等或错类型身份。
- **待核对证据**：#61 身份合同、两类不同标识但字符串相同的差异输入、实际结果和适用身份语义。当前为待裁决疑点，尚未确认违反合同。

### NEW-002 — Failure 发行时间与 Assessment 时间切片脱节（待 API / 合同复核）

- **明确源码**：[`_failure_entry`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1284-L1302) 调用 `_optional_datetime(issued_at)` 但只保留字符串；[`_in_scope_failures`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1798-L1845) 根据 Scope/Action/Phase 决定失败是否生效，**没有比较 `failure.issued_at` 与 `evaluated_at`**；同模块 [`_effective_adjudication`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1779-L1796) 却会排除未来 `decided_at`。
- **条件反例**：在合法且封闭的相同 Assessment 输入中，保留历史合格 PASS 和相同 Scope，把独立失败事实 `issued_at` 改为评估时间之后，观察 `status/blocked_reasons` 是否变化。比较 `issued_at` 早于/等于/晚于 `evaluated_at`，以及旧 Run 与当前 Run 是否支持相同时间解释。
- **尚未知**：R1 是严格“按 assessed_at 的 as-of 截面”还是“用当前知道的全部认证失败做再评价”。先从 #61 的原始时间合同与真实 Producer 查证，不以防御性理由把时间语义任意改成一律忽略/一律阻断。**此条不是已证实生产 BUG**。

### HYP-002 — `all_observations_reconciled` 收到但未参与 Required 消费（待裁决）

- **实际源码**：[`_validate_admission`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1431-L1437) 接收且核查布尔类型；R1 其他位置没有读取该字段。相应 Receipt 也记录 `all_observations_reconciled`，但本阶段四事实只基于实际列举的 Required/Scope/证据。
- **待裁决**：如果它涵盖尚未列举的**当前必需**观察，R1 的 `passed` 可能缺全局执行闭包；如果只是额外不适用的运行数据，直接阻断会误拒合法范围。#66-C Receipt、#63 Admission 的原始定义及“非 Required 未关联 Observation”与“当前 Required 未对账 Observation”两组对照可区分此语义。

### NEW-003 — R4 Manifest 消费 API 只检查身份、不检查授权位

- **固定代码**：[`require_reconciliation_manifest()`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L242-L250) 先调用 `validate_release_reconciliation(envelope)`，再计算 `bind_manifest_identity` 并比较 `manifest_identity`，**没有 `release_authorized is True` 或 `status/mode` 检查**。
- **已有正证**：[`test_validation_mode_is_verified_but_does_not_authorize_publication`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment_release.py#L207-L222) 证明一个合法 R4 Envelope 可 `status=verified` 且 `release_authorized=False`；现有 `require_reconciliation_manifest` 测试只用真正 `authorized` 的例子，未覆盖这个差异。
- **候选差异 API 反例**：对同一合法验证阶段 Manifest，调用 `reconcile(...,publish_context.mode=validation)` 得 `release_authorized=False`，再传入 `require_reconciliation_manifest`；对照 true 的 Publication Envelope。**身份匹配与可发布权限是两个不同谓词**。实际调用方是否把本接口用作发布保护仍待核查；当前已知的是公开 API 的身份检查与发布授权并不等价，生产权限绕过未证。

### NEW-004 — Check 绑定可指向固定未启用的 Manifest Check

- **固定代码**：[`release.manifest`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L290-L300) 强制两个 optional 记录 `required=False,status=not_enabled`；[`manifest.validate`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L393-L410) 允许该固定形态。[`_normalize_bindings`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L251-L271) 不限制 key 到 Required；[`reconcile_release_assessment` 绑定循环](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L772-L815) 只要求关联 R1 Row 的 `status` 属于 `satisfied/not_applicable/not_yet_due`，**不使用已读出的 `manifest_status` 进行判断**。
- **可触发合同路径**：已有 Passed R1 + v1 Passed Manifest + `check_bindings=[{manifest_check_key:"core_coverage_threshold",expected_check_ref:<satisfied-check>}]`，在受控 authority 接受下不会触发该绑定的 `not_enabled` 失败。现有唯一绑定测试只验证 **R1 blocked** 被阻断，不能证明实际 Manifest Check 对账。
- **合同区分**：如果 `check_bindings` 表示原生/Manifest 执行关联，当前映射缺少对应执行证明；如果只是描述性关联，需以真实消费端判断其是否承担授权语义。固定 optional Check 与 Profile Required 的适用性是另一独立属性。

### NEW-005 — R4 没有验证 Assessment 与 Release 的因果顺序

- **固定代码**：[`_profile_snapshot`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L331-L356) 保存 `assessment_evaluated_at`；[`_normalize_publish_context`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L117-L169) 正常化 `publish_context.evaluated_at`；[`reconcile_release_assessment`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L673-L905) 检查 Assessment/Manifest Phase、Candidate/Run/Policy 当前授权，但**没有本地 `assessment_evaluated_at <= release evaluated_at` 的因果断言**。将两个时间交给 `policy_snapshot_authority` 不等于 R4 自己已经保证。
- **候选反例**：构造在 `t2` 真实产生且 `status=passed` 的 R1 Assessment；采用合法 v1 Manifest，设 Release `evaluated_at=t1<t2`，受控 Policy/Artifact/Provider/Handoff 仍接受，观察 `release_authorized`。比较正常 `t2<=t1`、授权窗口有效/已失效及跨 Run 的语义。Issuer 可能在生产自行阻止，不能凭源码宣称真实 Release 已在过去获得授权。
- **关系**：与 NEW-002 同属“评估时间切片”审阅族，但前者是 **R1 内 Failure 输入时间**，这里是 **R1→R4 先后因果**，影响不同调用边，**不直接去重**。

### NEW-006 — 同一 Result 的多个裁决按列表先后选择

- **固定代码**：[`_validate_admission`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1397-L1406) 将所有 Adjudication 按 `expected_check_ref` 原顺序收集，不拒绝对同一 Result 的多重重叠决策；[`_effective_adjudication`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1779-L1796) 找到第一个时间有效、未撤销、非 `unresolved` 的记录即返回；[`_in_scope_failures`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1798-L1845) 用该记录的 Disposition/Authority/Policy 解除 Failure。
- **可重复反例**：同一 `scope_id + expected_check_ref + result_id` 的两条有效 Adjudication，使用不同的 `authority_ref/policy_ref/disposition/decided_at`，调换数组顺序（重新计算合法 Admission Digest），比较 R1 `authenticated_failures[].adjudication` 的归属。当前测试覆盖“单裁决解决双 Failure”“过期/撤销裁决阻断”，但未覆盖**同一 Failure 的重叠双裁决**。
- **合同与反证**：#63 是否要求唯一有效决策、后决覆盖前决或允许多签并存尚待确认；源端是否已提供唯一性证明也不明。R1 来源选择的顺序敏感是代码事实，两项有效解除裁决并存并不必然改变 PASS/FAIL，真实发布影响未证。

### NEW-007 — R4 接受零 Check Binding 时的完整性合同待确定

- **静态事实**：[`_normalize_bindings(None)`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L251-L270) 返回空列表；[`reconcile_release_assessment`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L750-L820) 只循环显式 Binding，没有把 Assessment 全部 Required Check 与 Manifest 所需执行 Check 做**完整双向配对**。已有 [正例](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment_release.py#L179-L244) 使用空绑定也可在受控权限下得到 `release_authorized=True`。
- **独立性**：NEW-004 是绑定了未启用的 Check；本项是空映射情况下也可获得 R4 授权。两项可能属于相同的 Check 映射完整性问题，但当前 R1 PASS 与 Manifest PASS 只证明各自集合满足，并未建立二者实际 Check 实例一一对应。
- **待裁决合同**：若 R4 规定两个系统只要求各自独立全量通过，零绑定可能是合法设计；若声称 Manifest 各 Check 证明了当前 Profile 的 Required，必须有有权映射/完整性证据。当前只证明空绑定允许授权，不证明生产绕过。

### NEW-008 — R1 Adjudication Disposition 不区分被消除的失败类型

- **固定代码**：[`_in_scope_failures`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1798-L1845) 对 `failure_kind=native_failure/conflict` 都只要求某个非 `unresolved` 的有效裁决，**没有检查 `failure_superseded` 是否用于 Native Failure、`conflict_resolved` 是否用于 Conflict**。
- **现有测试明确接受这种行为**：[`test_two_conflicting_failures_require_an_explicit_resolution`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment.py#L2053-L2090) 同时包含 `native_failure` 和 `conflict`，只用一种 Disposition 覆盖两个 Result，参数 `failure_superseded/conflict_resolved` 均断言 `assessment.status=passed`。所以不是测试缺失，而是**被测试固化的当前合同**。
- **待裁决**：#63 Adjudication 的真实许可语义尚待查证：一个有权结果是否可以覆盖两类失败，或不同失败性质分别需要额外证明。当前既有测试记录了复合处置行为；是否违反 R1 失败传播/历史复用合同仍未证明。

### NEW-009 — 未来才发生的 Failure 可被更早 Adjudication 消除

- **代码条件**：[`_effective_adjudication`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1779-L1796) 只比较 `decided_at <= evaluated_at`、有效期与撤销；[`_failure_entry`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1284-L1302) 会记录可选 `issued_at`，但是 `_in_scope_failures` 没有要求 `adjudication.decided_at >= failure.issued_at`。例如同一 `result_id` 的裁决早于已认证失败的实际时间，仍可能解除该失败。
- **依赖关系**：和 NEW-002（未来失败被早期 Assessment 消费）共用 `issued_at` 时间语义，但这条关注的是**裁决是否可以在事实产生前被签发**，不是简单评估快照的过滤；和 NEW-006（多个有效裁决的顺序敏感性）分别对待。
- **待对照**：检查授权上游是否允许预先批准具体已知 Result ID、或 `issued_at` 实为接纳时间而非失败发生时间；运行相同 Result / 三种先后组合及不同来源。当前仅源代码证明缺乏因果比较，未运行真实 API 反例。

### HYP-003 — Hook Exit 是否允许一次 Invocation 中失败结果与成功 Witness 并存

- **代码事实**：[`hook_exit_code`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L986-L1020) 只要求 Receipt `status=matched`、`all_observations_reconciled=True`、entry `binding_status=matched/not_applicable`；不直接读 `native_status`。`reconciliation` 的 `matched` 依据 [`native_satisfaction.status=satisfied`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L1599-L1618)，选中的 Result 必须为 Passed；多个观察值中其余 Failed 是否仍导致阻断取决于原生工具需求、Attempt 资格和重试语义。
- **反证方向**：合法重试可能包含此前 Failure 与最终成功；独立 Check/Requirement 的满足语义也可能区别于 Job 全局状态。同一 eligible Attempt、相同/不同 Requirement 的 PASS/FAILED 组合和 #66 Hook 终态/重试合同可区分这两种情形。
- **潜在影响**：若未获授权的失败结果被当前 Hook Exit 静默忽略，则本地 Commit/Push 可能以 `exit=0` 放行；仅当受控反例确实得到该结果才提升为确认 BUG。此问题与已有“没有 Lefthook 配置/Hook 真实接线未交付”的生产欠账是两种不同问题。

### NEW-010 — 成功发布序列化记录缺少真实 Assessment PASS 证明

- **精确源码**：[`required_assessment_check.validate_publication`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_check.py#L452-L539) 只在 `assessment_status=="blocked"` 时拒绝 `conclusion=="success"`，没有要求 `assessment_status=="passed"`，也不取回 R1 Assessment 重验。 [`publish_check_run`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_check.py#L696-L740) 校验结构、Publication 的身份和外部 `publish_authority`；[`workflow_publish_authority`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L515-L614) 核对 Actions/Approval/Execution/Evaluator/Policy，但没有读取所指向的 R1 Assessment 原始结果。
- **差异反例**：以 `build_check_publication` 得到合法 passed Publication，单独改为 `assessment_status=None` 或 `"unverified"`，保留 `conclusion=success` 与原 `assessment_ref`，重新计算 `publication_ref`；验证 `validate_publication` 及受控 `publish_check_run` 是否接受。进一步试验指向不存在的 `assessment_ref`，区分可重算的内容摘要与**有权来源签发证明**。
- **关键限制**：直接构造的正常路径由 `build_check_publication(真实 Assessment)` 验证原件；风险发生在 Publish 作为独立**序列化消费边界**时。即使有真实 Publisher Approval，也不是自动证明该 Assessment 的四事实通过。要查真实调用方是否允许不受信 Envelope 进入；**未声称现在有生产攻击者或已成功绕过 GitHub**。

### NEW-011 — G2 对相同 Commit 的不同授权 Scope 只绑定部分身份

- **代码**：[`_bind_assessment_target`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_check.py#L218-L238) 若 `candidate.commit` 存在，仅检查等于 `head_sha` 然后返回；如不存在，在允许 `event_head/explicit_assessment_target` 时以独立 `target_binding` 通过。后续只将 `assessment.action/target/phase` 写入输出 Summary，**没有对 `assessment.refs` 与实际被治理 Ref/Action 绑定**。
- **差异条件**：相同 HEAD 上，令合法 R1 Assessment 仅针对 A Ref/Action/Phase，尝试为 B Ref/Action 构造 G2 正式 Check Envelope；与真正 B Scope 的 Assessment 做对照。检查 Published Required Check 被服务端使用时是否可能将不适用的 PASS 当作目标授权。
- **证据边界**：现有 `tests/test_required_assessment_check.py` 覆盖 SHA、event/run 的 Publication 与 Target Binding 自身一致，却没有覆盖 **Assessment 内部 Ref/Action/Phase** 与该 Binding 的交叉一致性。属于可能错误复用真实 PASS 的身份错配，不是 NEW-010 的“缺失 PASS 来源”。

### NEW-012 — R4 对 Release 仅比较 Assessment Commit/Phase，忽略 Scope

- **代码**：[`reconcile_release_assessment`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L673-L735) 验证 `candidate.commit=head_sha` 与 `phase`；[`_profile_snapshot`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L331-L357) 传给 `policy_snapshot_authority` 的快照没有包含 Assessment `action/target/refs`，因此这些字段并非 R4 原地约束。Manifest v1 验证的是独立 Release Repository/Tag/HEAD 身份。
- **条件反例**：同 SHA、同 Phase、合法 Manifest、可信 Provider/Handoff/Policy 回调，但 R1 Assessment 来自不同 Target/Ref/Action；若 R4 仍返回 `release_authorized=True`，即可证明在被消费的 R1 Scope 上缺乏对账。当前 Release target/action 的实际合同与 Ref 等价关系需要明确定义，否则输入差异不足以证明错误授权。
- **去重**：与 NEW-011 共享 **“相同 Commit ≠ 相同授权 Scope”** 的上游语义问题，但 G2 与 R4 分别是 GitHub Check 与 Release 允许性出口；统一接口裁决，保留两条消费者反例，不据此各自新建映射库。

### NEW-013 — R5 Trigger 的 Artifact/Run Attempt 未被本地消费者关联

- **固定代码**：[`_normalize_trigger`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L382-L406) 将 `artifact_digest` 规范化、Run ID/Attempt 解析；全模块随后没有读取 `trigger["artifact_digest"]` 进行 Candidate/Artifact 关系比对，也没有将 Trigger Run/Attempt 与真实 Assessment 原生 Run 身份直接对账。[`_bind_lifecycle`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L276-L316) 只检验前后 Assessment 的 Context/Action/Target/Refs/Candidate 与 Trigger `target`；[`trigger_authority` 的上下文](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L667-L694) **包含完整 Trigger 和 lifecycle identity**，因此正确的外部有权 Producer 有机会负责关联。
- **差异条件**：以正常合法 prior/post 和授权 Trigger 为基线，只改变 `artifact_available` 的 Artifact Digest 或 `run_completed` 的 Attempt 身份，保留受控（只检查事件存在）Authority，观察 `status=verified`。然后换真实 Issuer/Verifier 是否建立此关系，判断是 R5 接口合同应明确的验证前提还是消费代码真正缺失。
- **语义待定**：Trigger Artifact Digest 可能引用独立受治理产物，而非 Candidate 的某一个固定字段。其类型化身份映射和 D-G04 所属真实 Producer 尚待验证；受控回调的通过仅证明 API 条件可达。

### NEW-014 — T1-a 对同一 Job/Run/Attempt 的独立 Report Failed 视为诊断

- **既有测试直接证明**：[`test_t1a_unrelated_bad_observation_does_not_contaminate_selected_witness`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_admission.py#L742-L764) 构造相同 `job_ref/run_ref/attempt_ref` 标签、但不同 Report/Job Receipt 的 PASS/FAILED；最后断言 `entry["admitted"] is True`、`observed_diagnostics` 非空。该行为被测试固化，不能假称测试未覆盖。
- **代码链**：[`_same_observed_scope`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/admission.py#L471-L480) 只按 Report 摘要或 Receipt 身份共享判断，不比较 Run/Attempt；[`_entry_admission_v2`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/admission.py#L617-L751) 在选中 PASS 可信且 `contaminated=False` 时仍可 admitted，虽对全部已验证 FAILED 写入 `native_failed` 原始 reasons。
- **待裁决**：同一 Job/Run/Attempt 下是否允许多份有权 Report，以及对应的独立执行/最终 Attempt 语义尚不明确。真实 Producer 的 Report/Receipt、Attempt 终态与 #63 失败投影是区分性证据。D-F02 已证明输入不兼容，本项调查 Admission 选择/失败传播，两者结论不同。

### NEW-015 — #66-C 重复 Result 记账未参与完整对账与 Hook Exit

- **既有测试证据**：[`test_duplicate_observation_is_recorded_without_double_counting`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_reconciliation.py#L1365-L1390) 将同一个 Result ID 放到两个已激活 Job，得到 `receipt.entries[0].binding_status="matched"`、`receipt.duplicate` 非空。
- **代码链**：[`_receipt_aggregates`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L1717-L1745) 统计 `duplicate` 却只将 unexpected/rejected 加入 `unreconciled`；[`hook_exit_code`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L986-L1020) 要求 `matched` 和 `all_observations_reconciled=True`，没有独立拒绝 duplicate。
- **条件反例**：在合法 complete Expected、同时两个 Job/Attempt 都被允许的情况下，调用原生 `reconcile_expected_results` 生成 Receipt，再交 `hook_exit_code`，核对是否 `exit=0`；用合法共享/非法跨 Job 冒用两组 Producer Origin 作对照。**重复不自动等于不受信**，是否必须阻断取决于真正 Result ownership/Qualification 合同。

### NEW-016 — 额外 Job 的空 Native Report 可能在对账中不可见

- **固定源码**：[`_normalize_observations`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L727-L845) 允许 `native_results=[]` 的 Observation；[`_unexpected_observations`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L1629-L1687) 对未路由 Job 仅 `for result in observation["native_results"]` 追加 Unexpected，**空列表没有 Job 级事件记录**；[`_receipt_aggregates`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L1717-L1745) 只看 resulting unexpected/rejected 项。
- **区分性案例**：在完整 matched Expected 与合法 Report 的基础上，分别增加未路由但受信的失败 Execution Report（零 Native Result）及包含 Failed Native Result 的对照组，记录 `summary.unexpected/status/all_observations_reconciled` 的变化。Job activation 与当前 Required Scope 的真实归属决定其是否属于需要传播的失败。

### NEW-017 — T2 相同历史身份的成功候选未形成稳定全序

- **源码**：[`admit_historical_reuse`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/admission.py#L1527-L1789) 先对 `historical_candidates` 逐项验证，若多条 `admitted`，只按 `(historical_scope_id, historical_expected_check_ref)` 排序，取 `admitted[0]`；[`_historical_candidate`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/admission.py#L1114-L1184) 不要求整个候选数组中这两字段唯一。
- **最小反例**：同一历史 Scope/Expected Ref，构造两个不同 Result/Report 的有效、等价、权威接受且都 `admitted` 的 Candidate，仅交换输入顺序；预期当前代码选择不同 `result_ids` 而 `status` 同为 admitted。需要原生 T2 API 实测，并确认上游 Historical Candidate Provider 是否保证这两项身份绝对唯一；若上游已证明唯一，则这项应降级为边界前置约束而非运行时 BUG。
- **影响**：证据身份/Provenance 不稳定可能扰动缓存键和审计确定性；它和 NEW-006 的 Adjudication 排序同属“不充分的裁决全序”，但不是同一个消费者或策略规则。

### NEW-018 — 重复 Job 激活覆盖先前生产事实

- **位置**：[`RunBinding.record_job`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L129-L177)。验证名称、资格、Mapping 后，无条件 `self.jobs[job_ref]=entry`；若为 eligible，`production_ref` 含新的随机 nonce。没有重复 Job 身份拒绝或状态沿革。
- **触发时序**：激活 Job → `execute_activated_job` 记录 `delivered_report_ref` → 同 Job 再激活 → 前次 Delivery/Production Ref 从 Job Map 消失。改变 Eligibility 还可能改变后续 Routing 的归属和资格。
- **影响/不确定**：已证原地覆盖，未证最终 Hook 错误放行。检查真正 Dispatch 是否可能重复激活同 Attempt 的相同 Job；合法 Retry 应按现有 Run/Attempt 身份语义判定，不自行新增调度框架。

### NEW-019 — Job Receipt 的 Event/Expected Scope 未锚定真实 Invocation

- **位置**：[`issue_job_receipt`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L812-L890)。Report 已 Collected/Attempt 已验证之后，调用方传入的 `event_ref` 仅检查为 dict，`scope_id/expected_set_ref` 只检查 `sha256:` 前缀，未与 `run_binding.binding.event` 或实际 Expected 相等校验；Receipt 仍写 `verification_status=upstream_verified`。
- **已有测试例证**：[`test_precommit_execute_requires_captured_index_source`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_hook_coordinator.py#L927-L947) 以构造的 Event Ref、任意示例 Scope/Expected Digest 签发 Receipt，断言上游验证标志成立。
- **依赖关系**：#66-C `_attribution_problem` 会核对 Receipt **声明**与当前 Expected，但不能单凭该检查证明 Receipt 的声明与真实 Invocation 同源。#63 外部 Issuer Authority 可能补足来源认证。下轮应以“Invocation A 的真实执行结果被赋予 B Scope”做组合反例，不直接宣称生产越权。

- **补充 #63 交叉证据（2026-11-18）**： [admission._authenticate_evidence](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/admission.py#L337-L470) 读取 Job Receipt 原件、验证 Digest 和 Job/Run/Attempt/Report 关联，并向外部 Issuer Authority 发送 job_receipt_ref 等上下文；但未直接展开 event_ref、scope_id、expected_set_ref。外部 Authority **可能通过受信 Receipt Ref 独立取原件核对**，因此不能认定完整认证已绕过。此处补入前一轮摘要声称已更新、但原详细条目漏记的证据。

### NEW-020 — Native Result Attempt 实例匹配缺少 Job 分隔边界

- **位置**：[`_require_attempt_bound_natives`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L604-L617) 使用 `instance.startswith(expected_prefix)`，其中 `expected_prefix=run_ref/attempt_ref/job_ref`；#52 [`_native_results`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/execution.py#L3680-L3805) 的正确结果再接 `:origin:index:check:access_id`。
- **条件反例**：`job_ref=job-1` 的校验会接受文本 `run/123/attempt/1/job-1-extra:host:0:...` 的错误实例。实际 `collect_job_report` 还需要 `delivered_report_ref`、`production_ref` 和 Report Digest，与本地字符串判断不同；应复核有权 Runner 是否真的可能提供该值。当前仅认定边界语法缺陷。

### NEW-021 — 多 Ref Final Reconciliation 静默丢弃未知 Scoped 数据

- **位置**：[`finalize_invocation`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L1022-L1101) 先验证 Expected 覆盖 Invocation，但只循环 `_iter_scopes(expected)` 的 key，从三个 `*_by_scope` 映射中取对应条目；额外未知 key 从未检测或进入 Reconciliation，`hook_exit_code` 只检查已产生 Receipts。
- **差异反例**：复用 [multi-ref Final 正例](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_hook_coordinator.py#L1186-L1336) 的两个合法 Scope/成功 Job，添加 `observations_by_scope[unknown]=[来自本 Invocation 的 Failed Job Observation]`，观察 `exit_code`/Receipt 是否完全不变。要进一步证明真实故障，应确认该额外 Job 的激活、Assignment 与上游 Scoped key 来源，不能把故意提供的无关数据当真实当前失败。

### NEW-022 — 失败/阻断的 Execution Report 仍可提供可匹配 Native PASS

- **源链**：[#52 `execution._finalize_report`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/execution.py#L4488-L4635) 在 Test/Prerequisite 失败、Artifact Delivery blocked 或 Checks unfinished 时将整份 Report 置为 `failed/blocked`；但可包含另一个 Check 的原生 PASS。 [#66-C `_execution_report`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L310-L415) 只有 `not_executed`、显式 Plan 不一致/Contract Kind 错误成为 Report-wide rejection；`failed/blocked` 不在分支中。[Check-level `_check_report_rejection`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L234-L309) 仅在当前 Check 自身有依赖失败或状态矛盾时拒绝。
- **时序与差异反例**：以合法 `execution.execute` 得到“当前 Required Check PASS、另一 Prerequisite/Artifact 失败导致 Report `blocked/failed`”为 A 组；完整 Report 均 PASS 为 B 组。将 A 组真实 Report 经 Collect → Reconciliation → T1-a，比较局部 `matched/admitted` 与整体失败保留位置。**不能用手工伪造 `report.status` 代替原生失败运行**。
- **边界/去重**：NEW-014 是跨独立 Report/Receipt 的失败隔离；这里是**同一 Report 的整体终态/单 Check 见证**。允许独立 Check 局部 PASS 可能完全合理，只有当消费者的 Required Scope 还包含那个失败的全局前置时才是授权缺陷。若已由 Plan/Expected 独立枚举，需据实降级。

### NEW-023 — ECI Report 的 Plan 一致性证明缺失时未明确拒绝

- **源代码**：[`reconciliation._execution_report`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L326-L360) 将 `schema_version>=2` 及 `plan/plan_authorized/check_contract` 等判为 ECI Report，却仅在 `plan_declaration_consistent is False` 时拒绝，没有拒绝 **None/缺失**。[#52 `execution.execute`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/execution.py#L4682-L4728) 正常新计划产生 `plan_declaration_consistent=True`；[既有测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_reconciliation.py#L2511-L2548) 只证明显式 `False` 会被拒绝。
- **待测**：同一已通过 Native 的 ECI Report 固定其他字段，分别设 Plan 一致性 `True/False/None/缺失`、重新计算对应完整 Report Digest 并生成合法 Observation，观察 `report_rejections/binding_status`。再查原 Runner/Issuer 的真实受信来源，确认是否强制签发 `True`；单纯可重算 Digest 不等于外部认证。
- **影响级别**：这属于来源证明缺失是否被错误容许的合同问题，不是已证生产伪造。它与 NEW-022 的 **已明确 Report failed** 不同：这里 `Plan declaration 未获验证` 是未知，而非明示失败。

### NEW-024 — Attempt Qualification 只做存在与格式检查，没有资格来源证明

- **代码**：[`reconciliation._job_instance`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L588-L637) 只要求 `attempt_eligibility!="unknown"` 时存在 `qualification_ref` 并调用 `_evidence_ref` 校验形状；不比较 Qualification 与当前 `job_ref/run_ref/attempt_ref/invocation_ref`，也不查询真实 Scheduler Issuer。`_reconcile_entry` 继而把该状态用于选择 eligible 实例。 [#66 Hook `attempt_qualification_ref`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L89-L104) 以 Run/Attempt/Invocation 自算 SHA，且真实 `record_job` 产生此引用。
- **对照**：`tests/test_reconciliation.py` 中 `test_unqualified_or_ambiguous_attempts_stay_unknown` 证明 `unknown` 与多 eligible 尝试有阻断，但没有证明一个单独的、**来源错误的形状合法 qualification_ref** 必然被拒绝。
- **后续**：使用相同 Job/Report，分别提供当前 Attempt 的真实 Qualification 与另一 Attempt 的合法引用，在独立 Reconciliation 中比较 `matched`；再核实原生 Dispatch 是否存在被跳过的授权边界。#66-C 明确称其为结构性 Witness，不应据此声称 #63 已接受可伪造的资格。

### NEW-025 — Hook Process Exit 使用结构性 Receipt，而非已认证 Admission

- **实际调用**：[`finalize_invocation`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L1022-L1101) 在 Reconciliation 后直接调用 `hook_exit_code`，输出 `trusted=False, required=False`；[`hook_exit_code`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L986-L1020) 用结构性 `status=matched`、`all_observations_reconciled`、Expected completeness 算 `0`；[`main finalize`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L1137-L1222) 接受 JSON Routing/Resolution/Observation 路径后直接返回此 Exit Code。 [#66-C Receipt](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L1788-L1827) 自述 `trusted_evidence_not_admitted`、`required_gate_not_evaluated`。
- **边界**：零退出的本地 Git Hook 可能只表示“所声明的 Native Checks 全部匹配”，无需等同 R1/G2 发布授权；但是，如果用户的原始要求是**本地 Hook 必须执行真实 Native、不能接受任意自填的 Report/Eligibility 证据**，那么 `main finalize` 的外部 JSON 输入链是否受信就成为必要的事实。该项不是 NEW-019 的 Event/Scope 签发错配，而是**消费阶段将何种结论提升为允许 Git 操作**。
- **待核查工程事实**：真实 Lefthook 可执行入口、JSON 原始生产与保护、`execute_activated_job`/Collect 交付及 Native FAIL 到退出码的传播，以及当前 Hook Coordinator 与 Lefthook 的职责对照。进程 Exit=0 只记录本地执行结果，GitHub/Release 是否实际放行另有证据层次。

### NEW-026 — R1 重新验证的 Assessment 不等同原 R1 计算合同

- **实际差异**：[`_authority_fact`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1540-L1563) 计算原始 PASS 时强制 `evaluated_at >= authority.evaluated_at/effective_at/valid_from`、不过期且未撤销；[`validate_assessment`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L2637-L2827) 对 PASS 只验证 `authority.status`、`revoked_at` 和 `valid_until`，没有重新核对前三个时间。它也只检查 `inputs.contract_sample` 为布尔值，不验证与其他来源/限制一致。
- **最小差异**：用既有 R1 合法 PASS Assessment，保持四事实/Rows 不变；更改 `inputs.authority.evaluated_at` 到 `assessment.evaluated_at` 之后、或将 `inputs.contract_sample=True` 改为 False，重新计算 `assessment_ref`；比较 `validate_assessment` 及 R4/R5 下游结果。注意：**能自算 SHA 摘要不是取得受信签发身份**；真正风险取决于这些消费者是否有额外受信原件约束。
- **依赖**：与 NEW-010 的 Publication 层“成功不绑定真实 PASS”不同，本项涉及 **R1 Assessment 序列化校验与 Producer 原判定不等价**；实际签发者、原件读取方式、当前消费者是否具备来源证明均待核实。

### NEW-027 — R5 Authorized Reevaluation 可使 Assessment 时间倒退

- **源码**：[`_bind_temporal`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L315-L366) 对 `post_at < prior_at` 仅在 Trigger 不等于 `authorized_reevaluation` 时阻断，对 `post_at < trigger.observed_at` 只约束 completed/publication/artifact Trigger。[`_bind_phase_relation`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L208-L275) 对重新评估仅要求同 Phase。
- **现有正例与盲区**：[`test_authorized_reevaluation_requires_same_phase`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment_post.py#L407-L440) 确认可 `verified`，但未改变前后 `evaluated_at` 排序。将合法 prior/post 的时间颠倒，在受控回调接受时检查是否仍 `verified`；同时核对 Reevaluation 是否是“重新计算历史时点”的明确授权类型。
- **待裁决**：如果产品合同允许历史追溯重评估，`post_at>=prior_at` 可能不适用；历史回溯计算与当期新授权应在实际消费者处区分。时间标准、策略来源及实际消费尚未验明，当前不属于已复现生产 BUG。

### NEW-028 — Publisher Approval 可传入非当前时钟判断有效期

- **源码**：[`normalize_publisher_approval`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L220-L321) 以 `clock = now or datetime.now(UTC)` 对 `valid_from/valid_to` 判断；[`authorize_and_publish`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L615-L669)、`workflow_publish_authority`、`verify_publisher_identity` 均允许调用方传入 `now`。现有 Publisher 测试中 Approval 有效期均为 None，没有覆盖这条差异。
- **候选反例**：Approval 原始有效期 `valid_to=t1`，当前实际本地时间 `t2>t1`；以 `now=t0<t1` 同时传入发布路径，并保留独立 Approval Authority 接受签发证明、真实 Actions 环境和合法执行者身份。检查其是否按注入时钟放行。若生产调用固定可信时钟、或独立 Authority 也校验当前时间，应按实证降级风险。
- **时间边界**：测试的可注入时钟与生产可信时钟来源是不同对象。生产调用方对 `now` 的控制、Approval Authority 自身的时间核对，以及真实 Check 发布时的授权窗口尚待验证。

### NEW-029 — R5 Due Authority 无法直接绑定被接受的具体晚到 Assessment

- **代码**：[`reconcile_post_stage` 的 `due_window_authority` 上下文](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L700-L736) 提供 `due_window/due_state/evaluated_at/trigger_observed_at/prior_evaluated_at/post_evaluated_at/lifecycle_identity`，但没有 `post_assessment_ref` 或其原始证据集合。随后只检查返回的 `late_evidence_accepted=True` 来允许 `overdue` 证据。[既有测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment_post.py#L229-L253) 验证 Late Authority True/False 的影响，没有检查 A/B 同时间不同 Assessment 的区分。
- **差异案例**：构造两份通过、Profile/Candidate/Phase/时间相同但具体 Obligation/Evidence 引用不同的合法 Post Assessment A/B；将 Due Authority 授权 A 的 Late Evidence，而触发链消费 B。由于回调没有 B 的具体 Ref，存在无法凭当前 Context 区分的风险。Trigger Authority 的 Context **确实**含 `post_assessment_ref`，所以不能说所有 R5 外部权威都失去此身份。
- **关联**：与 NEW-001 的样本标志传播、NEW-013 的 Trigger Artifact 归属不同；这里是 **Due Authority 对 Late Evidence 的逐件授权粒度**。未验实际 Authority、未证生产误放行。

### NEW-030 — Non-Check Evidence 的验证者身份匹配不等于真实签发认证

- **权威需求**：[Issue #61 的权威分层](https://github.com/dymzz/engineering-ci/issues/61) 明确要求消费“各领域非 Check 原生证明”；[R1 设计](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/docs/architecture/required-assessment.md#L46-L50) 宣称校验独立 Verifier Authorization，但不能据此默认验证真实签发已经发生。
- **实现**：[`_validate_non_check_entry`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1486-L1539) 只验证 Verifier/Authorization/Rule/Evidence Ref 结构；[`_non_check_evidence_usable`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L2095-L2130) 只比较 Ref 相等、时间和 Candidate Subject；[`_non_check_obligation_row`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L2158-L2247) 在所有可用 Evidence 为 `satisfied` 时授该义务满足。没有 Verifier Issuer 调用、原件加载/验签，也未核对 `evidence_ref.sha256` 实际对应出具的内容。
- **已有正例证据**：[`test_authorized_non_check_evidence_satisfies`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment.py#L908-L931) 中，构造与义务匹配的 Evidence Document 就可得到 `status=passed`。这证明合同当前接受了自带元数据的结果，**但不能推论部署环境允许不受信用户注入该文档**。
- **后续反例**：对相同有权 Obligation 使用同一 Scope/Subject，分别传真实领域 Verifier 签发的 `satisfied` 和外部自造同字段/不同实际 Result Ref 的 `satisfied`；追查 R1 前真实 Producer/Issuer 与 Evidence 来源认证所在位置，不能基于 Ref 相同认定签发同源。与 D-G05 重叠生产真实性前提，但属**具体 Non-Check 消费边界**。

### NEW-031 — Universe 求值时间与 R1 Assessment 求值时间没有显式绑定

- **源码事实**：[`_normalize_evaluation`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L583-L615) 保留 `universe.evaluation.evaluated_at`；[`_validate_universe`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L791-L832) 检查该字段格式并将其置入自摘要；[`assess_required_obligations`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L2449-L2636) 另用外部 `evaluated_at` 作当前评估时间，没有将两时间对账。`_cross_check_universe` 只对 Action/Target/Phase/Ref/Candidate/Source 身份做对比，未检查二者时间关系。
- **反例条件**：正常闭合 Universe 的求值时刻 `t2`，将独立 R1 `evaluated_at` 设为 `t1<t2`（或反之），保持 Profile/Expected/Scope 一致，使用经验证的真实 Non-Check/Check 见证；观察是否不改变 PASS，却将不同求值切面拼接成同一 Assessment。还要验证原 Universe Producer 是否有唯一 As-of、是否只允许重用相同时间快照，不能用自重算摘要冒充权威事实。
- **根因关系**：与 NEW-002（Native Failure `issued_at`）、NEW-005（R1→R4 时序）、NEW-026（R1 序列化重验证）同属时间来源族，但本项是 **#66-B2 Obligation Universe 的求值时间 → #61-R1 使用时间**，属于新的输入接口，必须单独复核。

### NEW-032 — Profile Approval 的签发时间没有进入 R1 当前有效性评价

- **源码**：[`_normalize_authority`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L457-L500) 仅对 `authority.approved_at` 调用 `_datetime` 解析，没有与 `effective_at`、`evaluated_at` 建立顺序；[`_authority_fact`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1540-L1564) 校验 `status/effective_at/valid_from/valid_until/revoked_at`，没有读取 `approved_at`。
- **差异条件**：同一经来源证明的有效 Profile，令 `approved_at=t2 > assessment.evaluated_at=t1`、`effective_at<=t1`，对照 `approved_at<=t1`，比较 R1 Authority Fact。当前可从代码证明 ApprovedAt 仅是记录，不影响允许性。
- **待裁决**：某些有权治理源可能允许明确、可追溯的**追认或回溯生效**；如有此机制应有显式授权与事件/交易时间证明，不可默默以未来 Approval 为早期 PASS。若原合同明确允许此行为，应据实降低本项严重性，不擅自附加更严格策略。

### NEW-033 — G2 执行身份是调用者声明，而非已认证的实际 Evaluator 版本

- **源码**：[`normalize_execution_identity`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L182-L205) 只归一化调用方 `workflow_sha/evaluator_ref/policy_ref`；[`_require_execution_matches_approval`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L355-L381) 将 Workflow SHA 与 Runtime SHA/Approval 对比，但 Evaluator/Policy Ref 仅与 Approval 列表对比；[`authorize_and_publish`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L615-L669) 的 `execution_identity` 是外部入参。 `workflow_publish_authority` 再把这个同样自报值与 Publication 对齐；没有验证实际运行的 Evaluator 二进制/源码内容摘要。
- **现有测试**：[`test_workflow_publish_authority_rejects_mismatched_actual_evaluator`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment_publish.py#L205-L220) 仅模拟调用方把 `execution_identity.evaluator_ref` 置为不受批准版本，从而拒绝；它没有模拟 **实际运行错误版本、但向 Authority 申报受批准版本** 的对抗输入。
- **信任边界**：外部 `approval_authority` 能收到 `execution_identity` 并自行交叉验证真实执行 Artifact/Attestation，当前仓库缺少对应生产证据。不可将“Workflow 版本得到 GitHub Runtime 证明”偷换成“本次真正运行的 Evaluator/Policy 版本已认证”。与 NEW-010 的 Assessment PASS 原件来源不同，也与 NEW-028 的时钟注入不同，属于执行代码来源绑定风险。

### NEW-034 — Dagger Policy 子 Check 的原始证明与 Manifest 交接边界

- **已观察事实**：[Manifest facts](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L162-L193) 要求 `remaining-checks.txt` 的 Dagger 子检查名称；[Manifest 构造](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L274-L334) 据此与 Dagger 基础 Job 生成 `coverage/python_matrix`；[validate](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L393-L447) 检查派生行结构和聚合状态；[Verification](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/verification.py#L172-L197) / [Publisher](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/publishing.py#L114-L156) 重取基础 `CHECKS` Job/Step，不在发布端单独读取 Dagger 子检查原件。
- **已有证据边界**：[release.yml](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/release.yml#L132-L211) 由 `verified` Job 生成并上传 Manifest，`publish` Job 按精确 Artifact ID 下载；该交接可能已经为派生字段提供完整可信来源。现有 [生成器测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_release_manifest.py#L190-L202) 验证缺子检查时新 Manifest 不通过；它与“已受信签发 Manifest 被替换”的情况不是同一证明对象。
- **待裁决**：以未包含 Dagger 子 Check 的原始事实和篡改后的序列化 Manifest 构造差异案例，结合真实 Artifact 访问/替换能力、消费者及发布策略，判断是否有实际来源漏洞。仅因下游没有重复执行同一校验，还不足以确认违规；重复验证的必要性、方式与收益尚待证明。

### NEW-035 — Tool Version 记录与 Manifest 来源可信度的关系

- **已观察事实**：[facts](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L162-L193) 收集原始版本文件；[clean_tools](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L141-L160) 检查版本格式、工具集合、排序与唯一性；[Provider](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/publishing.py#L114-L156) 不重新读取工具版本原始文件比对 Manifest 中的字符串。
- **可信边界与合法对照**：原生 Facts 由 `evidence`→`verified` 交付，再以固定 Manifest Artifact 提供给 Publisher。合法、未被修改的受信 Manifest 可以合理继承生产者的验证结论；Tool Version 是审计属性还是发布授权的强制前提也需对照当前合同。
- **待裁决**：版本字段被替换但其他原始事实不变时，是否存在真实可达的未受信写入路径，后续消费者是否把其作为未经认证的授权依据。与 NEW-034 存在共同来源调查面，但是否为同一个必须修复的缺陷尚未证明。

### NEW-036 — 首次 Publish 对过期 Artifact 的复用合同

- **已观察事实**：[artifact_metadata](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L74-L100) 默认对过期 Artifact 返回 None；[published_evidence](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/publishing.py#L114-L156) 使用 `allow_expired=True` 检查原始元数据；[publish_release](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/publishing.py#L213-L282) 首次发布和历史已发布 Release 核验均调用此函数。现有 [过期行为测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_release_manifest.py) 覆盖新 Manifest 构造与历史元数据可读取性。
- **差异条件**：t0 有权生成 Passed Manifest，t1 Artifact 到期，其原始 Run/Attempt/Commit/Digest 仍可核对，t1 再进行首次发布。比较历史只读核验、首次发布与恢复/重试三种路径的现行策略。
- **待裁决**：当期 Profile 对发布时资产可下载性和历史证明有效性的要求、Artifact 类型以及原生 Release 出口的实际消费者。过期本身只确认可访问性状态变化，历史证明是否失效需要合同与独立证据支持。

### NEW-037 — 固定 Artifact 缺 Digest 的能力边界

- **已观察事实**：[artifact_metadata](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L74-L100) 返回可选 Digest；[Manifest validate](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L448-L475) 对固定 Artifact 的名称/ID/URL 及聚合 Check 形状验证，即使 Digest 为 None 也可通过；[Verification](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/verification.py#L172-L197) 与 [Publisher](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/publishing.py#L114-L156) 仅比较已声明的 Digest。
- **相邻验证**：[R4 消费](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L822-L868) 对 publication 且确实适用的 Artifact Obligation 缺 Digest 情况有独立约束；当前 [release.yml](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/release.yml#L132-L211) 没有证明 R4 已成为实际发行前置。
- **待裁决**：真实 GitHub Artifact API 在当前行为下是否会缺失 Digest、当期 Profile 是否要求这些具体 Artifact 字节一致性、真实消费链能否达到该组合。固定 ID/Run/Commit 证明来源身份，Digest 证明字节完整性；两类证明的适用性由现实合同决定。

### NEW-038 — Hook Routing 的调度器及生效配置来源与实际激活事实脱节

- **合同依据**：[Issue #66](https://github.com/dymzz/engineering-ci/issues/66) 要求在真实调度器与实际生效配置确定后，将 `scheduler_ref/effective_config_ref` 及 Job 激活/过滤结果绑定到 Expected；[PR #72 原设计](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/docs/issue-revisions/issue-66-eci-managed-git-hooks.proposal.md) 将 Lefthook 2.2.2 视为成熟调度候选，不把其安装或静态配置声明等同真实 Job 激活。
- **源码事实**：[`DEFAULT_SCHEDULER_REF/DEFAULT_CONFIG_REF`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L36-L45) 固定为 `{"name":"lefthook","version":"2.2.2"}`、`{"name":"hook-job","version":"1"}`；[`record_job`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L129-L178) 由调用者声明 Job 激活及资格；[`build_routing`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L915-L966) 在未获得调度器执行输出时填充两个默认来源标签。[`_normalize_routing`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L640-L695) 仅要求来源字段为字典，[`_reconcile_entry`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L1355-L1370) 将其保存到 Receipt。
- **现有区别性测试**：[`test_finalize_matched_invocation_exits_zero`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_hook_coordinator.py#L378-L415) 直接运行本地 `record_job → execution.execute → build_routing → finalize_invocation`，得到 `matched`/Exit 0，期间没有 Lefthook 实际运行。Check 原生执行是真实模块调用，调度器身份的来源却仅是默认字符串。
- **实际链路边界**：main 文件树没有 `lefthook.yml`；Hook CLI 只有 Event 捕获、`verify-invocation`、`finalize`，没有完整 Hook 安装/调度入口；此交付缺口在 Issue #66 已有记录。#66-C Receipt 明确 `trusted=False/required=False`，因此当前并无 #63 Trusted 或 GitHub/Release 错误放行的证明。NEW-024 是 Attempt Qualification 自报，NEW-025 是本地退出码，两者与本项的 Scheduler/Config Provenance 各有不同证明对象。
- **待裁决**：当前字段是否仅为测试/参考名称，或有真实消费者以此作为调度来源证明；实际 Lefthook 版本、合并配置、Job 激活、Event 关联和过滤结果由哪个受信入口提供。修订方案应依据真实原生调度能力、原始目标和受影响消费者判定，而非预设新增机制。

### NEW-039 — 前置 Command 的 Runner 内部 TypeError 触发隐式再次执行

- **源码行为**：[`execution._run_command`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/execution.py#L1212-L1250) 在传入 `runner` 时先调用 `runner(list(rendered),root)`，将**整个 Runner 调用过程**包在 `except TypeError` 中；任何从 Runner 内部传播的 `TypeError` 都被当成参数签名不兼容，再次调用 `runner(list(rendered),root,None)`。[`run_prerequisite_action`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/execution.py#L1475-L1606) 的 `type=command` 路径调用此接口，并可生产后续 `data/pre_execution_pass` 边的原生前置证据。对于本就接受两个参数、但执行过程中在不可逆操作后抛出 `TypeError` 的 Runner，第二次调用可能重复该操作，且原异常被第二次调用的输出或异常覆盖。
- **可区分性复现**：提供接受 `argv,root,data=None` 的受控 Runner，第一次已记录副作用后抛 `TypeError`，第二次返回成功或另一个失败；观察调用次数、真实输出与 `run_prerequisite_action` 的 Result。再对照仅接受第三个必需参数的 Runner：其首次 TypeError 发生于函数绑定、未实际调用，三参回退应只有一次真实原生执行。当前仅固定源码和调用关系；本轮**未执行新增 Runner 注入/原生 Host 反例**。
- **实际风险边界**：[Dagger `_execute_dag`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.dagger/src/engineering_ci_module/plan_execution.py#L865-L910) 的模块本地命令走 `run_prerequisite_action` 且常为 `runner=None`；此时使用直接 `subprocess.run`，不经过该双调用分支。Host API 显式支持 Runner 适配器（[既有测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_plan_contract.py#L675-L730)），但目前没有证据表明正式内置 Runner 在真实命令已经启动后会触发内部 TypeError。需要根据真实调用端与副作用性质评估严重性。
- **工程判断**：根因是用异常类型猜测调用签名，未区分参数绑定失败和执行期错误。现有 Runner 可能具有二参或三参形式，修订时应以其真实公共契约和调用证据确定适配方式；不因这个局部异常分支推断整个 Dagger/Host DAG 重复建设。

### NEW-040 — 导出的 Dagger 报告脚本在有可选私有 Token 的 Host 环境直接执行

- **完整执行链**：[Reusable Python CI](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L145-L195) 在可选私有 `ENGINEERING_CI_READ_TOKEN` 非空时将其存入 Host Git Credential Helper（`credential.useHttpPath=false`），而 [Run Dagger Checks](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L229-L315) 在携带同一 Secret 的步骤环境中执行 `dagger api call ... export` 返回的 `ci.py plan`；同一个导出的 `ci.py` 由 [后续 Gate/summary](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L316-L356) 继续执行。导出目录的脚本没有在 Host 执行前与独立批准的 Helper SHA/来源关联。
- **独立于 Dagger Engine 是否继承环境变量**：风险路径发生在 Host 原生 Python，而非必需依靠 Dagger 容器读取 Secret。导出 `ci.py plan` 还能控制 `remaining-selectors.txt`，所以除凭证边界外还涉及剩余 Check 的选择与 Gate 退出值。自行配置的全局 Git Credential Store 直到 [Cleanup](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L452-L463) 才移除；Git [credential.useHttpPath](https://git-scm.com/docs/git-credential) 关闭时同主机的不同仓库路径不参与凭证定位。
- **合法正例与有力反证**：ECI 自身 [`_pytest_bundle`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.dagger/src/engineering_ci_module/main.py#L1401-L1435) 从受信模块文件 [`pytest_ci.py`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.dagger/src/engineering_ci_module/pytest_ci.py) 写出 `ci.py`，既有 [测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_pytest_ci.py) 覆盖其正常行为。[当前 Release `complete-ci` 调用](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/release.yml#L63-L83) **没有传入** `ENGINEERING_CI_READ_TOKEN`；GitHub [可复用工作流](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows) 需要调用方传递/继承 Secrets，且 [fork PR](https://docs.github.com/en/code-security/reference/secret-security/secret-types) 默认无普通仓库 Secret。这些情况下不能声称凭证遭到泄漏。
- **消费者与固定版实证更新（2026-11-18）**：[vesryn 实际 CI](https://github.com/dymzz/vesryn/blob/f3446915e64f0a2f6d8a997e3aee6fbfbbde77b6/.github/workflows/ci.yml#L1-L20) 在 PR 事件使用固定 ECI Reusable `@0f74b2f...`，启用 `pytest_report_function=test-results`，并显式声明传递 `secrets.ENGINEERING_CI_READ_TOKEN`；[消费者 `dagger.toml`](https://github.com/dymzz/vesryn/blob/f3446915e64f0a2f6d8a997e3aee6fbfbbde77b6/dagger.toml) 的入口 Module 为 `source="."`，同时声明 `gitToken` 来源于该环境变量。[消费者本地 `test_results`](https://github.com/dymzz/vesryn/blob/f3446915e64f0a2f6d8a997e3aee6fbfbbde77b6/.dagger/src/vesryn_ci/main.py#L65-L74) 是受检仓库内的函数，虽然 [`dagger.json`](https://github.com/dymzz/vesryn/blob/f3446915e64f0a2f6d8a997e3aee6fbfbbde77b6/dagger.json) 把共享 ECI 依赖钉到 `0f74b2f`，却**没有钉住可随 PR 改动的本地函数代码**。已核对[固定旧版 ECI Reusable](https://github.com/dymzz/engineering-ci/blob/0f74b2f776796b9db83f7a61c0849e504afdbea0/.github/workflows/reusable-python-ci.yml#L87-L260)，同样在 Host 执行导出的 `ci.py plan/gate/summary`，说明 NEW-040 不是只有当前 ECI main 才存在的未被采用路径。
- **运行证明及边界**：读取 [vesryn PR #32](https://github.com/dymzz/vesryn/pull/32) 的 [Run 39994772068](https://github.com/dymzz/vesryn/actions/runs/39994772068)、[PR #29](https://github.com/dymzz/vesryn/pull/29) 的 [Run 39721728911](https://github.com/dymzz/vesryn/actions/runs/39721728911)：`harness / checks` 以及凭证设置、Run Dagger Checks、pytest Gate 步骤均成功；两个 PR Head 的 Workflow/Dagger 配置分别与现有调用合同一致。但凭证设置步骤遇到 Secret 空值也返回 success，故成功运行**不能证明 Secret 实际非空**。未获得仓库 Secret 的元数据、Scope，也未执行有凭证的攻击反例。该仓是私有 Consumer，仅作为 ECI 交付链证据，不是新增审计对象。
- **待证可达性/归属**：需要实际 Caller 的 Dagger Module/Lock 来源、报告函数返回目录可修改性、Secret 非空、当前 PR 代码控制范围及凭证权限。若产物真实来自独立批准且不可修改的模块，则可能是合法执行；若同时满足未受信代码和 Secret 条件，则有 Runner Host 执行/来源隔离问题。尚未运行任何带真实凭证的远端反例，风险等级待评估；不与 #61 G2/G3 或 D-G05 的资产 Policy 许可混同。

## B. 未验证的真实来源与生产接线（**不计作已发现 BUG**）

源自已合并 [D 阶段去重证据](authorization-closure/independent-review/findings.json) 的 `D-G01..D-G05`。这些都是**权威来源/真实交接/服务端事实尚未证明**，不是“已经证明实现错误”：

| 缺口 ID | 实际需要核验的事实 | 原有实施归属 | 当前状态 |
| --- | --- | --- | --- |
| D-G01 | 完整治理/委托/继承/排除域、真实 Phase/Universe Producer、有效期与撤销 | #66-B2、#61-R1 | 未验证 |
| D-G02 | T1/T2/T1-b 受信 Issuer、Local/Actions Receiver、P0 当前授权、跨 Run 复用和 Remaining Work 消费 | #63、#66-P0 | 未验证 |
| D-G03 | 真实 Required Check 发布者、GitHub 服务端 Required 与受控拒绝证明 | #61-G2/G3 | 当前 `main` Branch API 报 `protected=false`、Required Status Checks `off`；可见 Rulesets 列表为空；直接 Protection GET 因权限 403，故服务端完整配置/真实拒绝尚未做端到端验证。比旧“仅 HTTP401，不知是否有保护”证据更具体 |
| D-G04 | R4/R5 真正 Release/Post 消费、同候选 Manifest/Artifact/Policy/Trigger/Due | #61-R4/R5、现有 Release | 未验证 |
| D-G05 | 实际受保护工具、Policy anchor、Source/Revision 与持久授权边界 | 现有 Security Policy/Tool Runtime 集成 | 未验证 |

## C. 全仓调查覆盖与原有授权链结果（非修改门槛）

### C.1 #125 授权链现有基线（只代表该切面）

| 证据集合 | 已有结果 | 仍未核实的范围 |
| --- | --- | --- |
| A/B 依赖图 | 93 节点、86 条边；两份快照共 254 个候选，24 条动态/外部边未解析 | 动态调用与真正受信消费者；这些数字不是全仓覆盖率 |
| C 来源/覆盖 | 13 条受控交接合同、5 个已知缺口、68 条尚未调查 | 合同证明与生产证明的差异 |
| D 独立案例 | 82 个受控 API 案例、77 例符合受控预期、5 例反例归为 D-F01/D-F02 | 受控 API 结论不能替代真实原生/部署链 |
| 当前台账 | D-F01/D-F02；NEW-001～NEW-040；HYP-001～HYP-003；D-G01～D-G05 | NEW 是分级中的源码观察或疑点，并非 40 个已复现生产 BUG |

### C.2 ECI 全仓调查覆盖

| 工程领域 | 已有可复用观察 | 未系统调查或尚未证实 |
| --- | --- | --- |
| 原始需求、架构演变、Issue/PR Review | #125 与 #61/#63/#66 部分合同、历史切片链接 | 全仓需求与实现逐项对账、被错误收缩的需求、旧 Review 遮蔽的错误结论 |
| Hook/Lefthook/本地调度 | NEW-015～025/038；**PR #115 已合并** Event→Run/Attempt、Report、Routing、Final/Exit 结构性 API；真实 main 无 Lefthook 配置 | Issue #66 将已交付 Coordinator/Final 标为未交付，实际调度器路由、安装生命周期与已实现能力边界待重新结算；#115 实际运行时与 Lefthook 的职责对照待完整验收 |
| Dagger、DAG 与工程 CI | Host/Dagger/NEW-039、PR #119 Native Cache、NEW-040 导出 Gate/凭证已核；追加 vesryn 固定 ECI 旧版与两个 PR Run 的真实交付证据 | 区分已固定 ECI 依赖与可随 PR 修改的消费者 Module、Secret 是否实际存在、PR 作者信任边界及所需原生隔离；不扩张独立审计对象 |
| Hygiene、Tach、选择器和工具依赖 | #66 Expected、部分 Native Check 的授权证据 | 真实文件/项目级调度、成熟工具职责、检查与配置的重复实现及实际 Coverage |
| Source/Expected/Native/Admission | #52/#63/#66 原始接口及 D-F01/D-F02；已核对 T1/T2 输出与 R1 的 Kind/Phase/身份/Failure 字段不兼容；带 Phase 的 R1 当前总会阻断 | 已确认缺失 Gate Projection、Phase Producer/有权验证和完整 Universe 交接；实际 Issuer/受信历史复用/当前 Required 生产仍须按真实消费者验证 |
| Tool Runtime、安全 Policy 与权限 | D-G05、NEW-024/030/033 的身份/来源疑点 | Tool Catalog/Binding/Admission 及实际部署权限隔离、重复实现和消费者闭包 |
| OTel、Weaver、Telemetry 与 Terminal | #52 D4、#74/#75 相关授权链引用 | 原生 SemConv/Weaver 行为、Collector/协议、真实根/任务关联与终态 Gate |
| GitHub Actions / Check / 服务端保护 | 已检查 main 四份 Actions Workflow，未调用最终 R1 Publisher；2026-11-18 公开 [main Branch API](https://api.github.com/repos/dymzz/engineering-ci/branches/main) 报 `protected=false`/Required Status Checks `off`，[Rulesets API](https://api.github.com/repos/dymzz/engineering-ci/rulesets) 返回 `[]` | G2 真实 Publisher/Approval 仍缺生产运行；G3 目前可见服务端配置支持“Required 未启用”，但详细 Branch Protection GET 403，仍需对服务端真实拒绝做独立验收 |
| Release、制品、真实交付 | 已核对 `release.yml` 手动发布及 `verified→publish` 真实 Manifest/Artifact ID；发布入口仍未调用 R4；相关 NEW-003～013/034～037 | R4 授权与当前实际发布消费整合、当期有权 Release Profile 及正确复用历史制品的验证范围；原发行保护有效性单独保留 |
| 文档、证据与工程过程 | PR #126～129 的部分文档/依赖索引 | 历史设计/实施偏差、错误验收口径及多轮 Review 的实际纠错效果 |

### C.3 本轮反证与已排除的误报方向

| 调查疑点 | 证据与裁决 |
| --- | --- |
| `selection.full=True` 是否错误遗漏所有 Hygiene Check | [`selection._non_test_check_scopes`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L2447-L2479) 确实对 Hygiene 传 `full=False`；但已有 [`test_full_selection_explicitly_marks_tests_only`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_selection.py#L223-L237) 明确把 full 定义为**测试全集**，Hygiene 保持真实增量适用。当前不存在足以认定漏跑的合同反例，暂不另编号；若实际面向用户的 CLI 承诺全工具全量执行，按该入口的独立证据重新评价。 |
| `execution._execute_with_dependencies` 与 Dagger DAG 是否必然属于重复建设 | [Host `execution.py`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/execution.py#L4083-L4210) 有面向本地命令/Prerequisite 的轻量执行依赖消费；[Issue #86](https://github.com/dymzz/engineering-ci/issues/86) 说明 Dagger `plan_execution._execute_dag` 面向原生 Function/Container/File/Directory 及 Engine 缓存，Host-only 工作不当然应搬到 Dagger。两条路径职责有差异，**尚未发现仅凭两者共存即可证明的重复调度 BUG**；进一步调查实际 Check 是否在两个路径重复执行及共享 Result 是否真实生效。 |
| Workflow Analysis 缓存字典共享是否导致并发 Actionlint 重复启动 | [`workflow_analysis.run_workflow_script_suite`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/workflow_analysis.py#L1516-L1594) 的同键 Actionlint Runner 调用受同一个 `threading.Lock` 保护，且 Shellcheck 版本前置也受锁保护；[Dagger `_execute_dag`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.dagger/src/engineering_ci_module/plan_execution.py#L624-L995) 的 `script_shared_cache` 共享恰好传入上述受控缓存。先前“因为共享 dict 所以缺原子性”的猜测被源码反证，现有具体路径不成立；不同命令键、Dagger 层原生缓存及真实跨 Source 场景仍分别按实际消费者判断。 |

### C.4 已有信息的独立复审：状态漂移、证据分层与系统性未交付（2026-11-18）

本节重新核对 **PR #115/#119 的合并事实、Issue #66/#86 的现行状态、四个实际 GitHub Actions Workflow、#125-C/D 的原始证据级别** 和 NEW-001～039 已记录的源码发现。不是新一轮按编号寻找风险；本节的结果继续接受新的原生运行和反证。

| 重审对象 | 固定原始证据 | 更新后的裁决与影响 |
| --- | --- | --- |
| **Issue #66 的现行交付表述** | [PR #115](https://github.com/dymzz/engineering-ci/pull/115) 于 2026-11-17 05:52:31 UTC 合并，主仓已有 [`hook_coordinator.py`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py) 及 [`tests/test_hook_coordinator.py`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_hook_coordinator.py)；但 [Issue #66](https://github.com/dymzz/engineering-ci/issues/66) 于 12:11:20 UTC 更新后，顶部现行条目仍将外层 Coordinator、Collect、Final Reconciliation、Hook Exit 泛称未交付 | **状态矛盾已确认**：#115 实际交付 Event/Invocation 结构性核对、Run/Attempt、Job 生产/Report 收集、Routing、Final、Exit 的 Python 模块接口；`hook-coordinator` CLI 仅提供 `verify-invocation/finalize`，main 仍无实际 `lefthook.yml`、原生条件调度及安装生命周期。已交付模块接口与真实 Lefthook 生产连接属于不同工作；旧 Issue 混写会诱发重复实施 |
| **Issue #86 的现行交付表述** | [PR #119](https://github.com/dymzz/engineering-ci/pull/119) 于 2026-11-17 09:01:33 UTC 合并 D.1 在线输入刷新和来源绑定原生缓存；[Issue #86](https://github.com/dymzz/engineering-ci/issues/86) 于 12:10:12 UTC 更新，现行表仍描述 #119 Draft/Open 和待合并验收；[设计 plan.md](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/design/execution-graph/plan.md#L1-L5) 仍写“候选实施计划，尚未实施” | **状态矛盾已确认**：已合并的 D.1 代码/正反执行证据有效，剩余应是其未覆盖的真实生产场景及变更后差异，而不是同名 PR 再次合并。现行文档和设计状态分层更新存在缺口 |
| **真实 PR/main Required Publisher** | 当前四份 [Actions Workflow](https://github.com/dymzz/engineering-ci/tree/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows) 没有最终 R1 Required Check 发布；2026-11-18 读取 [main 分支元数据](https://api.github.com/repos/dymzz/engineering-ci/branches/main) 为 `protected=false`、Required Status Checks `off`，可见 [Rulesets](https://api.github.com/repos/dymzz/engineering-ci/rulesets) 为 `[]`；详细 Protection API 仍因访问权限 403 | D-G03 具有**缺生产发布入口 + 当前公开门禁配置未启用**两项独立证据；不能将 403 当作“服务器有保护”，也没有实际验证服务端拒绝非法合并。当前远端配置证据的时点和权限范围应随新的服务端结果更新 |
| **真实 Release 与 R4** | [release.yml](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/release.yml#L137-L214) 通过 `verified → publish` 和固定 Artifact ID 检查执行来源，且 `publish` 是手动触发受控分支；其命令调用原 [`release.publishing`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/publishing.py)，不调用 [`reconcile_release_assessment`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py) | **D-G04 是真实交付缺口**：R4 授权合同与已存在的原生发布保护尚未接线。保留现有 Manifest/Run/Job/Artifact 保障，不因缺 R4 就推断原生 Release 完全无保护，也不把 NEW-034/035 的重复重验推测提升为独立生产漏洞 |
| **#125 原始 86 边证明级别** | [PR #127 Coverage](authorization-closure/coverage/assessment.md) 明确 13 条已验证合同交接、5 个已知缺口、68 条未调查、**0 条来源认证、0 条生产接线证明**；[PR #128 D Findings](authorization-closure/independent-review/findings.json) 共 82 个受控案例、5 个反例归为 D-F01/D-F02 | **39 条 NEW 不是 39 项已证明运行时缺陷**。有些观察被既有测试直接断言，其余为源码条件、外部 Callback 或合同解释。#125 的 86 边索引也不代表整个 ECI 的覆盖率 |
| **D-F01 / D-F02 的事实层级** | [#124](https://github.com/dymzz/engineering-ci/pull/124) 至今 Open/Draft、未合并；D-F01 出现在 #124 候选 Binder；D-F02 由 main 的 T1/T2 原生 Admission 直接送 R1 得到 Phase/Kind 合同错误 | D-F01 为已复现的**未合并候选**缺陷，不能写成 main 当前生产绕过；D-F02 为已复现跨模块 **Gate Projection 未交付**，T1-a 的真实失败输入是抛异常，不是被允许。两者均非完整生产服务器放行证据 |

#### C.4.1 对 39 条 NEW 的证据再分类

| 调查簇 / 原有 ID | 当前可成立的事实 | 影响后续裁决的最小区别证据 |
| --- | --- | --- |
| **R1 身份、裁决与时钟**：002、006、008、009、026、030、031、032；HYP-001/002 | 局部 API/序列化校验行为及部分既有测试存在；多数并非生产授权来源受信结论 | 上游 Issuer、真正评价时间、追认/撤销合同、原始 R1 Producer 与实际消费者 |
| **G2/G3 发布身份**：010、011、028、033 | 公开 Publisher/Publication 的输入校验差异成立，主 CI 尚无真实 G2 最终发布接线 | 当前 Publisher 取得的 R1 原件/身份、Approval Authority 与服务端保护的实际运行 |
| **R4 Release**：003、004、005、007、012、034～037 | Manifest、映射、时序和可选 Digest 的具体处理方式可定位；发布端已有独立原生保护但 R4 未接通 | 当前 Release Profile、已验签 Manifest 的不可变交接、真正受信发布入口和 Release 对各证明字段的义务 |
| **R5 后置**：001、013、027、029 | 源码中的 Trigger/Due/时间/样本组合可疑条件已明确 | 受信 Trigger/Due Issuer、当前与历史重评价合同和具体后置消费者 |
| **T1/T2/Native Result 与失败保留**：014、017、022、023 | 有些测试已验证局部隔离或排序行为，生产 Report/Attempt/Issuer 关系不完整 | 相同实例的合法重试、原生失败何时属于共享 Required 前置、真实 Producer 数据 |
| **#66 Hook/Receipt/调度**：015、016、018～021、024、025、038；HYP-003 | 本地结构性 Receipt 与部分 Hook API 已存在；#115 的模块能力不能代替真实 Lefthook Job 激活和 Effective Config | 已安装 Hook 的真实执行来源、Job/Attempt 原件、#63 Admission 与当前 Hook Exit 的实际契约。NEW-038 的默认 `lefthook` 字符串属**未绑定元数据**，在 Receipt `trusted=False` 下尚非独立受信授权错误 |
| **Host Runner**：039 | `_run_command` 以捕获 `TypeError` 选择第二次 Runner 调用确有条件重复执行路径 | 实际被注入 Runner 的参数合同、是否存在首次命令产生副作用后抛 TypeError 的合法/生产执行情况 |

以上恰好覆盖 39 项 NEW；其中 NEW-008、014、015 等已有测试观察，与 NEW-039 这样的未执行条件反例证据等级不同。NEW-034/035/038 保留原编号，但当前证据主要证明来源或部署边界，**未建立独立于既有 D-G02/D-G04/#66 Hook 缺口的生产授权失败**。本轮没有基于这批材料新增编号。

#### C.4.2 工程后果与调查优先级

现有明确的交付缺口依次是 **真正有权 Profile/Obligations 生产链**（D-G01）、**T1/T2 受信来源与 R1 Gate Projection**（D-F02/D-G02）、**实际 PR/main G2 最终 Check 与 G3 服务端保护**（D-G03）、**真实 Release 对 R4 的消费**（D-G04）、**Lefthook 原生调度/ECI 接线与 #115 已交付职责边界**（#66、NEW-038）、以及 **Tool Runtime 与环境 Policy 的实际授权来源**（D-G05）。这些是有具体原始需求与缺少实际生产证据的系统性事项，其真实影响并不由 NEW 数量衡量。

已合并模块、已保存 Native Run、测试可覆盖的合法场景继续按现有证据使用。跨模块变更按真实依赖组织；Issue/设计文档的“现行状态”需要以实际 PR 合并/源代码结算，避免后续执行者依据过时任务重复实施或在错误入口修复。


### C.5 下一阶段：逐生产者到消费者的实证追踪（2026-11-18）

本轮固定当前 `main@fc0df2a`，对已登记的 D-F02/D-G01/#66-P0/#86-B 追查原始代码和已有测试，不用组件同时存在、字段不一致或缺少重复校验直接推导生产安全漏洞。本轮不增加 NEW 编号。

#### C.5.1 #63 T1/T2 → #61 R1：精确交接差异

| 证明对象 | 实际上游对象 | 当前 R1 消费合同 | 实际结论 |
| --- | --- | --- | --- |
| Kind/Phase | [`admission.admit_evidence`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/admission.py#L779-L951) 输出 `engineering-ci-evidence-admission`，但无 `phase`；[`admit_historical_reuse`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/admission.py#L1527-L1849) 输出 `engineering-ci-historical-reuse-admission`，无当前 Gate Phase | [`required_assessment._validate_admission`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1341-L1443) 仅接受 Gate Kind、`schema_version=1`、`phase=post_run_evidence_to_gate_projection` | T1 进入 R1 抛 `admission_phase_invalid`；T2 抛 `admission_kind_invalid`；D-F02 的原生受控反例已经证明 |
| Receipt/摘要身份 | T1 保留 `receipt_ref`、原始 `report_refs/job_receipt_refs/result_ids`，T2 合法历史接纳 `receipt_ref=None` | R1 需要可重算的 `admission_ref`，且历史复用必须维持原 Result/来源身份 | 交接缺的并非普通字段默认值：Gate 对应的身份、授权范围和认证来源需要实际证明 |
| 逐实例成功 Witness | T1 `accepted_coverage_by_instance` 含 `admitted/result_ids/origin_status/integrity_status/native_outcome`；T2 含历史 `result_origins/equivalence_basis` | R1 的 [`_coverage_entry`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1231-L1283) 使用 `witness_result_id/raw_result_verified/issuer_verified/origin_trace/applicable_to/source_kind` 等，且要求已接纳项具备 Witness 与来源 Trace | 现有成功 Witness/历史等价性应由可追溯的受信投影携带，R1 单独 Fixture 构造的这些字段不等于真实 Producer 已交付 |
| 可信失败/冲突 | T1 输出 `verified_native_facts/rejected_evidence/unverified_evidence/remaining_work`，T2 保留被授权历史候选及不满足原因 | R1 将 `authenticated_failures_by_instance` 和 `adjudications` 与同一 Expected 实例的成功覆盖并列消费（[`_validate_admission`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1395-L1442)） | 这是一条**失败传播和有权裁决**交接边；仅有 `admitted=True` 或 `remaining_work=[]` 无法表达完整已认证失败事实 |
| 完整 Required 结论 | T1/T2 显式 `required_gate=not_evaluated` | R1 [`assess_required_obligations`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L2449-L2530) 还消费有权 Universe、Expected 与各领域 Non-Check，分别判断四项独立事实 | #63 合同切片可以成立，却仍不能产出可被 R1 原样接受的完整 Gate 证据；D-F02 继续保持已复现跨模块合同错误 |

#### C.5.2 合法 Phase 闭包：已确认的全路径阻断

- [`expected_checks._derive_v2_from_profile`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L1548-L1884) 对含 Phase 的 EffectiveProfile 仍产生 `phase_status="unverified"`，同时把治理 Domain、Definition、Rule、Appplicability 的多项缺口压缩至单一状态/原因。
- [`required_assessment._check_domain_fact`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1565-L1599) 对任何 `scope.phase is not None` 均产生 Phase 证明缺失原因：若为 `unverified`，写 `expected_phase_unverified`；即使传入 `phase_status="verified"`，仍写 `expected_phase_proof_unverified`。当前函数没有认证真实 lifecycle Phase Producer 后接纳合法证明的分支。
- [现有测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment.py#L1562-L1628) **显式断言此行为**：一个 Check 已满足、Universe 的匹配 Phase 和完整义务已验证，仍因 Phase 来源未证而导致 R1 Overall `blocked`。这不是测试遗漏或用户要求的常态阻断；它是 Phase Producer/Consumer 联合接口尚未交付时的刻意安全界限。
- **裁决**：在当前代码里，任何带 Phase 的合法 Required Assessment 尚无法达到真正完整四事实 PASS。此为 D-G01 的**已证明生产能力缺口**，和 D-F01 的 #124 Binder 错误授权属于不同执行边；已经存在的有效 Check/历史原生结果不因此失效。真实有权 Phase 生产、认证以及 R1 接纳位置均在既有 #66-B2/#61 职责内核对；不能把任意调用者自称 `verified` 视为证明。

#### C.5.3 已确认的 Source/执行反证

| 先前疑点 | 实际源码及既有保护 | 本轮结论 |
| --- | --- | --- |
| Index 执行是否用 Worktree 当前内容冒充 Staged Tree | [`selection.index_source_refusal`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L946-L984) 在当前 Index Tree 验证后，另查与 Worktree 的差异、未跟踪文件、skip-worktree 和 assume-unchanged 异常；[`selection.run_tests`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L4323-L4387) 运行前再次执行此拒绝逻辑；[`execution._verify_git_execution_binding`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/execution.py#L277-L301) 使用相同入口 | **“Index 只核树 ID，所以 Worktree 不同也会 PASS”被源码反证**。#66-P0 的真实闭包仍针对更深层工具依赖、环境和未来 Source Handoff 审计，已有 Index Source refusal 保护保留 |
| Dagger 调用 Host 是否重复执行同一批 Tests | [`plan_execution._host_adapter_plan`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.dagger/src/engineering_ci_module/plan_execution.py#L598-L623) 清空 Host Adapter 的 `selection.tests`、限定其 CheckScope；Dagger [_native_results](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.dagger/src/engineering_ci_module/plan_execution.py#L289-L359) 将 Tests 作为独立原生计算，Host 仅执行确实委托的 Check/Prerequisites | **单次执行中必然二次运行测试的怀疑不成立**。仍可追查跨 Plan/缓存/不同 Source 的真实重复工作，但以原生执行记录判定 |
| Dagger Host/Native 路径为什么共存 | [Dagger execute](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.dagger/src/engineering_ci_module/plan_execution.py#L1376-L1435) 根据 `pre_execution_edges` 选择原生 DAG 或普通独立分支/Host Adapter；[`execution.execute`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/execution.py#L4682-L4832) 对前置依赖亦走自己的受控消费入口 | 二者代表不同执行上下文，当前没有确认同一次调用必然重复某 Check 的证据；已有 #86-B/#119 的 Native Result Sharing 与 Source Cache 证据继续在其原范围有效 |

#### C.5.4 对后续实施范围的直接影响

D-F02 的具体缺口现已追到 Gate Kind/Phase、Admission Ref、受信 Witness 来源、独立失败/冲突和历史复用五类必须分别保存的事实。D-G01 则进一步表明 **#66-B2 即使生产 Phase 声明，R1 目前仍没有受信 Phase Proof 的消费成功路径**；二者都会妨碍真实 G2/R4 与已验收合同的正确连接。与之相比，Host/Dagger 重复测试和 Index 读错内容这两条泛化猜测已经有原始源码反证；继续在同样前提上要求重构不会增加工程收益。


### C.6 有权 Profile/Phase/Obligations 的生产边界（2026-11-18）

本轮在当前 `main@fc0df2a` 上对照 [Issue #66-B2](https://github.com/dymzz/engineering-ci/issues/66)、[Issue #61-R1](https://github.com/dymzz/engineering-ci/issues/61)、Source/Selection、R1 源码及阶段/批准来源测试。结论将局部 Phase 适用性、Profile 内容一致性、独立批准认证、完整治理 Universe 和 R1 阶段消费分开记账；不增加 NEW 编号。

| 证明对象 | 已读的原始源码及测试 | 裁决与影响 |
| --- | --- | --- |
| **已有 Phase 过滤** | [`_obligation_matches_scope`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L773-L794) 已按 `phases` 列表过滤当前 Phase；[`_parse_profile_obligations`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L839-L938) 将当前 Profile Phase 应用于 Check/Non-Check/Advisory；[阶段正反测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_expected_checks.py#L2375-L2474) 验证 pre_publish 与 post_publish 的不同投影 | **Issue #66-B2/#61 当前“仅处理 target_refs/actions”的现状描述过期**。已有 Phase 规则过滤不等于独立有权 Phase 求值；真正缺口是源认证、治理域闭包与消费证明，而不是再实现一套列表筛选器 |
| **Profile 局部一致性** | [`build_effective_profile`/`validate_effective_profile`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L264-L420) 计算 Profile/Approval Digest；[`_verify_profile_authority`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L687-L762) 核对 Profile 的标志、Selection Config Policy 和 Governed Rule refs；[原测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_expected_checks.py#L1382-L1505) 在本地 Config 写入与 Profile 相同的 `approval_digest` 后获得 `authority_status=verified` 与 `status=complete` | **已证仓库内 Profile/Rule/Config 的内容一致性**；该测试同时构造 Profile 与配置，并没有独立验证组织/项目实际批准者身份。若消费者将此 `verified` 单独理解为签名批准，就会越过当前证据范围；真实受信来源是否另有保护应通过实际部署/Issuer 核对 |
| **Approval 摘要的 Phase 覆盖** | [`_approval_digest`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L172-L199) 的摘要输入包括 Action/Target/Required/Domain，但**不包含当前 Phase**；[`build_effective_profile`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L264-L329) 将 Phase 写进 `approval_binding.target_scope`，完整 [`profile_digest`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L200-L226) 则包含 Phase；[`_verify_profile_authority`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L696-L710) 另外用 Config Policy Phase 比较 | 相同授权义务定义、不同当前 Phase 的两个 Profile 可共享 `approval_digest`，但完整 Profile SHA 不同。若批准范围只凭 Approval Digest 判断，无法区别 Phase；若独立受保护 Policy Phase 或完整签发范围也被验证，则可提供额外保障。**跨 Phase 错误放行尚未证明**，在 D-G01 记录身份绑定边界 |
| **完整 Obligations Universe** | [R1 `_validate_universe`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L791-L833) 检查来件的结构、条目唯一性、自洽哈希；[`_normalize_authority`/`_normalize_governance`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L457-L547) 规范化已声明 Status/批准时刻/委托域；[`assess_required_obligations`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L2449-L2530) 接收调用者提供的 Universe | R1 当前是外部有权 Universe 消费者，不承担真实批准/签发。已查看的 Source、R1/R4/G2 入口与现有四个 Actions Workflow 没有展现组织/项目完整治理域的实际签发闭环；不因此推断所有外部项目都不存在合法 Producer |
| **R1 受信 Phase 消费** | [`_check_domain_fact`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1565-L1598) 对任意带 Phase 的 Expected Scope 都写入 `expected_phase_unverified` 或 `expected_phase_proof_unverified`；[现有测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment.py#L1562-L1628) 即使 Check 和 Universe 局部证据成立也保持阶段 Gate 阻断 | Phase 来源未认证时的当前阻断是有意行为；但**真实受信 Phase Producer 即使补充数据，R1 仍缺少合法接纳成功路径**。属于 D-G01 生产能力缺口，不能误报为当前生产权限绕过 |

#### C.6.1 根因与已有证据的关系

- **D-G01**：已有 Phase 过滤、Profile 的内容摘要及 Config Policy 一致性验证，不等于有权 Profile 的签发者认证、委托域完整枚举与 R1 对阶段证明的合法接纳；`approval_digest` 与 Phase/完整 Profile 身份的关系是具体调查边界。
- **D-F02**：T1/T2 Native Admission 与 R1 Gate Projection 仍有 Kind/Phase、Admission Ref、成功 Witness、真实 FAIL/冲突和历史结果身份的交接差异。独立 Phase 来源补齐不会自动修复该接口断裂。
- **D-G03/D-G04**：真实 GitHub 最终 Check 与 Release 发布入口的消费证据继续单独验证；源端自洽摘要、Fixture PASS 或 Scope 字段变化不能代替服务端/发布时授权。
- **当前可复用的正例**：已交付的按 Phase 区分 Check/Non-Check 投影、配置/来源摘要校验与受控负例均保留。对 Issue 现状文字的纠正不会推翻这些已核验结果，也不预先限制必要的架构调整。

**本轮证据等级**：Phase 过滤、Approval Digest 不含 Phase、Repo Config 通过局部一致性使 Expected authority_status=verified，以及 R1 带 Phase 的确定性阻断，都有源码或已有测试支持；真实外部 Issuer、跨域审批签发、跨 Phase 生产放行未获新运行证据。


### C.7 治理批准来源是否真正独立：从代码标志追踪到可信根（2026-11-18）

本轮针对 **D-G01 的源认证部分**，对照 [#66-B2](https://github.com/dymzz/engineering-ci/issues/66)、[#61-R1/G2](https://github.com/dymzz/engineering-ci/issues/61) 和当前 `main@fc0df2a`。调查目标是判断哪里证明**对象内容相互一致**、哪里证明**组织/项目确实批准**、哪里证明**当前授权范围没有缺失**。沿用既有 D-G01，不新增同义的 NEW 编号。

| 边界 | 原始代码或既有测试事实 | 结论与有效反证 |
| --- | --- | --- |
| **批准来源标识** | [`expected_checks.AUTHORITATIVE_APPROVAL_SOURCES`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L33-L37) 是允许字符串集合 `org-governance/project-governance/governance-signed`；[`validate_effective_profile`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L512-L551) 验证字符串属于集合、`authority_verified` 为布尔、摘要自洽 | 当前 Schema 对**批准来源名称**有枚举约束，对**批准者身份/权限**没有在此函数内进行外部认证。这是结构准入和真实 Issuer 认证两个不同层次 |
| **本地 Policy 匹配产生 Authority Verified** | [`_verify_profile_authority`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L687-L761) 返回条件为 `profile.authority_verified is True`、同一 Selection Config Policy 的 ProfileRef/Revision/ApprovalSource/ApprovalDigest/可选 Phase 等一致，及适用的 Governed Rule refs；[`test_p1_1_authority_provenance_default_unverified_and_config_policy`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_expected_checks.py#L1382-L1505) 把测试生成的 `approval_digest` 写入临时仓库 `ci-selection.toml`，即得到 `authority_status=verified`、Expected `status=complete` | **“配置/规则一致”是可复现正例**，但此测试没有独立组织批准者签发。若 Config 是受保护、经批准且与受检 PR 分离的权威文件，可成为更大来源认证链的一部分；若 Config 与 Profile 同时受同一不受信候选控制，则二者相等并不足以证明外部批准 |
| **配置文件所属 Source** | [`selection._policy_file_scope`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L624-L638) 把位于仓库根目录内的 Config 标注为 `repo_target_tree`，外部文件标注 `event_external`；[`selection.config_identity`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L684-L736) 绑定文件字节与规范化内容 | 文件归属 Target Tree 和内容 Digest 能证明“读的是哪份文件”，不自动证明“该文件是被组织批准的权威基线”。真实执行环境可以用受保护 Ref/授权 Source 解决，但当前所读代码没有在上述局部接口展示这层验证 |
| **完整 Profile 摘要的来源** | [`_approval_digest`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L172-L199) 是可由持有人重算的规范化 SHA 摘要；[`profile_digest`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L200-L226) 把 Profile 的 Phase 和 `approval_binding` 也绑定进整体内容；[`build_effective_profile`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/expected_checks.py#L264-L341) 可由普通调用方构造载荷 | 这些摘要证明相应声明**内容身份**，不证明自称 `governance-signed` 的来源获得批准。上一节 C.6 已确认 Approval Digest 不直接包含当前 Phase；另有 Policy Phase/完整 Profile SHA 保护，不能只凭单个摘要遗漏就断言跨 Phase 放行 |
| **R1 对完整治理 Universe 的认可** | [`required_assessment._validate_universe`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L791-L833) 校验结构、重复实例与由传入内容重算的 `universe_ref`；[`_normalize_authority` / `_normalize_governance`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L457-L547) 接受并规范化来件中标为 `verified/closed` 的 Status、证明引用和时间；[`_authority_fact`/`_universe_fact`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1540-L1631) 检查状态、时间与闭包标志 | **R1 在这里消费有权事实的声明，不独立验证组织签发者或从外部枚举受管域**。但 [`_cross_check_universe`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L936-L1170) 已把 Universe 与现有 EffectiveProfile、当前 Action/Target/Phase、Expected Check 及 Non-Check 逐项交叉核对，不能反过来声称 R1 完全不检查义务内容 |
| **当前真实出口** | [当前 CI Workflow](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/ci.yml#L1-L100) 接受 PR 事件，检出代码后运行原生 Dagger Checks，但没有调用 Profile/Universe/R1 最终授权；[Release Workflow](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/release.yml) 继续采用已有 Manifest/Artifact 原生保护，R4 未接线 | 目前没有生产 G2/R4 路径把上述自洽声明直接变成有权最终 PASS 的证据。事实结论是 **D-G01 签发与认证闭包未交付，D-G03/D-G04 消费入口也尚未接通**，而不是已实证 PR 通过伪造审批发布了 Required 成功 |

#### C.7.1 对原任务要求的修正

1. **有效的已交付层**：Profile/Config/规则身份、规范化摘要、局部 Phase 适用性和 Expected/Universe 的跨对象成员关系均有真实代码与测试；这些是后续受信来源交接可复用的契约。
2. **未闭合的源认证层**：实际受信治理根、批准权限、受控 Profile/委托域/排除事实的完整取得、版本/有效性及 Evaluation Context×Action×Target×Phase×Ref 的真实签发与失效依据，需要对应的来源证据。对这层的身份约束应从实际审批/受保护 Workflow/受信配置等现存能力比较，而不是预设独立 Policy 微服务。
3. **消费层**：#66-B2 的闭合治理源接入、#63 的证据接纳、R1 的 Phase Proof 成功验证及 G2/R4 真实受控出口是不同接口。局部 `authority_status=verified` 不能替代整个门禁已部署的事实；现有 Phase 阻断仍维持真实未认证时的安全边界。
4. **差异验证对象**：在同一有效 Profile 下分别考察已批准的受保护配置与受检 PR 自行改写的配置，追踪实际使用的基线 SHA、批准主体与当前评估逻辑；对照合法的单项目无委托域、存在委托域及已失效/撤销域。已有字段/Unit Test 可复用，本轮尚未取得真实生产 Issuer 或完成新增组合 API 正反执行。

**审计裁决**：本批证据更准确地支持一个**上游共同缺口**：Profile/规则/Universe 的内容自洽验证已具备，独立的治理批准与域闭包来源证明未在当前已读真实生产链中出现。此缺口由 D-G01 追踪；NEW-032 的批准时间语义、D-F02 的 Gate Projection 以及 D-G03/D-G04 的实际出口仍分别存在，不因“缺签发者”再重复登记同义缺陷。


### C.8 受检 PR 与有权策略来源的隔离事实（2026-11-18）

这次沿 [Reusable Python CI](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L630-L692)、[`selection`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py) 和已实现的 [Security Policy 接纳合同](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/docs/architecture/security-policy.md) 核实真实来源。主要问题不是“缺少文件摘要”，而是**摘要证明哪些字节属于哪个 Tree，与哪些字节获得组织/项目批准，属于两个不同谓词**。

| 来源/交接 | 现有源码或成熟机制 | 本轮裁决 |
| --- | --- | --- |
| **固定 Helper 版本** | [`reusable-python-ci.yml`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L630-L669) 检查调用者提供的 `selection_source_ref` 是完整 40 位 Commit SHA，并从 `dymzz/engineering-ci` 检出此提交、安装对应的 Selection CLI | Helper 的**内容版本被固定**；由哪一方批准选择该 SHA、它是否属于当期允许范围是另一项来源权威事实。固定 CLI SHA 不能认证调用方 Required Policy |
| **消费者 Config 的真实来源** | 同一 Workflow 先 [Checkout Selection Target](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L635-L643)，再将 `selection_config` 作为工作区相对路径传给 [`engineering-ci select --root . --config "$SELECTION_CONFIG"`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L670-L688)。[`selection.main`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L4392-L4555) 从指定 `root/config` 加载本地 TOML | 现有输入在**受检仓库的工作区**，并非一个已经证明来自独立受保护 Ref 的治理批准文件；是否有工作流/平台侧配置保护应依据实际消费者的审批和 Ref 权限证明，而非默认推断 |
| **目标 Tree 文件绑定** | [`selection._ref_config_binding`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L2909-L3001) 对 `repo_target_tree` 比较加载文件字节与目标 Ref Tree 的 `git show <tree>:<path>`；[`config_identity`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L684-L770) 保存路径、SHA、规范化配置及 `policy_scope`；[已有漂移测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_expected_checks.py#L1799-L1880) 可阻断与目标 Ref 不一致的本地文件 | **版本/Source 绑定已证明且有效**；但是同一个候选 Tree 内的 Profile 和 Config 即使都被变更且互相一致，也仍属于**同一被审查来源**。仅比较目标 Tree 不足以得出“候选修改得到独立批准”。这是 D-G01 的实际来源层次，不把现有 Ref Drift 拒绝逻辑误判为缺失 |
| **外部 Config 分类** | [`_policy_file_scope`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L614-L626) 将仓库外的 Config 分类为 `event_external`；[`_ref_config_binding`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L2953-L2999) 给外部文件记 SHA 与 `event_shared`，不对目标 Tree 校验同一个文件路径 | 外部位置与内建 Tree 分离已经实现，但 `event_external` + SHA **也不自动等于授权者已签发**；外部来源如何受到实际环境保护，是后续原生接线可验证的具体对象 |
| **实际 Reusable Workflow 调用层** | 当前 Selection 步骤调用 `select --config`、`execute --plan`，**没有**传 `--effective-profile`；[`selection.main`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/selection.py#L4418-L4522) 仅在显式指定时读取 EffectiveProfile | 这条生产工作流主要运行 Selection/Host Check 现有合同，不等于正在运行完整 EffectiveProfile→Universe→R1。即使 Consumer TOML 有 `[effective_profile]`，它仍不等于完整 Profile/Universe 的实际生产或消费 |
| **仓库内已实现的受信来源技术** | [`security_policy.receive`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/security_policy.py#L692-L750) 对工具资产 Policy 使用保护主机提供的 Anchor、真实 [Release/Manifest/Asset 校验](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/security_policy.py#L624-L691)、持久 Revision State 与回滚拒绝；[架构文档](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/docs/architecture/security-policy.md#L103-L175) 明确独立信任根和使用边界 | **来源身份与版本回滚的成熟实现已经存在**；但作用域是资产 Allow/Deny，而非 `org/project EffectiveProfile`、完整委托治理域或 R1 Obligation。可比较其成熟机制与接口复用价值，但没有当前代码证据说明它已经为 D-G01 自动签发政策；也不由此要求新建平行 Policy 服务 |

#### C.8.1 额外服务端证据：#61-G3 的现行配置

2026-11-18 通过 GitHub 公开读取 [`branches/main`](https://api.github.com/repos/dymzz/engineering-ci/branches/main)：`protected=false`，`protection.enabled=false`，`required_status_checks.enforcement_level=off`，`contexts=[]`、`checks=[]`。[Rulesets 列表](https://api.github.com/repos/dymzz/engineering-ci/rulesets) 返回空数组 `[]`；但直接读取 `branches/main/protection` 返回 `403 Resource not accessible by integration`，无法取得管理级详情，也未进行真实无效合并/服务器拒绝验收。

此证据更新了 D-G03 的状态：**已知的 GitHub 对外元数据表明 main 当前未启用 Required Check 保护，且实际 R1 Publisher 没有 Workflow 接线**。它没有证明所有可能的上级权限控制均不存在，也不表示本次审计执行了远端写入。历史“只因详细 GET 403 就完全无法了解保护状态”不是最准确的证据描述。

#### C.8.2 D-G01 的准确归属与可复用事实

本批没有形成新的独立生产授权绕过反例，而是确认了 **受检 Target Tree、受保护 Policy Baseline、Profile 批准者和 ECI Helper 版本四种身份的实际区别**。

- D-G01 继续负责组织/项目真实治理源的批准、域闭包与当前 Evaluation 作用域绑定；现有 `_ref_config_binding`、Content Digest、Phase 投影及 Security Policy 的 Anchor/Revision/Release 成熟能力可作为事实证据与技术比较对象。
- #66/Reusable Workflow 负责目前已交付的 Source/Selection 真实性，不因“Config 来源不等于批准来源”就推翻已有按 Tree 比对或原生 Source 拒绝功能。
- D-G03 的 GitHub Required 服务器实际配置得到了更强的只读负面证据；R1 Publisher 接线和独立服务端拒绝验收仍有不同的证明责任。D-G04 的 Release 当前原生保护与 R4 合同消费继续单独记账。
- 合法受保护配置可以作为可信来源之一；候选 PR 同时控制 Profile 与用于验证它的 Config，则需要受信基线或有权签发者的外部事实才能区分。这是当前合同的关键未知，不是基于猜测设计某种新协调器。


### C.9 GitHub Actions 原生 PR 事件、写入权限与 G2 目标身份（2026-11-18）

本轮对照当前四份 Workflow、[`required_assessment_publish.py`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py)、[`required_assessment_check.py`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_check.py)、#61-G2/G3 原设计和 GitHub 官方 Actions/Checks 文档。调查目标是区分**实际受检 Commit、Workflow 实际执行版本、发布 Check 的权限主体及有权 Assessment Scope**。本轮保留现有 D-G03、NEW-011、NEW-033，不因同一缺口的跨层表现增加 NEW 编号。

| 边界 | 已证源码/平台语义 | 裁决及真正未知 |
| --- | --- | --- |
| **PR/主 CI** | [`ci.yml`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/ci.yml#L1-L38) 用 `pull_request`/`push`/ `workflow_call` 触发，WorkFlow 级 `permissions: contents: read`；[Reusable Python CI](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L141-L158) 也仅声明 `contents: read`。GitHub 对显式指定权限之外的 `GITHUB_TOKEN` 权限设为 `none`（[官方工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)）；Reusable Workflow 从调用者继承的权限只可降低（[官方复用说明](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)） | **现有读写隔离成立**：普通 PR 代码运行不具备利用这份 `GITHUB_TOKEN` 直接调用正式 `POST /check-runs` 的权限；但现在也没有实际 G2 Required Check 发布 Job。这个未交付属于 D-G03，不能把所有普通 PR Job 增加写权限当作默认前提 |
| **G2 API/凭证来源** | [`required_assessment_check.publish_check_run`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_check.py#L696-L740) 最终使用 `POST check-runs`；GitHub [Create a check run](https://docs.github.com/en/rest/checks/runs) 要求相应 GitHub App/Token 的 `Checks: write`。[`required_assessment_publish.authorize_and_publish`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L615-L669) 要求独立 `approval_authority`、Live Actions 及成功时的执行代码身份匹配；[现有测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment_publish.py#L238-L285) 使用可控回调和 `FakeGitHub`，没有真实服务端 Check 发布 | 现有合同设计的受信 Authority 与写入授权边界清楚；**受控 Publisher 的实际 Workflow/安装 App/写入凭证/最终服务器 Check 仍无生产交付证据**。调用方是否能够提供经批准的 App 与 Scope，需要真实配置而非单元测试证明 |
| **真实 PR Head vs Merge Commit** | GitHub [`pull_request` 事件文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) 指出 `GITHUB_SHA` 是 `refs/pull/N/merge` 上的**测试合并提交**；PR Head 应从 `github.event.pull_request.head.sha` 取得。[G2 `actions_runtime_identity`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L136-L172) 将 `GITHUB_SHA` 放入名为 `head_sha` 的字段；[`publication_context_from_actions`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L377-L460) 默认生成 `event_head`、同一 SHA 的绑定 | **身份语义存在未明确区分的两种 Commit**：如果 R1 实际评估 PR Head，而 G2 按 Merge Commit 发布，[`_bind_assessment_target`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_check.py#L218-L239) 将拒绝 Candidate Commit 不同；如果当前阶段明确评估 GitHub 测试合并结果，向 Merge SHA 发布 Check 可能完全合法。真实 Required 规则可能检查 Head 或测试 Merge 的结果，故应由原生当前事件/受检对象/Required Context 明确对账，归 NEW-011 的 Scope/目标绑定调查 |
| **G2 的身份认证覆盖** | [`actions_runtime_identity`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L136-L172) 从 `GITHUB_WORKFLOW_REF/GITHUB_WORKFLOW_SHA/GITHUB_RUN_ID/ATTEMPT` 获取运行标识；[`workflow_publish_authority`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_publish.py#L515-L606) 将批准的 Workflow SHA、事件和声明的 Evaluator/Policy Ref 与 Publication 对齐；审批须经过外部 `approval_authority` | GitHub 运行时 Workflow SHA 是可核对的环境身份，但**不是 Evaluator/Policy 实际二进制或脚本的独立证明**。NEW-033 已记录执行身份调用方自报问题；如果批准来源能取得真实运行 Attestation/受信 Artifact，则可以补足验证。现有回调 Fixture 的成功尚无此证明 |
| **Release 单独写权限** | [`release.yml`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/release.yml#L20-L24) 默认 `contents: read`、`actions: read`；仅 [`publish` Job](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/release.yml#L179-L211) 扩展 `contents: write` 并在该 Job 内使用 `GH_TOKEN`。正式 Release 由 `workflow_dispatch` 触发，执行前有 Candidate Prepare，发布前另核 main | 原生 Release 写权限没有混入普通 CI；现有发布依赖、Manifest/Artifact 身份与重试检查有实际保护。它与 G2 的 `Checks: write` 是不同权限，不代表 R4 已连入正式 Release |
| **Post-run Workflow 的受检执行隔离** | [`cicd-telemetry-post-run.yml`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/cicd-telemetry-post-run.yml#L1-L64) 由 `workflow_run: completed` 或手动入口启动，声明 `actions: read`、`contents: read`；Checkout 默认分支 Workflow SHA，而将原 Run Head SHA 当作**只读证据属性**。实际代码在 [后置 Producer](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/cicd-telemetry-post-run.yml#L109-L169) 不以事件中的 PR Head 检出并执行 | **已实现的合法隔离**：PR 源 Run 产生的 Head SHA 与执行 Producer 的默认分支代码不同，不因有 `workflow_run` 就直接宣称提升权限或执行恶意 PR Code；真实 OTLP Endpoint/Auth/Run Attribution 另按证据范围调查 |
| **可选私人读取凭证与 PR Dagger** | [私有凭证设置](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L154-L195) + [Run Dagger Checks](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L229-L315) + [宿主 Gate](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L316-L356) 形成可选 Secret/Global Git 凭证与 Dagger 导出 `ci.py` 的原生代码交接 | NEW-040：已证 Host 直接执行导出的脚本，**未证受检 PR 能控制选定产物且 Secret 在真实 Caller 中非空**。当前 ECI Release 未传可选 Token；fork PR 受 GitHub Secret 限制。Dagger 容器内 Secret 传播不是本项成立的必要条件 |

#### C.9.1 对已有交付差距的影响

- **D-G03** 从泛化“缺 Publisher”细化为：普通 PR/主 CI 是只读执行来源；有权最终 Check 的**独立写入主体**、批准的 Workflow/执行代码来源、R1 Assessment 和实际 GitHub `Check Run` 并未形成可观察的生产链，且 G3 `main` 现有公开 Required 配置仍为 off。其独立服务端拒绝/Source App 证明继续保留。
- **NEW-011** 加入 GitHub 官方 PR Merge Ref 与 Head Ref 不同这一具体反证。仅以 `GITHUB_SHA` 命名为 `head_sha` 尚不足以定性 BUG；真正合同问题是当前受治理 Scope、被评估的 Commit、发布目标 SHA 及实际 GitHub Required Context 是否一一对应。
- **NEW-033** 继续评估 Workflow SHA 对执行代码版本的真实证明范围，外部 Approval Authority 是否取得独立运行身份/Artifact；来源审批与环境字符串一致并不是同一事实。
- **可选 Dagger 读取凭证** 目前仅为需要进一步分析的 Secret 传播边界，未确认违约或恶意外泄，也未据此新建运行时授权机制。已有分 Job、`persist-credentials: false`、低权限 PR 和默认分支 Telemetry 执行保护保持作为有效证据。

**复审结论**：现有 Workflow 使用正确的原生读/写分层；G2 真正缺的是被批准的独立 Check Publisher 接线、准确的 PR Head/Merge/Target 与受信 Scope 映射，而不是把所有 CI 任务变成有写权限。现有测试和合同代码可以支持实施，但它们本身不能代替真实 App/Check 的原生执行和服务端门禁验收。本轮没有对 Issue/Workflow/运行时代码做修改。


### C.10 Dagger 报告产物跨 Host 代码边界与 G2 原生 Checks Publisher（2026-11-18）

此处重新审阅 C.9 的可选私有 Token 结论，区分已证代码路径与未验证的真实利用条件。

| 层次 | 实际证据 | 裁决 |
| --- | --- | --- |
| 可选 Token 与 Host 执行 | [Reusable Workflow](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L145-L315) 设 Host Git Credential Store，并在 Secret 环境中执行 Dagger 导出的 `ci.py plan`；[后续](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/reusable-python-ci.yml#L316-L356) 又执行 `gate/summary` | Host 执行导出代码与 Host 凭证作用域同时存在是明确源码事实；能否由受检 PR 控制脚本、是否传入 Secret 仍需实际调用方证据 |
| 可信 Module 合法对照 | [ECI `_pytest_bundle`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.dagger/src/engineering_ci_module/main.py#L1401-L1435) 由本模块固定 `pytest_ci.py` 生成脚本；[当前 ECI Release Caller](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/.github/workflows/release.yml#L63-L83) 未向被调用 Workflow 传 Secret | 已有正常 Producer 与 Gate 检查可复用；不把任意 Dagger 导出结果自动等同不可信，更不能断言本仓已泄漏私有 Token |
| GitHub 原生 Secret 规则 | [Reusable Workflow Secrets](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows) 明确需调用方传递或继承；[GitHub Secret Types](https://docs.github.com/en/code-security/reference/secret-security/secret-types) 明确 fork PR 默认没有普通 Secrets | 真实触发条件必须包含调用方授权和适用事件；不同 PR 来源的合法对照不可省略 |
| Checks App 的成熟选择 | [GitHub `GITHUB_TOKEN`](https://docs.github.com/en/actions/concepts/security/github_token) 本来是 GitHub App Installation Token；[Actions Job `permissions`](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) 原生支持 `checks: write`，[Checks API](https://docs.github.com/en/rest/checks/runs) 要求相应 App 写权限 | #61-G2 缺的是**可真实运行的受信发布者**及 R1/批准/Scope 到 Check 的完整绑定，不能以“Checks API 必须 App”为由强制再造一个新 App；现有 PR Check Job 只读隔离应保留 |
| 根因与状态 | NEW-040 属于 Dagger 报告产物→Host Python/Secret 的新边界；D-G03 属于最终 Check 发布工作流；NEW-033 属于实际 Evaluator 代码身份 | 这三项作用域不同，不以条件性 Token 风险替代 G2 实际门禁未交付，也不把 G2 失败混同 Host 凭证已泄露 |


### C.11 NEW-040：从源码条件风险到实际固定版消费者与 PR Run（2026-11-18）

本节使用 `dymzz/vesryn` 的只读信息验证 ECI Reusable 的真实消费；**审计对象仍仅 ECI**。

| 证据 | 原始观察 | 重新裁决 |
| --- | --- | --- |
| Consumer 调用 | [vesryn CI](https://github.com/dymzz/vesryn/blob/f3446915e64f0a2f6d8a997e3aee6fbfbbde77b6/.github/workflows/ci.yml#L1-L20) 触发 PR，固定 ECI Reusable `@0f74b2f...`，设置 `test-results` 和 `vesryn-ci:tests`，显式传递 `secrets.ENGINEERING_CI_READ_TOKEN` | 已找到实际消费者；声明传 Secret 不等于它已在该仓库配置、非空或获授权 |
| 共享依赖与本地函数 | [vesryn `dagger.json`](https://github.com/dymzz/vesryn/blob/f3446915e64f0a2f6d8a997e3aee6fbfbbde77b6/dagger.json) 将共享 ECI Module 固定到 0f74b2f；[`dagger.toml`](https://github.com/dymzz/vesryn/blob/f3446915e64f0a2f6d8a997e3aee6fbfbbde77b6/dagger.toml) 指定本地入口 `source="."` 及 `gitToken` 环境来源；[本地函数 `test_results`](https://github.com/dymzz/vesryn/blob/f3446915e64f0a2f6d8a997e3aee6fbfbbde77b6/.dagger/src/vesryn_ci/main.py#L65-L74) 调用共享 `python_test_artifacts` | **固定 ECI 依赖不等于固定消费者本地 Dagger 代码**。当前函数委托共享实现是合法正例，候选 PR 对本地函数的变更可能改变导出脚本来源 |
| 固定旧版仍存在原路径 | [ECI 0f74b2f Reusable Workflow](https://github.com/dymzz/engineering-ci/blob/0f74b2f776796b9db83f7a61c0849e504afdbea0/.github/workflows/reusable-python-ci.yml#L87-L260) 也在 `checks` Job 配置 Host Git Credential Store、向 Dagger 步骤传 Secret、导出报告后直接执行 `ci.py plan/gate` | NEW-040 的代码执行交接不是 ECI 当前 main 才出现的变化，而是有消费者实际采用的固定旧版既有能力 |
| PR 事件真实执行 | [PR #32 / Run 39994772068](https://github.com/dymzz/vesryn/actions/runs/39994772068)、[PR #29 / Run 39721728911](https://github.com/dymzz/vesryn/actions/runs/39721728911) 的 `harness / checks`、凭证 Setup、Dagger、pytest Gate 都成功；分别读取原始 PR Head 版本的 Workflow、Dagger 配置与函数 | **原生执行路径确实跑过**，不再只是静态猜测。Setup 在 Token 为空时同样成功，无法从 Job 成功推断 Secret 非空或已经泄漏 |
| 受信界限与风险等级 | [GitHub PR 事件文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) 区分 PR Merge Ref 与 fork Secret 限制；[Reusable Secrets](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows) 由 Caller 控制传递 | 如果所有有权修改同仓 Dagger Module 的贡献者也获准使用该 Secret，可能是合法信任合同；如果目标是安全执行不受信 PR 而保护 Token，当前证据没有证明 Host 可执行产物与令牌隔离。**未确认真实凭证存在及非授权访问** |

本轮进一步确认 **Workflow Commit、共享 Dagger Dependency Pin、消费者本地 Module、导出 `ci.py`、Host Secret** 是五个不同的身份与权限对象。NEW-040 的“实际调用方是否存在”已获得正证，剩余问题转向“Secret 是否非空、哪些 PR 作者受信、真正的模块来源审批与代码执行边界”。不凭引用 Secret 的 YAML 文本认定已发生凭证泄露，也不以此决定新增框架或重做成熟的 Dagger Gate。


每个领域的“尚未系统调查”表示调查状态，既不是缺陷成立也不是缺陷不存在。发现新证据时原 ID/链接持续更新；实际修订的测试由变更范围与风险决定，有效既有证据继续使用。该覆盖表不规定实现方案、PR 数量、模块修改权限或统一修复顺序。

