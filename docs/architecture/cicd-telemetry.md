# CI/CD Telemetry Producer

Issue [#75](https://github.com/dymzz/engineering-ci/issues/75) / Knowledge
[PR #79](https://github.com/dymzz/engineering-knowledge/pull/79) stream C (O2/O3).

## Producer decision

| Object | Producer | Native facts |
| --- | --- | --- |
| Pipeline Run Span | `eci_core.cicd_telemetry` from separate Run facts | Workflow name, Run ID, Attempt, **Run** conclusion, real start/end |
| Task Run Span | Same adapter from separate Job facts | Job id/name/**url**, **Job** conclusion, real start/end |
| Dagger Function Trace | Dagger SDK only | Not owned here; do not re-instrument |

Inputs (first → second slice):

1. Explicit `--facts` mapping or Actions env (untrusted / incomplete identity).
2. **GitHub Actions API** via native `gh api`: `--repository` + `--run-id`
   (+ optional `--job-id` / `--attempt`). Run `conclusion` and Job `conclusion`
   stay independent. Run bounds use `run_started_at` and, when
   `status=completed`, `updated_at` as completed-at (Workflow Run has no
   separate `completed_at`). Job bounds use Job `started_at` / `completed_at`.

Job conclusion never becomes Pipeline result. Selection / Required /
`blocked` / `not_executed` stay in ECI Plan/Report and are never encoded as
`cicd.pipeline.result`.

Span kinds follow public CI/CD SemConv: Pipeline Run `SERVER`, Task Run
`INTERNAL`. Only justified GitHub conclusions map to SemConv results
(`success` / `failure` / `cancellation` / `skip` / `timeout`);
`neutral` / `action_required` / `stale` stay unsupported until a fixed registry
mapping is cited. `error.type` is set only when the SemConv result is `failure`
or `error`.

When both candidate spans are emitted for the same completed Run/Attempt, a Job
that starts or finishes after the Run completed-at is `partial`
(`inconsistent_run_job_timestamps`); individually valid spans are kept.

## Export

`engineering-ci cicd-telemetry` writes:

- a **candidate-record** JSON artifact (`evidence_class=candidate_record`);
- optional **`--otlp-json`** file in OTLP/JSON `resourceSpans` shape for a
  neutral receiver or replay path.

`claims.otlp_json_exported` may become true after a successful file flush;
`claims.otlp_delivered` stays false until a later live OTLP/gRPC slice.
`github_api_fetched` records an API read; it is **not** a Weaver/trusted
provenance claim. Flush failures exit 3 with `otlp_json_flush_failed` /
`otlp_json_unavailable` and do not invent spans.

## Related

- Authoritative requirements: [Issue #75](https://github.com/dymzz/engineering-ci/issues/75)
- [Verification](../verification/cicd-telemetry.md)
- Parallel checker: [Issue #74](https://github.com/dymzz/engineering-ci/issues/74)
