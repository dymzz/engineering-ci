# OTLP/gRPC live receiver + O2/O3 signal evidence (#75 / PR #81)

Controlled **in-process** OTLP/gRPC receiver evidence for one producer execution.
This directory is a **mapping fixture**, not a GitHub Attempt API capture and not
a Weaver PASS or #63 trusted admission.

## What this execution is

| Fact | Value |
| --- | --- |
| `source` | `mapping` (`github_api_fetched=false`, `github_authenticated=false`) |
| `candidate_record_only` | `true` (network receipt does not change the source class) |
| `terminal_source` | `mapping` |
| `end_time_source` | `explicit_mapping` |
| `time_precision` | `exact_input` |
| `time_source_verified` | `false` (a closed numeric input is not a GitHub completion timestamp) |
| `snapshot_phase` | `receiver_confirmed` after Traces, Logs, and Metrics were all observed |
| Protocol | OTLP/gRPC to `127.0.0.1:<ephemeral>` (insecure), one channel |
| Traces | `rpc_accepted`, `receiver_observed`, and `otlp_delivered` are all `true` for this controlled receipt |
| Logs | Separate `otlp_logs_rpc_accepted` / `otlp_logs_receiver_observed` / `otlp_logs_delivered`, all `true` |
| Metrics | Histogram `cicd.pipeline.run.duration` for an **explicit** `executing` window equal to the fixture Job bounds (80s). `pending` and `finalizing` stay `phase_interval_unproven`. Run id / attempt / head revision are omitted (`include_run_entity_metrics` default false) |
| `weaver_pass` / `trusted` | `false` |

`otlp_delivered=true` here means this process observed the same Trace set the
producer exported. It does not mean a real GitHub Run was delivered, and it
does not mean the Attempt API supplied `run_completed_at`.

## Reproduce

```text
uv run pytest tests/test_otlp_grpc_live.py -q
```

The files in this directory were written by one execution of that same
producer path (fixture facts, local receiver, then `mark_receiver_observed`).
Negative cases — unreachable endpoint, Trace partial reject, Logs partial
reject, missing span, duplicate span, wrong attempt, wrong trace id — are in
`tests/test_otlp_grpc_live.py` and `tests/test_cicd_telemetry.py`.

## Files

| File | Role |
| --- | --- |
| `candidate.json` | Post-receipt candidate. Claims match the receiver receipt |
| `otel-traces.json` | `ExportTraceServiceRequest` |
| `otel-logs.json` | `ExportLogsServiceRequest` (Job terminal event, not Job stdout) |
| `otel-metrics.json` | `ExportMetricsServiceRequest` Histogram |
| `receiver-observed.json` | Receiver identities and per-signal delivery flags |
| `SHA256SUMS` | Digests of those five files |
