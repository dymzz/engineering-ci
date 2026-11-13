# CI Provider Adapter 与 Action-specific Gates

**状态：目标接口，尚非已交付统一 Provider API。** [总览](README.md)

## 1. 权威与接口
项目/组织的**当前生效工程 Profile** 根据 Context × Action × Target 决定 Required。ECI #63 对账原生执行/可信复用与 Expected Coverage；#61 决定 Required/Release 授权并连接平台保护规则。Selector、Knowledge RDF 查询及 GitHub Job success 不能自行删减 Required。

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

### 1.2 CheckDefinition / Expected / Native Result：沿用唯一 Profile 权威的接口边界（设计修订，非已实施）

**本节只细化 §1.1 已存在的交接；不创建第二套 Required 权威或新的 Expected 规范来源。** 原生工具及其版本/规则的真实语义来自对应 Native Tool；可被项目采用的工程知识由其原始/已接纳来源定义；是否在某次工程动作中成为 Required、允许哪些不适用或例外，**只由当时有权生效的项目/组织 Engineering Profile** 决定。ECI 负责该策略的受控解析、工程执行和证据/门禁，不因新增 `CheckDefinition`、`ExpectedSet` 或 `check_contract` 改变权威。知识 RDF 查询零行、Hook Job 未激活、Selection 没选中或 Result 缺席均不能删除 Profile 义务。

| 已有事实/接口 | 真正来源或负责实现的组件 | 接口含义与禁止的替代 |
|---|---|---|
| 规范/工具规则与领域定义 | 原生工具/标准；消费者受控配置；按需引用已接纳工程知识 | ECI 不用 `check_id` 重新定义 Tach/Weaver 等原生语义；Knowledge 不成为 ECI 核心运行依赖 |
| 有效 Profile、Required、允许不适用 | **项目/组织有权策略来源**；固定批准来源、修订、生效条件、Context×Action×Target | #52/#66/#63/#61 读取**同一**受控策略修订，不分别维护 Required 规则；Required 地位不属于 Check 的恒定属性 |
| CheckDefinition 和 Native Result/Report 适配 | **ECI #52**（实际原生工具保留自己的执行结论） | 稳定 `check_id`、Check/规则引用、适用 ToolAsset/ToolAccess、输入/比较依赖、必要工具组合关系；原生执行身份、状态、输出/Artifact；**不是**本次消费者完整 Expected/Required 集合 |
| Context、Git Event、逐 Ref Source 与受检字节 | **ECI #66-P0**；平台原生 Run/Attempt 身份由 Provider 产生 | 目标 Index/Commit/Push/PR Merge 的声明与实际读取范围分开；无法证明实际输入闭包不能由 Tree 字符串或 changed-path digest 推导完整字节绑定 |
| Expected Requirements 派生和固定引用 | **ECI #66-B** 在现有 Selection/Plan 公共接口扩展，消费有效 Profile + CheckDefinition + Context/Action/Target + Source | 生成同一 Profile 的 `expected_set_ref` 及适用/不适用/未知原因；**不能**由已经 selected/executed 的集合、Job 清单或 Result 反推完整 Expected |
| Plan、有效 Job 路由和同次结果对账 | **ECI #66-C**；Hook 实际调度按 [#72](https://github.com/dymzz/engineering-ci/pull/72) 设计实现 | 对账 Expected→Selection/Plan→Routing→Native Result，Check↔Job 多对多、Job 缺失不删除 Expected；在输入等价/比较基线成立时可共享同次结果，不重做扫描 |
| 真实 Dagger 执行依赖/计算缓存 | **Dagger Engine + ECI #86** 的实际 Function/Check 声明 | Engine 运行 DAG；不由 #52/#66 再造调度器、图引擎或第二种检查 PASS |
| 受信来源、有效复用和 Required 覆盖接纳 | **ECI #63** | 核对同一 `expected_set_ref`、原执行/输入/策略与当前有效性；不生产另一套 Required 义务 |
| Policy Assessment、最终 Required/Release 放行与平台保护规则 | **ECI #61** | 消费同一有效 Profile/Expected 和 #63 的证据结论；可以再次核验一致性，**不能重算一份相竞争的策略定义** |

**计算方向与状态分离：**

1. 先解析当时有效 Profile 的 `required_obligations/allowed_inapplicability`、来源和版本，并固定 Context/Action/Target；再结合受控 CheckDefinition 和实际 Source 判定本次 Expected 的完整作用域。这里的 `#66-B` 是**派生计算接口**，不是授予 Required 的新策略权威；可复用 `selection.py` 的源码/规则分析能力，但不可先按 Job/Selected 截断 Expected。
2. 把同一个 `expected_set_ref` 与其 Profile 修订、Source、Event/逐 Ref、CheckDefinition/规则修订一起传给 Plan、#66-C、#63、#61；任一消费者若缺失/更改这些依赖，应报告部分、未知或拒绝，而非生成第二份可独立编辑的“应有检查”。
3. **区分五种结论：** (`CheckDefinition` 在已声明的名称/版本域内解析成功)、(`Native Result` 结构/摘要有效且原生运行状态已知)、(本次 Expected/Selection/Result 对账是否覆盖)、(#63 已受信接纳的 Required 覆盖)、(#61 当前动作是否授权)。这不是五个互相竞争的 Profile/Gate；任一前段 PASS 均不自动推出后段 PASS。
4. **合法空 Expected** 不是 `resolve_definitions([])` 找不到 Check，也不是空 Job/空 Result 或任意非空字段的 `completeness` 字典。必须证明同一有权 Profile、声明检查域、适用规则、Context/Action/Target、Source 均完整且确实无适用义务；`expected_set_ref` 应绑定受控的具体 Expected 集合内容与来源。#66-B 负责产生该派生完整性事实，#66-C 核对实例覆盖，#63 判定受信；#52 可以校验单个 Result/候选关联，但**不得从自填字符串签发全局 ExpectedSet 完整性**。
5. **身份不混合：** `check_id` 是受控 Check 定义身份；[证据设计](evidence-and-identity.md#3-身份拟议的交接语义)的 `check_key` 是 Check + Subject + 输入闭包/规则/工具/环境的语义等价键；`origin_execution_id` 是原生执行来源；`native_result_ref` 是一次原生结果引用；`expected_set_ref` 是本次 Profile/Context 下派生要求集合。不能用 Tool ID、Job ID、Check 名、Tree SHA、哈希一致或成功 Exit 相互取代。
6. **组合与基线：** 某 Check 是否依赖 old→new 比较基线，应由它实际采用的原生规则及 CheckDefinition 输入合同决定，不能仅看 Expected 调用方是否传入 `comparison=true`；多个 Tool 共同必要/可替代关系来自真实 CheckDefinition/有效 Profile 的相应职责，不由一条 Job PASS 推断完整 Check 成功；#86 只执行确有依据的前置/Data/PASS 关系。

**版本化与独立实施：** 当前 §1.1 仍是目标设计；本节不会静默向既有 `plan_contract` Schema 2 或 `execution` Report Schema 1 写入新字段。具体扩展须在原公共契约上明确生产者、字段来源、有效范围、版本/兼容迁移及真实 Consumer。#52-A [PR #93](https://github.com/dymzz/engineering-ci/pull/93) 可独立验收**已支持的 Check 身份与原生 Result 生产→校验→Report 合同**，无须等待整个 #66/#63/#61/#86；但必须准确区分局部字段验证与最终 Expected 完整性，不能留下公开 API 的误导性“整体覆盖 PASS”。#66-P0 可并行，#66-B 在真实 Profile/Check/Source 输入上交付 Expected，#66-C 再落实 Routing/Result 对账，#63/#61 后续独立验收受信与 Gate。没有交付的 Profile/Check 扩展、输入闭包或组合能力均明确为未就绪，不用缩水设计换取“冻结”字样。

**审阅负例（分别归实际责任方，不要求一个 PR 重跑整套 CI）：** Profile 缺失/不受信 vs 真实合法空集；已声明 Required 但未配置 Job；同新 Tree、不同 old→new 比较检查；Source Index B 与 Worktree C 不同；双原生工具都必需但只执行一项；Native Result/Report PASS 但 #63 来源不受信；有效历史 PASS 满足当前等价输入而无需重扫；后置 Terminal 不能成为产生它的原 Workflow 的前置。已有有效 Native 和 CI 证据复用，仅验证新接口风险。

**规范出处与冲突优先级：** [Knowledge 总设计 §6.3](https://github.com/dymzz/engineering-knowledge/blob/main/design/software-engineering-knowledge-architecture.md)（有权项目策略与原生执行边界）、[Engineering Verification §2/§4](https://github.com/dymzz/engineering-knowledge/blob/main/design/engineering-verification/overall-design.md)（跨仓公共接口）、本设计 §1.1、[Evidence and Identity](evidence-and-identity.md)（身份、复用）及 [Dagger Execution](dagger-execution.md)（DAG）共同约束。Issue #52/#66 和审阅记录应引用这些现有定义并写出实施差分；不得通过后发 Issue 的“优先解释”覆盖它们。若真正改变此权威链，必须在本专题明确提出与旧定义的差异及理由，而不是追加平行的总裁决。

Provider 的可选能力是 `Context`（repo/source/Run/Attempt/环境）、`Dispatch`（请求远端执行）、`Observe`（真实平台状态/时间）、`Artifacts`（取得/保存）、`Publish`（发布 Check/Decision）。本地不要求实现远程能力；不同 Provider 不伪造不支持的状态。

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