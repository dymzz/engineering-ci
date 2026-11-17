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

Module-local `command` actions do not acquire an independent Engine cache
from that policy. They run for each new Plan; the cache-never entrypoint is
not a claim that an entire mixed Plan deduplicates its deterministic work.
For an explicitly source-only producer, the existing v2 prerequisite boundary
also accepts `dagger_command`:

```toml
[prerequisite_actions.build_docs]
type = "dagger_command"
argv = ["python", "tools/build_docs.py"]
image = "registry.example/python@sha256:<64-lowercase-hex-digest>"
platform = "linux/amd64"
cache = "source"
```

This declaration requires a digest-pinned Linux amd64 image with `python`
3.11+ and the standard library. Only these five keys and `cache="source"`
are supported. The caller declares that Source, fixed runtime and arguments
close the computation's inputs; ECI does not infer determinism from an action
name or authenticate that declaration. Use this mode for source-only
computation, with an actual v2 Data/PASS edge. Dynamic external observations,
Host authorization and publication/remote writes need their own execution
boundary and cannot use this source-only declaration as freshness evidence.

The native Exec mounts the complete caller Directory read-only at
`/workspace`, runs the declared argv in that directory, and exposes
`ENGINEERING_CI_ARTIFACT_DIR=/eci-artifacts`; `{artifact_dir}` substitutions use
the same path. Image digest, platform, Directory and argv are real native
inputs. No Run/Attempt or observation time enters this Exec. Its output
Directory feeds existing v2 closure verification and native File/Directory
overlays; raw outputs, native exit/stdout/stderr, original process times and
Directory references remain in the bundle, including failed partial outputs.
Host execution refuses this native-only type with
`prerequisite_requires_dagger`; policy-only/post-check declarations refuse
before execution. Older consumers reject the unknown action type.

The producer records `engine_cache_outcome="unknown"` and does not invent
Span cache attribution. Native execution counts and the remaining cheap
module-local/local-Check reevaluations are documented in the
[mixed-Plan verification](../../verification/dagger-input-cache/mixed-plan/README.md).

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
