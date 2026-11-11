# OTLP/gRPC live receiver + O2/O3 signal evidence (#75 / PR #81)

Controlled **in-process** OTLP/gRPC `TraceService` receiver evidence for the
producer Export path, plus Job-terminal LogRecords and exact-duration Metrics.
**Not** Weaver PASS, **not** #63 trusted admission, **not** a production Collector
deployment.

## What was proven

| Claim | Value |
| --- | --- |
| Protocol | OTLP/gRPC `TraceService.Export` to `127.0.0.1:<ephemeral>` (insecure) |
| `rpc_accepted` | `true` (zero `rejected_spans`) |
| `receiver_observed` | `true` (receiver Span IDs match exported Pipeline+Task set) |
| `otlp_delivered` | `true` only for this controlled observation |
| `weaver_pass` / `trusted` | `false` |
| LogRecords | 1× `github_job_terminal` from Task Span (Job API terminal facts) |
| Metrics | `cicd.pipeline.run.duration` emitted only because exact Run bounds exist |
| Negative cases | Covered in `tests/test_otlp_grpc_live.py` (unreachable + partial reject) |

## Reproduce

```text
uv run pytest tests/test_otlp_grpc_live.py -q
```

Evidence files in this directory were regenerated from the same producer path
used by the live receiver test (exact-bounds fixture facts, not a network
GitHub Run). Workflow wiring for an optional remote endpoint remains
`ECI_OTLP_ENDPOINT` on the post-run Workflow.

## Files

| File | Role |
| --- | --- |
| `candidate.json` | Candidate report (claims + signals) |
| `otel-traces.json` | ExportTraceServiceRequest |
| `otel-logs.json` | ExportLogsServiceRequest |
| `otel-metrics.json` | ExportMetricsServiceRequest |
| `receiver-observed.json` | Receiver-side Span IDs + claim snapshot |
| `SHA256SUMS` | Digests |
