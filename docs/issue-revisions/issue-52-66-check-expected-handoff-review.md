# ECI #52-A ↔ #66-B/C：Check 定义完整性与 Expected/Native Result 覆盖交接（Design Review Only）

> **本文件是审阅索引，不是第二份规范性定义，也不代表运行时实施。**
> 唯一任务权威：[ECI #52](https://github.com/dymzz/engineering-ci/issues/52) 定义 Check/Result/Profile 原生合同；[ECI #66](https://github.com/dymzz/engineering-ci/issues/66) 负责 Source/Selection/Expected 与实际结果关联；受信 Required 覆盖由 [#63](https://github.com/dymzz/engineering-ci/issues/63)、最终门禁由 [#61](https://github.com/dymzz/engineering-ci/issues/61) 承担。此设计 PR 只记录两个 Issue 中实际完成的接口修订及分阶段独立验收要求，不修改旧 PR #72/#82/#91，不实施代码。

## 审阅基线（已直接修订 Issue）

| Source | 更新后状态 | 本批修改位置 |
| --- | --- | --- |
| [#52](https://github.com/dymzz/engineering-ci/issues/52) | `2026-11-13T03:05:49Z` · 21,639 chars / 198 lines · Open | **设计补充：#52-A Check 定义完整性与 Native Result 实例覆盖**；位于现有 #52-A 第一实现 PR 条款后 |
| [#66](https://github.com/dymzz/engineering-ci/issues/66) | `2026-11-13T03:05:54Z` · 42,886 chars / 328 lines · Open | **设计补充：#66-B/C 对 #52-A 定义完整性与 Expected/Result 覆盖的消费**；紧接现有交接切片，先于 S1 接口冻结条款 |

GitHub Issue 正文是直接更新的，**不属于此 PR 的 Git 文件 Diff**。本 PR 合并也不能单独证明这些 Issue 修改或 Native Check/Expected 代码已验收。不能用这个文档取代 Issue 中的详细定义。

## 1. 两项 P1 与两项 P2 的修订裁决

| ID | 问题 | #52 生产约束 | #66 消费约束 | 验收反例 |
| --- | --- | --- | --- | --- |
| P1-1 | 定义/Profile 枚举只返回部分内容被误当完整 | 给定受控消费者、Profile、规则/检查域，提供明确解析范围、版本与完整/部分/未知/失败及合法空集依据 | Expected 的合法空集必须以定义/规则已完整解析为前提，缺定义不自证 `not_applicable` | 跨受控检查域的部分枚举不得使缺失 Check 消失 |
| P1-2 | 原生 Result 与 Expected 实例覆盖不匹配 | 关联稳定 Check 定义及版本、Result 实例、真实输入闭包、Source、比较基线、工具/规则/环境和 native exit | 对每 Event/Ref/Source/Check/比较基线判断覆盖；一个原生 Result 复用到多个 Expected 需实质等价 | 同 Check+Tree，不同 old→new 比较基线不得盲目复用 |
| P2-1 | 多 Tool/Prerequisite 组合缺满足语义 | 受控 Check 定义说明全部必需、可替代实现、前置条件；实际计算 DAG 仍归 #86 | 逐项应用组合满足关系，不因一个 Tool Job PASS 即全 Check PASS | 两项共同必需仅完成一项时保持未满足 |
| P2-2 | Required 的可选引用被误解为可任意降级 | 生产可验证的 Check/Profile/Rule 身份和原生事实，不直接授权 Required | 若此 Check 应为 Required 而缺受信策略依据，保留未知/拒绝，不默认为 Optional | 策略关联缺失不转成 PASS 或 Not Applicable |

## 2. 已有接口与禁止重复建设

**#52-A：** 继续扩展已交付的原生 Check/Result/Report/Enforce。Runtime #27 的 `tool_id`/`group_id`/`access_id` 不等于 `check_id`；当前 `execution.SCHEMA_VERSION=1`、`plan_contract.PLAN_VERSION=2` 不证明存在已验收的跨生态稳定 Check/Profile Schema。检查身份及定义列表不来自 Lefthook Job/Result。保留实际 Native Result，一次扫描后多处消费，不因对账二次运行检查。

**#66-B/C：** 在现有 `ChangeSet → selection.select() → selection.plan() → plan_contract` 上定义 Event/Ref 的 Expected 实例及其路由无关的适用性。实际调度器 Job Binding 与 Expected 分离，允许未路由实例存在、Check↔Job 多对多。#66 已有公共 `execution.execute()` 不因职责划分而废弃。实际结果只在 Source、输入、规则、环境、比较基线/闭包和先决条件足够等价时覆盖多个 Expected；#63/#61 继续各自受信完整性和最终 Required 责任。

**不允许：** 新 Check Registry、Expected 数据库、重复 Native Runner、独立 Report Envelope、把 Tool ID/Job ID 充当 Check ID、把任一 Job PASS 当 Required 覆盖、用新 Schema 名义静默破坏 Plan V2、重算已执行工具来补结果、以完整 Hook 产品选型阻断 Source P0 或 #52-A、重写 PR #91 的 S0.5 历史审计。

## 3. 已有 PR 适配性裁决

| PR | 当前职能 | 此次处理 |
| --- | --- | --- |
| [#82](https://github.com/dymzz/engineering-ci/pull/82)（Open Draft） | #52 OTel Task-only 原生 Checker/Producer 集成，大量专项文件 | **不混入** Check 身份通用实现或本轮合同修订；未来触及共用代码时按真实文件修改协调 |
| [#72](https://github.com/dymzz/engineering-ci/pull/72)（Open Draft） | #66 多工具 Hook 调度/生命周期、Routing/Result 架构设计 | **保持完整**，后续 #66-C/调度接口在此方案复审，不能把它当作 #52-A 实现 |
| [#91](https://github.com/dymzz/engineering-ci/pull/91)（Open Draft） | S0/S0.5 历史冲突审计与 S1 导航 | **不修改**既有九处 applied.diff、避免用历史审计继续承载新的原生 Check 定义 |
| [#68](https://github.com/dymzz/engineering-ci/pull/68)（Open） | 旧 `eci_core` 代码与统一 run API 提议 | 不作为此轮 Check ID/Expected 新接口的自然承载，尤其不能恢复已退出的旧实现 |
| **本 PR** | #52/#66 跨契约设计差分和 Review | 仅新增一份审阅索引；不开始实现、不触发因 docs-only 更新而重复全量 CI |

## 4. 实施 PR 分配的设计结论（尚未启动实施）

1. **#52-A 第一独立实现 [PR #93](https://github.com/dymzz/engineering-ci/pull/93)（已创建、Draft）**：独立交付已支持 CheckDefinition 的稳定身份与版本、原生 Result 生产/校验及同次 Report 绑定；消费者级 ExpectedSet 完整性归 #66-B，跨实例/Job 对账归 #66-C，Source 字节闭包归 #66-P0。受控 Profile/外部 Tool 与组合 Check 满足关系继续作为 #52 后续切片，不因本首 PR 尚未交付而删除。
2. **#66-P0 Source 切片（可并行）**：确保 Index/Push/Worktree 实际字节与 Native Check 使用的 Source 一致；不用等待 #52-A 身份合同整体完成。
3. **#66-B Expected/Plan 扩展 PR（在 #52-A 真正可消费后）**：消费稳定 Check/规则身份、在不依赖路由结果的情况下产生 Expected；缺 Check 定义不自证合法空集；明确 schema 版本及旧 Host/Dagger/Actions 消费迁移。
4. **#66-C / PR #72 多工具 Routing/Result 设计与集成**：保留成熟调度器 Job 级选择、并行、完整 pre-push stdin/每 Ref 以及实际有效配置绑定；落实实例级覆盖、漏路由/漏执行和 Check 组合关系。具体拆分依据真实代码文件及接口，而非新建同义 Issue。

**上述实施顺序不是所有 Issue 的全局串行门禁。** 只对真实依赖的接口按顺序交接；可并行切片继续独立设计和验收。此次没有执行以上任一代码步骤。

## 5. 本 PR Review 应回答

- [ ] 解析完整性是否能识别“真正空集”与“仅得到一个子集”，而不要求全生态枚举？
- [ ] Expected 实例和 Native Result 的匹配是否保留 Event/Ref、实际 Source、输入闭包和比较基线，避免假复用？
- [ ] Check 内多工具结果的全部必需/允许替代/前置语义，是否与 #86 真正 Data/PASS DAG 边界一致？
- [ ] Required 受信引用丢失时是否维持未知/拒绝，且不重复 #63 的证据核验或 #61 最终门禁？
- [ ] #52-A/#66-B 的真实接口版本仍是未交付项，是否没有被文档或 PR Merge 冒充已验收？
- [ ] #72/#82/#91 的现有设计/实施边界是否保持，没有为了低价值“统一”而压缩或重做？

## 6. 2026-11-13 架构根因裁决：定义域 ≠ 消费者 ExpectedSet ≠ 可信门禁（取代旧的实施归属扩大解释）

**起因及实际代码验证。** #52-A [PR #93](https://github.com/dymzz/engineering-ci/pull/93) 的 `check_contract.py` 已能表达局部七项 Check 身份与 Native Result，但是 `resolve_definitions(required_names)` 只解析请求的名字，不能证明某消费者在给定 Event/Ref 的应有全集。它的 `validate_expected_coverage(..., completeness=...)` 通过字符串 `consumer/expected_set_ref/definitions_source/profile_ref` 的结构验证允许空集，不能证明规则/Profile/Source 实际全部加载；这不是在 #52 再加字段即可独立解决的事。

### 裁决表（以当前 Issue 正文为最终任务权威）

| 层 | 生产者与真正责任 | 交付/验收事实 | 不应承担 |
| --- | --- | --- | --- |
| CheckDefinition / Native Result | **ECI #52-A / PR #93**；必要的后续 #52 切片 | 稳定 Check/Rule/Tool 关联、定义域来源与版本、真实 Native Result/原生状态/摘要、比较依赖声明、多 Tool all-required/alternative 元数据、结构校验 | 消费者项目级 Expected 全集、Git 字节物化、跨 Job 路由、受信 Required |
| Event/Source | **ECI #66-P0** | 目标 Index/Commit/Push/PR 的真实受检字节，及 Check 实际读取/规则配置闭包或不足的回执 | 伪称 changed-path digest 等于全闭包 |
| ExpectedSet 及合法空集 | **ECI #66-B**，消费者（Knowledge #68 等）提供项目规则 | 独立于 Job/Result、按 Event/逐 Ref/Consumer/Profile/Rule/Source 生成 `scope_id`、`expected_set_ref`、Expected 实例、定义与规则解析完整性、合法空集及缺失原因 | 从七项内置列表或已完成 Job/Result 反推全集 |
| Routing 与 Native 对账 | **ECI #66-C**；[PR #72](https://github.com/dymzz/engineering-ci/pull/72) 保留多工具调度设计 | Expected→实际有效 Job→同次 Native Result，多对多关系、未路由/未执行/不等价、比较基线与组合结果覆盖 | 建立第二套 Runner/Result Ledger/Check Registry |
| DAG / 来源信任 / Gate | **#86 / #63 / #61** 分别负责 | 真正 Dagger Data/PASS DAG / 受信历史与 Required 完整性接纳 / 最终当前阶段 Required & Release Gate | 把这些判断折叠到 `check_contract=valid` |

**必须区分四种不同的 PASS/Valid：** (1) Result schema/内容摘要一致；(2) Native Check 本次实际运行并满足自己的原生语义；(3) 按完整 ExpectedSet 和 Source/规则/输入前提对账的实例覆盖；(4) 经过 #63 受信接纳、#61 当前阶段最终门禁。任何一层的结论均不自动证明后一层。

### PR #93 应如何验收，而非无限追加其他组件缺口

首个 #52-A 实施 PR 可在**局部 CheckDefinition 域与 Native Result 合同自洽**时独立验收合并：真实 Host Check 生产、公共校验器与 Report 状态一致；内部 Check 不伪造退出码、配置前置失败不伪造 Native Result；版本/Rule/Source 等级与比较基线诚实，摘要与诊断保真，未知 ID 失败；提供消费者可用的版本化接口/真实样本。未完成的受控 Profile/扩展 Tool、全 Check input closure、组合 Tool 关系应在 #52 继续明确后续切片及 #66 消费前置，不得将尚未交付事项标记已冻结。**真正 Source P0、ExpectedSet、路由多对多、#63/#61 不作为 PR #93 基础接口单独合并的全局前置**；这些能力仍须按所属 Issue 后续完成。

### 旧要求调整及 PR 责任

- 上文 §1 的 P1-1/P1-2：#52 负责声明域与 Native Result 所需事实，#66-B/C 才计算消费者全集、空集合与多 Expected 覆盖；不能让 `validate_expected_coverage` 接受几个自填字符串就当成全集完整。
- 上文 §1 的 P2-1/P2-2：#52 提供真实 Check 组合定义与身份，#86 实施 DAG，#66-C 消费实际多结果，#63/#61 完成可信 Required/Gate；不得让单 Tool PASS 独立证明组合 Required。
- [PR #92](https://github.com/dymzz/engineering-ci/pull/92) 本文件只记录设计归属与验收裁决；不是运行时实施，不应成为第二规范权威。[PR #93](https://github.com/dymzz/engineering-ci/pull/93) 继续只承担 #52-A 基础 Check/Result 合同，不要求它代写 #66-B/C。[PR #72](https://github.com/dymzz/engineering-ci/pull/72) 保留调度方案。[PR #91](https://github.com/dymzz/engineering-ci/pull/91) 历史 S0.5 审计不因本次改变。
- 前述 §4 的“首个 #52-A PR 提供受控消费者全集/合法空集、实际多 Job 路由验收”的扩大解释**由本节替代**；不删除功能，只将其归还对应生产方与联调阶段。

**定向验收策略：** 沿用已成功的原生 CI；仅新增对应风险的交接契约/反例。#66-B 真正空集 vs 未解析、#66-C 无 Job/不同基线/组合少结果、#66-P0 实际 Index 字节和检查目录闭包、#52 合同生成与验收一致分别验证，不重跑无关工具矩阵。不要创建新 Issue、Check Registry、第二个 Report/Runner 或多余审计副本。
