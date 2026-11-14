# CI Provider Adapter 与 Action-specific Gates

**状态：目标接口，尚非已交付统一 Provider API。** [总览](README.md)

## 1. 权威与接口
项目/组织的**当前生效工程 Profile** 根据 Context × Action × Target 决定 Required。ECI #63 对账原生执行/可信复用与 Expected Coverage；#61 决定 Required/Release 授权并连接平台保护规则。Selector、Knowledge RDF 查询及 GitHub Job success 不能自行删减 Required。

Provider 的可选能力是 `Context`（repo/source/Run/Attempt/环境）、`Dispatch`（请求远端执行）、`Observe`（真实平台状态/时间）、`Artifacts`（取得/保存）、`Publish`（发布 Check/Decision）。本地不要求实现远程能力；不同 Provider 不伪造不支持的状态。

### 1.1 Profile → Expected Requirements → Plan → Assessment（目标交接）

当前并无已交付的统一 Profile→Expected API；以下是**拟议的跨组件固定交接**，不得写成现有 `selection.plan()` 字段。生产/选取责任与最小核验如下：

| 输入/引用 | 生产者／选择者 | 消费与拒绝规则 |
|---|---|---|
| `profile_ref`、`profile_revision`、批准来源 | 项目/组织工程策略权威；项目侧配置或合法授权的选择机制 | 固定版本与批准依据；未知、被撤销或不受信的 Profile 不得授予 Required PASS |
| `effective_from/to` 或有来源的有效性条件 | 同一策略权威 | 以本次动作对应的有效性时点/条件核验；不得用当前 latest 偷换历史运行策略 |
| `context / action / target_ref` | 实际调用方、Git Source Binding、Provider（适用时） | 例如 local、PR merge、Run/Attempt、post-completion、Release；目标引用须指向实际被检 Source/制品，不凭 HEAD 名称推断 Merge Tree |
| `source_ref / provider_run_ref / attempt`（按需） | ECI Git/Source 生产方 #66、实际 Provider #75 等 | Commit/Index/Worktree/Merge Tree 与 Run/Attempt 分别核验；没有 Provider 的本地检查不要求 GitHub ID |
| `required_obligations` 与 `allowed_inapplicability` | 当前生效的项目 Profile | 包含 Check、被检对象、期望覆盖、条件；不适用必须有明确适用性裁决，不能以 Selector 未选中替代 |
| `expected_set_ref` / 本次绑定回执 | 负责解析生效策略的 ECI/项目接口（拟议） | 固定实际 Expected 集合及来源，供 Selection/Plan 对账、#63 Coverage 和 #61 Gate 引用同一次要求定义 |

**流程：** 按 Context×Action×Target 选取并验证当前有效 Profile → 确定完整 Expected 集合及允许不适用条件 → 与 `selection.plan()` 的实际 Check/Scope 对账 → 将原生结果/受信复用逐项对应 Expected → #61 对同一 `expected_set_ref` 作最终判定。#63 核验受信与覆盖，不生产项目 Required；Knowledge RDF 查询零行不得删除已生效义务。

**拒绝/降级：** Required Profile 不存在或不可信、有效性/目标绑定无法证明、Expected 缺失、选中集遗漏或不适用无授权时，该动作不能获得完整 Required PASS；局部可执行 Check 可保留自身原生结果，不得全部伪装为失败。Profile 解析或授权状态缺失不强制阻断不依赖该 Profile 的普通本地检查。

### 1.2 CheckDefinition / Expected / Native Result：统一 Profile 交接设计基线

**地位：** 本节已随 PR #92 合并，是 #52/#66/#63/#61 分工与交接的设计基线，**不是已实现的完整 Profile→Expected API**。已交付 #93/#95 的局部 Check/Native Result 与 #96 的局部 Expected 投影继续有效；剩余完整 Profile 派生、#66-C Routing/Observed Binding、#63 受信及 #61 Required 按其真实接口分别交付。设计基线不自动改写原生 Tool 语义或现有已验收行为，具体冲突在所属专题以实际事实修正。

**单 Check 多 Native Result 的已实现接口（PR #98 待验收）：** #52 `evaluate_check_satisfaction(definition, results)` 维护 ToolRequirement/前置关系；`evaluate_expected_satisfaction(results, resolved_expected)` 在**同一次成功 Witness 选择**中检验 Expected 的 Rule/Source/Baseline/Input/Coverage，并返回 `selected_result_ids`、状态和原因。失败、未执行、未选中的结果不为该组合补足覆盖；合法的其他成功候选仍可组成满足条件的 Witness。旧 `results_cover_expected` 布尔入口保持可用，不能另建第二套 DAG/Result 选择器。默认最终 Check Coverage 取声明依赖图的终端 Requirement，明确 `coverage_by_requirement` 时按指定受控 Requirement 逐项核对，不把 Inventory 等前置数据误当最终扫描结果。

**#66-B/C 执行前后绑定：** #66-B 在相关 Job 之前产出不变的 `expected_check_ref`、`expected_set_ref`、`scope_id` 和带未知项的预投影；#66-C 在保持该 Expected 不变的前提下，依据来源有权的规则/配置、#66-P0 Source 物化、真实 Job/Result 输入和比较基线形成独立 Observed Binding Receipt。Receipt 关联上述三个身份、解析后的调用参数与原生 Witness 身份、验证的来源/摘要、缺失项和原因；只有来源与约束等价已证明时调用 #52 严格组合判定。Result 本身只能证明实际观察，不能凭其 Source、Rule 或 Input 值倒造预期策略。未知 Source/Rule/Closure 为 unknown/insufficient，保留真实已执行事实；#63/#61 继续独立裁定可信及 Required。

**唯一规范性策略来源：** 对某次 Context×Action×Target，项目/组织有权生效的 Engineering Profile 决定 Required Obligations 与允许的不适用条件；受控项目检查配置还可以声明适用的非 Required/Advisory Check。原生标准/工具仍拥有其规则语义，Knowledge 保留已接纳工程知识和领域规则权威；CheckDefinition 是 ECI 对原生 Check 的身份及适配合同，Expected 是固定作用域下的派生事实，**均不是第二份可编辑 Required Policy**。具体输入范围、批准来源及有效时点按 §1.1 固定；不能用 RDF 空查询、Selection 未选中、Hook Job 缺席或结果 PASS 改写策略。

| 交接事实 | 生产/实现位置 | 本阶段应维护的边界 |
| --- | --- | --- |
| 有效 Profile 与 `required_obligations/allowed_inapplicability` | 有权项目/组织策略来源 | 消费者固定相同受控修订、批准依据、生效时点及 Context×Action×Target；#52/#66/#63/#61 不自建第二策略 |
| CheckDefinition / Native Result | ECI #52 + 真实 Native Tool | Check ID/Rule/Tool/真实输入依赖/比较基线、原生执行及 Report 适配；不据此独立认定本次 Required/Expected 全集 |
| Git Source 与执行 Source Binding | ECI #66-P0；Provider 原生身份按对应生产者 | 提供目标 Git 对象/隔离 Source 的可核验事实及实际绑定范围；不把 Tree 或 changed-path 摘要冒充全部检查输入闭包 |
| Expected Checks / Required 引用 | ECI #66-B，复用既有 Selection/Plan 入口 | 在相关执行前由有效 Profile、受控 Check/项目适用规则、Context/Action/Target 与已知 Source 派生同一 `expected_set_ref`；区分适用性、Required 地位及来源/定义完整性 |
| Plan、Routing、Native Result 关联 | ECI #66-C，Hook 实际调度按 #72 | Expected→Plan→实际 Job→Result 对账，支持多对多和缺 Job 不删除 Expected；只消费真实结果，不重新执行检查 |
| 证据有效性、历史复用、Required 覆盖 | ECI #63 | **执行前**核验可复用的受信历史覆盖，交付 Remaining Work；**执行后**核验新增 Native Result、复用与缺项；均固定同一 `expected_set_ref` |
| Dagger 计算依赖与最终放行 | Dagger Engine / #86；#61 | Engine 执行剩余计算和必要产物；#61 基于相同 Profile/Expected 与 #63 受信结论决定当前动作的 Required/Release Gate，不重新制定 Required |

**Required Obligations 与 Expected Checks 的无损关系（补齐 §1.1，而不是创建第二套 Required Policy）：**

- 当前生效且有权的 Profile 对本次 Context×Action×Target 定义的 `required_obligations` 是**完整的授权义务集合**；`Expected Checks` 是在声明的检查域、真实 Source 和规则条件下对原生可检验项的**派生预期集合**。两者不得互相覆盖、简单改名或默认一对一。每条 Required Obligation 的受控来源/修订、适用对象及允许的不适用条件必须保留到最终 Assessment。
- **映射可为零到多、多对多：** 一个 Obligation 可能由多个 Check 的组合、额外 Artifact/Prerequisite、可信历史结果或已完成 Run 的 Terminal 见证等共同满足；一个 Check 也可能为多个义务提供证据。义务若本身不对应 Native Check（如 Release/Promotion 的既有 Terminal 证据义务），仍留在 Required 集合中并由相应实际事实生产者/证据消费者核验，不能从 `Expected Checks` 未命中而删除；也不能为它伪造一个 Native Check PASS。真实 Data/PASS 依赖继续由 #86/Dagger 表达。
- `expected_set_ref` 仍绑定**明确作用域内**的 Expected Checks 内容与其来源/规则版本，并可关联来源 Obligation 引用，但**不是整个 `required_obligations` 的替代身份或完整性证明**。同一 Profile/动作/目标的 Requirement/Obligation 关联应可追溯至 Check、受信历史复用或其他必要证据；`required_obligations` 的完整身份与原 Profile 修订必须在 #61 当前 Assessment 中一并核对，不另造平行 Policy Registry。
- **验收不变量：** `Expected Checks` 全部 `passed`（或被 #63 受信复用），并不自动推出 `Required Obligations` 全部满足。#61 必须逐项覆盖当前生效 Profile 的完整 Required 集合，包含未映射为 Native Check 的义务；缺失、未证实、不适用未经授权或后置 Terminal 尚未形成时，不得发布该动作的完整 Required PASS。独立、不依赖该 Required Profile 的本地 Check 仍按既有 §1.1 允许产生自己的原生事实。
- **设计反例：** Profile 对 Release 同时要求若干 Native Checks 的合格证据和**上一 Run 的已验收 Terminal**，即便这些 Check 已 PASS，缺 Terminal 仍不能放行；Profile 有 Required Obligation 但本次 `Expected Checks=[]`，亦不能仅凭“检查集合合法为空”令 Required Gate PASS；反过来 Required 子集为空时，适用 Advisory Check 仍应保留在 Expected 中。

**作用域与集合语义：**

- `Expected Checks` 必须声明消费者、Check/规则域、Profile/配置版本、Event/逐 Ref、Source 及作用域完整性；包含该声明域内适用的 Required **和**非 Required Check，并明确 `applicability`、`requirement_ref`/策略地位或其不存在的理由。Required/Advisory 与适用/不适用是**正交维度**。一个动作的 Required 子集为空，不能据此清空适用的 Advisory Check。
- `expected_set_ref` 绑定的是声明作用域、有效策略/规则版本和该次具体 Expected 内容；它不是第二份策略注册项。未加载/未批准的 Profile、未解析的规则、未知 Source/条件等要保留适用性或完整性未知与原因，不能用空数组伪造合法空集。
- **合法空集仅针对声明的完整检查域**：所有有关的受控 Profile/配置、定义与适用条件均已核实，且该域下确无适用 Check。空 Required 子集与空 Expected Check 集合分别判断；`resolve_definitions([])`、空 Job、空 Result 或自填非空字符串的 `completeness` Receipt 均不能单独证明任一种完整性。
- **Expected 必须早于实际 Job/Result 决定其义务**，但**不必等原生工具运行结束才生成**：#66-P0 可先给目标 Source 身份/可用的物化回执，#66-B 输出含明确未知项的 Expected；#52 的 Native Result 在实际执行时引用真正读到的 Source、规则/配置与 Check 输入/分析闭包。#66-C 和 #63 仅在证据足以证明等价/覆盖时提升结果资格，避免“先要结果闭包才能生成 Expected”的循环依赖。

**Expected Input Constraints → Observed Input Evidence 的交接（#66-B/C 消费已合并 #52-A 合同）：**

| 阶段与负责方 | 产生或固定的事实 | 允许下游作出的判断 |
| --- | --- | --- |
| **执行前：#66-B（含 #66-P0 当前可证明的 Source）** | 从生效 Profile/适用规则及版本化 CheckDefinition 得到 Expected Check、`expected_set_ref`、目标 Source/逐 Ref、规则与比较基线、已知的**预期输入约束**及未解析项；Check 的适用性与 Required 义务不能由后续 Result 反向定义 | 确定本次应有义务、可启动的计划及仍未知的依赖；不将尚未观察的输入范围声明为已验证的 `check_input_closure` |
| **执行或有效历史复用：#66-P0 + #52 原生执行者 / #63 历史证据** | #66-P0 提供目标 Git Source 的真实物化与绑定范围；#52 Native Result 记录实际观察到的规则、配置、工具/环境、输入引用、范围、比较基线与原生结果；历史 Result 保留原执行及来源身份 | 保留已执行/可复用候选事实；目标 Tree 或 changed-path digest 不自动成为实际 Check 全输入闭包 |
| **对账：#66-C，然后 #63** | 将**原有** Expected 实例的预期约束与观察事实按 Check/版本、Source、Rule、必要 old→new、输入及其闭包核对；记录 matched / insufficient / mismatched / unknown 的依据。#63 独立核验来源真实性、输入等价与受信复用 | 仅在对应证明成立时确认单项满足/可复用覆盖；否则保留 `unknown` 或不满足，不删除 Expected，不创建第二份 Expected/Result |
| **最终：#61** | 消费同一个有效 Profile 的**完整** `required_obligations`、Expected Check 投影和 #63 已接纳证据 | 判断当前 Action/Target 的完整 Required / Release 授权；不由 Check 全部 PASS 代替非 Check 义务 |

**接口约束：** [已合并 #52-A / PR #93](https://github.com/dymzz/engineering-ci/pull/93) 的 `validate_expected_instance()` / `result_covers_expected()` 是**已提供的单实例严格校验原语**，它们要求提供可核验的 Check 输入闭包；但**不是**执行前 Expected 枚举器、消费者完整性凭据或受信判定器。#66-B 在工具运行前可以合法地产生带待证输入条件的 Expected；#66-C 在获得原生/历史证据后再对已固定的实例与实际输入进行严格核对，必要时明确记录已验证绑定的依据，**不得通过修改 Expected 的规则/作用域或复制 Native Result 来源倒造预期**。具体输入引用字段、版本兼容与 `plan_contract` 消费位置在 #66-B/C 的实际接口切片设计/实施，不能在本设计 PR 中假称 Schema 2/Report 1 已扩展。已有 [Evidence and Identity](evidence-and-identity.md) 仍是历史证据等价和受信复用细则的唯一维护位置。

**实际执行与受信复用的顺序（参见 [Evidence and Identity §1–§3](evidence-and-identity.md)）：**

```text
Effective Profile + Context/Action/Target + CheckDefinition + Source
  -> Authoritative Expected Checks / Required subset (expected_set_ref)
  -> Selection/Plan 对账
  -> #63 执行前的受信 Evidence Resolution
  -> Remaining Work + Artifact/Prerequisite Closure
  -> #86/Dagger 原生 DAG 或必要 Host Checks
  -> 同次 Native Result / Report
  -> #66-C Expected/Plan/Routing/Result 对账
  -> #63 当前覆盖与受信证据核验
  -> #61 Current Policy Assessment / Final Gate（逐项核验完整 Required Obligations，非仅 Check 子集）
```

历史 PASS 只有在 [Evidence and Identity §1、§3](evidence-and-identity.md) 已定义的 `check_key` 语义等价（Check、Subject、实际输入/规则/工具/相关环境）与 #63 来源接纳、作用域/策略复用条件**均有证明**时，才能使本次符合条件的 Check Verdict 免跑；执行前不要求先获取本次尚未运行工具的 Native Result，但若目标输入闭包未知，也不能假定历史结果可复用。历史 PASS 不自动恢复下游所需的 Directory、文件或准备数据；产物仍按 [Evidence and Identity §2](evidence-and-identity.md) 和 [Dagger Execution](dagger-execution.md) 补足可核验闭包。#63 复用条件、原生执行及 Assessment 身份和 Dagger Data/PASS 前置关系的**详细定义仅在对应专题维护**，本节只规定其交接位置，避免重复规范。Result schema-valid、原生执行成功、Expected 覆盖、受信接纳、最终放行各有独立含义，不自动递推。尤其 Process Exit、Job Success、Tool/Check ID、Tree SHA 都不替代真实 Native Result、Expected 或 Required。

**实施与验收边界：** 本节不修改已发布 `plan_contract` Schema 2 或 `execution` Report Schema 1。#52-A [PR #93](https://github.com/dymzz/engineering-ci/pull/93) 已于 2026-11-13 合并（merge `c8dbdd2edfef57bfe1fbb1900b7b49d04df44977`）：已支持 Check/Native Result→公共校验→Report 的局部合同，保留单 Check 严格满足原语，并移除了不能证明完整 ExpectedSet 的字符串式集合完整性 API。**合并事实不代表**目标 Source 全输入闭包、完整 Profile→Expected 生产、跨 Job 路由、#63 受信或 #61 Gate 已交付；#52 后续仍负责扩展受控 Check/多 Tool/外部适配。#66-P0、#66-B/C 按上表在真实消费接口交付 Source、执行前预期与执行后/复用证据对账；#86/#63/#61 按现有设计独立验收。本 PR 的设计验收不等于 Issue 已原位同步、下游接口已冻结或运行时代码已实现。仅对各自改变的风险补定向反例：合法空 Required 但有 Advisory、缺 Profile 与真实空 Expected、未配置 Job 的 Required、同目标 Tree 不同 old→new、Index/Worktree 字节不一致、预期输入闭包未证实、Required 组合少结果、可信历史 PASS 但产物缺失等；复用已有有效原生/CI 证据。

**依据：** [Knowledge 总设计 §4/§6.3](https://github.com/dymzz/engineering-knowledge/blob/main/design/software-engineering-knowledge-architecture.md)；[Engineering Verification §2/§4](https://github.com/dymzz/engineering-knowledge/blob/main/design/engineering-verification/overall-design.md)；本文件 §1.1；[Evidence and Identity](evidence-and-identity.md)；[Dagger Execution](dagger-execution.md)；[Execution Graph 实施计划](plan.md)。如有冲突，应在真实专题比较现有约束与提案并审阅，不通过追加“优先解释”隐式覆盖。

## 2. 执行与结果消费
- **Local/Agent**：直接消费现有 `engineering-ci select/execute` 或原生 `dagger check`，不依赖 GitHub Run ID。当前 `execute-plan`、Host 和原生 Check 的功能不完全等价，目标是统一适用的结果和证据语义而非强迫一个执行容器。
- **GitHub Actions**：作为 CI Provider 实现保留原生 Workflow/Job 并行、权限、Artifact、Check/Release 保护配置。不要仅为单 Root 合并全部 Job。
- **其他 Provider**：仅在实际需要时实现对应能力，不建立全平台虚构状态表。
- **发布**：ECI 已验收结果交给 Provider；Publication 状态独立于 Native Result/Gate。外部副作用不能靠 Dagger 缓存冒充完成。

## 3. 按动作执行的时序
| 动作 | 允许发布的事实 | 最终授权约束 |
|---|---|---|
| 本地 Preflight | 本地 Check/原生诊断/证据候选 | 不冒充 GitHub 受保护 Required |
| PR/Merge Gate | 当前被检 Source/合并 Tree 的适用 Check | 缺失、skipped/neutral、未经受信复用不得假 PASS |
| Run 阶段性报告 | 已完成的 Task/Job/Check 的真实结果 | 不宣称完整 Attempt Terminal 已验收 |
| Post-completion | 同一已完成 Attempt 的 Terminal/Run/Artifact 证据 | 不让原 Workflow 等待自身 post-run |
| Release/Promotion | 对指定晋级目标的完整前置 Run 与 Terminal 证据 | #61 按本次有效 Profile 和覆盖进行最终放行 |

若要求“同一动作必须先证明自己完成后才会生成的终态事实”则是**不可满足的自依赖**；须阻断或由策略权威正式修订，不能伪造时间或绕过该要求。若目标 Release 要求的是此前 Run 的 Terminal，则在此前 Run 完成并验收 Terminal 后再授权。

## 4. 覆盖与回执
应分别记录 Expected Requirements、Plan、实际 Check Result、已接纳历史复用、不适用、未执行、当前 Policy Assessment、Provider Publication。存在 Output/Artifact 不代表 Check 已执行；发布成功不代表 Gate 通过。

[Required #61](https://github.com/dymzz/engineering-ci/issues/61) · [Evidence #63](https://github.com/dymzz/engineering-ci/issues/63) · [现行 Release](../../docs/architecture/release/README.md)