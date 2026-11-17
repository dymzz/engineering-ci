# C 阶段的实际证据范围

派生输入为已合并 #126 的审阅 HEAD `363bb19d381146890b57341f686b03f311fd0d0a`；本轮实现基线为 `990cd59f83153a1ad3978dc933f89af5ca4829e3`。
运行时代码/测试/Workflow/依赖相对 A/B main 无变化，索引沿用原 main 与 #124 两个独立快照，未刷新旧服务端观察的时间。

## 按边分类

| 分类 | 边数 |
| --- | ---: |
| 已验证真实生产 (`verified_production`) | 0 |
| 已验证合同但真实权威未接通 (`verified_contract_authority_unconnected`) | 13 |
| 已知缺口 (`known_gap`) | 5 |
| 证据矛盾 (`evidence_contradiction`) | 0 |
| 尚未调查 (`not_investigated`) | 68 |

以上是 **86 条已列依赖边**的证据类别，不是 ECI 授权覆盖率。0 项真实生产表示这些允许性链路没有完成所需真实来源闭包；旧 Native/CI 事实仍在原范围有效。0 项证据矛盾表示 C 未建立此类裁决，不代表 D 已证明没有矛盾。

## 证明对象与层次

[Review #5943207575](https://github.com/dymzz/engineering-ci/pull/127#pullrequestreview-5943207575) 指出的默认合同分类已移除。每条边都有自己的 `edge_proof.proof_object`；层次分别记录组件合同、交接合同、来源认证、生产接线。只有该边显式登记的交接证明，才支持“合同已验证”分类。未逐边绑定的 Family fixtures/CI 只作上下文。

| 已有直接证明的层次 | 边数 |
| --- | ---: |
| component_contract | 21 |
| handoff_contract | 13 |
| origin_authentication | 0 |
| production_wiring | 0 |

这些计数限于每项 `proof_object` / `scope_limit`。组件层通过不能提升交接层；交接层可只证明受控 API/格式/参数传递，不提升实际 issuer、token/App 或端到端运行权限。

## 逐边直接证明

[证明输入](evidence/edge-proof-claims.json)逐项写明本边条件、固定源码路径、测试调用点、断言角色及其证明内容；[机器索引](index.json)进一步保存原文摘录、行号和 SHA。校核器核对这些手工审阅的绑定与真实 AST/源字节一致，不能自动裁定全部业务语义。

| 证明 | 层次 | 证明对象 | 直接断言（固定源码行） |
| --- | --- | --- | --- |
| E001.handoff | component_contract / handoff_contract | selection.plan 的 legacy/v1 partial 分支将 Selection/Source scope 传给真实 derive_expected_checks，并把返回对象纳入 Plan；断言仅证明 schema=1、partial 与 Source snapshot 传递。 | [66](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_expected_checks.py#L66)、[67](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_expected_checks.py#L67)、[72](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_expected_checks.py#L72) |
| E020.handoff | component_contract / handoff_contract | 真实 reconciliation Receipt fixture 进入 T1-a API 后，Coverage 保持同一 Expected instance；不证明 raw issuer transport。 | [382](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L382)、[384](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L384)、[391](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L391)、[393](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L393) |
| E021.handoff | component_contract / handoff_contract | T1-a API 调用 Receipt validator 后使用相同 scope/Expected instance；不证明 Receipt 来源认证。 | [382](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L382)、[384](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L384)、[391](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L391)、[393](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L393) |
| E023.handoff | component_contract / handoff_contract | 验证过的 Package 原对象送入 verify_local_handoff；受控授权下返回材料摘要和该 Package 相同。 | [152](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_local_actions_handoff.py#L152)、[155](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_local_actions_handoff.py#L155)、[164](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_local_actions_handoff.py#L164)、[165](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_local_actions_handoff.py#L165) |
| E025.component | component_contract | 实际 T1-a builder 的受控 fixture 能形成逐实例 Coverage；此证明只属于该组件。 | [391](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L391)、[393](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L393) |
| E026.handoff | component_contract / handoff_contract | R1 内部 Admission validator 的结果参加当前实例评估；此输入是 make_admission fixture，不是实际 T1-a 输出。 | [830](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment.py#L830)、[840](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment.py#L840)、[844](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment.py#L844)、[845](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment.py#L845) |
| E029.handoff | component_contract / handoff_contract | 真实 R1 builder 返回的同一个 Assessment 对象可通过纯 Assessment validator；只证明结构/digest 合同。 | [830](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment.py#L830)、[849](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment.py#L849)、[844](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment.py#L844) |
| E031.handoff | component_contract / handoff_contract | build_check_publication 产生 publication 后，publisher 将其中 request 原样交给模拟 API 并返回 ID。 | [298](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_check.py#L298)、[294](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_check.py#L294)、[297](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_check.py#L297) |
| E035.handoff | component_contract / handoff_contract | authorize_and_publish 成功分支将 publication/授权/注入 client 交给真实低层 publisher，保留其 API 返回 ID。 | [261](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_publish.py#L261)、[273](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_publish.py#L273)、[274](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_publish.py#L274) |
| E047.handoff | component_contract / handoff_contract | Workflow Actions 的 release 操作定位 release.publishing.main，并原样转发 argv/返回值；消费函数在测试中被替换。 | [125](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_unified_cli.py#L125)、[124](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_unified_cli.py#L124) |
| E053.handoff | component_contract / handoff_contract | post_completion_plan 的配置闭包交给真实 validator 重算，返回一致摘要；修改文件后同一 Plan 被拒。 | [639](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_selection.py#L639)、[640](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_selection.py#L640)、[660](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_selection.py#L660) |
| E059.component | component_contract | Admission 对缺 raw Report 的本地接收行为：origin 未验证、实例未接纳且仍有剩余工作。 | [415](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L415)、[417](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L417)、[418](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_admission.py#L418) |
| E060.component | component_contract | T2 对自报 trusted/eligible 但缺 origin verifier 的本地响应。 | [346](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_historical_reuse.py#L346)、[347](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_historical_reuse.py#L347) |
| E061.component | component_contract | 本地 Handoff receiver 对受控 callbacks/package 的材料摘要和 consumer scope 投影。 | [164](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_local_actions_handoff.py#L164)、[165](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_local_actions_handoff.py#L165) |
| E062.component | component_contract | Publisher 对明确拒绝的 approval callback 的本地拒绝行为。 | [289](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_publish.py#L289) |
| E063.handoff | component_contract / handoff_contract | publisher 与受控 GitHub callable 的精确请求/响应接口：repository、check-runs、POST、publication.request 与 ID。 | [295](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_check.py#L295)、[296](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_check.py#L296)、[297](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_check.py#L297)、[298](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_check.py#L298)、[294](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_check.py#L294) |
| E065.component | component_contract | R4 的 validation 模式虽 verified，仍不授予 publication 授权。 | [211](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_release.py#L211)、[213](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_release.py#L213)、[214](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_release.py#L214)、[217](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_release.py#L217) |
| E068.component | component_contract | R5 在最早 due 时间前返回 pending，而不聚合为全生命周期通过。 | [209](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_post.py#L209)、[210](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_post.py#L210)、[211](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/tests/test_required_assessment_post.py#L211) |
| E079.handoff | component_contract / handoff_contract | 结构 seal 的 witness 被 Binder 判为 structurally_aligned，但缺 Phase authority 时仍不授权。 | [262](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/tests/test_terminal_expected.py#L262)、[276](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/tests/test_terminal_expected.py#L276)、[274](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/tests/test_terminal_expected.py#L274)、[277](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/tests/test_terminal_expected.py#L277) |
| E081.handoff | component_contract / handoff_contract | wrapper 将原 Expected 和创建的 Terminal Plan/显式参数传给 Binder，并返回其拒绝自报证明的结果。 | [854](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/tests/test_terminal_expected.py#L854)、[853](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/tests/test_terminal_expected.py#L853) |
| E085.component | component_contract | Binder 对 digest 自洽、issuer 自报但缺 Phase authority 的本地拒绝行为。 | [274](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/tests/test_terminal_expected.py#L274)、[275](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/tests/test_terminal_expected.py#L275)、[277](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/tests/test_terminal_expected.py#L277) |

E059/E062/E085 仅保留明确的组件断言，外部来源交接仍未调查。E063 保留精确 Python transport API 正例：repository、`check-runs`、`POST`、请求体等于 `publication.request`、返回 ID；真实 token/App 与服务器授权没有据此验证。E079 的范围仅为结构 seal→Binder 的对齐/拒绝行为，与 E085 的真实 Phase 来源分开。

## 逐边索引

每行的固定源码、身份/Phase/时间、failure、原始证据/测试、trust boundary 与原 Issue/PR 均在 [机器索引](index.json) 的同名 edge 中。`contract_input` 不能充作生产接线；测试名中的 real/verified 不改变 fixture 证据类型。

| 边 | 生产/验证 → 消费 | 分类 | 已证层次 | 原归属 |
| --- | --- | --- | --- | --- |
| [E001](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/selection.py#L3651) | `selection.plan` → `expected_checks.derive_expected_checks` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #66 / #125 |
| [E002](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/expected_checks.py#L687) | `expected_checks._verify_profile_authority` → `expected_checks.derive_expected_checks` | 尚未调查 | 未登记直接证明；上下文保留 | #66 / #125 |
| [E003](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/expected_checks.py#L1885) | `expected_checks.derive_expected_checks` → `reconciliation.derive_strict_expected` | 尚未调查 | 未登记直接证明；上下文保留 | #66 / #125 |
| [E004](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/expected_checks.py#L1885) | `expected_checks.derive_expected_checks` → `required_assessment.assess_required_obligations` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #66 / #63 |
| [E005](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/expected_checks.py#L2874) | `expected_checks.validate_projection` → `required_assessment.assess_required_obligations` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #66 / #63 |
| [E006](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/selection.py#L3233) | `selection.verify_materialized_source_snapshot` → `execution.execute` | 尚未调查 | 未登记直接证明；上下文保留 | #66 / #52 / #86 |
| [E007](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/execution.py#L2754) | `execution.build_pre_execution_source_proof` → `admission.admit_historical_reuse` | 尚未调查 | 未登记直接证明；上下文保留 | #63 / #66 |
| [E008](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/execution.py#L3030) | `execution.compare_pre_execution_to_historical_result` → `admission.admit_historical_reuse` | 尚未调查 | 未登记直接证明；上下文保留 | #63 / #66 |
| [E009](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/execution.py#L3362) | `execution.build_source_resolution` → `reconciliation.derive_strict_expected` | 尚未调查 | 未登记直接证明；上下文保留 | #66 / #52 / #86 |
| [E010](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/execution.py#L3498) | `execution.source_resolution_receipt` → `admission.admit_evidence` | 尚未调查 | 未登记直接证明；上下文保留 | #66 / #52 / #86 |
| [E011](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/execution.py#L4682) | `execution.execute` → `check_contract.validate_result` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #63 |
| [E012](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/.dagger/src/engineering_ci_module/main.py#L273) | `engineering_ci_module.main.EngineeringCi.plan_execution` → `engineering_ci_module.plan_execution.execute` | 尚未调查 | 未登记直接证明；上下文保留 | #86 / #52 / #63 |
| [E013](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/.dagger/src/engineering_ci_module/plan_execution.py#L1225) | `engineering_ci_module.plan_execution.execute` → `engineering_ci_module.plan_result.PlanExecution.enforce` | 尚未调查 | 未登记直接证明；上下文保留 | #86 / #52 / #63 |
| [E014](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/.dagger/src/engineering_ci_module/main.py#L296) | `engineering_ci_module.main.EngineeringCi.execute_plan` → `engineering_ci_module.plan_result.PlanExecution.report` | 尚未调查 | 未登记直接证明；上下文保留 | #86 / #52 / #63 |
| [E015](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/check_contract.py#L927) | `check_contract.native_result` → `check_contract.validate_result` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #63 |
| [E016](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/native_producer.py#L95) | `native_producer.test_check` → `check_contract.native_result` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #63 |
| [E017](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/native_producer.py#L157) | `native_producer.weaver_check` → `check_contract.native_result` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #63 |
| [E018](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/check_contract.py#L1053) | `check_contract.validate_result` → `check_contract.evaluate_expected_satisfaction` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #63 |
| [E019](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/check_contract.py#L1451) | `check_contract.evaluate_expected_satisfaction` → `reconciliation.reconcile_expected_results` | 尚未调查 | 未登记直接证明；上下文保留 | #66 / #52 / #63 |
| [E020](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/reconciliation.py#L1748) | `reconciliation.reconcile_expected_results` → `admission.admit_evidence` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #63 / #66 / #61 |
| [E021](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/reconciliation.py#L1984) | `reconciliation.validate_receipt` → `admission.admit_evidence` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #63 / #66 / #61 |
| [E022](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/local_actions_handoff.py#L254) | `local_actions_handoff.build_local_handoff_package` → `local_actions_handoff.validate_local_handoff_package` | 尚未调查 | 未登记直接证明；上下文保留 | #63 |
| [E023](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/local_actions_handoff.py#L326) | `local_actions_handoff.validate_local_handoff_package` → `local_actions_handoff.verify_local_handoff` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #63 |
| [E024](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/local_actions_handoff.py#L526) | `local_actions_handoff.verify_local_handoff` → `admission.admit_evidence` | 尚未调查 | 未登记直接证明；上下文保留 | #63 |
| [E025](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/admission.py#L779) | `admission.admit_evidence` → `required_assessment._validate_admission` | 尚未调查 | component_contract | #63 / #66 / #61 |
| [E026](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L1341) | `required_assessment._validate_admission` → `required_assessment.assess_required_obligations` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #63 / #66 / #61 |
| [E027](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/reconciliation.py#L1984) | `reconciliation.validate_receipt` → `required_assessment.assess_required_obligations` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #66 / #63 |
| [E028](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L791) | `required_assessment._validate_universe` → `required_assessment.assess_required_obligations` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #66 / #63 |
| [E029](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L2449) | `required_assessment.assess_required_obligations` → `required_assessment.validate_assessment` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #61 / #66 / #63 |
| [E030](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L2637) | `required_assessment.validate_assessment` → `required_assessment_check.build_check_publication` | 尚未调查 | 未登记直接证明；上下文保留 | #61 |
| [E031](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_check.py#L306) | `required_assessment_check.build_check_publication` → `required_assessment_check.publish_check_run` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #61 |
| [E032](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_check.py#L388) | `required_assessment_check.publication_for_missing_assessment` → `required_assessment_check.publish_check_run` | 尚未调查 | 未登记直接证明；上下文保留 | #61 |
| [E033](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_publish.py#L463) | `required_assessment_publish.verify_publisher_identity` → `required_assessment_publish.authorize_and_publish` | 尚未调查 | 未登记直接证明；上下文保留 | #61 |
| [E034](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_publish.py#L515) | `required_assessment_publish.workflow_publish_authority` → `required_assessment_check.publish_check_run` | 尚未调查 | 未登记直接证明；上下文保留 | #61 |
| [E035](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_publish.py#L615) | `required_assessment_publish.authorize_and_publish` → `required_assessment_check.publish_check_run` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #61 |
| [E036](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_protection.py#L257) | `required_assessment_protection.inspect_server_protection` → `required_assessment_protection.validate_protection_observation` | 尚未调查 | 未登记直接证明；上下文保留 | #61 |
| [E037](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L2637) | `required_assessment.validate_assessment` → `required_assessment_release.reconcile_release_assessment` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E038](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/manifest.py#L340) | `release.manifest.validate` → `required_assessment_release.reconcile_release_assessment` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E039](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L409) | `required_assessment_release.manifest_provider_from_verification` → `required_assessment_release.reconcile_release_assessment` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E040](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L430) | `required_assessment_release.manifest_provider_from_publication` → `required_assessment_release.reconcile_release_assessment` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E041](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L673) | `required_assessment_release.reconcile_release_assessment` → `required_assessment_release.validate_release_reconciliation` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E042](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L981) | `required_assessment_release.validate_release_reconciliation` → `required_assessment_release.require_reconciliation_manifest` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E043](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L2637) | `required_assessment.validate_assessment` → `required_assessment_post.reconcile_post_stage` | 尚未调查 | 未登记直接证明；上下文保留 | #61 |
| [E044](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/security_policy.py#L684) | `security_policy.receive` → `security_policy.decide` | 尚未调查 | 未登记直接证明；上下文保留 | #56 / #53 / #57 |
| [E045](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/security_policy.py#L814) | `security_policy.decide` → `execution._build_workflow_admission_fn` | 尚未调查 | 未登记直接证明；上下文保留 | #56 / #53 / #57 |
| [E046](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/request.py#L55) | `release.request.dispatch` → `release.publishing.main` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E047](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/workflow_actions.py#L10) | `workflow_actions.main` → `release.publishing.main` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #61 / #63 |
| [E048](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/manifest.py#L340) | `release.manifest.validate` → `release.publishing.published_evidence` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E049](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/publishing.py#L114) | `release.publishing.published_evidence` → `release.publishing.publish_release` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E050](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/publishing.py#L336) | `release.publishing.main` → `release.publishing.publish_release` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E051](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/cicd_telemetry.py#L2646) | `cicd_telemetry.fetch_github_run_job` → `cicd_telemetry.produce` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #74 / #75 / #86 / #87 |
| [E052](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/cicd_telemetry.py#L857) | `cicd_telemetry.produce` → `selection.post_completion_plan` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #74 / #75 / #86 / #87 |
| [E053](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/selection.py#L4048) | `selection.post_completion_plan` → `selection.validate_post_completion_binding` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #52 / #74 / #75 / #86 / #87 |
| [E054](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/selection.py#L4174) | `selection.validate_post_completion_binding` → `execution.execute` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #74 / #75 / #86 / #87 |
| [E055](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/dagger_telemetry.py#L1649) | `dagger_telemetry.correlate_capture` → `dagger_telemetry.verify_a1_suite` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #74 / #75 / #86 / #87 |
| [E056](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/hook_coordinator.py#L575) | `hook_coordinator._require_runner_produced_report` → `hook_coordinator.execute_activated_job` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #63 |
| [E057](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/expected_checks.py#L687) | `governed-profile-and-phase` → `expected_checks._verify_profile_authority` | 已知缺口 | 未登记直接证明；上下文保留 | #66 / #125 |
| [E058](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L2449) | `complete-universe-and-non-check-evidence` → `required_assessment.assess_required_obligations` | 已知缺口 | 未登记直接证明；上下文保留 | #61 / #66 / #63 |
| [E059](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/admission.py#L779) | `raw-origin-and-resolution-authorities` → `admission.admit_evidence` | 尚未调查 | component_contract | #63 / #66 / #61 |
| [E060](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/admission.py#L1527) | `historical-authorities` → `admission.admit_historical_reuse` | 尚未调查 | component_contract | #63 / #66 |
| [E061](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/local_actions_handoff.py#L526) | `local-controlled-boundary` → `local_actions_handoff.verify_local_handoff` | 尚未调查 | component_contract | #63 |
| [E062](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_publish.py#L615) | `actions-approval-and-execution` → `required_assessment_publish.authorize_and_publish` | 尚未调查 | component_contract | #61 |
| [E063](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_check.py#L696) | `github-check-transport` → `required_assessment_check.publish_check_run` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #61 |
| [E064](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_protection.py#L257) | `github-server-enforcement` → `required_assessment_protection.inspect_server_protection` | 已知缺口 | 未登记直接证明；上下文保留 | #61 |
| [E065](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L673) | `release-authorities` → `required_assessment_release.reconcile_release_assessment` | 尚未调查 | component_contract | #61 / #63 |
| [E066](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/publishing.py#L336) | `release-workflow-server` → `release.publishing.main` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E067](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/publishing.py#L213) | `release-api-writes` → `release.publishing.publish_release` | 尚未调查 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E068](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_post.py#L582) | `post-authorities` → `required_assessment_post.reconcile_post_stage` | 尚未调查 | component_contract | #61 |
| [E069](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/security_policy.py#L684) | `policy-anchor-and-provider` → `security_policy.receive` | 尚未调查 | 未登记直接证明；上下文保留 | #56 / #53 / #57 |
| [E070](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/.dagger/src/engineering_ci_module/plan_execution.py#L1225) | `dagger-engine-source` → `engineering_ci_module.plan_execution.execute` | 尚未调查 | 未登记直接证明；上下文保留 | #86 / #52 / #63 |
| [E071](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/admission.py#L1527) | `admission.admit_historical_reuse` → `remaining-work-dispatch-consumer` | 尚未调查 | 未登记直接证明；上下文保留 | #63 / #66 |
| [E072](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L242) | `required_assessment_release.require_reconciliation_manifest` → `r4-actual-publication-consumer` | 已知缺口 | 未登记直接证明；上下文保留 | #61 / #63 |
| [E073](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/admission.py#L779) | `admission.admit_evidence` → `same-run-coverage-dispatch` | 尚未调查 | 未登记直接证明；上下文保留 | #63 / #66 / #61 |
| [E074](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_check.py#L696) | `required_assessment_check.publish_check_run` → `server-check-consumer` | 尚未调查 | 未登记直接证明；上下文保留 | #61 |
| [E075](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_protection.py#L257) | `required_assessment_protection.inspect_server_protection` → `server-deny-enforcement` | 已知缺口 | 未登记直接证明；上下文保留 | #61 |
| [E076](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_post.py#L582) | `required_assessment_post.reconcile_post_stage` → `post-disposition-enforcer` | 尚未调查 | 未登记直接证明；上下文保留 | #61 |
| [E077](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/expected_checks.py#L1885) | `expected_checks.derive_expected_checks` → `terminal_expected.bind_terminal_expected` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #61 |
| [E078](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/expected_checks.py#L2874) | `expected_checks.validate_projection` → `terminal_expected.bind_terminal_expected` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #61 |
| [E079](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1086) | `terminal_expected.seal_phase_witness` → `terminal_expected.bind_terminal_expected` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #52 / #66 / #61 |
| [E080](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/selection.py#L4042) | `selection.post_completion_plan` → `terminal_expected.post_completion_with_expected` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #61 |
| [E081](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1457) | `terminal_expected.post_completion_with_expected` → `terminal_expected.bind_terminal_expected` | 已验证合同但真实权威未接通 | component_contract / handoff_contract | #52 / #66 / #61 |
| [E082](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1121) | `terminal_expected.bind_terminal_expected` → `terminal_expected.validate_terminal_expected_binding` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #61 |
| [E083](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1121) | `terminal_expected.bind_terminal_expected` → `terminal_expected.post_completion_with_expected` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #61 |
| [E084](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/selection.py#L4168) | `selection.validate_post_completion_binding` → `terminal_expected.bind_terminal_expected` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #61 |
| [E085](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1121) | `expected-phase-completed-authorities` → `terminal_expected.bind_terminal_expected` | 尚未调查 | component_contract | #52 / #66 / #61 |
| [E086](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1457) | `terminal_expected.post_completion_with_expected` → `terminal-runtime-consumer` | 尚未调查 | 未登记直接证明；上下文保留 | #52 / #66 / #61 |

## 候选去向与未调查范围

原 A/B 254 项 disposition 原文不改写。C 从完整保留 AST 查找同分区 callee→caller 路径，允许中间 helper 不在候选列表；每一步仍有实际调用表达式依据。正向调用路径与原人工数据边分开校验。

| 快照 | 同分区静态路径 | 仅相同实现，消费者待查 | 尚未找到图路径 | 明确排除 |
| --- | ---: | ---: | ---: | ---: |
| main | 111 | 0 | 13 | 3 |
| pr124 | 14 | 65 | 45 | 3 |

[候选关系](candidate-relations.json)逐项列出原 disposition、固定源码、完整 helper 调用链、同分区边以及实现证据引用。跨快照 main 引用只列在 `implementation_only_main_edge_refs`，不复制 main 的生产/消费者结论。无映射项保留 `not_investigated` 和待补观察；不按 route 或零调用者自动排除。

## 已知缺口及后续观察

| 边 | 实际依据 / 待补事实 |
| --- | --- |
| E057 | #125 §0 明确完整有权治理/Phase Producer 尚未交付；当前构造/摘要/规则匹配不能补造批准来源。 |
| E058 | #125 §0 与 R1 接口分别要求完整有权 Universe/Non-Check 事实；现有合同切片不交付该生产来源。 |
| E064 | 原始 GET records[2]：Branch Protection HTTP401/native exit1，现有 observer 因可见性不足给 not_verified。 |
| E072 | 固定代码中 R4 optional require API 与现有 workflow_actions→publishing.main→publish_release 的消费路径未接通；不外推其他外部消费者不存在。 |
| E075 | 同一服务端记录明确 probe=not_performed 且保护可见性不足；本快照没有 actual deny/bypass/App 执法事实。 |
| E004 | Expected Producer 与 R1 各有合同测试；尚未核查真实派生 Expected 到 R1 的组合输入链，不以两侧测试绿代替本边验证。 |
| E005 | A/B 明确 R1 使用自己的 _verify_expected_scope，并不调用 validate_projection；两种验证的组合充分性尚未独立核查。 |
| E010 | Resolution Receipt 构造与 Admission 分别有合同；当前所引用证据未建立生产构造器到接纳方的真实传递与认证链。 |
| E024 | 未观察实际 Actions Receiver 将 T1-b 材料送入 T1-a；材料 verified 不等于生产接纳已执行。 |
| E025 | R1 现有 Admission 输入测试包含合同 fixture；尚未核查真实 admit_evidence 输出到 R1 的组合消费，不因两侧对象自洽即确认本边。 |
| E032 | 已核查缺失 Assessment 构造 failure 的合同；该特定 failure 请求传至实际 publisher 的组合/服务器路径尚未核查。 |
| E046 | 仅静态定位 CLI dispatch 与 Release Workflow，未跟踪一次真实请求到服务器调度/执行的当前身份链。 |
| E066 | 未观察当前发行 Workflow 的实际 needs/permissions/环境与新鲜 provider 事实。 |
| E067 | 本轮没有执行 Tag/Release/asset 写入；native publication authority/服务器响应不在已有全链验收范围。 |
| E051 | Run/Job provider 解析与 Produce 各有证据；本边实际 provider 事实到当前 Producer 的来源/版本/组合断言尚未单独核查。 |
| E052 | Produce/Terminal Plan 各自输入合同存在，尚未核查一次真实 Produce 输出经配置文件送入 post_completion_plan 的组合链。 |
| E054 | Terminal closure validator 的合同已核查；本边到 execute 的原生 Terminal 执行与失败传播组合留待定向观察。 |
| E071 | 未观察真实 executor 按 T2 Remaining Work 减小当前执行集合。 |
| E073 | 未观察真实 executor/receiver 消费 T1-a Coverage/Remaining Work。 |
| E074 | 三个固定 SHA 的可见 Check 列表未出现稳定 Required 名称；没有观察实际 POST/App→Required 服务器消费链。 |
| E076 | 未观察 R5 Disposition 的实际 retain/revoke 执行者；API 返回文档不代行执法。 |
| E086 | 未观察 #124 wrapper/bound/authorized 的真实执行/Admission/R1 消费者。 |
| E002 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E003 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E006 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E007 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E008 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E009 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E011 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E012 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E013 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E014 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E015 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E016 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E017 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E018 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E019 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E022 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E027 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E028 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E030 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E033 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E034 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E036 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E037 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E038 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E039 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E040 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E041 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E042 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E043 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E044 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E045 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E048 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E049 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E050 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E055 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E056 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E059 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E060 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E061 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E062 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E065 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E068 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E069 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E070 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E077 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E078 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E080 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E082 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E083 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E084 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |
| E085 | 本边尚未登记直接交接断言/已核实数据路径；相关组件测试保留为上下文，局部已证层次单独列出。 |

权威回调尚未接通的合同边还分别列出真实 Issuer、当前批准/撤销/有效期、Scope、Provider/transport 与消费者的 `needed_observation`。这些条目用于原 Issue 的后续验收；本阶段没有新增修复方案、修改公共接口或运行 D 的组合对抗测试。

上级入口：[C 阶段索引](README.md)。
