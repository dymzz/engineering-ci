# PR #165 impact mapping (D1 focused reconciliation)

Objects changed by this round or named by review 6095806576 / 6095883153 /
6095896716, resolved with the existing Tach graph, `ci-selection.toml`
module/reverse closure and exact/prefix rules. Full 55-object matrix method is
the same; this table is the verified subset and the four newly mapped dynamic
consumers (ci_applicability, ci_check_inputs, pytest_contracts, verify_report).

Method: `selection.load_config` + `selection._tach_graph` reverse closure;
module tests are the union of `module_tests` over the module and its reverse
closure; Dagger helpers use the matching exact rule first, otherwise the
`.dagger/src/engineering_ci_module/` prefix rule.

| Object | Rule | Selected tests (count) | Tests |
|---|---|---|---|
| engineering_ci.ci_applicability | module_tests + reverse | 6 | tests.test_ci_applicability, tests.test_unified_cli, tests.test_github_runs, tests.test_native_hooks, tests.test_release_verification, tests.test_verify_cli |
| engineering_ci.ci_check_inputs | module_tests + reverse | 9 | tests.test_ci_applicability, tests.test_link_check, tests.test_plan_result, tests.test_python_static, tests.test_unified_cli, tests.test_github_runs, tests.test_native_hooks, tests.test_release_verification, tests.test_verify_cli |
| engineering_ci.pytest_contracts | module_tests + reverse | 13 | tests.test_ci_performance, tests.test_manual_release, tests.test_plan_result, tests.test_pytest_ci, tests.test_pytest_matrix, tests.test_pytest_quality, tests.test_pytest_report, tests.test_verify_report, tests.test_unified_cli, tests.test_github_runs, tests.test_native_hooks, tests.test_release_verification … |
| engineering_ci.verify_report | module_tests + reverse | 6 | tests.test_verify_cli, tests.test_verify_report, tests.test_unified_cli, tests.test_github_runs, tests.test_native_hooks, tests.test_release_verification |
| engineering_ci.execution | module_tests + reverse | 27 | tests.test_d_f02_interop, tests.test_execution, tests.test_expected_checks, tests.test_generated_producer_resolution, tests.test_native_producer, tests.test_plan_contract, tests.test_plan_result, tests.test_pre_execution_source_proof, tests.test_selection, tests.test_workflow_security_checks, tests.test_workflow_target_sources, tests.test_unified_cli … |
| engineering_ci.hook_checks | module_tests + reverse | 8 | tests.test_execution, tests.test_native_hooks, tests.test_unified_cli, tests.test_github_runs, tests.test_release_verification, tests.test_verify_cli, tests.test_hooks, tests.test_verify_report |
| engineering_ci.reconciliation | module_tests + reverse | 24 | tests.test_admission, tests.test_d_f02_interop, tests.test_execution, tests.test_generated_producer_resolution, tests.test_historical_reuse, tests.test_issue52_githubreceiver_e2e, tests.test_plan_result, tests.test_reconciliation, tests.test_required_assessment, tests.test_workflow_security_checks, tests.test_workflow_target_sources, tests.test_unified_cli … |
| .dagger/src/engineering_ci_module/pytest_report.py | exact | 8 | tests.test_ci_performance, tests.test_manual_release, tests.test_plan_result, tests.test_pytest_ci, tests.test_pytest_matrix, tests.test_pytest_quality, tests.test_pytest_report, tests.test_verify_report |
| .dagger/src/engineering_ci_module/repository.py | exact | 11 | tests.test_ci_applicability, tests.test_dagger_native_telemetry, tests.test_execution, tests.test_link_check, tests.test_plan_result, tests.test_pytest_ci, tests.test_pytest_matrix, tests.test_pytest_quality, tests.test_python_static, tests.test_script_analysis, tests.test_weaver |
| .dagger/src/engineering_ci_module/tooling.py | exact | 8 | tests.test_ci_applicability, tests.test_dagger_native_telemetry, tests.test_execution, tests.test_link_check, tests.test_plan_result, tests.test_python_static, tests.test_script_analysis, tests.test_weaver |
| .dagger/src/engineering_ci_module/ (prefix) | prefix | 8 | tests.test_ci_applicability, tests.test_dagger_native_telemetry, tests.test_execution, tests.test_link_check, tests.test_plan_result, tests.test_python_static, tests.test_script_analysis, tests.test_weaver |

## Fixed gaps this round

- repository.py / tooling.py exact rules and the Dagger prefix rule now select
  `tests.test_ci_applicability`.
- `pytest_contracts` selects `tests.test_ci_performance`.
- Dagger implementation identity includes `engineering_ci/ci_check_inputs.py`.
- `tests/test_execution.py` fixtures hash the inspected Tach blob (blob first,
  file fallback) so Windows autocrlf checkouts validate against the Plan digest.

## Not covered by this table

- Full 55-object matrix and every asset/AST/动态 loader relation are not
  enumerated here; only the changed/named objects above were reconciled.
- Selection result per single-file change was not re-executed for each row;
  closure is derived from the configured graph, not from a runtime selection.
