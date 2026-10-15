# Python Runtime and Tooling

Inventory: [technology](../technology.md). Up: [Integrations](README.md).

## Python

- **Status:** adopted
- **Pin:** `>=3.14` — `pyproject.toml`, `.dagger/pyproject.toml`; `3.14` — `.python-version`,
  `config/pipeline.toml` (`[toolchain]`), `.github/workflows/ci.yml`
- **Used by:** the entire project codebase in `src/` and `.dagger/src/`
- **Contract:** [repository overview](../repository-overview.md)
- **Verify:** version declarations match across manifests and configuration
- **Verified:** 2026-10-14 @ `963d809a295f`

## uv and uv_build

- **Status:** adopted
- **Pin:** `uv_build>=0.12.23,<0.13.0` — `pyproject.toml`; `uv_build>=0.8.4,<0.9.0` —
  `.dagger/pyproject.toml`; uv `0.12.23` — `config/pipeline.toml`,
  `.github/workflows/reusable-python-ci.yml`
- **Used by:** dependency resolution, lockfiles (`uv.lock`, `.dagger/uv.lock`), wheel builds
  and virtual environment management; `src/engineering_ci/tools/bootstrap_flow.py` explicitly
  runs native consumer lock/sync steps and records the observed uv version
- **Contract:** [local checks](../local-checks.md),
  [consumer bootstrap](../../../guides/new-repository.md#交互式建仓入口)
- **Verify:** `uv lock --check`, `uv build --wheel`, `tests/test_bootstrap_flow.py`
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
  `workflow_adapters/evidence.py`, `cli/workflow.py` for atomic state persistence
- **Contract:** [execution state and recovery](../execution/state-and-recovery.md)
- **Verify:** `tests/test_execution_state.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## PyYAML

- **Status:** adopted
- **Pin:** `PyYAML==6.0.3` — `pyproject.toml` (`dependencies`)
- **Used by:** `src/engineering_ci/workflow_consistency.py` — customized `yaml.SafeLoader`
  preventing automatic boolean type coercion
- **Contract:** [.github/AGENTS.md](../../../.github/AGENTS.md)
- **Verify:** `tests/test_workflow_consistency.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## jsonschema

- **Status:** adopted
- **Pin:** `jsonschema==4.26.0` — `pyproject.toml` (`dependencies`)
- **Used by:** `src/engineering_ci/execution/models.py`, `release/manifest.py` for schema
  validation of state and release manifests
- **Contract:** [execution state and recovery](../execution/state-and-recovery.md),
  [release manifest](../release/manifest.schema.json)
- **Verify:** `tests/test_execution_state.py`, `tests/test_release_manifest.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## pytest

- **Status:** adopted
- **Pin:** `pytest==9.0.3` — `pyproject.toml` (`dev` group); `pytest>=9.1.1` —
  `.dagger/pyproject.toml` (`dev` group)
- **Used by:** running unit, process, and integration test suites
- **Contract:** [testing](../testing/README.md), [pytest reporting](../testing/pytest-reporting.md)
- **Verify:** `uv run --locked pytest -q`
- **Verified:** 2026-10-14 @ `963d809a295f`

## pytest-cov

- **Status:** adopted
- **Pin:** `pytest-cov==7.0.0` — `pyproject.toml` (`dev` group)
- **Used by:** `.dagger/src/engineering_ci/pytest_quality.py` — enforces minimum line coverage
  and outputs XML/JSON reports
- **Contract:** [quality gates](../testing/quality-gates.md)
- **Verify:** `tests/test_pytest_quality.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## Vesryn

- **Status:** adopted
- **Pin:** `vesryn==0.1.0` (pinned git rev `fb984d44f84e1b4674ccb391746da14818de2bd4` in
  `[tool.uv.sources]`); optional extras `harness`, `trace`, `mining`, `telemetry`
- **Used by:** `src/engineering_ci/workflow_adapters/`, delegating portable Agent Harness
  workflow execution, tracing archive, projection and mining backends
- **Contract:** [repository split](../harness-integration/repository-split.md),
  [Harness capabilities](../harness-integration/capabilities.md),
  [Provider and selection Binding](../harness-integration/registry.md)
- **Verify:** `tests/test_harness_boundary.py`, `tests/test_workflow_adapter.py`,
  `tests/test_selection_binding.py`
- **Verified:** 2026-10-15 @ `8dc51e8c2524`
