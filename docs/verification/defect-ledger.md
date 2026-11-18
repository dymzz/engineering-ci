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

审计快照始终保留 commit。`#124@4a41b4e` 独立于 `main@fc0df2a`，不是可在同一生产执行中直接拼接的身份。

### 0.2 授权数据链与顺序约束

依赖方向表示 **需要核查的事实流与合同**，不是所有箭头都已在生产环境接通。事件时间至少应区分：`Profile approval/effective/revoke` → `Expected & Source closure (pre-execution)` → `Run/Attempt/Native Result` → `Receipt/Admission` → `R1 evaluated_at` → `GitHub Check/Release` → `post-stage Trigger/Due/evaluated_at`；历史复用单独需要旧事实的来源与**当前**授权。不同 Stage 不允许用后置结果倒填前置 Expected。

| 序位 | Producer / 关键事实 | Validator → Consumer | 涉及的发现 | 已证状态 / 待核实 |
| --- | --- | --- | --- | --- |
| T0 | 有权 EffectiveProfile、治理/委托闭包、Action×Target×Phase×Ref、Obligations Universe；输入 Source 闭包 | #66-B2/P0 → Expected v2/selection；R1 检查前置权威 | D-F01（共享 Producer）、D-G01 | Domain/Phase/时效真实来源未验收；#124 Binder 中的错误解除已复现，但 **不等于 R1 已错** |
| T1 | Execution Run/Attempt、原生执行事实与历史证据/当前授权 | #52/#66-C/#63 T1/T2 → Admission/Reuse Gate 输入 | D-F02、D-G02、NEW-002 | T1/T2→R1 合同断裂已复现；Future-dated failure 采纳仅是待复现时间疑点 |
| T2 | 归一化 Admission、Scope/Instance、Failure、Adjudication、non-check evidence | #61-R1 → 阶段 Required Assessment | D-F02、HYP-001、HYP-002、**NEW-006** | Adjudication 多版本/多签发者同时生效时，选择顺序与冲突语义待验证 |
| T3 | 经 R1 认可的阶段判定 + Approved Workflow/Execution identity | #61-G2/G3 → GitHub Check → 服务端 Required | D-G03 | 服务端保护为 forbidden/not_verified；不可因 CI 绿冒充真实阻断 |
| T4 | R1、当前 Policy、Manifest/Artifact、Provider/Handoff、Run/Attempt | #61-R4 → 真实 Release 发布消费方 | D-G04、**NEW-003～NEW-005** | 授权位与仅身份检查不得混淆；Check 映射必须有已定义语义；Assessment/Release 时序待对账 |
| T5 | 已完成 Run、有效 Trigger/Due、相同 Candidate/Phase/Policy、前后 R1 Assessment | #61-R5 → Post-stage disposition/后续消费 | NEW-001、D-G04 | post-only 样本传播遗漏为静态证据；R5 未证明全生命周期 PASS |
| TX | Tool/Hook 执行权限、批准锚点、真实 Policy revision 和持久状态 | Native Tool Runtime / ECI Security Policy / Hook 路径 | D-G05 | 生产运行环境待证；不能以 Hook Coordinator 源码存在证明安装、选用或受权 |

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
| 跨模块整体根因 | 2 个已复现根因 + NEW-001～NEW-009/HYP-001～HYP-003 待复核 | 已完成本轮两批各四项的代码级排查：R4 Check 映射/身份授权、R1 时间及裁决类型、Hook Exit 实际放行。下一轮须基于各合同及真实 API 差异反例确诊并继续找未调查边；**不据此宣布已找到所有 BUG** | 继续 |

**进入集中修改的条件**：本轮已发现的授权关键出口/入边及其待调查项有可核对的结算；每个根因有受影响全部消费者的枚举、正反例、证据级别和接口边界；彼此冲突的方案已统一裁决。对于仍无法访问的真实服务端/外部来源，记录阻断与权限事实，不虚构“零未知”；也不要求为每次增量修改重新跑无关全仓测试。

本台账只在新发现/新证据/实际修复时更新：保留 ID，标出首次发现与最近复核版本，链接相应原 Issue/PR 与原始日志。**未开始 I01/I02/NEW-001 的任何运行时代码修复。**
