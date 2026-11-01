# External Tools and CLIs

Inventory: [technology](../technology.md). Up: [Integrations](README.md).

## Ruff

- **Status:** adopted
- **Pin:** `ruff==0.16.10` in root dev dependencies and `uv.lock`; standard settings in `pyproject.toml`.
- **Used by:** native Dagger `lint` and `format-check`, and public consumer functions.
- **Contract:** [local checks](../local-checks.md).
- **Verify:** `dagger check --engineering-ci --check=lint --check=format-check`.

## Git CLI

- **Status:** adopted; host / runner Git is not separately pinned.
- **Used by:** `release/` and `tools/consumer.py` for source inspection and release tagging; operators use native Git/gh for PR delivery.
- **Contract:** [release workflow](../release/workflow.md), [consumer usage](../../../guides/new-repository.md).
- **Verify:** `tests/test_manual_release.py`, `tests/test_sync_consumer.py`.

## GitHub CLI (gh)

- **Status:** adopted; host / runner gh is not separately pinned.
- **Used by:** `github_cli.py`, `cli/download.py`, `cli/release.py` for API access, workflow requests and artifacts.
- **Contract:** [CLI boundaries](../cli-boundaries.md), [release workflow](../release/workflow.md).
- **Verify:** `tests/test_github_runs.py`, `tests/test_release_verification.py` and `tests/test_release_transport.py`; mock dispatch never creates a real Workflow.

## OSV-Scanner

- **Status:** adopted
- **Pin:** `2.6.0` — `src/engineering_ci/dependency_security.py` (`SCANNER_VERSION`),
  `.github/workflows/reusable-python-ci.yml` (fixed SHA256 checksums per platform)
- **Used by:** `src/engineering_ci/dependency_security.py`, `src/engineering_ci/tools/security.py`,
  `.github/workflows/reusable-python-ci.yml` (`dependency-security` job) — native `uv.lock`
  vulnerability scanning against OSV database
- **Contract:** [dependency security](../dependency-security.md)
- **Verify:** `tests/test_dependency_security.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## actionlint

- **Status:** adopted
- **Pin:** `actionlint-py==1.7.12.25` wrapping actionlint `1.7.12` —
  `.dagger/src/engineering_ci/script_analysis.py` (`ACTIONLINT_WRAPPER`, `ACTIONLINT_VERSION`)
- **Used by:** the `workflow-analysis` native Dagger Check and the public
  `actionlint_workflows` function — GitHub Actions Workflow static analysis
- **Contract:** [script analysis](../testing/script-analysis.md)
- **Verify:** `tests/test_script_analysis.py`; the pinned tool is executed against the
  committed clean and broken Workflows by `fixture_workflow_analysis` in CI

## PSScriptAnalyzer

- **Status:** adopted
- **Pin:** `1.9.0` — `src/engineering_ci/powershell_analysis.py` and
  `.dagger/src/engineering_ci/script_analysis.py` (`PSSCRIPTANALYZER_VERSION`)
- **Used by:** `src/engineering_ci/powershell_analysis.py` on the `windows-2025` Job of
  `.github/workflows/ci.yml` and of the reusable Workflow — PowerShell static analysis
- **Contract:** [script analysis](../testing/script-analysis.md)
- **Verify:** `tests/test_script_analysis.py`; the analyzer itself has not been run yet
