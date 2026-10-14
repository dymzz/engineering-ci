# GitHub Actions Integrations

Inventory: [technology](../technology.md). Up: [Integrations](README.md).
Rules: [.github/AGENTS.md](../../../.github/AGENTS.md). Contracts: [Release workflow](../release/workflow.md),
[Pipeline bindings](../../configuration/pipeline.md).

## GitHub Actions

- **Status:** adopted
- **Pin:** runner `ubuntu-24.04`; `actions/checkout@v7.0.1`, `actions/upload-artifact@v7.0.2`,
  `actions/download-artifact@v5`, `astral-sh/setup-uv@v7` (uv `0.12.23`),
  `actions/cache@v6.1.0`
- **Used by:** `.github/workflows/ci.yml` (PR and push verification),
  `.github/workflows/release.yml` (formal release workflow),
  `.github/workflows/reusable-python-ci.yml` (reusable check orchestrator)
- **Contract:** [.github/AGENTS.md](../../../.github/AGENTS.md),
  [Release workflow](../release/workflow.md)
- **Verify:** `tests/test_workflow_consistency.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## Reusable Python CI Workflow

- **Status:** adopted
- **Pin:** internal definition `.github/workflows/reusable-python-ci.yml`; published via Git
  tags and commit SHAs for consumer repositories (e.g. Vesryn pins `v0.6.5` @
  `804d791c60d34468166b6910c88fbc8149fdbf26`)
- **Used by:** standard multi-phase CI orchestration for Python projects: runs Dagger check
  functions, gates single-execution pytest reports, and provides optional standalone
  OSV-Scanner execution
- **Contract:** [.github/AGENTS.md](../../../.github/AGENTS.md),
  [pytest reporting](../testing/pytest-reporting.md),
  [dependency security](../dependency-security.md)
- **Verify:** `tests/test_pytest_ci.py`, `tests/test_pytest_report.py`
- **Verified:** 2026-10-14 @ `963d809a295f`
