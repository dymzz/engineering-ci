# CI/CD Telemetry Producer

Issue [#75](https://github.com/dymzz/engineering-ci/issues/75) / Knowledge
[PR #79](https://github.com/dymzz/engineering-knowledge/pull/79) stream C (O2/O3).

## Producer decision

| Object | Producer | Native facts |
| --- | --- | --- |
| Pipeline Run Span | `eci_core.cicd_telemetry` from GitHub Actions env or explicit facts JSON / API mapping | `GITHUB_WORKFLOW`, `GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT`, conclusion |
| Task Run Span | Same adapter when Job ID/name are present | Job id/name/url + shared Run identity |
| Dagger Function Trace | Dagger SDK only | Not owned here; do not re-instrument |

Selection / Required / `blocked` / `not_executed` stay in ECI Plan/Report. They
are never encoded as `cicd.pipeline.result`.

## Export

`engineering-ci cicd-telemetry` writes a verifiable JSON artifact
(`kind=engineering-ci-cicd-telemetry`). Live OTLP/gRPC wiring and Weaver
live-check are later slices; missing identity or terminal conclusion yields
`incomplete` (exit 3), never a forged complete Pipeline Span.

## Related

- [Issue #75 proposed body](../issue-revisions/issue-75.proposed.md)
- [Verification](../verification/cicd-telemetry.md)
- Parallel checker: [Issue #74](https://github.com/dymzz/engineering-ci/issues/74)
