# Tracing Integrations

Inventory: [technology](../technology.md). Up: [Integrations](README.md).
Contracts: [tracing configuration](../../configuration/tracing.md),
[Harness integration](../harness-integration/README.md).

## PostgreSQL

- **Status:** adopted
- **Pin:** unpinned — host-provided via `VESRYN_DATABASE`. Archive schema version 3 in
  `engineering_ci_trace`, initialized and migrated via Vesryn SQL migrations 001–003
- **Used by:** `src/engineering_ci/tracing/store.py`, `catalog.py`, `preflight.py`,
  `feedback_query.py`, `feedback_summary.py`
- **Contract:** [tracing configuration](../../configuration/tracing.md),
  [Harness capabilities](../harness-integration/capabilities.md)
- **Verify:** `tests/test_workflow_capabilities.py`, `tests/test_workflow_feedback_query.py`
- **Verified:** 2026-10-14 @ `963d809a295f`

## Psycopg 3

- **Status:** adopted
- **Pin:** `psycopg[binary]==3.3.6` — `pyproject.toml` (`dev` group)
- **Used by:** `src/engineering_ci/tracing/store.py`, `catalog.py` for PostgreSQL access
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
