# Workflow Adapters — Agent Instructions

Adapts CI domain operations (`checks.py`, `inspection.py`) into Vesryn workflow harness tools. Extends [src/engineering_ci/AGENTS.md](../AGENTS.md).

## Boundaries & Catalog

- **Entry Point**: Exported via `checks:catalog` registered in `pyproject.toml`. Do not declare duplicate tool registries.
- **Tool IDs**: Map operations to stable IDs (e.g., `python.<name>`, `engineering-ci.version-check`, `uv.lock-check`).
- **Exit Code Integrity**: Never swallow or alter underlying check exit codes. A failed check must retain its original exit code and `failed` outcome.

## Evidence & Reuse

- **Workspace Scoping**: Reports and evidence must remain inside the primary checkout root. Symlinks and external paths are rejected.
- **Digest Verification**: Reusing previous check results requires matching `sha256` digests of report and evidence files. If inputs or outputs change, re-run.

## Capability Admission

- **Approval Gates**: Approving a capability candidate requires unchanged comparison evidence, `optimization_verified == true`, and `exit_code == 0`.
- **Provenance Binding**: Candidates must bind explicit provenance targets matching the registered profile and strategy digest.
- **Runtime Admission**: Capability admission re-verifies historical comparison integrity, but tools still validate current workspace inputs.

## Feedback Handling

- **Append-Only**: Record feedback snapshots immutably; never mutate historical feedback entries.
- **Query Bounds**: Feedback queries and reviews are strictly read-only. Enforce page and byte budget limits to avoid unbounded memory usage.
