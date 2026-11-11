# CI/CD telemetry all_completed Task coverage evidence (#75 / PR #80)

Unsigned local evidence from one native `gh api` CLI run against a completed
Engineering CI Workflow, **without** `--job-id`. Proves full Attempt completed-Job
→ multi-Task Span coverage. **Not** #63 trusted admission and **not** Weaver PASS.

Keep [single-Job diagnostic evidence](../cicd-telemetry-evidence/README.md)
(`--job-id`) separate; do not overwrite those digests with this bundle.

## Fixed Run

| Field | Value |
| --- | --- |
| Repository | `dymzz/engineering-ci` |
| Run ID | `42821429094` |
| Attempt | `1` |
| Job selection | `all_completed` (no `--job-id`) |
| Run HEAD | `7ea85c24a62e65a9384a0abe9eb541ca54064cbc` |
| Native Run conclusion | `success` |
| Completed Jobs on Attempt | `3` |
| Task Spans emitted | `3` |

## Command

```text
uv run python -m engineering_ci.cicd_telemetry \
  --repository dymzz/engineering-ci \
  --run-id 42821429094 \
  --attempt 1 \
  --output docs/verification/cicd-telemetry-evidence-all-completed/candidate-42821429094-all-completed.json \
  --otlp-json docs/verification/cicd-telemetry-evidence-all-completed/otel-42821429094-all-completed.json \
  --json
```

CLI exit code: **0** (candidate generator; status is `partial`, not complete PASS).

## Observed candidate outcome

| Field | Value |
| --- | --- |
| `source` | `github_api` |
| `status` / `reason` | `partial` / `run_completed_at_unproven` |
| Pipeline Span | not emitted (no proven Run completed-at; `updated_at` kept as provenance only) |
| Task Spans | 3 (`sync-tool-tests`, `version-metadata`, `python-ci / checks`) |
| Task Span IDs | distinct (`7d2f4e7f18a603cd`, `a7263c2e65a30498`, `7d98cbfec3f44c1a`) |
| `coverage` | `jobs_observed_total=3`, `completed_jobs_total=3`, `noncompleted_jobs_total=0`, `job_selection=all_completed`, `task_spans_emitted=3`, `tasks_omitted=[]` |
| Invariant | `completed_jobs_total == task_spans_emitted + len(tasks_omitted)` |
| `claims.github_api_fetched` | `true` |
| `claims.github_authenticated` | `false` |
| `claims.otlp_json_exported` | `true` |
| `claims.otlp_delivered` | `false` |
| Resource `cicd.pipeline.run.id` | `42821429094/1` |
| Task Span `status.code` | `0` (UNSET; success must not set OK) |

## OTLP protobuf decode

`opentelemetry-proto` parses the flushed OTLP/JSON as
`ExportTraceServiceRequest` with **3** INTERNAL Task spans and distinct
`cicd.pipeline.task.run.id` values `129847643854`, `129847644372`,
`129847644459`.

## Files

| File | Role |
| --- | --- |
| `candidate-42821429094-all-completed.json` | Candidate report artifact |
| `otel-42821429094-all-completed.json` | OTLP/JSON ExportTraceServiceRequest body |
| `cli-42821429094-all-completed.stdout.json` | CLI `--json` stdout capture |
| `SHA256SUMS` | Digests of the three artifacts |

Exact post-run Pipeline Root, live OTLP/gRPC, and trusted admission remain
Issue #75 follow-through outside this PR slice.
