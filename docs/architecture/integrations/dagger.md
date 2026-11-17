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

## Plan and link observation cache boundaries

`plan-execution`, `execute-plan` and the compound Plan fixture use the pinned
SDK's `cache="never"` Function policy. A new request reevaluates module-local
commands and dynamic inputs. This policy does not disable native Container
Exec, tool installation or Directory/File caching. A retained `PlanExecution`
still supplies Report, Artifact and Enforce without starting tools again.

The Lychee container consumes the full caller Directory except
`tooling.SOURCE_NOISE`; document targets, local link destinations, ignore files
and Lychee configuration remain inputs. It does not use the broader pytest
Source exclusions. An explicit CLI `--offline`/`--offline=true` scan keeps the
deterministic Exec cache. A config-only offline setting is treated
conservatively as potentially online.

For online scans, only the link-reading Exec receives the actual request time
in `ECI_LINK_READ_REQUESTED_AT_NS`. The engine's beta.16 `currentTimestamp`
returns second-resolution values, so it cannot distinguish two reads in the
same second. Module `time.time_ns()` supplies the more precise request time;
it is not applied to installation, Source or unrelated computation. The
adapter forces the native `--cache=false` option for these scans, replacing
valid CLI cache toggles and overriding `cache=true` in Lychee configuration,
so refreshing Exec cannot silently reuse an older `.lycheecache` response.
Other native options and malformed cache values retain native diagnostics.

The saved evidence records the effective argv, consumed Directory digest,
requested time and original process start/end times. Container-delegated Git
inventory and Lychee results retain `result_origin=dagger`; their host adapter
context remains explicit. These locations and timestamps do not prove #87
Trace/Origin attribution, #66 target Source closure, #63 historical acceptance
or #61 Release permission. Host execution retains its own native behavior.
Observed cache and HTTP results are in the
[input-cache verification](../../verification/dagger-input-cache/README.md).

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
