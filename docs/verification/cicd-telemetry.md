# CI/CD Telemetry Verification

Verification records for [architecture/cicd-telemetry.md](../architecture/cicd-telemetry.md),
[configuration/github-receiver.md](../configuration/github-receiver.md),
Issue [#75](https://github.com/dymzz/engineering-ci/issues/75), and
Issue [#52](https://github.com/dymzz/engineering-ci/issues/52).

## Official `githubreceiver` + `#52 -> #74 -> #66-C -> #63 -> #61` E2E verification

| Case | Scope & Command | Verified Result |
| --- | --- | --- |
| Upstream `githubreceiver` SemConv audit & OTTL normalization | `uv run --locked pytest -v tests/test_github_receiver.py` (`otelcol-contrib v0.165.0`, live Docker + Dagger Weaver `v1.45.0`) | Unnormalized capture has 27 SemConv violations; OTTL `transform/semconv_v1_45_0` + `filter/drop_raw_webhook_events` passes Weaver with 0 violations (`official_registry_verified=true`) |
| Full E2E consumer chain (`#52 -> #74 -> #52-D4 -> #66-C -> #63 -> #61 R1/R5`) | `uv run --locked pytest -v tests/test_issue52_githubreceiver_e2e.py` | Controlled signed webhook → `otelcol-contrib` → OTLP/gRPC → Dagger `weaver-artifacts --kind=terminal` → `#52-D4` binding → `#66-C` Reconciliation → `#63` Admission → `#61 R1/R5` PASS; Run `44713410255` Attempt 2 partial-rerun separation (`result_inventory` vs `terminal_job_records`/`reused_job_records` companion logs) verified; wrong Attempt / missing Root / scope mismatch / Exit 0 + Correlation FAIL or Missing fail closed (`production_handoff=not_verified` retains pre-cutover fallback cleanly in workflow) |

## Witness CLI, duplicate producer retirement & signal transport slice

Historical schema 7/8/9 trace-synthesis archives remain frozen below for
reference only; ECI's duplicate trace producer is retired (`status=witness_only`,
`spans=[]`, `otlp_json=None`).

| Case | Input | Expected |
| --- | --- | --- |
| Witness default production | Separate run/job conclusions + timestamps + Task URL | `status=witness_only`, `spans=[]`, `trace_producer_retired=true`, `terminal_job_records` populated, `evidence_class=candidate_record`, `production_handoff.status=not_verified` |
| Retired trace producer requests | `--otlp-json`, `--end-time-mode exact|observed_upper_bound`, or `--max-end-uncertainty-ns` | Rejected with `duplicate_trace_producer_retired` (`SystemExit(2)` in CLI; `TelemetryError` in API); pre-existing `--otlp-json` file preserved untouched |
| Attempt API witness default observation | `--repository` + `--run-id` without `--terminal-observed-at` (incl. zero-job `completed` / `cancelled`) | Auto-captures `time_ns()`, confirms `terminal_confirmed=true`, emits `github_attempt_terminal` LogRecord (missing `run_started_at` stays `status=partial`, `missing_run_timestamps`, `duration_valid_for_metrics=false`) |
| Forged non-API terminal confirmation | `mapping` / `env` input with `run_status=completed` + `terminal_observed_at_ns` | `terminal_confirmed=false`, no `github_attempt_terminal` LogRecord (`terminal_source_not_github_api`), `github_authenticated=false` |
| Job success / Run in progress | Job terminal only | `partial` (`missing_run_terminal_result`), `terminal_job_records` kept, `spans=[]` |
| Missing Run ID / Attempt | Incomplete facts | `incomplete`, no spans/logs, exit 3 |
| Missing timestamps / unmapped conclusion | Missing bounds or `neutral` / `action_required` / `stale` | `partial` with explicit reason (`missing_run_timestamps` / `unsupported_run_conclusion`), zero valid Job records |
| Reversed Run/Job bounds | `start > end` | `invalid_*_timestamps`; affected Job omitted or Run invalid |
| Pipeline Run ID & Expected ID scheme | Attempt-scoped execution | `cicd.pipeline.run.id = {run_id}/{attempt}`; `ID_SCHEME=otel-githubreceiver-usecheckrunid-v1` (with `LEGACY_ID_SCHEME` read-only for frozen archives) |
| API Attempt fetch & pagination guards | Injected attempt Run + paginated Jobs | Same-Attempt filtering, `jobs_page_incomplete`, `jobs_snapshot_inconsistent`, `github_cli_unavailable` / `github_api_timeout` |
| Observation before Job end | Observed timestamp `<` max Job `completed_at` | `terminal_observed_before_jobs` / `end_bounds_unverified` under `--end-time-mode witness` |
| OTLP/gRPC transport & per-signal ACK isolation | Logs/Metrics + attached external `githubreceiver` traces | Separate `rpc_accepted` / `otlp_logs_rpc_accepted` / `otlp_metrics_rpc_accepted`; `mark_receiver_observed` required before `receiver_confirmed` |
| Companion Logs / Duration Histogram | Witness Job/Attempt records + explicit phase interval | `github_job_terminal` / `github_attempt_terminal` Logs + `cicd.pipeline.run.duration` Histogram per proven state (otherwise `phase_interval_unproven`) |
| Historical schema 7/8/9 trace archives (frozen) | Run `42821429094` Attempt 1 pre-retirement bundles | Preserved read-only in [cicd-telemetry-evidence](./cicd-telemetry-evidence/README.md), [cicd-telemetry-evidence-all-completed](./cicd-telemetry-evidence-all-completed/README.md), [cicd-telemetry-real-run-receiver](./cicd-telemetry-real-run-receiver/README.md), [cicd-telemetry-real-run-identity-v8](./cicd-telemetry-real-run-identity-v8/README.md), [cicd-telemetry-attribute-mapping](./cicd-telemetry-attribute-mapping/README.md), [cicd-telemetry-otlp-grpc-evidence](./cicd-telemetry-otlp-grpc-evidence/README.md) |

Run:

```text
uv run --locked pytest tests/test_github_receiver.py tests/test_issue52_githubreceiver_e2e.py tests/test_cicd_telemetry.py tests/test_cicd_telemetry_attributes.py tests/test_otlp_grpc_live.py -q
```

Manual API witness check (optional, requires `gh` auth):

```text
engineering-ci cicd-telemetry --repository dymzz/engineering-ci --run-id <id> --end-time-mode witness --otlp-logs-json otel.logs.json
```
