# External Tools and CLIs

Inventory: [technology](../technology.md). Up: [Integrations](README.md).

## Ruff

- **Status:** adopted
- **Pin:** `0.16.10` — `config/pipeline.toml` (`[toolchain]`), `.dagger/pyproject.toml` (`dev`
  group); target `py311` — `config/pipeline.toml` (`[arguments]`)
- **Used by:** `src/engineering_ci/checks.py`, `check_operations.py`,
  `.dagger/src/engineering_ci/main.py` — lint and format checks in local PR flow and CI
- **Contract:** [local checks](../local-checks.md), [Pipeline configuration](../../configuration/pipeline.md)
- **Verify:** `tests/test_checks.py`, `tests/test_dagger_checks.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## Git CLI

- **Status:** adopted
- **Pin:** unpinned — host / runner `git`
- **Used by:** `.dagger/src/engineering_ci/pr_checks.py`, `src/engineering_ci/dagger_checks.py`,
  `src/engineering_ci/release/publishing.py`, `src/engineering_ci/tools/consumer.py` for
  head commit inspection, diff inspection and release tagging;
  `src/engineering_ci/tools/bootstrap.py` — consumer initialization and seed-only push
- **Contract:** [local checks](../local-checks.md), [release workflow](../release/workflow.md),
  [consumer bootstrap](../../../guides/new-repository.md#交互式建仓入口)
- **Verify:** `tests/test_manual_release.py`, `tests/test_sync_consumer.py`,
  `tests/test_repository_bootstrap.py` (real temporary Git repositories)
- **Verified:** 2026-10-15 @ `f600eeaebf74`

## GitHub CLI (gh)

- **Status:** adopted
- **Pin:** unpinned — host / runner `gh`
- **Used by:** `src/engineering_ci/github_cli.py`, `cli/download.py`, `cli/release.py` for
  GitHub REST API access, release asset uploads and workflow artifact downloads;
  `src/engineering_ci/tools/bootstrap.py` — explicit private consumer creation and identity checks
- **Contract:** [CLI boundaries](../cli-boundaries.md), [release workflow](../release/workflow.md),
  [consumer bootstrap](../../../guides/new-repository.md#交互式建仓入口)
- **Verify:** `tests/test_ghwf_cli.py`, `tests/test_repository_bootstrap.py` (substituted GitHub calls)
- **Verified:** 2026-10-15 @ `f600eeaebf74`

## aria2c

- **Status:** adopted
- **Pin:** unpinned — optional host executable configured via `config/ghwf.local.toml` or
  `GHWF_ARIA2C_PATH`
- **Used by:** `src/engineering_ci/execution/download.py`, `cli/_execution.py` — optional
  multi-connection HTTP download backend with Range resume
- **Contract:** [download configuration](../../configuration/downloads.md)
- **Verify:** `tests/test_download_backends.py`, `tests/test_http_download.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

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
