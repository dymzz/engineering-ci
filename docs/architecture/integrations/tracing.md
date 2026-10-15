# Tracing Integrations

Inventory: [technology](../technology.md). Up: [Integrations](README.md).
Contracts: [tracing configuration](../../configuration/tracing.md),
[Harness integration](../harness-integration/README.md).

## vesryn-log

- **Status:** adopted
- **Pin:** `vesryn-log==0.1.0`, immutable Git rev `030c5a8edf820588e29f53803b6baaf53b1f6e57`
  — `pyproject.toml` dev group / `uv.lock`; optional `trace` extra enables archive dependencies.
- **Used by:** `src/engineering_ci/task_observation.py` uses installed `JsonFileSink` and
  `JsonlSink`; `tracing/config.py`, `store.py`, `catalog.py`, `envelope.py`, `preflight.py`
  and `sources/codex_rollout.py` delegate physical archives directly. `cli/trace.py` retains
  basic archive command syntax without requiring Vesryn. Missing logging installation reports
  recording unavailable independently; basic version/lint gates do not require private downloads.
- **Contract:** [task recording](../cli-boundaries.md#默认任务采集与日志记录),
  [upstream public API](https://github.com/dymzz/vesryn-log/blob/b7fae198303743a327652246cdd9d951ac319a59/docs/python-api.md),
  [upstream integration](https://github.com/dymzz/vesryn-log/blob/b7fae198303743a327652246cdd9d951ac319a59/docs/integration.md).
  The documentation export under `.artifacts/vesryn-log-docs` is a local reading aid;
  it is not imported, copied into the package or treated as another implementation.
- **Verify:** `tests/test_task_observation.py` uses the real installed writers through an
  actual host dispatcher; `tests/test_trace_logging.py` blocks Harness imports for raw
  inspection and checks the logger Store/Catalog boundary. PostgreSQL acceptance remains separate.
- **Verified:** 2026-10-16 @ `742bfc4f833d`

## PostgreSQL

- **Status:** adopted
- **Pin:** unpinned — host-provided via `VESRYN_DATABASE`. Archive schema version 3 in
  `engineering_ci_trace`, initialized and migrated via vesryn-log SQL migrations 001–003
- **Used by:** `src/engineering_ci/tracing/store.py`, `catalog.py`, `preflight.py`,
  `feedback_query.py`, `feedback_summary.py`
- **Contract:** [tracing configuration](../../configuration/tracing.md),
  [Harness capabilities](../harness-integration/capabilities.md)
- **Verify:** `tests/test_workflow_capabilities.py`, `tests/test_workflow_feedback_query.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## Psycopg 3

- **Status:** adopted
- **Pin:** `psycopg[binary]==3.3.6` — `pyproject.toml` (`dev` group)
- **Used by:** `src/engineering_ci/tracing/store.py`, `catalog.py` delegate PostgreSQL access to vesryn-log
- **Contract:** [tracing configuration](../../configuration/tracing.md)
- **Verify:** `tests/test_archive.py`, `tests/integration/trace_store.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## CloudEvents SDK

- **Status:** adopted
- **Pin:** `cloudevents==2.2.0` — `pyproject.toml` (`dev` group)
- **Used by:** `src/engineering_ci/tracing/envelope.py` — CloudEvents 1.0 JSON payload
  encapsulation
- **Contract:** [tracing configuration](../../configuration/tracing.md)
- **Verify:** import in `envelope.py`; `tests/test_archive.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## OpenTelemetry SDK and OTLP Exporter

- **Status:** adopted
- **Pin:** `opentelemetry-sdk==1.45.1`, `opentelemetry-exporter-otlp-proto-http==1.45.1` —
  consumed via pinned Vesryn dependencies
- **Used by:** `src/engineering_ci/tracing/telemetry.py`, `workflow_spans.py`,
  `tests/integration/trace_telemetry.py`
- **Contract:** [tracing configuration](../../configuration/tracing.md)
- **Verify:** `tests/integration/trace_telemetry.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## Jaeger and OTel Collector

- **Status:** adopted
- **Pin:** Jaeger `cr.jaegertracing.io/jaegertracing/jaeger:2.22.0`, Collector
  `otel/opentelemetry-collector:0.162.0` — `tests/integration/trace_jaeger.py`,
  `config/collector.yaml`
- **Used by:** optional local trace forwarding configuration and telemetry integration tests
- **Contract:** [tracing configuration](../../configuration/tracing.md)
- **Verify:** `tests/integration/trace_jaeger.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## PM4Py

- **Status:** removed
- **Pin:** none — removed from manifests and dependencies
- **Used by:** none. Replaced by Rust4PM in Vesryn. Historical `/1` results remain readable
  and registrable
- **Contract:** [tracing configuration](../../configuration/tracing.md)
- **Verify:** no `pm4py` imports in `src/` or `pyproject.toml`
- **Verified:** 2026-10-14 @ `963d809a295f`

## Propel

- **Status:** removed
- **Pin:** none — external desktop GUI application
- **Used by:** none. Connection troubleshooting discontinued by user decision; XES file export
  is the supported interoperability path
- **Contract:** [Propel guide](../../../guides/propel-postgresql.md)
- **Verify:** `guides/propel-postgresql.md`
- **Verified:** 2026-10-14 @ `963d809a295f`
