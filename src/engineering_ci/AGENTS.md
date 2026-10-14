# Engineering CI Source Tree — Agent Instructions

Applies to `src/engineering_ci/`. Extends the project root [AGENTS.md](../../AGENTS.md).
Architectural reference: [repository overview](../../docs/architecture/repository-overview.md),
[CLI boundaries](../../docs/architecture/cli-boundaries.md).

## Subsystem Navigation

| Directory / File | Subsystem & Primary Responsibility | Nested Rules |
| --- | --- | --- |
| `checks.py`, `check_operations.py`, `checks_container.py`, `dagger_checks.py` | Local and container check orchestration, check evidence reuse, tool discovery, and environment probing. | — |
| `pipeline.py`, `toolchain.py`, `_paths.py` | Pipeline TOML configuration parsing, scope matching, toolchain invocation, and path normalization. | — |
| `workflow_actions.py` | GitHub Actions internal step dispatcher (`checks`, `release`, `manifest`, `security`). | — |
| `workflow_consistency.py` | Validates consistency between Actions workflow YAML and `pipeline.toml` check bindings. | — |
| `dependency_security.py` | Standalone `uv.lock` parser and pinned OSV-Scanner runner for vulnerability scanning. | — |
| `github_cli.py` | Low-level wrapper for `gh` CLI invocations (API calls, artifact downloads, release assets). | — |
| `cli/` | Public CLI command dispatch (`checks`, `workflow`, `download`, `release`, `trace`, `mine`). | — |
| `workflow_adapters/` | Harness tool catalog registration, CI domain policy adaptation, capability admission, and feedback. | [workflow_adapters/AGENTS.md](workflow_adapters/AGENTS.md) |
| `execution/` | Asynchronous workflow execution FSM, run identity, state persistence, and download collection. | [execution/AGENTS.md](execution/AGENTS.md) |
| `release/` | Release lifecycle FSM, delivery context, version management, manifest validation, and publication. | [release/AGENTS.md](release/AGENTS.md) |
| `workflow_core/` | Compatibility facade delegating generic workflow contracts and runtime to `vesryn.workflow_core`. | — |
| `tracing/`, `mining/` | Facades delegating generic trace archive and mining to Vesryn, retaining CI-specific feedback and bindings. | — |
| `tools/` | Standalone verification scripts and E2E tools (`security.py`, `consumer.py`, `workflow_e2e.py`). | — |

## Cross-Module Modification Entry Points

- **Adding or modifying checks:**
  1. Register check name, scope, arguments, and required tools in `config/pipeline.toml`.
  2. Implement or update operation logic in `checks.py` / `check_operations.py`.
  3. Wire CI step invocation in `workflow_actions.py` and `.dagger/src/engineering_ci/pr_checks.py`.
  4. Verify YAML/TOML alignment with `workflow_consistency.py`.
- **Modifying CLI entry points:**
  1. Update argument parsing and dispatch under `cli/`.
  2. Implement underlying logic in the owning subsystem (`execution/`, `release/`, `checks.py`).
  3. Ensure non-interactive mode emits clean JSON on stdout and redirects progress/diagnostics to stderr.
  4. Update [docs/architecture/cli-boundaries.md](../../docs/architecture/cli-boundaries.md).
- **Updating state or release schemas:**
  - Modifying `execution/models.py` requires updating [docs/architecture/execution/state.schema.json](../../docs/architecture/execution/state.schema.json).
  - Modifying `release/manifest.py` requires updating [docs/architecture/release/manifest.schema.json](../../docs/architecture/release/manifest.schema.json).
- **Modifying Harness adapters:**
  - Adapter changes in `workflow_adapters/` must preserve the public entry-point signature registered in `pyproject.toml` (`engineering-ci = "engineering_ci.workflow_adapters.checks:catalog"`).
  - Keep `workflow_core/` import-free of engineering-ci domain modules to maintain clean extraction boundaries.
