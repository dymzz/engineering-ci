# Python Runtime and Tooling

Inventory: [technology](../technology.md). Up: [Integrations](README.md).

## Python

- **Status:** adopted
- **Pin:** `>=3.14` — `pyproject.toml`, `.dagger/pyproject.toml`; `3.14` — `.python-version`,
  the Dagger repository container and release Workflow
- **Used by:** the entire project codebase in `src/` and `.dagger/src/`
- **Contract:** [repository overview](../repository-overview.md)
- **Verify:** version declarations match across manifests and configuration
- **Verified:** 2026-10-14 @ `963d809a295f`

## uv and uv_build

- **Status:** adopted
- **Pin:** `uv_build>=0.12.23,<0.13.0` — `pyproject.toml`; `uv_build>=0.8.4,<0.9.0` —
  `.dagger/pyproject.toml`; uv `0.12.23` — release Workflow setup; native check image is declared in `repository.py`,
  `.github/workflows/reusable-python-ci.yml`
- **Used by:** dependency resolution, lockfiles (`uv.lock`, `.dagger/uv.lock`), wheel builds
  and virtual environment management
- **Contract:** [local checks](../local-checks.md)
- **Verify:** `uv lock --check`, `uv build --wheel`
- **Verified:** 2026-10-15 @ `04388dbdcf7a`

## transitions

- **Status:** adopted
- **Pin:** `transitions==0.9.3` — `pyproject.toml` (`dependencies`)
- **Used by:** `src/engineering_ci/execution/fsm.py` (download and execution FSM),
  `src/engineering_ci/release/lifecycle.py` (release lifecycle FSM)
- **Contract:** [execution state and recovery](../execution/state-and-recovery.md),
  [release workflow](../release/workflow.md)
- **Verify:** `tests/test_execution_state.py`, `tests/test_release_lifecycle.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## filelock

- **Status:** adopted
- **Pin:** `filelock==3.20.3` — `pyproject.toml` (`dependencies`)
- **Used by:** `src/engineering_ci/execution/store.py`, `execution/collector.py`,
  for atomic state persistence
- **Contract:** [execution state and recovery](../execution/state-and-recovery.md)
- **Verify:** `tests/test_execution_state.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## pytest

- **Status:** adopted
- **Pin:** `pytest==9.0.3` — `pyproject.toml` (`dev` group); `pytest>=9.1.1` —
  `.dagger/pyproject.toml` (`dev` group)
- **Used by:** running unit, process, and integration test suites
- **Contract:** [testing](../testing/README.md), [pytest reporting](../testing/pytest-reporting.md)
- **Verify:** `dagger check --engineering-ci --check=tests`
- **Verified:** 2026-10-14 @ `963d809a295f`

## pytest-cov

- **Status:** adopted
- **Pin:** `pytest-cov==7.0.0` — `pyproject.toml` (`dev` group)
- **Used by:** `.dagger/src/engineering_ci/pytest_quality.py` — enforces minimum line coverage
  and outputs XML/JSON reports
- **Contract:** [quality gates](../testing/quality-gates.md)
- **Verify:** `tests/test_pytest_quality.py`
- **Verified:** 2026-10-14 @ `963d809a295f`
