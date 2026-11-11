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
| Run end | Explicit `run_completed_at` / `ENGINEERING_CI_RUN_COMPLETED_AT` only | Exact Pipeline end bound |
| Run `updated_at` | GitHub Workflow Run object update time | Provenance / estimate only (`run_updated_at_ns`); **not** a proven Attempt completed-at |
| Job bounds | Job `started_at` / `completed_at` | Exact Task bounds |

Without a proven Run completed-at the producer reports
`run_completed_at_unproven` (when `updated_at` is present) or
`missing_run_timestamps` and does **not** emit a completed Pipeline Span end.

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
match on Windows). The candidate artifact is written **after** the export
attempt so top-level mirrors match claims. `claims.otlp_delivered` stays
false until a later live OTLP/gRPC slice. `github_api_fetched` records an
API read; it is **not** a Weaver/trusted provenance claim. Flush failures
exit 3 with `otlp_json_flush_failed` / `otlp_json_unavailable` and do not
invent spans. CLI exit 0 for `partial` is candidate-generator only —
consumers must read `report.status` / coverage, not promote exit 0 to
terminal PASS. Missing identity, terminal Run/Job results, required Task URL,
or real timestamps yield `incomplete` / `partial` (exit 3 when nothing is
emitted).

When GitHub reports `total_count`, a short/empty page before that count is
`jobs_page_incomplete`. Missing `gh` or API timeouts map to
`github_cli_unavailable` / `github_api_timeout` (no retry engine).

Single-Job diagnostic evidence (`--job-id`) lives under
[cicd-telemetry-evidence](../verification/cicd-telemetry-evidence/README.md).
Full Attempt `all_completed` multi-Task evidence (no `--job-id`) lives under
[cicd-telemetry-evidence-all-completed](../verification/cicd-telemetry-evidence-all-completed/README.md).
Baseline after PR #76/#77/#78/#80: candidate Pipeline/Task construction, Attempt
API/OTLP file export, and full completed-Job Task coverage live under
`engineering_ci.cicd_telemetry`. Exact post-run Pipeline Root, live OTLP/gRPC,
and trusted admission remain open on Issue #75.

## Related

- Authoritative requirements: [Issue #75](https://github.com/dymzz/engineering-ci/issues/75)
- [Verification](../verification/cicd-telemetry.md)
- Parallel checker: [Issue #74](https://github.com/dymzz/engineering-ci/issues/74)
