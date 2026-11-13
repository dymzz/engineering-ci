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

1. **#52-A 第一独立实现 PR（以后创建）**：确认正式 Check Identity/Profile/原生 Result 的代码位置和版本，提供受控定义全集完整性、实例覆盖的原生字段与组合满足关系；优先不修改 #82 正在更改的 OTel 代码路径。
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
