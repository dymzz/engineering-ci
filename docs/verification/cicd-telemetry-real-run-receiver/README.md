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

No `--max-end-uncertainty-ns` was passed. The source is GitHub Attempt API for
Run `42821429094` Attempt 1
(`head_sha=7ea85c24a62e65a9384a0abe9eb541ca54064cbc`, three completed Jobs).
The receiver is an in-process OTLP/gRPC server on `127.0.0.1`. Producer exit
is 0. Report status stays `partial` / `end_time_uncertain`.

| Fact | Value |
| --- | --- |
| `source` / `terminal_source` | `github_api` |
| `github_api_fetched` | `true` |
| `github_authenticated` / `trusted` / `weaver_pass` | `false` |
| `candidate_record_only` | `true` |
| `terminal_confirmed` | `true` (`run_status=completed`) |
| `root_emission` | `not_emitted` |
| `end_lower_bound_source` | `last_completed_job_end` |
| `end_upper_bound_source` | `terminal_observed_at` |
| `time_precision` | `observed_upper_bound` |
| `time_source_verified` / `exact_run_duration` / `duration_valid_for_metrics` | `false` |
| `run_completed_at_ns` | null. `updated_at` was not used as the span end |
| Traces | 3 Task Spans received. No Pipeline Root, so the trace does not show a multi-hour Run duration |
| Logs | 3 Job-terminal LogRecords plus 1 Attempt-terminal LogRecord (`time_role=terminal_observed_at`, not a Run duration); `otlp_logs_delivered=true` |
| `terminal_check` | `not_verified` while the Root span is withheld. `weaver_pass` stays false |
| Metrics | `otlp_metrics_skip=not_observable`. The Job envelope is coverage metadata, not `cicd.pipeline.run.duration` |
| `snapshot_phase` | `receiver_confirmed` for the prepared Traces and Logs |

The uncertainty between the last Job end and this observation is many hours.
That interval is a constraint, not a proven workflow end. This is not a
`workflow_run` of `.github/workflows/cicd-telemetry-post-run.yml` on the
default branch, and it is not a Weaver match.

## Files

| File | Role |
| --- | --- |
| `candidate.json` | Post-receipt candidate |
| `otel-traces.json` / `otel-logs.json` | OTLP requests that were exported |
| `receiver-observed.json` | Receiver Span/Log identities, expected Jobs, transport sets |
| `cli.stdout.json` / `cli.stderr.txt` / `producer.exit` | CLI result before receipt confirmation |
| `SHA256SUMS` | Digests |

Schema 8 identity fields for this same Run are a separate file check under
[cicd-telemetry-real-run-identity-v8](../cicd-telemetry-real-run-identity-v8/README.md).
Those files are not a second receiver ACK. This directory stays the network receipt.
