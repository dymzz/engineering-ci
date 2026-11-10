# CI/CD Telemetry Producer

Issue [#75](https://github.com/dymzz/engineering-ci/issues/75) / Knowledge
[PR #79](https://github.com/dymzz/engineering-knowledge/pull/79) stream C (O2/O3).

## Producer decision

| Object | Producer | Native facts |
| --- | --- | --- |
| Pipeline Run Span | `engineering_ci.cicd_telemetry` from separate Run facts | Workflow name, Run ID, Attempt, **Run** conclusion, real start/end |
| Task Run Span | Same adapter from separate Job facts | Job id/name/**url**, **Job** conclusion, real start/end |
| Dagger Function Trace | Dagger SDK only | Not owned here; do not re-instrument |

Job conclusion never becomes Pipeline result. Selection / Required /
`blocked` / `not_executed` stay in ECI Plan/Report and are never encoded as
`cicd.pipeline.result`.

Span kinds follow public CI/CD SemConv: Pipeline Run `SERVER`, Task Run
`INTERNAL`. Only justified GitHub conclusions map to SemConv results
(`success` / `failure` / `cancellation` / `skip` / `timeout`);
`neutral` / `action_required` / `stale` stay unsupported until a fixed registry
mapping is cited. `error.type` is set only when the SemConv result is `failure`
or `error`.

Timestamp fields are adapter-supplied Run/Job `started_at`/`completed_at`
equivalents. When both candidate spans are emitted for the same completed
Run/Attempt, a Job that starts or finishes after the Run `completed_at` is
`partial` (`inconsistent_run_job_timestamps`); individually valid spans are kept.

## Export

The default report producer name remains `eci_core.cicd_telemetry` for persisted
evidence compatibility. This is an evidence label, not an import path; Python
callers use `engineering_ci.cicd_telemetry`.

`engineering-ci cicd-telemetry` writes a **candidate-record** JSON artifact
(`kind=engineering-ci-cicd-telemetry`, `evidence_class=candidate_record`).
Caller `--facts` JSON is untrusted input, not authenticated GitHub provenance.
This slice does **not** claim OTLP/gRPC delivery or Weaver PASS. Missing
identity, terminal Run/Job results, required Task URL, or real timestamps yield
`incomplete` / `partial` (exit 3 when nothing is emitted).

## Related

- Authoritative requirements: [Issue #75](https://github.com/dymzz/engineering-ci/issues/75)
- [Verification](../verification/cicd-telemetry.md)
- Parallel checker: [Issue #74](https://github.com/dymzz/engineering-ci/issues/74)
