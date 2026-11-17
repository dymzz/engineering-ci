# C 阶段的实际证据范围

派生输入为已合并 #126 的审阅 HEAD `363bb19d381146890b57341f686b03f311fd0d0a`；本轮实现基线为 `990cd59f83153a1ad3978dc933f89af5ca4829e3`。
运行时代码/测试/Workflow/依赖相对 A/B main 无变化，索引沿用原 main 与 #124 两个独立快照，未刷新旧服务端观察的时间。

## 按边分类

| 分类 | 边数 |
| --- | ---: |
| 已验证真实生产 (`verified_production`) | 0 |
| 已验证合同但真实权威未接通 (`verified_contract_authority_unconnected`) | 64 |
| 已知缺口 (`known_gap`) | 5 |
| 证据矛盾 (`evidence_contradiction`) | 0 |
| 尚未调查 (`not_investigated`) | 17 |

以上是 **86 条已列依赖边**的证据类别，不是 ECI 授权覆盖率。0 项真实生产表示这些允许性链路没有完成所需真实来源闭包；旧 Native/CI 事实仍在原范围有效。0 项证据矛盾表示 C 未建立此类裁决，不代表 D 已证明没有矛盾。

## 逐边索引

每行的固定源码、身份/Phase/时间、failure、原始证据/测试、trust boundary 与原 Issue/PR 均在 [机器索引](index.json) 的同名 edge 中。`contract_input` 不能充作生产接线；测试名中的 real/verified 不改变 fixture 证据类型。

| 边 | 生产/验证 → 消费 | 分类 | 原归属 |
| --- | --- | --- | --- |
| [E001](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/selection.py#L3651) | `selection.plan` → `expected_checks.derive_expected_checks` | 已验证合同但真实权威未接通 | #66 / #125 |
| [E002](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/expected_checks.py#L687) | `expected_checks._verify_profile_authority` → `expected_checks.derive_expected_checks` | 已验证合同但真实权威未接通 | #66 / #125 |
| [E003](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/expected_checks.py#L1885) | `expected_checks.derive_expected_checks` → `reconciliation.derive_strict_expected` | 已验证合同但真实权威未接通 | #66 / #125 |
| [E004](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/expected_checks.py#L1885) | `expected_checks.derive_expected_checks` → `required_assessment.assess_required_obligations` | 尚未调查 | #61 / #66 / #63 |
| [E005](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/expected_checks.py#L2874) | `expected_checks.validate_projection` → `required_assessment.assess_required_obligations` | 尚未调查 | #61 / #66 / #63 |
| [E006](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/selection.py#L3233) | `selection.verify_materialized_source_snapshot` → `execution.execute` | 已验证合同但真实权威未接通 | #66 / #52 / #86 |
| [E007](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/execution.py#L2754) | `execution.build_pre_execution_source_proof` → `admission.admit_historical_reuse` | 已验证合同但真实权威未接通 | #63 / #66 |
| [E008](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/execution.py#L3030) | `execution.compare_pre_execution_to_historical_result` → `admission.admit_historical_reuse` | 已验证合同但真实权威未接通 | #63 / #66 |
| [E009](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/execution.py#L3362) | `execution.build_source_resolution` → `reconciliation.derive_strict_expected` | 已验证合同但真实权威未接通 | #66 / #52 / #86 |
| [E010](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/execution.py#L3498) | `execution.source_resolution_receipt` → `admission.admit_evidence` | 尚未调查 | #66 / #52 / #86 |
| [E011](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/execution.py#L4682) | `execution.execute` → `check_contract.validate_result` | 已验证合同但真实权威未接通 | #52 / #66 / #63 |
| [E012](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/.dagger/src/engineering_ci_module/main.py#L273) | `engineering_ci_module.main.EngineeringCi.plan_execution` → `engineering_ci_module.plan_execution.execute` | 已验证合同但真实权威未接通 | #86 / #52 / #63 |
| [E013](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/.dagger/src/engineering_ci_module/plan_execution.py#L1225) | `engineering_ci_module.plan_execution.execute` → `engineering_ci_module.plan_result.PlanExecution.enforce` | 已验证合同但真实权威未接通 | #86 / #52 / #63 |
| [E014](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/.dagger/src/engineering_ci_module/main.py#L296) | `engineering_ci_module.main.EngineeringCi.execute_plan` → `engineering_ci_module.plan_result.PlanExecution.report` | 已验证合同但真实权威未接通 | #86 / #52 / #63 |
| [E015](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/check_contract.py#L927) | `check_contract.native_result` → `check_contract.validate_result` | 已验证合同但真实权威未接通 | #52 / #66 / #63 |
| [E016](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/native_producer.py#L95) | `native_producer.test_check` → `check_contract.native_result` | 已验证合同但真实权威未接通 | #52 / #66 / #63 |
| [E017](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/native_producer.py#L157) | `native_producer.weaver_check` → `check_contract.native_result` | 已验证合同但真实权威未接通 | #52 / #66 / #63 |
| [E018](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/check_contract.py#L1053) | `check_contract.validate_result` → `check_contract.evaluate_expected_satisfaction` | 已验证合同但真实权威未接通 | #52 / #66 / #63 |
| [E019](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/check_contract.py#L1451) | `check_contract.evaluate_expected_satisfaction` → `reconciliation.reconcile_expected_results` | 已验证合同但真实权威未接通 | #66 / #52 / #63 |
| [E020](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/reconciliation.py#L1748) | `reconciliation.reconcile_expected_results` → `admission.admit_evidence` | 已验证合同但真实权威未接通 | #63 / #66 / #61 |
| [E021](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/reconciliation.py#L1984) | `reconciliation.validate_receipt` → `admission.admit_evidence` | 已验证合同但真实权威未接通 | #63 / #66 / #61 |
| [E022](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/local_actions_handoff.py#L254) | `local_actions_handoff.build_local_handoff_package` → `local_actions_handoff.validate_local_handoff_package` | 已验证合同但真实权威未接通 | #63 |
| [E023](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/local_actions_handoff.py#L326) | `local_actions_handoff.validate_local_handoff_package` → `local_actions_handoff.verify_local_handoff` | 已验证合同但真实权威未接通 | #63 |
| [E024](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/local_actions_handoff.py#L526) | `local_actions_handoff.verify_local_handoff` → `admission.admit_evidence` | 尚未调查 | #63 |
| [E025](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/admission.py#L779) | `admission.admit_evidence` → `required_assessment._validate_admission` | 尚未调查 | #63 / #66 / #61 |
| [E026](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L1341) | `required_assessment._validate_admission` → `required_assessment.assess_required_obligations` | 已验证合同但真实权威未接通 | #63 / #66 / #61 |
| [E027](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/reconciliation.py#L1984) | `reconciliation.validate_receipt` → `required_assessment.assess_required_obligations` | 已验证合同但真实权威未接通 | #61 / #66 / #63 |
| [E028](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L791) | `required_assessment._validate_universe` → `required_assessment.assess_required_obligations` | 已验证合同但真实权威未接通 | #61 / #66 / #63 |
| [E029](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L2449) | `required_assessment.assess_required_obligations` → `required_assessment.validate_assessment` | 已验证合同但真实权威未接通 | #61 / #66 / #63 |
| [E030](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L2637) | `required_assessment.validate_assessment` → `required_assessment_check.build_check_publication` | 已验证合同但真实权威未接通 | #61 |
| [E031](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_check.py#L306) | `required_assessment_check.build_check_publication` → `required_assessment_check.publish_check_run` | 已验证合同但真实权威未接通 | #61 |
| [E032](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_check.py#L388) | `required_assessment_check.publication_for_missing_assessment` → `required_assessment_check.publish_check_run` | 尚未调查 | #61 |
| [E033](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_publish.py#L463) | `required_assessment_publish.verify_publisher_identity` → `required_assessment_publish.authorize_and_publish` | 已验证合同但真实权威未接通 | #61 |
| [E034](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_publish.py#L515) | `required_assessment_publish.workflow_publish_authority` → `required_assessment_check.publish_check_run` | 已验证合同但真实权威未接通 | #61 |
| [E035](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_publish.py#L615) | `required_assessment_publish.authorize_and_publish` → `required_assessment_check.publish_check_run` | 已验证合同但真实权威未接通 | #61 |
| [E036](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_protection.py#L257) | `required_assessment_protection.inspect_server_protection` → `required_assessment_protection.validate_protection_observation` | 已验证合同但真实权威未接通 | #61 |
| [E037](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L2637) | `required_assessment.validate_assessment` → `required_assessment_release.reconcile_release_assessment` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E038](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/manifest.py#L340) | `release.manifest.validate` → `required_assessment_release.reconcile_release_assessment` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E039](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L409) | `required_assessment_release.manifest_provider_from_verification` → `required_assessment_release.reconcile_release_assessment` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E040](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L430) | `required_assessment_release.manifest_provider_from_publication` → `required_assessment_release.reconcile_release_assessment` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E041](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L673) | `required_assessment_release.reconcile_release_assessment` → `required_assessment_release.validate_release_reconciliation` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E042](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L981) | `required_assessment_release.validate_release_reconciliation` → `required_assessment_release.require_reconciliation_manifest` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E043](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L2637) | `required_assessment.validate_assessment` → `required_assessment_post.reconcile_post_stage` | 已验证合同但真实权威未接通 | #61 |
| [E044](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/security_policy.py#L684) | `security_policy.receive` → `security_policy.decide` | 已验证合同但真实权威未接通 | #56 / #53 / #57 |
| [E045](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/security_policy.py#L814) | `security_policy.decide` → `execution._build_workflow_admission_fn` | 已验证合同但真实权威未接通 | #56 / #53 / #57 |
| [E046](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/request.py#L55) | `release.request.dispatch` → `release.publishing.main` | 尚未调查 | #61 / #63 |
| [E047](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/workflow_actions.py#L10) | `workflow_actions.main` → `release.publishing.main` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E048](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/manifest.py#L340) | `release.manifest.validate` → `release.publishing.published_evidence` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E049](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/publishing.py#L114) | `release.publishing.published_evidence` → `release.publishing.publish_release` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E050](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/publishing.py#L336) | `release.publishing.main` → `release.publishing.publish_release` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E051](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/cicd_telemetry.py#L2646) | `cicd_telemetry.fetch_github_run_job` → `cicd_telemetry.produce` | 尚未调查 | #52 / #74 / #75 / #86 / #87 |
| [E052](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/cicd_telemetry.py#L857) | `cicd_telemetry.produce` → `selection.post_completion_plan` | 尚未调查 | #52 / #74 / #75 / #86 / #87 |
| [E053](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/selection.py#L4048) | `selection.post_completion_plan` → `selection.validate_post_completion_binding` | 已验证合同但真实权威未接通 | #52 / #74 / #75 / #86 / #87 |
| [E054](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/selection.py#L4174) | `selection.validate_post_completion_binding` → `execution.execute` | 尚未调查 | #52 / #74 / #75 / #86 / #87 |
| [E055](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/dagger_telemetry.py#L1649) | `dagger_telemetry.correlate_capture` → `dagger_telemetry.verify_a1_suite` | 已验证合同但真实权威未接通 | #52 / #74 / #75 / #86 / #87 |
| [E056](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/hook_coordinator.py#L575) | `hook_coordinator._require_runner_produced_report` → `hook_coordinator.execute_activated_job` | 已验证合同但真实权威未接通 | #52 / #66 / #63 |
| [E057](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/expected_checks.py#L687) | `governed-profile-and-phase` → `expected_checks._verify_profile_authority` | 已知缺口 | #66 / #125 |
| [E058](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment.py#L2449) | `complete-universe-and-non-check-evidence` → `required_assessment.assess_required_obligations` | 已知缺口 | #61 / #66 / #63 |
| [E059](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/admission.py#L779) | `raw-origin-and-resolution-authorities` → `admission.admit_evidence` | 已验证合同但真实权威未接通 | #63 / #66 / #61 |
| [E060](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/admission.py#L1527) | `historical-authorities` → `admission.admit_historical_reuse` | 已验证合同但真实权威未接通 | #63 / #66 |
| [E061](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/local_actions_handoff.py#L526) | `local-controlled-boundary` → `local_actions_handoff.verify_local_handoff` | 已验证合同但真实权威未接通 | #63 |
| [E062](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_publish.py#L615) | `actions-approval-and-execution` → `required_assessment_publish.authorize_and_publish` | 已验证合同但真实权威未接通 | #61 |
| [E063](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_check.py#L696) | `github-check-transport` → `required_assessment_check.publish_check_run` | 已验证合同但真实权威未接通 | #61 |
| [E064](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_protection.py#L257) | `github-server-enforcement` → `required_assessment_protection.inspect_server_protection` | 已知缺口 | #61 |
| [E065](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L673) | `release-authorities` → `required_assessment_release.reconcile_release_assessment` | 已验证合同但真实权威未接通 | #61 / #63 |
| [E066](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/publishing.py#L336) | `release-workflow-server` → `release.publishing.main` | 尚未调查 | #61 / #63 |
| [E067](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/release/publishing.py#L213) | `release-api-writes` → `release.publishing.publish_release` | 尚未调查 | #61 / #63 |
| [E068](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_post.py#L582) | `post-authorities` → `required_assessment_post.reconcile_post_stage` | 已验证合同但真实权威未接通 | #61 |
| [E069](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/security_policy.py#L684) | `policy-anchor-and-provider` → `security_policy.receive` | 已验证合同但真实权威未接通 | #56 / #53 / #57 |
| [E070](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/.dagger/src/engineering_ci_module/plan_execution.py#L1225) | `dagger-engine-source` → `engineering_ci_module.plan_execution.execute` | 已验证合同但真实权威未接通 | #86 / #52 / #63 |
| [E071](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/admission.py#L1527) | `admission.admit_historical_reuse` → `remaining-work-dispatch-consumer` | 尚未调查 | #63 / #66 |
| [E072](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_release.py#L242) | `required_assessment_release.require_reconciliation_manifest` → `r4-actual-publication-consumer` | 已知缺口 | #61 / #63 |
| [E073](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/admission.py#L779) | `admission.admit_evidence` → `same-run-coverage-dispatch` | 尚未调查 | #63 / #66 / #61 |
| [E074](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_check.py#L696) | `required_assessment_check.publish_check_run` → `server-check-consumer` | 尚未调查 | #61 |
| [E075](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_protection.py#L257) | `required_assessment_protection.inspect_server_protection` → `server-deny-enforcement` | 已知缺口 | #61 |
| [E076](https://github.com/dymzz/engineering-ci/blob/4bb346c57f99e4767eea78082639f0b367745140/src/engineering_ci/required_assessment_post.py#L582) | `required_assessment_post.reconcile_post_stage` → `post-disposition-enforcer` | 尚未调查 | #61 |
| [E077](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/expected_checks.py#L1885) | `expected_checks.derive_expected_checks` → `terminal_expected.bind_terminal_expected` | 已验证合同但真实权威未接通 | #52 / #66 / #61 |
| [E078](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/expected_checks.py#L2874) | `expected_checks.validate_projection` → `terminal_expected.bind_terminal_expected` | 已验证合同但真实权威未接通 | #52 / #66 / #61 |
| [E079](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1086) | `terminal_expected.seal_phase_witness` → `terminal_expected.bind_terminal_expected` | 已验证合同但真实权威未接通 | #52 / #66 / #61 |
| [E080](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/selection.py#L4042) | `selection.post_completion_plan` → `terminal_expected.post_completion_with_expected` | 已验证合同但真实权威未接通 | #52 / #66 / #61 |
| [E081](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1457) | `terminal_expected.post_completion_with_expected` → `terminal_expected.bind_terminal_expected` | 已验证合同但真实权威未接通 | #52 / #66 / #61 |
| [E082](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1121) | `terminal_expected.bind_terminal_expected` → `terminal_expected.validate_terminal_expected_binding` | 已验证合同但真实权威未接通 | #52 / #66 / #61 |
| [E083](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1121) | `terminal_expected.bind_terminal_expected` → `terminal_expected.post_completion_with_expected` | 已验证合同但真实权威未接通 | #52 / #66 / #61 |
| [E084](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/selection.py#L4168) | `selection.validate_post_completion_binding` → `terminal_expected.bind_terminal_expected` | 已验证合同但真实权威未接通 | #52 / #66 / #61 |
| [E085](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1121) | `expected-phase-completed-authorities` → `terminal_expected.bind_terminal_expected` | 已验证合同但真实权威未接通 | #52 / #66 / #61 |
| [E086](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L1457) | `terminal_expected.post_completion_with_expected` → `terminal-runtime-consumer` | 尚未调查 | #52 / #66 / #61 |

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

权威回调尚未接通的合同边还分别列出真实 Issuer、当前批准/撤销/有效期、Scope、Provider/transport 与消费者的 `needed_observation`。这些条目用于原 Issue 的后续验收；本阶段没有新增修复方案、修改公共接口或运行 D 的组合对抗测试。

上级入口：[C 阶段索引](README.md)。
