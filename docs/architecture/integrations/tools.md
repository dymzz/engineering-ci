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
- **Used by:** root `lefthook.yml`, `src/engineering_ci/hooks.py`, `src/engineering_ci/hook_checks.py`, `src/engineering_ci/cli/verify.py`, `src/engineering_ci/verify_report.py` — native Git hook installation (`pre-commit`, `pre-push`), job activation, shared `index.lock` private-snapshot `write-tree` fallback across capture and active-index verification, safe parallelism, activation-bound Hook stage timing sidecars (`.timing-*`, preserving first-success non-exclusive stage records and mid-push `per_ref` entries), unified `subprocess.Popen` `verify` timing/status receipts (`native_exit_code` separated from `report_status`; pre-spawn `spawn_failed` vs post-spawn `wait_failed` vs pre-output interrupt `native_started="unknown"`), and strictly read-only tiered report correlation (`load_invocation_binding(..., max_bytes=...)`, per-concrete-execution unit matching where multi-execution Hook containers associate per child execution without transitive bridging, `artifact_sha256` kept as cross-bundle content equivalence rather than execution identity, `hook_checks.verify_index_receipt_binding` / `verify_push_receipt_binding` `Plan[i]->Ref[i]->Receipt[i]->Execution[i]` checks plus legal zero-execution `pre-push` handling, `execution.verify_producer_handoff_bundle` File/Directory retention & `unverified_missing_plan` checks, bounded `testcase_verifies` raw diagnostic records separate from `#162 C2`, `time_source_verified=false`, diagnostic-only vs `#63`/`#61`, `#162 R17` outer launcher startup unmodified); `engineering-ci` owns Git event capture, Source materialization, Execution, Reconciliation, and local exit propagation.
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
