# External Tools and CLIs

Inventory: [technology](../technology.md). Up: [Integrations](README.md).

## Ruff

- **Status:** adopted
- **Pin:** `ruff==0.16.10` in root dev dependencies and `uv.lock`; standard settings in `pyproject.toml`.
- **Used by:** native Dagger `lint` and `format-check`, and public consumer functions.
- **Contract:** [local checks](../local-checks.md).
- **Verify:** `dagger check --engineering-ci --check=lint --check=format-check`.

## Lefthook

- **Status:** adopted
- **Pin:** `lefthook==2.2.2` in root dev dependencies (`pyproject.toml`, `uv.lock`) and `min_version: "2.2.2"` in `lefthook.yml`.
- **Used by:** root `lefthook.yml`, `src/engineering_ci/hooks.py`, `src/engineering_ci/hook_checks.py`, `src/engineering_ci/pytest_contracts.py`, `src/engineering_ci/cli/verify.py`, `src/engineering_ci/verify_report.py` — native Git hook installation (`pre-commit`, `pre-push`), job activation, shared `index.lock` private-snapshot `write-tree` fallback across capture and active-index verification, safe parallelism, activation-bound Hook stage timing sidecars (`.timing-*`, written at `finally:` exits with `per_ref` summaries on `run_push_checks` and preserving `.execution`/`.source-rejection`/`.timing-run` when post-check `_index_source` raises `index_changed_during_check`), unified `subprocess.Popen` `verify` timing/status receipts (`native_started`, `native_exit_code`, explicit `native_exit_source`, `outer_exit_code`, `declared_verify_jobs`, explicit non-emission `child_references`, `signal`, `error`, and `report_status` projected distinctly into `verify report` with `verify_timing_status_conflict`/`verify_timing_invalid` detection so corrupt timing or stale status files cannot mask seal failures), bounded Lefthook output parsing distinguishing `cumulative_child_durations` on `Group.Duration` (`wall_clock_seconds="unknown"`) vs `lefthook_reported_wall_seconds` and `bytes_parsed`, and strictly read-only tiered report correlation (`hook_checks.inspect_invocation_offline` offline owner validation with shared `.activation` conflict and Plan-based `expected_unit_count`/missing-sibling/push `remote_ref` checks, canonical tuple keys, per-concrete-execution unit matching where multi-execution Hook containers retain `multi_execution_container`/`same_execution_proven=false` even when expected siblings are missing, sibling-scoped conflict isolation, shared pure-stdlib `pytest_contracts.py` Schema v1 & JUnit contract plus `CollectReport`/`has_session_start`/`slowest_phases_scope`, explicit `#168` `--pytest-invocation` directory reading without `latest` heuristics, parent-conflict revocation of child `verified_manifest_digest` and `core-junit.xml` manifest verification before alias collapse, `content_equivalent` content tier, `#75`/`#131` Attempt-context & `tools/performance.py` metrics + `cicd_telemetry` membership validation, `#87 A.1` vs `#87 B` gate, and `execution.verify_producer_handoff_bundle` duplicate/unexpected artifact checks); `engineering-ci` owns Git event capture, Source materialization, Execution, Reconciliation, and local exit propagation.
- **Constraints:** `2.2.2` requires top-level `lefthook:` stream framing for space-containing `pre-push` arguments, single-consumer `use_stdin` handling, and native `files` / `{files}` activation on `pre-push` (see [upstream observations](../../../.agent/upstream-observations.md)). Local Hook Receipts remain `trusted=false`, `required=false`.
- **Contract:** [selection](../selection.md), [reconciliation](../reconciliation.md), [common tasks](../../../guides/common-tasks.md#lefthook-本地入口).
- **Verify:** `tests/test_hooks.py`, `tests/test_native_hooks.py`, `tests/test_verify_cli.py`, `tests/test_verify_report.py`.

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
