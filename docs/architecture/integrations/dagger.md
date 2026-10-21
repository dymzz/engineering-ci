# Dagger Integrations

Inventory: [technology](../technology.md). Up: [Integrations](README.md).
Rules: [.dagger/AGENTS.md](../../../.dagger/AGENTS.md).

## Dagger engine and module

Dagger 1.0.0-beta.16 is pinned by `dagger-module.toml`; the reusable Workflow installs the
same CLI/Engine. Native `dagger.toml` configures modules, environments and fixture selection.
`main.py` declares root Checks and reusable Python Functions; `repository.py` composes native
containers with manifest-first dependency preparation and a uv package cache.
Dagger owns graph scheduling, concurrency, input invalidation and execution cache. There is
no host Runner, pass receipt store or independent task registry.

Contracts: [configuration](../../configuration/dagger.md), [local checks](../local-checks.md),
[pytest results](../testing/pytest-reporting.md).

## Dagger Python SDK and packaging

The Engine-generated SDK in `.dagger/sdk` is a module dependency. Use the existing
ModuleSource code-generation API; do not edit generated SDK files.
`packaging` supplies Matrix version/specifier handling. The actual dependency versions
are in `.dagger/pyproject.toml` and `.dagger/uv.lock`.

## Docker

A working Docker Linux Engine is required locally. PR CI lets the pinned Dagger CLI start
its Engine; the existing reusable consumer Workflow retains its explicit Engine setup.
Host Docker is not version-pinned. Native caches are execution mechanics, not formal
release approval: remote SHA/Run Attempt/Manifest checks remain release responsibilities.
