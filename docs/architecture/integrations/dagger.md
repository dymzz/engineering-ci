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

## Dagger native OpenTelemetry export

- **Status:** adopted (the native export path); experimental (ECI's observation receipt)
- **Pin:** Engine `1.0.0-beta.16` (`dagger-module.toml`); the CLI is installed with the same
  version through `DAGGER_VERSION` and `dl.dagger.io/dagger/install.sh`; the generated SDK
  carries no version marker
- **Used by:** `engineering_ci.dagger_telemetry` (`engineering-ci dagger-telemetry`); the CI
  Engine container is started without any OTLP configuration today
- **Contract:** [Dagger native telemetry](../dagger-native-telemetry.md)
- **Verify:** a real `dagger check` whose exported Spans reach an OTLP receiver, bound to the
  invocation through the propagated traceparent or the engine's own Check attribute
- **Verified:** 2026-11-12 @ `95c70be` (capability inventory; the live capture is CI-only)

Native facts of the pinned version: the engine exports its own Spans when
`/etc/dagger/engine.json` sets `telemetry.workloadExport` and the standard OTLP environment
names an endpoint (`grpc` or `http/protobuf`); engine Spans carry `dagger.io/check.name`,
`dagger.io/check.passed` and the `dagger.io/cache.*` contract; the generated SDK exports
module-client traces over OTLP/HTTP only and propagates `TRACEPARENT` into the engine's
GraphQL requests. ECI neither re-instruments those Spans nor adds an exporter or a collector.

## Docker

A working Docker Linux Engine is required locally. PR CI lets the pinned Dagger CLI start
its Engine; the existing reusable consumer Workflow retains its explicit Engine setup.
Host Docker is not version-pinned. Native caches are execution mechanics, not formal
release approval: remote SHA/Run Attempt/Manifest checks remain release responsibilities.
