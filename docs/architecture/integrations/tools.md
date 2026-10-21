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
- **Used by:** `release/` and `tools/consumer.py` for commit/PR delivery, source inspection and release tagging.
- **Contract:** [release workflow](../release/workflow.md), [consumer usage](../../../guides/new-repository.md).
- **Verify:** `tests/test_manual_release.py`, `tests/test_sync_consumer.py`.

## GitHub CLI (gh)

- **Status:** adopted; host / runner gh is not separately pinned.
- **Used by:** `github_cli.py`, `cli/download.py`, `cli/release.py` for API access, workflow requests and artifacts.
- **Contract:** [CLI boundaries](../cli-boundaries.md), [release workflow](../release/workflow.md).
- **Verify:** `tests/test_ghwf_cli.py`, `tests/test_unified_cli.py` use substituted GitHub calls.

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
