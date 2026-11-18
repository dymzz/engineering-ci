# Engineering CI — 统一缺陷发现台账

> **审计状态：发现进行中；禁止将现有发现清单视为全集。** 本文是后续 ECI 源码/架构审阅的唯一**滚动 Markdown 缺陷记录入口**，跨 Issue 去重并保留完整证据。仍属于一次需要继续枚举与验证的审计，**本文件不签发、撤销或放宽任何运行时权限**。
>
> 初始版本：2026-11-18。基线：`main@fc0df2a85c2ebeb9c39cf9ecff588c15b27d0b83`（[#125-D / PR #128](https://github.com/dymzz/engineering-ci/pull/128) 已合并）。旧候选仍单独记作 `#124@4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5`，**不是 main 的一部分**。
>
> 记录归属：本仓的全部**实际调查到的**工程缺陷与架构/集成合同问题；本轮优先沿 [#125](https://github.com/dymzz/engineering-ci/issues/125) 的授权关键路径展开，不把未调查模块写成“无缺陷”。

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
| 跨模块整体根因 | 2 个已复现根因 + 新发现待复核 | 比较全部依赖链的失败传播、约束冲突、重复实现及语义漂移；**先去重再提出一次性接口裁决** | 继续 |

**进入集中修改的条件**：本轮已发现的授权关键出口/入边及其待调查项有可核对的结算；每个根因有受影响全部消费者的枚举、正反例、证据级别和接口边界；彼此冲突的方案已统一裁决。对于仍无法访问的真实服务端/外部来源，记录阻断与权限事实，不虚构“零未知”；也不要求为每次增量修改重新跑无关全仓测试。

本台账只在新发现/新证据/实际修复时更新：保留 ID，标出首次发现与最近复核版本，链接相应原 Issue/PR 与原始日志。**未开始 I01/I02/NEW-001 的任何运行时代码修复。**
