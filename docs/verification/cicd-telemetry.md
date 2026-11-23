# CI/CD Telemetry Verification

Local verification for [architecture/cicd-telemetry.md](../architecture/cicd-telemetry.md)
and Issue [#75](https://github.com/dymzz/engineering-ci/issues/75).

## 2026-11-21 current verification decision

The accepted target is **true GitHub Attempt terminal facts**, not an estimated Workflow completion timestamp. [#75](https://github.com/dymzz/engineering-ci/issues/75) is authoritative; Draft [PR #150](https://github.com/dymzz/engineering-ci/pull/150) implements `terminal_event` **on its branch only**. **Current `main` still runs `cicd-telemetry-post-run.yml` with `observed_upper_bound`; this is current behavior, not evidence that the approved target is deployed.** Native Job/Task spans, terminal LogRecord, independent API witness and `root_emission=not_emitted` / `time_precision=unproven` are the new target when full Pipeline End is not owner-proven. Dagger Function/Check timestamps remain scoped to the actual Dagger operation.

**Prohibited implementation direction:** treating `updated_at`, last Job end, post-run observation, guessed time intervals or invented `--max-end-uncertainty-ns` budgets as the GitHub Workflow's real Pipeline End, or reopening this route merely to get a #52B Terminal-positive result. #52A Task-only is independently valid; #52B requires a genuinely bound Pipeline Root and remains unverified otherwise. Reuse historical API/Receiver/Weaver evidence and verify only the actual new integration difference.

The table below contains **historical contract tests and current-main legacy behavior** for `observed_upper_bound` and approximate Root. Those rows are not the new `terminal_event` acceptance criteria, and schema/shape-valid synthetic Root tests do not establish #52B production coverage.

## ECI verification timing (#152)

Use the [timing command](../architecture/cicd-telemetry.md) on an explicit
local Lefthook `verify` invocation. This check leaves the four Actions Jobs
and the existing #87 A.1 workflow untouched.

- A native success and a native nonzero result retain both host clock readings,
  their original aggregate exit codes and readable stdout/stderr whose SHA-256
  digests match the independent `verify-timing.json`.
- Interruption before finalization leaves no completed receipt. A reversed
  clock yields `completed_at_ns: null`. Native nonzero status is retained.
- The receipt is self-observed timing with `time_source_verified=false`.
  Confirm the native output and timing reference separately. Dagger span
  associations require an actual #87 binding for the same invocation. Any
  unlinked spans or cache hits keep their native interpretation.

## Candidate records + API/OTLP JSON slice

| Case | Input | Expected |
| --- | --- | --- |
| Complete Run + Job + syntactically bounded input (historical producer contract) | Separate run/job conclusions + numeric timestamps + Task URL | `status=produced` and candidate SERVER + INTERNAL records in legacy contract; **not proof of an owner-measured full Pipeline Root** |
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
| Observed upper bound, no budget (legacy) | API `run_status=completed` + observation ≥ Job ends | Interval recorded; Root not emitted; Tasks remain; `end_time_uncertain` |
| Approximate Root (legacy behavior, non-acceptance) | Historical explicit `--max-end-uncertainty-ns` mode | Records previous numeric behavior only; observer end **does not establish** GitHub Workflow completed-at or qualified Terminal Root |
| Late observation | Upper bound far after the last Job, budget too small | No Root; no proven multi-hour Run duration |
| Observed Root without API completed | `run_status=in_progress` even with conclusion | No Root; `run_not_completed` |
| Event/API status conflict | event status ≠ Attempt API status | No Root / fetch refuse |
| Observation before Job end | Observed timestamp `<` max Job `completed_at` | No Root; `terminal_observed_before_jobs` |
| Expected Job inventory | stubs + omit/emit/not_selected | `coverage.expected_completed_jobs` machine-readable set |
| Pipeline-only Root (historical/synthetic) | Explicit bounded inputs, `completed_jobs_total=0`, empty tasks | Historical `produced`/SERVER shape only; #52B still requires independently qualified full Pipeline scope and true timestamps |
| OTLP/gRPC ACK | `--otlp-endpoint` Export success, zero rejects | Per-signal `rpc_accepted` / `otlp_logs_rpc_accepted` / `otlp_metrics_rpc_accepted`; still not delivered |
| OTLP/gRPC partial reject | `partial_success` rejects spans, log records, or datapoints | `otlp_grpc_partial_reject`; that signal's acceptance stays false |
| Duration Histogram | Explicit phase interval | `cicd.pipeline.run.duration` Histogram in seconds for that state only |
| No phase interval | Exact Run bounds without a state window | No `finalizing` datapoint; `phase_interval_unproven` |
| Mapping Root source | `--facts` with explicit completed-at | `terminal_source=mapping`, `end_time_source=explicit_mapping`, `time_precision=exact_input`, `time_source_verified=false` |
| Receiver receipt | In-process gRPC capture of the same Run/Attempt/Trace | `snapshot_phase=receiver_confirmed` only when every prepared signal matches; `candidate_record_only` stays true |
| Real Attempt → local receiver | Run `42821429094` Attempt 1, `--end-time-mode observed_upper_bound` | See [cicd-telemetry-real-run-receiver](./cicd-telemetry-real-run-receiver/README.md). Not a default-branch `workflow_run` |
| Schema 9 repository / extension mapping | Same fixed Run/Attempt, fresh independent API Witness, new Producer files and strict native positive/negatives | See [attribute mapping evidence](cicd-telemetry-attribute-mapping/README.md); original failure and network archives stay unchanged |

Run:

```text
uv run --locked pytest tests/test_cicd_telemetry.py tests/test_cicd_telemetry_attributes.py tests/test_otlp_grpc_live.py -q
```

The existing live receiver tests cover the unchanged three-signal transport.
The new attribute-mapping bundle records a separate native Weaver replay; it
does not promote the Producer's file export into a new receiver receipt.
Manual API check (optional, requires `gh` auth):

```text
engineering-ci cicd-telemetry --repository dymzz/engineering-ci --run-id <id> --job-id <id> --otlp-json otel.json
```
