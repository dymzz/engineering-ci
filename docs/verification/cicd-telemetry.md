# CI/CD Telemetry Verification

Local verification for [architecture/cicd-telemetry.md](../architecture/cicd-telemetry.md)
and Issue [#75](https://github.com/dymzz/engineering-ci/issues/75).

## Candidate records + API/OTLP JSON slice

| Case | Input | Expected |
| --- | --- | --- |
| Complete Run + Job + bounds | Separate run/job conclusions + timestamps + Task URL | `status=produced`, SERVER + INTERNAL spans, `evidence_class=candidate_record` |
| Job success / Run failure | Distinct conclusions | Pipeline `failure` (+ `error.type`), Task `success` |
| Job success / Run in progress | Job terminal only | `partial`, Task span only |
| Explicit null Run + Job success | Independent fields | No Pipeline terminal span |
| Legacy shared `conclusion` | Single field only | Ignored; no fabricated terminals |
| Missing Run ID / Attempt | Incomplete facts | `incomplete`, no spans, exit 3 |
| Missing timestamps | Terminal conclusions without bounds | `incomplete` / no fabricated durations |
| Reversed Run/Job bounds | `start > end` | `invalid_*_timestamps`; no affected span |
| Equal timestamps | Compatible `start == end` | Valid zero-duration span |
| Job after completed Run | Job start/end `> Run completed` | `partial` + `inconsistent_run_job_timestamps` |
| `neutral` / `action_required` / `stale` | Unmapped GitHub conclusions | `unsupported_*_conclusion`; native facts retained |
| Pipeline Run ID form | Attempt-scoped execution | Resource/Span `cicd.pipeline.run.id` = `{run_id}/{attempt}` |
| API Attempt fetch | Injected attempt Run + that Attempt's Jobs | Matching Attempt facts; no latest-Run mix |
| API `updated_at` only | Completed Run without explicit completed-at | `run_updated_at_ns` kept; no Pipeline end; `run_completed_at_unproven` |
| Opposite Attempt conclusions | Attempt 1 failure vs Attempt 2 success | Selected Attempt's conclusion/Jobs only |
| Job not in Attempt | `--job-id` absent from Attempt Jobs | `job_not_found` |
| Paginated Jobs | Target Job on page 2 | Selected after full Attempt Job set |
| Short page before `total_count` | Page 2 empty while count unmet | `jobs_page_incomplete` |
| Duplicate Job ID / shifting `total_count` | Same-page, cross-page, or page-total drift | `jobs_snapshot_inconsistent` |
| Missing `gh` / API timeout | `OSError` / `TimeoutExpired` | `github_cli_unavailable` / `github_api_timeout` |
| `--job-id` among many | Explicit single Job | `partial_job_coverage` |
| `all_completed` missing Job ID | Completed Job without ID in snapshot | Counted in denominator; omission via `source_index` |
| OTLP prepared vs exported | Spans present, no `--otlp-json` | `otlp_json_prepared=true`, `otlp_json_exported=false` |
| OTLP/JSON flush | `--otlp-json` writable path | File written; `otlp_json_exported`; digest; `otlp_delivered=false` |
| OTLP protobuf decode | Flushed file via `opentelemetry-proto` | `ExportTraceServiceRequest` parses after hex→base64 ID conversion |
| OTLP/JSON flush failure | Unwritable path / no spans | exit 3; candidate claims `exported=false` |
| Native API evidence (single Job) | Fixed Run `42821429094` Attempt 1 + `--job-id` | See [cicd-telemetry-evidence](./cicd-telemetry-evidence/README.md) |
| Native API evidence (`all_completed`) | Same Run, no `--job-id`, 3 Task Spans | See [cicd-telemetry-evidence-all-completed](./cicd-telemetry-evidence-all-completed/README.md) |
| Observed upper bound, no budget | API `run_status=completed` + observation ≥ Job ends | Interval recorded; Root not emitted; Tasks remain; `end_time_uncertain` |
| Approximate Root | Same, plus `--max-end-uncertainty-ns` covering the interval | Root end is the observation; `duration_valid_for_metrics=false` |
| Late observation | Upper bound far after the last Job, budget too small | No Root; no proven multi-hour Run duration |
| Observed Root without API completed | `run_status=in_progress` even with conclusion | No Root; `run_not_completed` |
| Event/API status conflict | event status ≠ Attempt API status | No Root / fetch refuse |
| Observation before Job end | Observed timestamp `<` max Job `completed_at` | No Root; `terminal_observed_before_jobs` |
| Expected Job inventory | stubs + omit/emit/not_selected | `coverage.expected_completed_jobs` machine-readable set |
| Pipeline-only Root | Exact bounds, `completed_jobs_total=0`, empty tasks | `produced` with SERVER only; `task_coverage=empty` |
| OTLP/gRPC ACK | `--otlp-endpoint` Export success, zero rejects | Per-signal `rpc_accepted` / `otlp_logs_rpc_accepted` / `otlp_metrics_rpc_accepted`; still not delivered |
| OTLP/gRPC partial reject | `partial_success` rejects spans, log records, or datapoints | `otlp_grpc_partial_reject`; that signal's acceptance stays false |
| Duration Histogram | Explicit phase interval | `cicd.pipeline.run.duration` Histogram in seconds for that state only |
| No phase interval | Exact Run bounds without a state window | No `finalizing` datapoint; `phase_interval_unproven` |
| Mapping Root source | `--facts` with explicit completed-at | `terminal_source=mapping`, `end_time_source=explicit_mapping`, `time_precision=exact_input`, `time_source_verified=false` |
| Receiver receipt | In-process gRPC capture of the same Run/Attempt/Trace | `snapshot_phase=receiver_confirmed` only when every prepared signal matches; `candidate_record_only` stays true |
| Real Attempt → local receiver | Run `42821429094` Attempt 1, `--end-time-mode observed_upper_bound` | See [cicd-telemetry-real-run-receiver](./cicd-telemetry-real-run-receiver/README.md). Not a default-branch `workflow_run` |

Run:

```text
uv run pytest tests/test_cicd_telemetry.py -q
```

Live OTLP/gRPC receiver delivery and Weaver sample handoff remain later slices.
Manual API check (optional, requires `gh` auth):

```text
engineering-ci cicd-telemetry --repository dymzz/engineering-ci --run-id <id> --job-id <id> --otlp-json otel.json
```
