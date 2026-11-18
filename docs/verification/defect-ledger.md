# Engineering CI — 统一缺陷发现台账

> **审计状态：发现进行中；禁止将现有发现清单视为全集。** 本文是后续 ECI 源码/架构审阅的唯一**滚动 Markdown 缺陷记录入口**，跨 Issue 去重并保留完整证据。仍属于一次需要继续枚举与验证的审计，**本文件不签发、撤销或放宽任何运行时权限**。
>
> 初始版本：2026-11-18。基线：`main@fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83`（[#125-D / PR #128](https://github.com/dymzz/engineering-ci/pull/128) 已合并）。旧候选仍单独记作 `#124@4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5`，**不是 main 的一部分**。
>
> 记录归属：本仓的全部**实际调查到的**工程缺陷与架构/集成合同问题；本轮优先沿 [#125](https://github.com/dymzz/engineering-ci/issues/125) 的授权关键路径展开，不把未调查模块写成“无缺陷”。

## 0. 缺陷编号、时间轴、依赖和影响

### 0.1 统一编号与记录时序

**编号不可随 PR 号重新编号：** `D-Fxx` 为 PR #128 已确证原生反例；`NEW-xxx` 是 PR #128 以后发现、有直接源码风险但待差异 API 核实的问题；`HYP-xxx` 为待语义裁决疑点；`D-Gxx` 为缺少实际生产验证的边界（不能计作 BUG）。后续新发现沿已有序列递增，重复根因链接既有 ID 而不新增编号。

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

审计快照始终保留 commit。`#124@4a41b4e` 独立于 `main@fc0df2a`，不是可在同一生产执行中直接拼接的身份。

### 0.2 授权数据链与顺序约束

依赖方向表示 **需要核查的事实流与合同**，不是所有箭头都已在生产环境接通。事件时间至少应区分：`Profile approval/effective/revoke` → `Expected & Source closure (pre-execution)` → `Run/Attempt/Native Result` → `Receipt/Admission` → `R1 evaluated_at` → `GitHub Check/Release` → `post-stage Trigger/Due/evaluated_at`；历史复用单独需要旧事实的来源与**当前**授权。不同 Stage 不允许用后置结果倒填前置 Expected。

| 序位 | Producer / 关键事实 | Validator → Consumer | 涉及的发现 | 已证状态 / 待核实 |
| --- | --- | --- | --- | --- |
| T0 | 有权 EffectiveProfile、治理/委托闭包、Action×Target×Phase×Ref、Obligations Universe；输入 Source 闭包 | #66-B2/P0 → Expected v2/selection；R1 检查前置权威 | D-F01（共享 Producer）、D-G01 | Domain/Phase/时效真实来源未验收；#124 Binder 中的错误解除已复现，但 **不等于 R1 已错** |
| T1 | Execution Run/Attempt、原生执行事实与历史证据/当前授权 | #52/#66-C/#63 T1/T2 → Admission/Reuse Gate 输入 | D-F02、D-G02、NEW-002、**NEW-014～NEW-017、NEW-022～NEW-024** | 进一步核对 Report-wide 终态、Check-level 可采纳性、Plan 一致性与 Attempt Qualification 的来源；不能从局部 PASS 或自声明资格推导当前授权 |
| T2 | 归一化 Admission、Scope/Instance、Failure、Adjudication、non-check evidence | #61-R1 → 阶段 Required Assessment | D-F02、HYP-001、HYP-002、**NEW-006** | Adjudication 多版本/多签发者同时生效时，选择顺序与冲突语义待验证 |
| T3 | 经 R1 认可的阶段判定 + Approved Workflow/Execution identity | #61-G2/G3 → GitHub Check → 服务端 Required | D-G03、**NEW-010、NEW-011** | 需要逐层分清授权发布者身份、真实 Assessment PASS 来源及目标 Scope 绑定；服务端保护仍为 forbidden/not_verified |
| T4 | R1、当前 Policy、Manifest/Artifact、Provider/Handoff、Run/Attempt | #61-R4 → 真实 Release 发布消费方 | D-G04、**NEW-003～NEW-005、NEW-012** | 除 Check/时序外，必须核对 Assessment 原 Target/Action/Ref 与 Release 权限作用域 |
| T5 | 已完成 Run、有效 Trigger/Due、相同 Candidate/Phase/Policy、前后 R1 Assessment | #61-R5 → Post-stage disposition/后续消费 | NEW-001、**NEW-013**、D-G04 | post-only 样本传播及 Trigger Artifact/Attempt 归属未验证；R5 未证明全生命周期 PASS |
| TX | Tool/Hook 执行权限、批准锚点、真实 Policy revision 和持久状态 | Native Tool Runtime / ECI Security Policy / Hook 路径 | D-G05、**NEW-018～NEW-021、NEW-025** | 除激活/签发/实例/Scope 闭包外，结构性 Receipt 能否作为真正的 Git Hook 进程成功依据仍须审计真实输入链和已安装 Hook 配置 |

### 0.3 缺陷影响半径、证据等级与连锁约束

下表把 **实际已证后果** 与 **可能受影响、仍待追踪的消费者**分开。后续每条新增发现都必须补此两列，避免用下游推测扩大 P 级别。

| ID | 最早确认的边界 / 时间条件 | 已证直接影响 | 潜在依赖与波及（**未证实**） | 不能据此做的修改 |
| --- | --- | --- | --- | --- |
| D-F01 | #124 Binder 评估时，原 Producer Domain 不闭合且 Terminal/Phase Witness 有效 | Binder 可返回 `authorized=True`、清空 `reasons`；候选实现局部错误放行 | #66 Expected Validator、执行消费者、#63/#61 是否实际消费该候选输出 | 不先把 R1 判为绕过；不先断言必须重新设计 B2 Schema |
| D-F02 | #63 T1/T2 完成受控 Admission/Reuse 后，交接 R1 同一 Scope | R1 抛 `admission_phase_invalid` / `admission_kind_invalid`，阻断合法互通 | G2、Release 依赖 R1 的后续正例；失败/冲突/复用语义交接 | 不以修改常量、无权转换或让 R1 接受任意 `admitted` 代替真正投影 |
| NEW-001 | R5 prior `contract_sample=False`、post `True`、显式参数 `False` | 源码能确认 post 样本未纳入汇总；**实际 `verified` 尚未 API 复现** | 若任何后置消费者以 `verified` 当受信事实，会受风险影响 | 不因单次代码扫描直接声称 G3/Release 已受攻击 |
| NEW-002 | R1 `failure.issued_at > assessed_at`；历史/当前评估的时间切面待确定 | 代码仅解析 `issued_at`，未在 `_in_scope_failures` 按 `evaluated_at` 过滤；**返回值尚未 API 复现** | R1 误阻断较早阶段；后续 G2/Release 的拒绝与证据有效期解释 | 未确认合同为 `as-of` 前不得删除未来失败或放宽真实 Native FAIL |
| HYP-001 | Candidate `commit/tree/artifact_digest/source_digest` 同值但字段类型不同 | 使用值集合做匹配这一实现事实成立；是否违规未知 | R1 Candidate Scope、Failure in-scope、Universe membership | 不擅自把通用 `candidate_digest` 合同改成某单一字段 |
| HYP-002 | Admission `all_observations_reconciled=False` 且局部受信 Coverage 足够 | R1 验证该布尔字段的**类型**，但未在阶段 Required 判断中直接使用；实际风险未知 | 未关联的 Raw Observation 是否影响 Required 的完整性，需与 #66-C/#63 校验边界联查 | 不简单要求所有附加观测都阻断 Required，先核对完整性语义 |
| NEW-003 | 同一 Manifest 与一个合法但 `release_authorized=False` 的 R4 Envelope | 函数主体没有验证授权位，只比较 Manifest identity；按静态分支可返回 identity | 若未来/现有真实 Publisher 把它当“放行”接口，将跳过独立授权判断；**生产消费者未证** | 不把全部 Manifest 绑定用途错误解释为发布授予；先区分 `bind` 与 `require_authorized` |
| NEW-004 | 明示绑定 `core_coverage_threshold` 或 `consumer_failure_probe`（v1 schema 固定 `required=False,status=not_enabled`）与 R1 satisfied Check | R4 的绑定循环忽略 Manifest `status/required`，只判断 R1 Row；不能证明该绑定代表已执行 Check | Release 对外报告的 `check_bindings` 可能产生无执行见证的“关联”；实际发布绕过未证 | 不盲目将所有 optional manifest checks 设为 Required；明确绑定合同 |
| NEW-005 | `assessment.evaluated_at` 晚于 `publish_context.evaluated_at`，而受控 Policy/Provider/Handoff 接受 | R4 只比较 Phase/commit/run，并将两个时间传递给 callback；本地没有同向先后断言 | 先发生的 Release 可能消费“未来评估的 PASS”，影响撤销、发布链审计与签发时间解释 | 不无依据地规定所有不同 Run 时刻必须相等；只追真实因果关系 |
| NEW-006 | 同 Result 两份有效且不同 Authority/Policy 或 Disposition 的 Adjudication，改变输入顺序 | `_validate_admission` 原样收集列表，`_effective_adjudication` 返回首条，选择的来源归属与输入顺序相关 | R1 失败解除的决策依据可能不确定；外部 Issuer/冲突/先后决策规则未证 | 不直接断言错误 PASS，先核实允许的裁决集合、优先级、撤销/替代语义 |
| NEW-007 | R4 合法 PASS Assessment 与 Manifest，未提供 Check Binding | 代码可在空绑定时返回 `release_authorized=True`；这属于映射缺失事实，而非既有必需 Check 失败 | R1/Manifest 的 Check 身份是否对应当前 Profile 尚未证明 | 不先把每项 Check 强制映射，先验证完整性合同 |
| NEW-008 | 同一 Admission 混合 native_failure/conflict，由单个复合裁决覆盖 | 现有测试直接断言该组合 `status=passed` | #63 签发范围/不同失败类别的独立撤销语义尚待证实 | 不根据 disposition 字符串名称单方面重定义标准 |
| NEW-009 | 同 Result 的 Adjudication 先于 Failure issued_at | 时间关系未在 R1 过滤条件中被比较 | R1 失败解除的真实因果链、历史/当前接纳时间 | 不自行补造事件时间来源 |
| HYP-003 | 同 Invocation 有 Failed 与 Passed 组合 Native Result | Hook Exit 只检查 binding/status，没有显式 Failed 集合否决 | 未授权失败是否能够在成功选取后被覆盖，需按 Check/Attempt/重试合同取证 | 不无差别拒绝合法重试 |
| NEW-010 | Serialized Check Publication `conclusion=success`，将 `assessment_status` 改为 `None`/任意非 blocked 值并重新计算公开摘要 | Validator 未要求成功必须对应 passed；Publisher 的授权层不会取回或校验 R1 原件 | 如果实际入口接收不受信 Publication，可错误发布 Required SUCCESS；目前未证明该入口可被非受信方控制 | 不无依据增设第二种认证 Store；先确定唯一可信 Assessment 来源和边界 |
| NEW-011 | R1 Assessment 同 SHA，但 `refs/action/target/phase` 与 GitHub Check 目标不同 | G2 的绑定逻辑未读取这些 Assessment 字段，仅从 Summary 展示 | GitHub Required 上下文可能被错 Scope 的 PASS 覆盖；受信工作流是否附加该绑定未知 | 不把不同授权 Target 简化为 SHA 相同就可复用 |
| NEW-012 | R1 Assessment Commit/Phase 正确，但 Target/Action/Ref 不符合 Release | R4 只比较 Commit/Phase；Profile snapshot 传入 Policy callback 不含 Action/Target/Refs | Release 可能接纳不适用的 R1 PASS；真实 Policy callback 的查证能力和权威结果未证 | 与 NEW-011 联合追查 Scope 源，但保留不同消费者的验收 |
| NEW-013 | `artifact_available` Trigger Artifact Digest 或 Run Attempt 与候选/真实 Post Evidence 不同 | R5 仅解析 Artifact Digest，回调可见完整 Trigger 而本地未比较；实际 Authority 可能负责验证 | 后置 verified 对应错误 Trigger 的风险，影响后置 disposition 和追溯 | 先确认 Artifact/Run 归属合同与实际 Issuer，避免硬编码无依据的相等关系 |
| NEW-014 | 同 Job/Run/Attempt，选中 PASS 与另一独立 Report/Receipt 中的 Verified Failed | 现有 T1-a 测试得到 `admitted=True` 且 Failed 留在 `observed_diagnostics` | 若后续 R1 投影只消费 Coverage PASS，则可能丢失同执行失败；是否属于不同受信 Attempt/独立 Check 待查 | 不直接把一切历史失败当当前 Fail，需先证明两个 Result 应归属相同有效执行 |
| NEW-015 | 两个 Job Observation 重复相同 Result ID，均合法参与当前 Scope | 已有 Reconciliation 测试证明 `binding_status=matched` 且 `duplicate` 非空，聚合不计重复为 `unreconciled` | 若重复是错误执行归属或独立 Witness 伪造，Hook `exit=0` 的证据资格可能受影响 | 不禁止合法的同一次计算跨 Job 共享，先检查资格和分发证据 |
| NEW-016 | 同一 Invocation 外多余未路由 Job 产生零 Native Result 的有效失败 Report | `_unexpected_observations` 只逐 Result 记录；空列表不会生成 `unexpected` | Hook 对失败 Job 的遗漏、`all_observations_reconciled` 语义不完整；仍需看 Report 状态/Job 资格 | 不默认要求所有无结果 Job 阻断，需查它是否为已激活且当前 Required 受检对象 |
| NEW-017 | 两条不同成功历史候选共享 `historical_scope_id` 和 `historical_expected_check_ref` | T2 对 admitted 列表按仅这两字段排序，平键依赖初始顺序选取 `result_ids` | 相同历史输入集合变更顺序可产生不同 Evidence Identity/Cache provenance，难以稳定追溯 | 不把多个合法等价 Witness 人为判失败；先裁决同键是否应该唯一或有明确 tie-breaker |
| NEW-018 | 同一 `run_ref/attempt_ref/job_ref` 再次 `record_job`，尤其首次 Report Delivered 后 | `jobs[job_ref]=entry` 丢失前次 Production Ref/Delivery，历史激活记录在对象内不可追溯 | Final Reconciliation/Attempt Qualification 可能引用新身份而掩盖旧运行事实 | 不新增 Coordinator/FSM；先检查真实调度是否允许重复声明 |
| NEW-019 | Invocation A 已收集真实 Report，却以 Event/Expected B 的声明调用 Receipt 签发 | 本地仍可将未对账字段写入 `upstream_verified` Receipt | 若后续消费仅比较 Receipt 和 Expected B，会发生来源错归属；外部权威是否防止未知 | 不把 `sha256:` 前缀或自重算 Digest 当签发认证 |
| NEW-020 | 两个同 Run/Attempt Job Ref 一个是另一个字符串前缀 | Attempt 前缀检查不能区分 `job-1` 与 `job-1-extra` 的 `result_instance` | 如错误 Result 进入有权 Delivered Report，Collect 的本地 Job 实例判断可错接 | 当前受控生产链未证可越过 Delivered Report 保护 |
| NEW-021 | 多 Ref 真实 Expected 完全覆盖 Invocation，但 `observations_by_scope` 多一个未知 Scope | Extra Scope 从未送入 `reconcile_expected_results`，不影响已生成 Receipt 与 Exit | 当前 Invocation 的实际失败若被错标 Scope，可能被 Final 静默丢弃 | 区分外来无关数据与真正已分发的受检 Job |
| NEW-022 | 同一完整 Execution Report 含合法匹配 PASS Native，同时 Report 因其他必需 Check/Prerequisite/Artifact Delivery 为 `failed/blocked` | `_execution_report` 对全局状态无一般失败拒绝；选中 PASS 只按单 Check admissibility 进入 `matched`，是否最终 Hook PASS 取决于 Expected 枚举 | 整体 Job/Producer 失败可能未通过 Report→Receipt 传播到需要该前置事实的消费者 | 不将独立可复用的 Check PASS 一律判错；先确定其要求的 Job-level 依赖和 Report 全局失败原因 |
| NEW-023 | ECI 样式 Report 保留 `plan_authorized=True/check_contract.valid`，但删除或设空 `plan_declaration_consistent` | 原生 Reconciliation Report 校验只拒绝显式 `False`，缺失值不进入 `report_reason` | Plan Source/选择一致性无证明时，若外部 Raw Report 被误当受信，可获得结构性匹配 | 不否定由 #52 真实产生且明确 `True` 的 Report；重点核对来源和必要字段合同 |
| NEW-024 | 构造 `attempt_eligibility=eligible` 与形状合法、但不属于当前 Run/Attempt 的 `qualification_ref` | `_job_instance` 未核验 Qualification 绑定，Reconciliation 可将该实例列为 Eligible | 结构性 Witness Selection 可使用无资格证据的 Attempt；#63 外部签发是否拒绝尚未知 | 不建立另一套本地资格存储；检查原始 Scheduler/Issuer 与 #63 的可信链 |
| NEW-025 | Hook Final 消费完整结构性 `matched` Receipt，而 #63/R1 尚未执行 | Hook 仅检查 `status`、`all_observations_reconciled`、Expected completeness，成功则返回进程 0 | 如果此进程零退出被 Lefthook 作为唯一准入，未认证 Report/Qualification 的结构性通过可能误放行 | 不预设本地 Hook 必须承担 Release Governance；确认用户原需求中 Hook 本地执行与生产授权不同职责 |




### 0.4 发现依赖的裁决顺序（**不是立即修复顺序**）

1. **上游真实性先裁决**：Profile/Obligations/Phase/Source 真实来源（D-G01）及与 Binder D-F01 的缺口分别证明；不能在未证实治理域闭合时将任何局部 `True` 推成当前授权。
2. **Producer→Consumer 交接先裁决**：D-F02 所属 #63→R1；同时核对失败 `issued_at`、判决 `decided_at/valid_until/revoked_at` 的时间语义（NEW-002）及全部正常/历史/失败/冲突路径。
3. **身份与完整性先裁决**：HYP-001/HYP-002 的真正合约边界，不能以“静态未使用”直接定义强制保障，也不能以局部测试通过就宣布全链覆盖。
4. **允许性出口最后结算**：R1→G2/G3→R4/Release→R5，验证 NEW-001 的 post-only 样本与真实 Trigger/Due；外部权威缺失时保持独立未验证，不据此盲目修改现有服务端/Release。
5. **将新反例逐条归入稳定 ID**，从原始证据回填时间/身份/消费者关系；一项根因影响多个出口时不拆分成多份相互漂移的改法。

以上 1～5 是 **查证/语义裁决的依赖序列**，不是推迟合理架构规划，更不是已完成覆盖的证明。能够独立调查的链可并行，不能让某个未验证来源代替对其他边的代码缺陷调查。

## 使用口径与状态

- **已复现**：固定版本真实 API、源码/原生执行证据明确展示与既有合同不一致。**源码证明、待 API 复现**：静态条件足以构造可疑放行路径，但未实际运行受控组合输入；不得当成已复现。**待裁决疑点**：有代码事实，缺少合同/有效反例。**未验证生产依赖**：实际接线或来源缺证据，不自动等同 BUG。
- 每项固定 **发现 ID（不使用 PR 编号作为 ID）**、严重性、审计快照、事实/原始证据、可复现输入、反例与合法正例、影响的所有已证消费者和未知边、根因、原有 Issue/PR 归属、裁决/修订状态。补充证据时更新同一条，避免在每次 Review 新开平行缺陷。
- **先继续找，再统一修订**：发现阶段先核对授权出口→生产者的逆向路径和生产者→消费者的正向路径、交叉身份/时间/失败传播、真实来源及动态边。不得因只发现若干 P1 就开始 I01/I02 修订。何时进入修改，依据**已列出口和边的覆盖结算、未知项及反证情况**明确决定；不许以“绝对不可能遗漏”的承诺代替证据。
- 记录不取代 [#125-D 原始冻结证据](authorization-closure/independent-review/README.md)、[findings.json](authorization-closure/independent-review/findings.json)、[#125-C 边索引](authorization-closure/coverage/README.md)、原有领域 Issue。原冻结证据不修改；本台账引用它们并追加新发现。
- 修复与验收状态必须依据实际 HEAD/PR/测试更新；文档本身的标记不构成修复事实。不得写 GitHub 自动关闭关键词，不自动合并或关闭任何 Issue。

## A. 缺陷与跨模块合同问题

| ID | 严重性 | 状态 | 所属快照与原归属 | 简要事实 | 修复状态 |
| --- | --- | --- | --- | --- | --- |
| **D-F01** | P1 | **已复现（受控 API）** | `#124@4a41b4e`；#66-B2、#52-D4/#124 | Domain 未闭合仍被 Terminal/Phase Binder 清除复合缺口，返回 `bound/authorized=True` | 未修复；待统一裁决 |
| **D-F02** | P1 | **已复现（受控 API）** | `main@adeeb4b`；#63 T1/T2、#61 R1 | 实际 Admission/Reuse 输出直接输入 R1，分别触发 `admission_phase_invalid` / `admission_kind_invalid`；为合法交接缺口，不是越权通过 | 未修复；待统一裁决 |
| **NEW-001** | 待按反例定级 | **源码证明、待 API 复现** | `main@fc0df2a`；#61-R5 | R5 的 `contract_sample` 汇总检查只看显式参数和 prior Assessment，**漏查 post Assessment 的样本位**；可能将后置样本标记为非样本且返回 `verified` | 未修复；须独立正反例确认 |
| **HYP-001** | 待裁决 | **待证实疑点** | `main@fc0df2a`；#61-R1 | `candidate_digest` 关联使用 Candidate 字段值集合，可能将不同类型的 Candidate 身份值混同；先核对合同是否授权这种无类型匹配，再构造区分字段的反例 | 未修复，不视作已确诊 |
| **NEW-002** | 待按反例定级 | **源码证明、待 API 复现** | `main@fc0df2a`；#61-R1 | `failure.issued_at` 仅做格式验证，没有参与评估时间过滤；需要证明过去 Assessment 是否被未来 Native Failure 改写 | 未修复，待 as-of 合同核查 |
| **HYP-002** | 待裁决 | **待证实疑点** | `main@fc0df2a`；#61-R1/#63/#66-C | R1 虽接收 `all_observations_reconciled` 却只校验类型，未用于阶段判定；是否需要该事实取决于 Required 完整性与附加 Observation 的合同语义 | 未修复，不视作确诊 |
| **NEW-003** | P1 候选，待消费证据 | **源码确认的消费合同缺口，待 API 复现** | `main@fc0df2a`；#61-R4 | `require_reconciliation_manifest()` 名为需检验“授权 Manifest”，实际只验证 Envelope 结构与 Manifest 身份，允许 `release_authorized=False` 的验证阶段 Envelope 通过身份检查 | 未修复；真实生产调用方未证明 |
| **NEW-004** | 待合同裁决 | **源码可达的错误 Check 绑定，待 API 复现** | `main@fc0df2a`；#61-R4 / Release Manifest v1 | 显式 `check_bindings` 可指向永远 `not_enabled` 的 optional Check；R4 只判断关联 R1 Check 的状态，不判断被绑定的 Manifest Check 状态或 `required` | 未修复；若绑定仅是附加说明则须明确定义，不宣称发布绕过 |
| **NEW-005** | P1 候选，待反例 | **源码确认时间校验缺失，待 API 复现** | `main@fc0df2a`；#61-R4 | R4 没有要求 `assessment.evaluated_at <= publish_context.evaluated_at`；即使两者逆序，也可能在外部 Policy/Provider 回调接受时 `release_authorized=True` | 未修复；真实外部 Authority 是否自行约束尚未知 |
| **NEW-006** | 待裁决 | **源码确认顺序敏感，待 API/策略核查** | `main@fc0df2a`；#61-R1、#63 adjudications | 同一 Result 多条同时有效的不同 Adjudication 允许并存，`_effective_adjudication()` 按输入顺序选第一条；缺少同一结果多裁决优先级/冲突判定 | 未修复；已确认来源选择受顺序影响，不等同证实错误 PASS |
| **NEW-007** | 待裁决 | **源码确认空映射可通行** | `main@fc0df2a`；#61-R4 | `check_bindings=None` 不执行 Assessment Check↔Manifest Check 双向证明；控制测试允许 R4 `release_authorized=True` | 先核对映射是否为强制合同，与 NEW-004 可能同根 |
| **NEW-008** | 待裁决 | **既有测试确认当前交叉类型裁决行为** | `main@fc0df2a`；#61-R1/#63 | 单种 `failure_superseded` 或 `conflict_resolved` 能解除 Native Failure+Conflict 混合结果 | 需上游授权规则裁决，不能直接称错误 PASS |
| **NEW-009** | 待裁决 | **源码确认缺少时序对账** | `main@fc0df2a`；#61-R1/#63 | Adjudication 可以早于它宣称解除的 Failure 的 `issued_at`，R1 本地无先后验证 | 待实测及 `issued_at` 语义裁决 |
| **HYP-003** | 待裁决 | **静态发现，待组合反例** | `main@fc0df2a`；#66 Hook/#52/#66-C | Hook Exit 只检查 Receipt matched，不直接读取未选中的 Failed Native Result；可能涉及合法重试与无效忽略的边界 | 待同 Invocation 多结果验证 |
| **NEW-010** | **P1 候选** | **源码可达，待序列化差异 API 反例** | `main@fc0df2a`；#61-G2 / GitHub Check Publisher | `validate_publication()` 只禁止 `assessment_status="blocked"` 与 `conclusion="success"`；不要求成功对应 `assessment_status="passed"`，正式 Publisher 的授权仅对发布者/执行身份作证，未重新取得实际 R1 PASS | 未修复；需要核查实际 Publication 来源/可信边界 |
| **NEW-011** | P1 候选 | **源码确认的身份映射缺口，待 API 反例** | `main@fc0df2a`；#61-G2/R2 | `build_check_publication()` 对 Assessment 与 GitHub target 只绑定 `candidate.commit`（缺省时可用 event_head），未检查 Assessment `action/target/refs/phase` 是否与被发布的受保护 Scope 对应 | 未修复；同 SHA 跨目标消费者仍待验证 |
| **NEW-012** | P1 候选 | **源码确认的身份映射缺口，待 API 反例** | `main@fc0df2a`；#61-R4/Release | `reconcile_release_assessment()` 只比较 Assessment Commit 与 Phase，没有在 R4 本地核对 Assessment `action/target/refs` 与 Release repository/tag/target 的适用性；Policy 回调所收快照也未包含这些作用域字段 | 未修复；与 NEW-011 同属跨消费者 Scope 漏绑定组，勿重复开发 |
| **NEW-013** | 待合同裁决 | **源码确认接线验证委托，待 API/Issuer 证明** | `main@fc0df2a`；#61-R5 | R5 Trigger 的 `artifact_digest` 仅格式化后传入 Authority 回调，`run_id/run_attempt` 也没有在本地与 Assessment 所引用的原始 Run 证据关联；若实际 Authority 只核验事件存在，可能把错 Artifact/Attempt 当作合法 Trigger | 未修复；必须先确认 Trigger Issuer/Artifact 归属及 Auth 回调真实实现 |
| **NEW-014** | P1 候选 | **既有测试证实允许性行为，风险待裁决** | `main@fc0df2a`；#63 T1-a / #66-C | 同一 Job/Run/Attempt 标签下，跨独立 Report/Receipt 的已认证 Failed Result 保留诊断但 Selected PASS 仍被 `admitted=True` | 未修复；须查是否为同一有效 Attempt 及失败传播规则 |
| **NEW-015** | 待合同裁决 | **既有测试证实 matched 与重复 Result 并存** | `main@fc0df2a`；#66-C / Hook Exit | 相同不可变 Result ID 在两个 Job Observation 出现时，Receipt `duplicate` 记账却不计入 `unreconciled`；可能保持 `matched` 并由 Hook Exit 放行 | 未修复；须查重复计算复用是否受信且合法 |
| **NEW-016** | P2 候选 | **源码路径可达，待真实 API 对照** | `main@fc0df2a`；#66-C / Hook | 未路由的额外 Job Observation 若不含 Native Result，`_unexpected_observations()` 不生成记录；失败但零 Native 输出的 Job 可能不影响 `all_observations_reconciled` | 未修复；需独立失败 Report/Job Receipt 的正反例 |
| **NEW-017** | P2 候选 | **源码确认同身份候选顺序敏感，待 API 复现** | `main@fc0df2a`；#63 T2 | 多个合法 `historical_candidates` 具有相同历史 Scope/Expected Ref 但不同 Result 时，`admitted` 排序只有两项相同键，稳定排序按调用方原始顺序选出不同见证 | 未修复；需确认候选唯一性要求与历史身份归属 |
| **NEW-018** | P1 候选 | **源码确定同 Job 覆盖行为，待调度 API 复现** | `main@fc0df2a`；#66 Hook Dispatch | `record_job` 不拒绝重复 `job_ref`，第二次直接覆盖前次 Eligibility/Production Ref/Delivery 状态 | 未修复；查合法 Retry 与唯一 Job 身份 |
| **NEW-019** | P1 候选 | **源码确定事件/Scope 签发缺少来源对账，现有测试提供行为例证** | `main@fc0df2a`；#66 Hook Producer/#63 T1-a | `issue_job_receipt` 从调用方接收 Event/Scope/Expected Set，仅做形状/前缀检查便写 `upstream_verified`，未与本 Invocation 实际 Event/Expected 对照 | 未修复；查原始 Issuer Authority 是否已独立阻断 |
| **NEW-020** | P2 候选 | **字符串前缀碰撞，真实 Runner 可达性待核对** | `main@fc0df2a`；#52 Native/#66 Hook Collect | `_require_attempt_bound_natives` 用 `startswith` 匹配实例，`job-1-extra` 文字会满足 `job-1` 的前缀 | 未修复；实际 Deliver/Production Ref 另有保护 |
| **NEW-021** | P1 候选 | **源码确定多余 Scoped 输入未进入计算，待多 Ref API 差异反例** | `main@fc0df2a`；#66 Hook/#66-C | `finalize_invocation` 不拒绝输入字典的未知 Scope key，额外 Observation 直接被忽略，Exit 仅依赖 Expected Scope Receipts | 未修复；先确认额外数据属于本 Invocation |
| **NEW-022** | P1 候选 | **源码可达 Report-wide 失败与单 Check PASS 分离，待原生组合反例** | `main@fc0df2a`；#52→#66-C/#63 | `_execution_report` 只全局拒绝 `status=not_executed`/显式 Plan 不一致，却未对 `status=failed/blocked`、Report 级 Artifact Delivery/Prerequisite 失败传递否决；符合局部 Check 的 PASS 仍可被选为 matched | 未修复；须区分独立 Check 可复用与整个 Job 的 Required FAIL |
| **NEW-023** | P2 候选 | **源码确认 ECI Report 缺失一致性断言不拒绝，待真假 Report 对照** | `main@fc0df2a`；#52→#66-C | ECI Report 仅 `plan_declaration_consistent is False` 会被整体拒绝，缺字段/None 不会触发拒绝；同一 Report 内 Checked Native Result 可获 matched | 未修复；实际 Runner 产物通常显式带 True；查是否强制受信原件 |
| **NEW-024** | P1 候选 | **源码确认 Attempt Qualification 仅按形状接纳，待 Issuer/Dispatch 证据核查** | `main@fc0df2a`；#66-C | `_job_instance` 只要求 eligible 时存在一个合法 `qualification_ref` 对象，不验证其 SHA 与 Job/Run/Attempt/Invocation 内容或有权资格签发；可独立自报 eligible | 未修复；这一级自称 structural，不可推断 #63 已承认来源 |
| **NEW-025** | P1 候选 | **源码证明结构性 matched 决定 Hook exit=0，合同意图待确认** | `main@fc0df2a`；#66 Hook Final/#66-C/#63 | `hook_exit_code` 将结构性 Receipt `matched` + 闭包标志直接映射进程成功，即使 `required_gate=not_evaluated`、`trusted_evidence_not_admitted`；若外部 Scoped JSON 并非可信 Dispatch 产物，可能错放行 | 未修复；须查实际 Lefthook 调用、输入来源和本地 Gate 目标 |

### D-F01 — Terminal/Phase 误清除未闭合 Domain

- **证据与输入**：[#125-D assessment](authorization-closure/independent-review/assessment.md)、[findings.json](authorization-closure/independent-review/findings.json)（`D-F01`）；原始案例 `terminal.compound_open_domain` 与 `terminal.compound_original_declared_domain`，详见 [cases.json](authorization-closure/independent-review/cases.json) 与保存的 `pr124-semantic-results.json.gz`。
- **实际结果**：`selection.plan()` 真正导出 `status=unknown`、`effective_profile.domain_complete=False`、`definitions_status=requested_names_complete`，与合法 Terminal/Phase 见证同时出现时，Binder 返回 `bound/authorized=True` 且 `reasons=[]`。合法闭域对照 `terminal.compound_closed_domain` 也应允许 `bound`。
- **定位**：[Producer `expected_checks._derive_v2_from_profile`](https://github.com/dymzz/engineering-ci/blob/adeeb4b15e47072c5091a31c048253f60471ff1b/src/engineering_ci/expected_checks.py#L1770)、[#124 `terminal_expected._terminal_applicability_is_sole_unknown`](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L261) 与 `_partial_is_phase_only`。现有 Producer 已保存部分并存事实，**尚未证实必须重写 B2 Schema**。
- **影响**：已证明仅候选 Binder 的错误授权；共享上游 Expected v2 其他消费者需要查受影响子图，不能直接外推“生产 Gate 已放行”。外部 Issuer/Completed Run/Phase 回调仍为受控替身。
- **后续发现要求**：枚举所有独立缺口（governance/check-domain、definition/rule、applicability、Phase、authority、source、time、Ref）；按合法单域、空域、Phase-only、复合缺口构造反例，核对 #66-B2 Validator 和每个实际消费者，**先决定原始证明对象是否充分，后决定 I01 的改动位置**。

### D-F02 — T1/T2 Producer → R1 接线合同断裂

- **证据与输入**：[D-F02](authorization-closure/independent-review/findings.json)；`interop.t1_pass_to_r1`、`interop.t1_fail_to_r1`、`interop.t2_pass_to_r1`。真实 T1/T2 模块的 API 被调用，原生 Result 是受控模型，Issuer/verifier 回调为替身。
- **实际结果**：T1-a admitted → R1 `admission_phase_invalid`；T2 admitted → R1 `admission_kind_invalid`；认证失败的 T1 也卡在输入形状，**未观察到 Native FAIL 被错误转成 PASS**。
- **定位**：[#63 `admission.admit_evidence`](https://github.com/dymzz/engineering-ci/blob/adeeb4b15e47072c5091a31c048253f60471ff1b/src/engineering_ci/admission.py#L779)、`admit_historical_reuse` 与 [#61 `_validate_admission`](https://github.com/dymzz/engineering-ci/blob/adeeb4b15e47072c5091a31c048253f60471ff1b/src/engineering_ci/required_assessment.py#L1341) 的 Gate 投影不同。
- **后续发现要求**：在改 R1 或 #63 前确认原生产对象、结构 Receipt、受信 Gate Projection、Non-Check、历史身份、失败/冲突/Remaining Work 在所有消费者上的对应关系。不得只修改 kind/phase 或凭 `admitted=True` 自造可信字段；须证明合法 T2 复用无需当前 Native 执行。

### NEW-001 — R5 对 post-only 合同样本的漏检（新发现，待 API 复现）

- **静态证据**：[R5 `reconcile_post_stage`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L582-L634) 的赋值与理由判断只读 `contract_sample`、`prior.get("inputs", {}).get("contract_sample")`；并未读 `post.get("inputs", {}).get("contract_sample")`。后续 [`_envelope_status`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L470) 与 [`_consistency`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L506) 只消费已汇总的样本位。R1 `_assemble` 能在局部四事实为真时令样本 Assessment 的 `status=passed`，同时保留 `inputs.contract_sample=True`。
- **测试盲区**：[现有 R5 测试](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment_post.py#L442) 只检查“prior 和 post 都是 sample，且显式 sample=True”；没有测试 **prior=False、post=True、显式=False**。
- **待运行反例**：用测试中现成 `prior_passed(contract_sample=False)`、`post_assessment(passed=True, contract_sample=True)`、合法 Trigger/PhaseOrder/DueWindow 与受控 authority，调用真实 `reconcile_post_stage(..., contract_sample=False)`；验证是否返回 `status=verified,contract_sample=False`。同时测试 prior-only 与 non-sample 的正例，不扩成全生命周期 PASS。
- **注意**：截至本条记录，API 组合**未执行**；此项只能称“由源码可推导的错误路径”，不能称“已复现生产绕过”。

### HYP-001 — Candidate 身份的无类型值匹配（待语义裁决）

- [`required_assessment._applicable_to_matches`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1188) 使用 `set((universe["evaluation"].get("candidate") or {}).values())` 比对单个 `applicable_to.candidate_digest`；[`_cross_check_universe`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L936) 也对 Obligation candidate digest 做值集合检查。候选类型允许 `commit/tree/artifact_digest/source_digest`。
- **未知**：`candidate_digest` 合同是否有意表示“任一已授权候选身份”，还是应该绑定明确的 digest 类型、对象与作用域。现有 version/tag 错配用例不覆盖 artifact/source 摘要碰巧相等或错类型身份。
- **后续**：先读 #61 身份合同，再以两类不同标识但字符串相同的输入运行差异反例；**没有合同证明与实际错误允许性输出前，不标为确认 BUG**。

### NEW-002 — Failure 发行时间与 Assessment 时间切片脱节（待 API / 合同复核）

- **明确源码**：[`_failure_entry`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1284-L1302) 调用 `_optional_datetime(issued_at)` 但只保留字符串；[`_in_scope_failures`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1798-L1845) 根据 Scope/Action/Phase 决定失败是否生效，**没有比较 `failure.issued_at` 与 `evaluated_at`**；同模块 [`_effective_adjudication`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1779-L1796) 却会排除未来 `decided_at`。
- **条件反例**：在合法且封闭的相同 Assessment 输入中，保留历史合格 PASS 和相同 Scope，把独立失败事实 `issued_at` 改为评估时间之后，观察 `status/blocked_reasons` 是否变化。比较 `issued_at` 早于/等于/晚于 `evaluated_at`，以及旧 Run 与当前 Run 是否支持相同时间解释。
- **尚未知**：R1 是严格“按 assessed_at 的 as-of 截面”还是“用当前知道的全部认证失败做再评价”。先从 #61 的原始时间合同与真实 Producer 查证，不以防御性理由把时间语义任意改成一律忽略/一律阻断。**此条不是已证实生产 BUG**。

### HYP-002 — `all_observations_reconciled` 收到但未参与 Required 消费（待裁决）

- **实际源码**：[`_validate_admission`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1431-L1437) 接收且核查布尔类型；R1 其他位置没有读取该字段。相应 Receipt 也记录 `all_observations_reconciled`，但本阶段四事实只基于实际列举的 Required/Scope/证据。
- **待裁决**：如果它涵盖尚未列举的**当前必需**观察，R1 的 `passed` 可能缺全局执行闭包；如果只是额外不适用的运行数据，直接阻断会误拒合法范围。需从 #66-C Receipt、#63 Admission 的原始定义出发，以“当前 Required 完全满足但存在非 Required 未关联 Observation”与“确有 Required 未对账 Observation”两组对照区分，不能凭一个布尔字段自行扩张门禁要求。

### NEW-003 — R4 Manifest 消费 API 只检查身份、不检查授权位

- **固定代码**：[`require_reconciliation_manifest()`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L242-L250) 先调用 `validate_release_reconciliation(envelope)`，再计算 `bind_manifest_identity` 并比较 `manifest_identity`，**没有 `release_authorized is True` 或 `status/mode` 检查**。
- **已有正证**：[`test_validation_mode_is_verified_but_does_not_authorize_publication`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment_release.py#L207-L222) 证明一个合法 R4 Envelope 可 `status=verified` 且 `release_authorized=False`；现有 `require_reconciliation_manifest` 测试只用真正 `authorized` 的例子，未覆盖这个差异。
- **候选差异 API 反例**：对同一合法验证阶段 Manifest，调用 `reconcile(...,publish_context.mode=validation)` 得 `release_authorized=False`，再传入 `require_reconciliation_manifest`；对照 true 的 Publication Envelope。**身份匹配与可发布权限是两个不同谓词**。必须先枚举实际调用方是否把本接口作为发布保护，否则仅能确认公开 API 的语义模糊，不能报告生产权限被越过。

### NEW-004 — Check 绑定可指向固定未启用的 Manifest Check

- **固定代码**：[`release.manifest`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L290-L300) 强制两个 optional 记录 `required=False,status=not_enabled`；[`manifest.validate`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/release/manifest.py#L393-L410) 允许该固定形态。[`_normalize_bindings`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L251-L271) 不限制 key 到 Required；[`reconcile_release_assessment` 绑定循环](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L772-L815) 只要求关联 R1 Row 的 `status` 属于 `satisfied/not_applicable/not_yet_due`，**不使用已读出的 `manifest_status` 进行判断**。
- **可触发合同路径**：已有 Passed R1 + v1 Passed Manifest + `check_bindings=[{manifest_check_key:"core_coverage_threshold",expected_check_ref:<satisfied-check>}]`，在受控 authority 接受下不会触发该绑定的 `not_enabled` 失败。现有唯一绑定测试只验证 **R1 blocked** 被阻断，不能证明实际 Manifest Check 对账。
- **裁决边界**：若 `check_bindings` 本意是“此 Check 的真正原生/Manifest 执行关联”，当前关联不成立；若只是对外的描述性映射，则需明确 **不构成执行证明**，并检查任何下游是否把它当授权事实。不能将固定 optional 项错误改为 Required 以掩盖映射错误。

### NEW-005 — R4 没有验证 Assessment 与 Release 的因果顺序

- **固定代码**：[`_profile_snapshot`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L331-L356) 保存 `assessment_evaluated_at`；[`_normalize_publish_context`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L117-L169) 正常化 `publish_context.evaluated_at`；[`reconcile_release_assessment`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L673-L905) 检查 Assessment/Manifest Phase、Candidate/Run/Policy 当前授权，但**没有本地 `assessment_evaluated_at <= release evaluated_at` 的因果断言**。将两个时间交给 `policy_snapshot_authority` 不等于 R4 自己已经保证。
- **候选反例**：构造在 `t2` 真实产生且 `status=passed` 的 R1 Assessment；采用合法 v1 Manifest，设 Release `evaluated_at=t1<t2`，受控 Policy/Artifact/Provider/Handoff 仍接受，观察 `release_authorized`。比较正常 `t2<=t1`、授权窗口有效/已失效及跨 Run 的语义。Issuer 可能在生产自行阻止，不能凭源码宣称真实 Release 已在过去获得授权。
- **关系**：与 NEW-002 同属“评估时间切片”审阅族，但前者是 **R1 内 Failure 输入时间**，这里是 **R1→R4 先后因果**，影响不同调用边，**不直接去重**。

### NEW-006 — 同一 Result 的多个裁决按列表先后选择

- **固定代码**：[`_validate_admission`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1397-L1406) 将所有 Adjudication 按 `expected_check_ref` 原顺序收集，不拒绝对同一 Result 的多重重叠决策；[`_effective_adjudication`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1779-L1796) 找到第一个时间有效、未撤销、非 `unresolved` 的记录即返回；[`_in_scope_failures`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1798-L1845) 用该记录的 Disposition/Authority/Policy 解除 Failure。
- **可重复反例**：同一 `scope_id + expected_check_ref + result_id` 的两条有效 Adjudication，使用不同的 `authority_ref/policy_ref/disposition/decided_at`，调换数组顺序（重新计算合法 Admission Digest），比较 R1 `authenticated_failures[].adjudication` 的归属。当前测试覆盖“单裁决解决双 Failure”“过期/撤销裁决阻断”，但未覆盖**同一 Failure 的重叠双裁决**。
- **风险边界**：先确认原 #63 是否只允许唯一有效决策、允许后决覆盖前决、还是允许多签并存；如果 #63 源端已禁止重叠，需要记录具体受信前置依据。R1 静态代码本身会产生**来源顺序敏感**，但两条可解除裁决之间未必造成 PASS/FAIL 变化；不得把这条当作已证明的错误发布。

### NEW-007 — R4 允许零 Check Binding，不能证明 Check 域双向覆盖

- **静态事实**：[`_normalize_bindings(None)`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L251-L270) 返回空列表；[`reconcile_release_assessment`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_release.py#L750-L820) 只循环显式 Binding，没有把 Assessment 全部 Required Check 与 Manifest 所需执行 Check 做**完整双向配对**。已有 [正例](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment_release.py#L179-L244) 使用空绑定也可在受控权限下得到 `release_authorized=True`。
- **独立性**：NEW-004 是绑定了**错误的未启用 Check**；本项是**不提供任何映射仍可能获得授权**。两项可能同属 R4 Check mapping 完整性根因，待全链证据后去重；不能将 R1 和 Manifest 各自 PASS 自动当作“是同一组 Checks”的证据。
- **待裁决合同**：若 R4 规定两个系统只要求各自独立全量通过，零绑定可能是合法设计；若声称 Manifest 各 Check 证明了当前 Profile 的 Required，必须有有权映射/完整性证据。当前只证明空绑定允许授权，不证明生产绕过。

### NEW-008 — R1 Adjudication Disposition 不区分被消除的失败类型

- **固定代码**：[`_in_scope_failures`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1798-L1845) 对 `failure_kind=native_failure/conflict` 都只要求某个非 `unresolved` 的有效裁决，**没有检查 `failure_superseded` 是否用于 Native Failure、`conflict_resolved` 是否用于 Conflict**。
- **现有测试明确接受这种行为**：[`test_two_conflicting_failures_require_an_explicit_resolution`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_required_assessment.py#L2053-L2090) 同时包含 `native_failure` 和 `conflict`，只用一种 Disposition 覆盖两个 Result，参数 `failure_superseded/conflict_resolved` 均断言 `assessment.status=passed`。所以不是测试缺失，而是**被测试固化的当前合同**。
- **待裁决**：先读 #63 的 Adjudication 实际许可语义：是否允许一个授权结果显式覆盖两种失败性质，还是必须按结果类型分别证明。若当前设计有意允许复合处置，这条不能仅凭名称判断为 BUG；若不允许，则影响 R1 失败传播和历史复用授权。归入 R1/#63 权威裁决组，**不直接修改测试**。

### NEW-009 — 未来才发生的 Failure 可被更早 Adjudication 消除

- **代码条件**：[`_effective_adjudication`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1779-L1796) 只比较 `decided_at <= evaluated_at`、有效期与撤销；[`_failure_entry`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment.py#L1284-L1302) 会记录可选 `issued_at`，但是 `_in_scope_failures` 没有要求 `adjudication.decided_at >= failure.issued_at`。例如同一 `result_id` 的裁决早于已认证失败的实际时间，仍可能解除该失败。
- **依赖关系**：和 NEW-002（未来失败被早期 Assessment 消费）共用 `issued_at` 时间语义，但这条关注的是**裁决是否可以在事实产生前被签发**，不是简单评估快照的过滤；和 NEW-006（多个有效裁决的顺序敏感性）分别对待。
- **待对照**：检查授权上游是否允许预先批准具体已知 Result ID、或 `issued_at` 实为接纳时间而非失败发生时间；运行相同 Result / 三种先后组合及不同来源。当前仅源代码证明缺乏因果比较，未运行真实 API 反例。

### HYP-003 — Hook Exit 是否允许一次 Invocation 中失败结果与成功 Witness 并存

- **代码事实**：[`hook_exit_code`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L986-L1020) 只要求 Receipt `status=matched`、`all_observations_reconciled=True`、entry `binding_status=matched/not_applicable`；不直接读 `native_status`。`reconciliation` 的 `matched` 依据 [`native_satisfaction.status=satisfied`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L1599-L1618)，选中的 Result 必须为 Passed；多个观察值中其余 Failed 是否仍导致阻断取决于原生工具需求、Attempt 资格和重试语义。
- **为何不是已确诊**：合法重试可能允许先前 Failure + 最终成功，且独立 Check/Requirement 的组合选择可能不适合“只要看见一个失败就阻断”的规则。必须先用**同一 eligible Attempt、同一 Check、相同/不同 requirement、failed + passed 两个 Result**重放实际 Reconciliation，再核对 #66 Hook 终态/重试合同；不能擅自一律 fail-closed 禁止有效重试。
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
- **条件反例**：同 SHA、同 Phase、合法 Manifest、可信 Provider/Handoff/Policy 回调，但 R1 Assessment 来自不同 Target/Ref/Action；若 R4 仍返回 `release_authorized=True`，即可证明在被消费的 R1 Scope 上缺乏对账。反例必须明确当前合同允许的 Release target/action，不能拿两个本应等价的 Ref 制造假缺陷。
- **去重**：与 NEW-011 共享 **“相同 Commit ≠ 相同授权 Scope”** 的上游语义问题，但 G2 与 R4 分别是 GitHub Check 与 Release 允许性出口；统一接口裁决，保留两条消费者反例，不据此各自新建映射库。

### NEW-013 — R5 Trigger 的 Artifact/Run Attempt 未被本地消费者关联

- **固定代码**：[`_normalize_trigger`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L382-L406) 将 `artifact_digest` 规范化、Run ID/Attempt 解析；全模块随后没有读取 `trigger["artifact_digest"]` 进行 Candidate/Artifact 关系比对，也没有将 Trigger Run/Attempt 与真实 Assessment 原生 Run 身份直接对账。[`_bind_lifecycle`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L276-L316) 只检验前后 Assessment 的 Context/Action/Target/Refs/Candidate 与 Trigger `target`；[`trigger_authority` 的上下文](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/required_assessment_post.py#L667-L694) **包含完整 Trigger 和 lifecycle identity**，因此正确的外部有权 Producer 有机会负责关联。
- **差异条件**：以正常合法 prior/post 和授权 Trigger 为基线，只改变 `artifact_available` 的 Artifact Digest 或 `run_completed` 的 Attempt 身份，保留受控（只检查事件存在）Authority，观察 `status=verified`。然后换真实 Issuer/Verifier 是否建立此关系，判断是 R5 接口合同应明确的验证前提还是消费代码真正缺失。
- **边界**：不是要求所有事件 Artifact Digest 必须等于 Candidate 的某个单字段；可能是另一受治理产物，需要先确认语义映射。与 D-G04 实际 Producer 未验证关联，**不能把受控回调通过直接说成生产缺陷**。

### NEW-014 — T1-a 对同一 Job/Run/Attempt 的独立 Report Failed 视为诊断

- **既有测试直接证明**：[`test_t1a_unrelated_bad_observation_does_not_contaminate_selected_witness`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_admission.py#L742-L764) 构造相同 `job_ref/run_ref/attempt_ref` 标签、但不同 Report/Job Receipt 的 PASS/FAILED；最后断言 `entry["admitted"] is True`、`observed_diagnostics` 非空。该行为被测试固化，不能假称测试未覆盖。
- **代码链**：[`_same_observed_scope`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/admission.py#L471-L480) 只按 Report 摘要或 Receipt 身份共享判断，不比较 Run/Attempt；[`_entry_admission_v2`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/admission.py#L617-L751) 在选中 PASS 可信且 `contaminated=False` 时仍可 admitted，虽对全部已验证 FAILED 写入 `native_failed` 原始 reasons。
- **待裁决**：相同 Job/Run/Attempt 是否依法允许多份有权 Report、对应不同独立执行/最终 Attempt？用真实 Producer 的 Report/Receipt 签发流程、Attempt 终态和 #63 失败投影核对；反例必须保持 Report/Receipt 原生正确，不能只改紧凑 JSON 就推论生产绕过。与 D-F02 同在 #63→R1 影响链，但 **D-F02 是输入不兼容**，本项是 **Admission 选择/失败传播策略**。

### NEW-015 — #66-C 重复 Result 记账未参与完整对账与 Hook Exit

- **既有测试证据**：[`test_duplicate_observation_is_recorded_without_double_counting`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/tests/test_reconciliation.py#L1365-L1390) 将同一个 Result ID 放到两个已激活 Job，得到 `receipt.entries[0].binding_status="matched"`、`receipt.duplicate` 非空。
- **代码链**：[`_receipt_aggregates`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L1717-L1745) 统计 `duplicate` 却只将 unexpected/rejected 加入 `unreconciled`；[`hook_exit_code`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/hook_coordinator.py#L986-L1020) 要求 `matched` 和 `all_observations_reconciled=True`，没有独立拒绝 duplicate。
- **条件反例**：在合法 complete Expected、同时两个 Job/Attempt 都被允许的情况下，调用原生 `reconcile_expected_results` 生成 Receipt，再交 `hook_exit_code`，核对是否 `exit=0`；用合法共享/非法跨 Job 冒用两组 Producer Origin 作对照。**重复不自动等于不受信**，是否必须阻断取决于真正 Result ownership/Qualification 合同。

### NEW-016 — 额外 Job 的空 Native Report 可能在对账中不可见

- **固定源码**：[`_normalize_observations`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L727-L845) 允许 `native_results=[]` 的 Observation；[`_unexpected_observations`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L1629-L1687) 对未路由 Job 仅 `for result in observation["native_results"]` 追加 Unexpected，**空列表没有 Job 级事件记录**；[`_receipt_aggregates`](https://github.com/dymzz/engineering-ci/blob/fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83/src/engineering_ci/reconciliation.py#L1717-L1745) 只看 resulting unexpected/rejected 项。
- **待运行差异组合**：先生成完整 matched Expected/已验证合法 Report；额外增加一个未路由 Job 的已存在且 digest 验证的失败 Execution Report（无 Native Result），核对 `summary.unexpected/status/all_observations_reconciled` 是否保持不变。对照该 Job 包含 Failed Native Result 的情形。不能把“无 Native Result 的无关 Job”与“应当阻断的 Failed Required 执行”混同，必须核对 Job activation 与原生 Report 归属。

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
- **待审查**：实际 Lefthook 配置的可执行入口、谁生成/保护这些 JSON、是否仅消费 `execute_activated_job`/Collect 已交付结果、真实 Native FAIL 是否导致非 0；不得借此又建 Hook Coordinator/FSM，亦不能以本地进程 exit 0 断言生产 GitHub/Release 权限已经开放。

## B. 未验证的真实来源与生产接线（**不计作已发现 BUG**）

源自已合并 [D 阶段去重证据](authorization-closure/independent-review/findings.json) 的 `D-G01..D-G05`。这些都是**权威来源/真实交接/服务端事实尚未证明**，不是“已经证明实现错误”：

| 缺口 ID | 实际需要核验的事实 | 原有实施归属 | 当前状态 |
| --- | --- | --- | --- |
| D-G01 | 完整治理/委托/继承/排除域、真实 Phase/Universe Producer、有效期与撤销 | #66-B2、#61-R1 | 未验证 |
| D-G02 | T1/T2/T1-b 受信 Issuer、Local/Actions Receiver、P0 当前授权、跨 Run 复用和 Remaining Work 消费 | #63、#66-P0 | 未验证 |
| D-G03 | 真实 Required Check 发布主体与 GitHub 服务端强制规则、受控拒绝证明 | #61-G2/G3 | 未验证；历史 GET Branch Protection HTTP401，不能推导保护不存在 |
| D-G04 | R4/R5 真正 Release/Post 消费、同候选 Manifest/Artifact/Policy/Trigger/Due | #61-R4/R5、现有 Release | 未验证 |
| D-G05 | 实际受保护工具、Policy anchor、Source/Revision 与持久授权边界 | 现有 Security Policy/Tool Runtime 集成 | 未验证 |

## C. 发现覆盖与“什么时候才能改”的客观门槛

| 检查面 | 已有证据 | **仍要找什么** | 状态 |
| --- | --- | --- | --- |
| A/B 源码出口与双向图 | 93 节点、86 边；两个快照共 254 候选；24 条动态/外部边显式 unresolved | 对新增/动态授权出口追溯真实调用与消费者；不能以静态搜索没找到证明不存在 | 继续 |
| C 逐边证明层次 | 13 条受控交接合同有直接证据，5 条已知缺口，68 条未调查 | 按受影响的允许性出口核对直接来源、交接、身份、时间和失败路径 | 继续 |
| D 独立三视角 | 82 个定向 API 案例，77 符合预期、5 个反例归并 D-F01/F02 | 新发现 NEW-001/HYP-001 的差异反例；扩大真实边/实际消费路径；别把 82 当穷尽 | 继续 |
| Runtime / Native / Server | 本次只读 GitHub Run/Check/Artifact 与 G3 可见性；无生产授权 PASS 证据 | 实际 Publisher、Release、受信 Issuer 与完整 Required Gate 的真实边界 | 继续 |
| 跨模块整体根因 | 2 个已复现根因 + NEW-001～NEW-025/HYP-001～HYP-003 待复核 | 本轮对 Report-wide 状态与单 Check PASS、Plan 一致性缺省、Attempt Qualification 来源及 Hook Exit 提升的四条路径提出不同的可核查反例；关联 #52→#66-C→#63→Hook。查实来源/原始合同后再统一裁决，不宣称已全仓穷尽 | 继续 |

**进入集中修改的条件**：本轮已发现的授权关键出口/入边及其待调查项有可核对的结算；每个根因有受影响全部消费者的枚举、正反例、证据级别和接口边界；彼此冲突的方案已统一裁决。对于仍无法访问的真实服务端/外部来源，记录阻断与权限事实，不虚构“零未知”；也不要求为每次增量修改重新跑无关全仓测试。

本台账只在新发现/新证据/实际修复时更新：保留 ID，标出首次发现与最近复核版本，链接相应原 Issue/PR 与原始日志。**未开始 I01/I02/NEW-001 的任何运行时代码修复。**
