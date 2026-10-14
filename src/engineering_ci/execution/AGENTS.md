# Execution Subsystem — Agent Instructions

Manages asynchronous CI run orchestration, state persistence, and artifact collection. Extends [src/engineering_ci/AGENTS.md](../AGENTS.md).

## FSM & State Transitions

- **Explicit Transitions Only**: Uses `transitions.Machine` with `auto_transitions=False`. State transitions must follow the formal graph (`INIT` -> `VALIDATED` -> `DISPATCHING` -> `RUNNING` -> `COLLECTING` -> `FINISHED`).
- **Pre-Persistence Invariant**: Every state change must be persisted atomically to `StateStore` (under `FileLock`) *before* issuing network requests or dispatching runs.
- **Schema Validation**: Execution state documents must validate against [docs/architecture/execution/state.schema.json](../../../docs/architecture/execution/state.schema.json).

## Run Identity & Recovery

- **Immutable Identity**: Run IDs, parameters, and creation timestamps are immutable once initialized.
- **Crash Recovery**: If an execution crashes or loses connectivity during `DISPATCHING`, transition to `UNKNOWN`. Never re-dispatch automatically.
- **Explicit Resume**: Resuming interrupted runs requires explicit user command (`--resume <run_id>`). New runs always require new run IDs.

## Artifact Transport & Safety

- **Transport Isolation**: `aria2` and `native` HTTP download backends must maintain independent state. Never mix `.aria2` control files and native `.part` files.
- **Extraction Guard**: Validate all zip/tar archive paths before unpacking to prevent path traversal outside destination directories.
