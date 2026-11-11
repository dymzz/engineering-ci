# Real Attempt → local OTLP/gRPC receiver (#75 / PR #81)

One execution of:

```text
python -m engineering_ci.cicd_telemetry ^
  --repository dymzz/engineering-ci ^
  --run-id 42821429094 ^
  --attempt 1 ^
  --end-time-mode observed_upper_bound ^
  --otlp-endpoint 127.0.0.1:<ephemeral> ^
  --otlp-insecure
```

The source is GitHub Attempt API for Run `42821429094` Attempt 1
(`head_sha=7ea85c24a62e65a9384a0abe9eb541ca54064cbc`, three completed Jobs).
The receiver is an in-process OTLP/gRPC server on `127.0.0.1`. Producer exit
is 0. Report status stays `partial` / `temporal_precision_unverified`.

| Fact | Value |
| --- | --- |
| `source` / `terminal_source` | `github_api` |
| `github_api_fetched` | `true` |
| `github_authenticated` / `trusted` / `weaver_pass` | `false` |
| `candidate_record_only` | `true` |
| `end_time_source` | `terminal_observed_at` |
| `time_precision` | `observed_upper_bound` |
| `time_source_verified` | `false` (`run_completed_at_ns` is null; `updated_at` was not renamed) |
| Traces | 1 Pipeline Root + 3 Task Spans received; `otlp_delivered=true` only after the receipt matched |
| Logs | 3 Job-terminal LogRecords on `LogsService.Export`; separate `otlp_logs_delivered=true` |
| Metrics | `otlp_metrics_skip=not_observable`. GitHub does not provide pending/executing/finalizing windows, so no Histogram was emitted |
| `snapshot_phase` | `receiver_confirmed` for the prepared Traces and Logs |

This is not a `workflow_run` of `.github/workflows/cicd-telemetry-post-run.yml`
on the default branch. That workflow is not on `main` until this PR is
merged, so that post-run execution is still absent. It is not a Weaver match.

## Files

| File | Role |
| --- | --- |
| `candidate.json` | Post-receipt candidate |
| `otel-traces.json` / `otel-logs.json` | OTLP requests that were exported |
| `receiver-observed.json` | Receiver Span/Log identities, expected Jobs, transport sets |
| `cli.stdout.json` / `cli.stderr.txt` / `producer.exit` | CLI result before receipt confirmation |
| `SHA256SUMS` | Digests |
