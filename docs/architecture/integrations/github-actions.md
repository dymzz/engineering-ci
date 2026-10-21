# GitHub Actions Integrations

Inventory: [technology](../technology.md). Up: [Integrations](README.md).
Rules: [Workflow contracts](../release/workflow.md). Contracts: [Release workflow](../release/workflow.md),
[native Checks](../local-checks.md).

## GitHub Actions

- **Status:** adopted
- **Pin:** runner `ubuntu-24.04`; `actions/checkout@v7.0.1`, `actions/upload-artifact@v7.0.2`,
  `actions/download-artifact@v5`, `astral-sh/setup-uv@v7` (uv `0.12.23`),
  `actions/cache@v6.1.0`
- **Used by:** `.github/workflows/ci.yml` (PR and push verification),
  `.github/workflows/release.yml` (formal release workflow),
  `.github/workflows/reusable-python-ci.yml` (reusable check orchestrator)
- **Contract:** [Workflow contracts](../release/workflow.md),
  [Release workflow](../release/workflow.md)
- **Verify:** actual native Check execution and release Manifest tests
- **Verified:** 2026-10-14 @ `963d809a295f`

## Reusable Workflow public contract

Consumers call `.github/workflows/reusable-python-ci.yml` through `workflow_call`,
pinned to the same published tag/SHA as their Dagger module. It checks out the
caller, discovers its Dagger checks and runs them on ubuntu-24.04 with the fixed
Dagger CLI/Engine. The caller retains its own Job `needs`, events and permissions;
Engineering CI does not create another remote DAG from TOML.

| Input | Type / default | Contract |
| --- | --- | --- |
| verify_failure_propagation | boolean / false | Existing Smoke-only Ruff negative/recovery probe; ordinary consumers leave disabled |
| verify_module_lock | boolean / false | Core Release SDK generation and module lock check |
| pytest_report_function | string / empty | Caller Dagger function exporting the original-fact bundle; empty runs existing checks normally |
| pytest_check | string / empty | Exact discovered check replaced by the report execution; must exist and match the function's suite |
| pytest_report_scenario | string / empty | Core fixture-only scenario; ordinary callers leave empty |
| pytest_artifact_name | string / pytest-report | Unique name when several report jobs share a Run |
| dependency_security_mode | string / off | off / report / enforce; errors remain visible even when non-blocking |
| dependency_security_project | string / . | Caller-relative uv project, confined to workspace |
| dependency_security_exceptions | string / empty | Explicit finite native advisory exceptions TOML, confined to workspace |
| dependency_security_public_indexes | string / empty | One approved public PyPI mirror URL per line |
| dependency_security_git_policy | string / empty | v0.6.7 exact repository/Commit query policy; empty retains rejection of Git sources |
| dependency_security_source_ref | string / v0.4.7 | Fixed helper ref; select the candidate/published SHA containing any newly used inputs |
| dependency_security_artifact_name | string / dependency-security | Native security evidence Artifact name |

The optional Secret `ENGINEERING_CI_READ_TOKEN` permits reading the private
module/helper and caller-approved Git dependencies. Configure minimal Contents
read access to the required repositories; it does not grant platform permission
to reuse a private Workflow. Enable the appropriate GitHub Actions access for
the caller separately. Dagger Secret forwarding remains explicit in the caller module;
merely defining a host variable does not pass it to test containers.

Outputs `pytest_artifact_id` and `security_artifact_id` are native uploaded
Artifact IDs; disabled modes or failed delivery may yield empty outputs. Empty
is unavailable, not a zero-valued ID or a passed check. Download through the
existing Actions API/Download CLI. Report Artifact/Summary delivery cannot
erase the original pytest, quality, other Dagger or security gate failure.
Reporting executes its chosen suite once and runs every other discovered check;
wrong names or missing original evidence fail configuration/gating.

The actual bundle and status contracts remain authoritative in
[pytest reporting](../testing/pytest-reporting.md),
[Matrix](../testing/python-matrix.md) and [dependency security](../dependency-security.md).
The separate core `ci.yml` is a repository verification implementation;
consumers use the reusable workflow above, not the core test repository profile.

## Reusable Python CI Workflow

- **Status:** adopted
- **Pin:** internal definition `.github/workflows/reusable-python-ci.yml`; published via Git
  tags and commit SHAs for consumer repositories (e.g. Vesryn pins `v0.6.5` @
  `804d791c60d34468166b6910c88fbc8149fdbf26`)
- **Used by:** standard multi-phase CI orchestration for Python projects: runs Dagger check
  functions, gates single-execution pytest reports, and provides optional standalone
  OSV-Scanner execution
- **Contract:** [Workflow contracts](../release/workflow.md),
  [pytest reporting](../testing/pytest-reporting.md),
  [dependency security](../dependency-security.md)
- **Verify:** `tests/test_pytest_ci.py`, `tests/test_pytest_report.py`
- **Verified:** 2026-10-14 @ `963d809a295f`
