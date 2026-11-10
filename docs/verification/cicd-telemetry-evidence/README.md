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
uv run python -m eci_core.cicd_telemetry \
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

## Files

| File | Role |
| --- | --- |
| `candidate-42821429094.json` | Candidate report artifact |
| `otel-42821429094.json` | OTLP/JSON ExportTraceServiceRequest body |
| `cli-42821429094.stdout.json` | CLI `--json` stdout capture |
| `SHA256SUMS` | Digests of the two artifacts |

Single-Job `--job-id` coverage is intentionally **partial**. Exact post-run
Pipeline Root, multi-Job Task emission, and live OTLP/gRPC remain Issue #75
follow-through outside this PR slice.
