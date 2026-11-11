# CI/CD Telemetry Producer

Issue [#75](https://github.com/dymzz/engineering-ci/issues/75) / Knowledge
[PR #79](https://github.com/dymzz/engineering-knowledge/pull/79) stream C (O2/O3).

## Producer decision

| Object | Producer | Native facts |
| --- | --- | --- |
| Pipeline Run Span | `engineering_ci.cicd_telemetry` from separate Run facts | Workflow name, Run ID, Attempt, **Run** conclusion, real start/end |
| Task Run Span | Same adapter from separate Job facts | Job id/name/**url**, **Job** conclusion, real start/end |
| Dagger Function Trace | Dagger SDK only | Not owned here; do not re-instrument |

Inputs (first → second slice):

1. Explicit `--facts` mapping or Actions env (untrusted / incomplete identity).
2. **GitHub Actions API** via native `gh api`: `--repository` + `--run-id`
   (+ optional `--job-id` / `--attempt`). Run `conclusion` and Job `conclusion`
   stay independent. The producer loads **the same Attempt**:
   `GET .../runs/{id}/attempts/{N}` plus that Attempt's paginated Jobs — never
   the latest Run view mixed with an older Attempt's Jobs.

### Timestamps

| Field | Source | Role |
| --- | --- | --- |
| Run start | GitHub `run_started_at` / explicit mapping | Exact Pipeline start when present |
| Run end (explicit input) | Explicit `run_completed_at` / `ENGINEERING_CI_RUN_COMPLETED_AT` only | Closed numeric bounds (`time_precision=exact_input`). This is not a verified GitHub completion timestamp (`time_source_verified=false`) |
| Terminal observed | Post-run `--terminal-observed-at` | Observation upper bound only (`time_precision=observed_upper_bound`, `end_time_source=terminal_observed_at`); never renamed to native `completed_at` |
| Run `updated_at` | GitHub Workflow Run object update time | Provenance / estimate only (`run_updated_at_ns`); **not** a proven Attempt completed-at |
| Job bounds | Job `started_at` / `completed_at` | Exact Task bounds |

Without a proven Run completed-at the producer reports
`run_completed_at_unproven` (when `updated_at` is present) or
`missing_run_timestamps` and does **not** emit an exact Pipeline Span end.
With `--end-time-mode observed_upper_bound`, native Attempt API
`run_status=completed` confirms the terminal and records a constraint interval:
last completed Job end (or Run start when there are no Jobs) through the
observation time. That interval is not a proven end. An approximate Root is
emitted only when `--max-end-uncertainty-ns` is set and the uncertainty fits
that budget; `duration_valid_for_metrics` stays false. A late observation does
not become the standard Root end. Tasks and Job-terminal logs stay. A withheld Root is recorded as
`terminal_check=not_verified` and, when an observation time exists, as one
Attempt-terminal LogRecord. That log is not a Run duration, and it is not a
Weaver result.
Event hints are conflict-checked against the Attempt API and never substitute
for API status. A Job start/end envelope is coverage metadata
(`job_execution_envelope`), not `cicd.pipeline.run.duration`.
Coverage records `pipeline_terminal_covered` separately from `task_coverage`,
plus a machine-readable `expected_completed_jobs` inventory for #52 hand-off.
`coverage.expected_trace` publishes the same Attempt's expected Trace ID and
Pipeline Span ID. `parent_exported` is false when that parent span is withheld;
the withheld id is `coverage.withheld_parent_span`, not a second list.
`expected_completed_jobs[].expected_task_span_id` is the Job mapping.
These IDs are not trusted admission.
`terminal_provenance.terminal_source` is the facts source that was actually
used (`github_api`, `mapping`, or `env`). `end_time_source` is
`explicit_mapping`, `explicit_env`, `not_native`, or `terminal_observed_at`.
`time_precision` (`exact_input` / `observed_upper_bound` / `unproven`) is
independent of `time_source_verified`. The Attempt API `status=completed`
does not prove an official run-completed timestamp, so
`time_source_verified` stays false. ECI Span attrs
`eci.cicd.end_time_quality` and `eci.cicd.exact_run_duration` match those
candidate fields. `exact_run_duration` means the numeric interval is closed
by an explicit input, not that GitHub certified the end instant.

### Expected Trace identity (`github-actions-attempt-sha256-v1`)

#52 rebuilds the expected Trace from the Run/Attempt/Job witness. It does not
copy `traceId` from the first span in a capture. The producer uses one
function, `_stable_ids`, and records `producer.id_scheme`.

| ID | Seed (UTF-8) | SHA-256 hex |
| --- | --- | --- |
| `expected_trace_id` | `pipeline:{run_id}:{attempt}` | first 32 hex characters (16 bytes) |
| `expected_pipeline_span_id` | same pipeline seed | next 16 hex characters (8 bytes) |
| `expected_task_span_id` | `task:{run_id}:{attempt}:{job_id}` | first 16 hex characters (8 bytes) |

`run_id` and `job_id` are their decimal text. `attempt` is the integer rendered
in decimal. A withheld Root still has this expected parent id, with
`parent_exported=false`.

### Pipeline Run identity (cross-PR with #74)

Each GitHub Actions **Attempt** is one distinct pipeline execution (own Trace).
SemConv `cicd.pipeline.run.id` is the stable canonical form
`{github_run_id}/{run_attempt}` on Resource, Pipeline Span, and Task
correlation. Raw GitHub Run ID and Attempt remain in `facts.*`;
`github.actions.run_attempt` is an ECI/GitHub correlation extension, **not** a
Required OTel SemConv attribute. When GitHub provides `html_url`, it is
emitted as Recommended `cicd.pipeline.run.url.full`.

Job conclusion never becomes Pipeline result. Selection / Required /
`blocked` / `not_executed` stay in ECI Plan/Report and are never encoded as
`cicd.pipeline.result`.

Span kinds follow public CI/CD SemConv: Pipeline Run `SERVER`, Task Run
`INTERNAL`. Only justified GitHub conclusions map to SemConv results
(`success` / `failure` / `cancellation` / `skip` / `timeout`);
`neutral` / `action_required` / `stale` stay unsupported until a fixed registry
mapping is cited. `error.type` is set for SemConv `failure` / `error` and for
GitHub `timed_out` → `timeout` (Recording Errors); cancellation/skip leave
Status UNSET without `error.type`.

When both candidate spans are emitted for the same completed Run/Attempt, a Job
that starts or finishes after the Run completed-at is `partial`
(`inconsistent_run_job_timestamps`); individually valid spans are kept.

Without `--job-id`, the producer emits Task Spans for **every completed Job**
on the Attempt (`job_selection=all_completed`) and records omitted Jobs under
`coverage.tasks_omitted`. Completed Jobs are counted by `status == "completed"`
without requiring an ID; a missing ID is an omission located by source index,
never dropped from the denominator. Coverage also records
`jobs_observed_total` and `noncompleted_jobs_total`. For
`job_selection=all_completed` with a valid source snapshot, every completed Job
has exactly one Task or omission:
`completed_jobs_total == task_spans_emitted + len(tasks_omitted)`.
`--job-id` remains a deliberate single-Job diagnostic slice
(`job_selection=job_id`): unselected completed Jobs are a coverage gap signaled
by `partial_job_coverage`, not fabricated `tasks_omitted` entries. When
completed Jobs exceed emitted Task spans, status is `partial`
(`partial_job_coverage`).

Job pagination refuses an inconsistent snapshot: shifting page `total_count` or
duplicate Job IDs across the Attempt raise `jobs_snapshot_inconsistent`
(rather than silently deduplicating).

## Export

The default report producer name remains `eci_core.cicd_telemetry` for persisted
evidence compatibility. This is an evidence label, not an import path; Python
callers use `engineering_ci.cicd_telemetry` (post-#78 / package unify).

`engineering-ci cicd-telemetry` writes:

- a **candidate-record** JSON artifact (`kind=engineering-ci-cicd-telemetry`,
  `evidence_class=candidate_record`);
- optional **`--otlp-json`** file in OTLP/JSON `resourceSpans` shape for a
  neutral receiver or replay path.

Caller `--facts` JSON is untrusted input, not authenticated GitHub provenance.
`claims.otlp_json_prepared` means an in-memory OTLP body exists.
`claims.otlp_json_exported` becomes true **only** after a successful file
flush (path + sha256 recorded when asserted; file bytes use LF so digests
match on Windows). `--otlp-endpoint` performs OTLP/gRPC `TraceService.Export`;
`claims.rpc_accepted` is true only when Trace `Export` returns with zero
`rejected_spans`. Logs and Metrics use `otlp_logs_rpc_accepted` and
`otlp_metrics_rpc_accepted`. An RPC ACK is **not** `receiver_observed`,
`otlp_delivered`, `weaver_pass`, or `trusted`. `mark_receiver_observed`
records one signal only after that signal's ACK, a matching Run/Attempt,
and (for Traces and Logs) the same Trace ID. It reconciles
`expected` / `matched` / `missing` / `duplicate` / `unexpected`.
`candidate_record_only` stays true: network receipt is a transport claim,
not a change of source class. The persisted candidate sets top-level trace
`otlp_delivered` is the historical name for whole-bundle delivery: it is true
only when every prepared signal was observed. `bundle_delivery_complete` is
the same fact. Per-signal delivery is `transport.traces`, `transport.logs`,
and `transport.metrics`. `snapshot_phase` is `before_receiver_confirmation`,
`partial_receiver_confirmation`, or `receiver_confirmed`. The candidate artifact is
written **after** the export attempt so top-level mirrors match claims.
`github_api_fetched` records an API read; it is **not** a Weaver/trusted
provenance claim. Flush / gRPC failures exit 3 with stable codes and do not
invent spans. CLI exit 0 for `partial` is candidate-generator only —
consumers must read `report.status` / coverage, not promote exit 0 to
terminal PASS. Missing identity, terminal Run/Job results, required Task URL,
or real timestamps yield `incomplete` / `partial` (exit 3 when nothing is
emitted).

Post-run entry: `.github/workflows/cicd-telemetry-post-run.yml` listens for
`workflow_run` completion of `Engineering CI` (and supports
`workflow_dispatch`). It reads the source Run via Attempt API using the event
identity, never executes the source PR head, and uploads candidate/OTLP
artifacts. Live Collector endpoint wiring remains optional via repository
secrets / dispatch inputs; absence of a receiver leaves `rpc_accepted=false`.

### O2 Logs / O3 Metrics (PR #81 decisions)

| Signal | Source → producer → OTLP | Status |
| --- | --- | --- |
| LogRecord | Attempt Job terminal mapped into Task Spans → `ExportLogsServiceRequest` (`eci.log.source=github_job_terminal`) | Emitted for each Task Span; full Job stdout/stderr archives stay on GitHub Actions. gRPC `LogsService.Export` is a separate `otlp_logs_*` claim and does not inherit Trace delivery |
| `cicd.pipeline.run.duration` | Caller-proven `pending` / `executing` / `finalizing` windows → Histogram, unit `s` | One Histogram datapoint per proven window. A missing window is `not_observable` (`phase_interval_unproven`). The whole Run duration is not recorded as `finalizing`. `cicd.pipeline.run.id` is omitted unless `include_run_entity_metrics` |
| `cicd.pipeline.run.active` | Current active-run count | `not_observable` (`active_count_unproven`) |

Controlled live OTLP/gRPC receiver evidence (ACK + receiver-observed Span IDs,
logs/metrics artifacts) lives under
[cicd-telemetry-otlp-grpc-evidence](../verification/cicd-telemetry-otlp-grpc-evidence/README.md).

When GitHub reports `total_count`, a short/empty page before that count is
`jobs_page_incomplete`. Missing `gh` or API timeouts map to
`github_cli_unavailable` / `github_api_timeout` (no retry engine).

Single-Job diagnostic evidence (`--job-id`) lives under
[cicd-telemetry-evidence](../verification/cicd-telemetry-evidence/README.md).
Full Attempt `all_completed` multi-Task evidence (no `--job-id`) lives under
[cicd-telemetry-evidence-all-completed](../verification/cicd-telemetry-evidence-all-completed/README.md).
Baseline after PR #76/#77/#78/#80/#81: candidate Pipeline/Task construction,
Attempt API/OTLP file export, full completed-Job Task coverage, observed
upper-bound Root mode (API `status=completed` gated), Expected Job inventory,
terminal provenance, OTLP/gRPC Export with controlled receiver observation,
Job-terminal LogRecords, and phase-interval Histogram Metrics (only when a
window is supplied) live under
`engineering_ci.cicd_telemetry`. Production Collector endpoint remains optional
via `ECI_OTLP_ENDPOINT`. Trusted admission (#63) and Required (#61) stay outside
this Issue's producer claims; Issue #75 stays Open until its remaining
acceptance items are separately confirmed.

## Related

- Authoritative requirements: [Issue #75](https://github.com/dymzz/engineering-ci/issues/75)
- [Verification](../verification/cicd-telemetry.md)
- Parallel checker: [Issue #74](https://github.com/dymzz/engineering-ci/issues/74)
