# CI/CD telemetry API → artifact evidence (#75 / PR #77)

Unsigned local evidence from one native `gh api` CLI run against a completed
Engineering CI Workflow. **Not** #63 trusted admission and **not** Weaver PASS.

## Fixed Run

| Field | Value |
| --- | --- |
| Repository | `dymzz/engineering-ci` |
| Run ID | `42821429094` |
| Attempt | `1` |
| Job ID (diagnostic slice) | `129847644459` (`python-ci / checks`) |
| Run HEAD | `7ea85c24a62e65a9384a0abe9eb541ca54064cbc` |
| Native Run conclusion | `success` |
| Native Job conclusion | `success` |
| Completed Jobs on Attempt | `3` |

## Command

```text
uv run python -m engineering_ci.cicd_telemetry \
  --repository dymzz/engineering-ci \
  --run-id 42821429094 \
  --attempt 1 \
  --job-id 129847644459 \
  --output docs/verification/cicd-telemetry-evidence/candidate-42821429094.json \
  --otlp-json docs/verification/cicd-telemetry-evidence/otel-42821429094.json \
  --json
```

CLI exit code: **0** (candidate generator; status is `partial`, not complete PASS).

## Observed candidate outcome

| Field | Value |
| --- | --- |
| `source` | `github_api` |
| `status` / `reason` | `partial` / `run_completed_at_unproven` |
| Pipeline Span | not emitted (no proven Run completed-at; `updated_at` kept as provenance only) |
| Task Span | 1 (`python-ci / checks`) |
| `coverage` | `completed_jobs_total=3`, `job_selection=job_id`, `task_spans_emitted=1` |
| `claims.github_api_fetched` | `true` |
| `claims.github_authenticated` | `false` |
| `claims.otlp_json_exported` | `true` |
| `claims.otlp_delivered` | `false` |
| Resource `cicd.pipeline.run.id` | `42821429094/1` |
| Task Span `status.code` | `0` (UNSET; success must not set OK) |

## Files

| File | Role |
| --- | --- |
| `candidate-42821429094.json` | Candidate report artifact |
| `otel-42821429094.json` | OTLP/JSON ExportTraceServiceRequest body |
| `cli-42821429094.stdout.json` | CLI `--json` stdout capture |
| `SHA256SUMS` | Digests of the three artifacts |

## Status-code rebuild (post-#78 / review P1)

Rebuilt from the **same saved Run facts** in the prior candidate (no new
`gh api` fetch). Only Span Status changed: success Task `status.code` `1`→`0`
(UNSET). Historical digests from HEAD `a32e278` (do not rewrite as if the
original native run emitted UNSET):

| File | Historical SHA-256 (`a32e278`) |
| --- | --- |
| `candidate-42821429094.json` | `882b536389171a13d2b8259918bbbbc89b7a0c5360ac7fe7c4ab35bc16fab658` |
| `otel-42821429094.json` | `4cf1627395205dacd76b7f3c01af154065fc842dc0839cd6977fdf47b374d4e8` |
| `cli-42821429094.stdout.json` | `c6b8fcbc1e89eddb2f4ac55a78bfc3b07763fe51a8f1b38a2cbd11e354dfe936` |

Current digests are in `SHA256SUMS`. Module path is
`engineering_ci.cicd_telemetry` after #78 package unify; evidence producer
label remains `eci_core.cicd_telemetry`.

Single-Job `--job-id` coverage is intentionally **partial**. Full Attempt
multi-Job Task emission evidence (no `--job-id`) is recorded separately under
[cicd-telemetry-evidence-all-completed](../cicd-telemetry-evidence-all-completed/README.md).
Exact post-run Pipeline Root and live OTLP/gRPC remain Issue #75 follow-through.
