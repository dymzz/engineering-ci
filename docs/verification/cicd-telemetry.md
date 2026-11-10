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
| Missing `gh` / API timeout | `OSError` / `TimeoutExpired` | `github_cli_unavailable` / `github_api_timeout` |
| `--job-id` among many | Explicit single Job | `partial_job_coverage` |
| OTLP prepared vs exported | Spans present, no `--otlp-json` | `otlp_json_prepared=true`, `otlp_json_exported=false` |
| OTLP/JSON flush | `--otlp-json` writable path | File written; `otlp_json_exported`; digest; `otlp_delivered=false` |
| OTLP protobuf decode | Flushed file via `opentelemetry-proto` | `ExportTraceServiceRequest` parses after hex→base64 ID conversion |
| OTLP/JSON flush failure | Unwritable path / no spans | exit 3; candidate claims `exported=false` |
| Native API evidence | Fixed Run `42821429094` Attempt 1 Job | See [cicd-telemetry-evidence](./cicd-telemetry-evidence/README.md) |

Run:

```text
uv run pytest tests/test_cicd_telemetry.py -q
```

Live OTLP/gRPC receiver delivery and Weaver sample handoff remain later slices.
Manual API check (optional, requires `gh` auth):

```text
engineering-ci cicd-telemetry --repository dymzz/engineering-ci --run-id <id> --job-id <id> --otlp-json otel.json
```
