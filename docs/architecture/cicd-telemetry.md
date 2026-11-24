# CI/CD Telemetry Architecture & Producer Retirement

Issue [#75](https://github.com/dymzz/engineering-ci/issues/75) / Issue [#52](https://github.com/dymzz/engineering-ci/issues/52) / Knowledge
[PR #79](https://github.com/dymzz/engineering-knowledge/pull/79) stream C (O2/O3).

> **Upstream-first architecture & duplicate ECI trace producer retirement.** GitHub Actions Workflow / Job / Step Trace production uses OpenTelemetry Collector Contrib's official `githubreceiver` (`Webhook -> githubreceiver -> OTTL transform/semconv_v1_45_0 -> filter/drop_raw_webhook_events -> OTLP -> SemConv v1.45.0 Weaver -> #52 -> #66-C -> #63 -> #61`; see [GitHub Receiver config](../configuration/github-receiver.md)). Following end-to-end #75 and #52 verification, ECI's duplicate GitHub Pipeline/Task Span synthesis in `engineering_ci.cicd_telemetry` is **retired** (`status=witness_only`, `trace_producer_retired=true`, `ID_SCHEME=otel-githubreceiver-usecheckrunid-v1`). `engineering_ci.cicd_telemetry` remains the independent GitHub Attempt API Witness, companion Terminal LogRecord / phase-Histogram carrier, and expected-ID reconciler (`reconcile_expected_trace` / `reconcile_attempt_witness_capture`). Explicitly requesting legacy synthesis (`--otlp-json`, `--end-time-mode exact|observed_upper_bound`, or `--max-end-uncertainty-ns`) fails with `duplicate_trace_producer_retired`.

## Producer decision

| Object | Producer | Native facts & ownership |
| --- | --- | --- |
| Pipeline / Job / Queue / Step Spans | OpenTelemetry Collector Contrib `githubreceiver` (pinned `0.165.0` image + [otel-collector-github-receiver.yaml](../../config/otel-collector-github-receiver.yaml)) | Authenticated `workflow_run` & `workflow_job` webhooks; deterministic Attempt Trace/Root/Job IDs (`UseCheckRunID`); normalized to SemConv `v1.45.0` via built-in OTTL `transform/semconv_v1_45_0` & `filter/drop_raw_webhook_events` and validated via pinned Weaver `v1.45.0` (`config/github-receiver.weaver.toml`) without mutating official Registry YAML |
| Independent Attempt Witness, LogRecords & Metrics | `engineering_ci.cicd_telemetry` (`status=witness_only`, `--end-time-mode witness`) | Attempt API completeness witness (`runs/{id}/attempts/{N}` + paginated Jobs), `terminal_job_records`, `github_job_terminal` / `github_attempt_terminal` LogRecords bound to `githubreceiver` IDs, and caller-proven phase-interval `cicd.pipeline.run.duration` Histograms |
| `#52 -> #66-C -> #63 -> #61` Consumer Closure | `engineering_ci.github_receiver`, `terminal_expected`, `reconciliation`, `admission`, `required_assessment` | Expected Run/Attempt/Job reconciliation against Weaver-verified `githubreceiver` Terminal signals (`terminal_bindings` in `reconcile_expected_results` & `assess_required_obligations`) |
| Dagger Function Trace | Dagger SDK only | Not owned here; do not re-instrument |

Inputs (first → second slice of the witness CLI):

1. Explicit `--facts` mapping or Actions env (untrusted / incomplete identity).
2. **GitHub Actions API** via native `gh api`: `--repository` + `--run-id`
   (+ optional `--job-id` / `--attempt`). Run `conclusion` and Job `conclusion`
   stay independent. The witness loads **the same Attempt**:
   `GET .../runs/{id}/attempts/{N}` plus that Attempt's paginated Jobs — never
   the latest Run view mixed with an older Attempt's Jobs.

### Timestamps & Witness Bounds

| Field | Source | Role |
| --- | --- | --- |
| Run start | GitHub `run_started_at` / explicit mapping | Exact Pipeline start when present |
| Run `updated_at` | GitHub Workflow Run object update time | Provenance / estimate only (`run_updated_at_ns`); **not** promoted to an ECI-certified Attempt completed-at |
| Terminal observed | Explicit `--terminal-observed-at`, or automatic host `time_ns()` after verified GitHub Attempt API fetch (`--end-time-mode witness`) | Observation upper bound only (`end_upper_bound_ns`); requires `source="github_api"` with `github_api_fetched=true`, keeps `time_precision=unproven`, `time_source_verified=false`, `root_emission=not_emitted` |
| Job bounds | Job `started_at` / `completed_at` | Independent Task completeness & lower-bound monotonicity checks |

Duplicate ECI Trace/Task Span synthesis is retired (`END_TIME_EXACT` and
`END_TIME_OBSERVED_UPPER_BOUND` raise `duplicate_trace_producer_retired`;
`report["spans"]` stays `[]` and `otlp_json` stays `None`). Under default
`--end-time-mode witness`, native Attempt API `run_status=completed` with
`--terminal-observed-at` (or host `time_ns()` automatically captured after
verified `github_api` fetch) records monotonic constraint bounds (last completed
Job end, or weak Run start when `completed_jobs_total=0`, through the
observation time) and emits `github_job_terminal` / `github_attempt_terminal`
LogRecords correlated to the expected `githubreceiver` IDs. When
`github_api_fetched=true` and `run_status=completed` has missing
`run_started_at`, the Attempt terminal observation LogRecord still emits while
`missing_run_timestamps` keeps `status=partial`, `duration_valid_for_metrics=false`,
and Histograms `not_observable`/`unproven`. Untrusted `--facts` / `env` inputs
cannot forge API terminal confirmation (`terminal_source_not_github_api`). Observation before any Job
`completed_at` fails with `terminal_observed_before_jobs` /
`end_bounds_unverified`. That LogRecord is not a Pipeline duration, and it is
not a Weaver result (`time_source_verified=false`, `duration_valid_for_metrics=false`).
Event hints are conflict-checked against the Attempt API and never substitute
for API status. A Job start/end envelope is coverage metadata
(`job_execution_envelope`), not `cicd.pipeline.run.duration`.
Coverage records `pipeline_terminal_covered=false` separately from `task_coverage`,
plus a machine-readable `expected_completed_jobs` inventory for `#52` hand-off
(`emitted=false`, populated `expected_task_span_id` under `otel-githubreceiver-usecheckrunid-v1`).
`coverage.expected_trace` publishes the Attempt's expected `githubreceiver` Trace ID and
Pipeline Span ID (`parent_exported=false`); `coverage.withheld_parent_span`
records `reason=duplicate_trace_producer_retired`.

### Expected Trace identity (`otel-githubreceiver-usecheckrunid-v1`)

`#52` aligns expected Trace/Span identity directly with upstream `githubreceiver`
(`UseCheckRunID`) rather than maintaining a parallel custom hash:

| ID | Formula (`engineering_ci.github_receiver`) | Encoding |
| --- | --- | --- |
| `expected_trace_id` | `sha256("{run_id}{run_attempt}t")` | first 32 hex characters (16 bytes) |
| `expected_pipeline_span_id` | `sha256("{run_id}{run_attempt}s")` | characters `16..32` (8 bytes) |
| `expected_task_span_id` | `sha256("{job_id}-j")` | characters `16..32` (8 bytes) |

`reconcile_expected_trace` defaults to `ID_SCHEME=otel-githubreceiver-usecheckrunid-v1`
and accepts `id_scheme=LEGACY_ID_SCHEME` (`github-actions-attempt-sha256-v1`) only when
auditing frozen historical schema artifacts. In `otel-githubreceiver-usecheckrunid-v1`,
nested `queue-*` / step child spans parented to a Job span are excluded from the
Job ID set unless they carry `cicd.pipeline.task.run.id` or attach directly to the
Pipeline Root Span. Duplicate, empty, or non-decimal Job IDs fail explicitly.
Full webhook capture + Attempt API Witness reconciliation lives in
`github_receiver.reconcile_github_receiver_trace` and `cicd_telemetry.reconcile_attempt_witness_capture` (which forwards Attempt `run_started_at` and optional independent `executed_job_ids` so partial reruns like Run `44713410255` Attempt 2 separate full Attempt `result_inventory` from `executed_jobs` vs `reused_jobs`, rather than inferring execution from `run_attempt` alone).

### Pipeline Run identity (cross-PR with #74)

Each GitHub Actions **Attempt** is one distinct pipeline execution (own Trace).
SemConv `cicd.pipeline.run.id` is the stable canonical form
`{github_run_id}/{run_attempt}` on Resource, Pipeline Span, and Task
correlation. Raw GitHub Run ID and Attempt remain in `facts.*`,
`coverage.expected_trace`, and the independent API Witness. Schema 9 omits the
unregistered `github.actions.run_attempt` attribute. Consumers must compare
the complete composite ID against the independently declared Run and Attempt,
then reconcile the Trace/Span IDs with that witness; comparing only the Run
prefix loses Attempt binding. When GitHub provides `html_url`, it is
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

Without `--job-id`, the witness inspects **every completed Job**
on the Attempt (`job_selection=all_completed`) and records omitted Jobs under
`coverage.tasks_omitted`. Completed Jobs are counted by `status == "completed"`
without requiring an ID; a missing ID is an omission located by source index,
never dropped from the denominator. Coverage also records
`jobs_observed_total` and `noncompleted_jobs_total`. Because ECI duplicate
Task Span emission is retired (`task_spans_emitted == 0`), valid completed Jobs
populate `terminal_job_records` and `coverage.expected_completed_jobs` while
invalid/incomplete Jobs are recorded under `coverage.tasks_omitted`:
`completed_jobs_total == len(terminal_job_records) + len(tasks_omitted)`.
`--job-id` remains a deliberate single-Job diagnostic slice
(`job_selection=job_id`): unselected completed Jobs are a coverage gap signaled
by `partial_job_coverage`, not fabricated `tasks_omitted` entries. When
completed Jobs exceed valid Job records or any omission occurs, status is
`partial`.

Job pagination refuses an inconsistent snapshot. Every page `total_count` must
be a non-negative integer (`bool` is not accepted). A missing, mistyped, or
negative total is `jobs_total_count_invalid`. A total that changes between
pages, or a duplicate Job ID, is `jobs_snapshot_inconsistent`. A short page
that does not reach the declared total is `jobs_page_incomplete`. The producer
does not declare a complete Attempt Job set without that total. After the
pages are read, every returned Job is checked for a provided `run_id` or
`run_attempt`. A missing Job ID still enters the omission list, and it does
not skip a mismatched or illegal membership field.

## Export

### Attribute contract (schema 9)

The upstream identity belongs to #74's shared
[`engineering_ci.semconv_registry` record](https://github.com/dymzz/engineering-ci/blob/94996d6cc28e73a9862003b836a6ad3b3fe9996b/src/engineering_ci/semconv_registry.py):
SemConv 1.45.0 at `fb30ca17193b551473b1f43c315482918ab6987c`.
That record is in unmerged PR #83, outside this Producer's `main` baseline.
The Producer does not copy its pin, verify a Registry, or elevate its
`producer.semconv_source.official_registry_verified=false`. The audit below
uses the original upstream definitions consumed by that existing Checker.
An OTLP protobuf decode alone proves no SemConv conformity.

R means Resource shared by Traces/Logs and, after the existing Run-identity
opt-in reduction, Metrics. P/T mean Pipeline/Task Span; L means LogRecord
attributes; M means Metric datapoint attributes. Attribute presence in the
catalog does not make it Required on every signal. Native Live validation in
this repository covers Traces only; it does not establish Logs/Metrics
semantic validation.

| OTLP field | Location | Native source / mapping | Classification and upstream constraint | Producer handling / consumer |
| --- | --- | --- | --- | --- |
| `cicd.pipeline.name` | R, P, M | Attempt API `name` / Actions `GITHUB_WORKFLOW` | Official, string human-readable pipeline name, `cicd/registry.yaml` | Keep; #74 native catalog, #52 source agreement |
| `cicd.pipeline.run.id` | R, P, T, L; M opt-in | GitHub Run ID + positive Attempt | Official string identifying an execution; upstream does not define GitHub's encoding | Keep existing `{run_id}/{attempt}`; independent Expected/Witness must bind the entire value |
| `cicd.pipeline.run.url.full` | R, P | Attempt `html_url` / explicit Run URL | Official full Run URL | Keep original; repository path, Run and supplied Attempt suffix must agree |
| `vcs.repository.url.full` | R | API `repository.html_url`, or supplied server/Run URL origin + `repository.full_name` / `GITHUB_REPOSITORY` | Official string, canonical complete HTTP(S) browser URL; Git URL should omit `.git`, `vcs/registry.yaml` | Use original repository URL when present; verify owner/repo path and all supplied origins; missing origin omits URL with `repository_identity.reason` |
| `vcs.ref.head.revision` | R, P | API `head_sha` / Actions SHA / explicit mapping | Official string VCS revision, `vcs/registry.yaml` | Keep; source Head SHA remains independently checked; it is not proof of the checked checkout Tree |
| `cicd.pipeline.result` | P; applicable M | Separate Run `conclusion` | Official enum: success/failure/error/timeout/cancellation/skip | Existing justified mapping; never copy Job or ECI gate state |
| `cicd.pipeline.task.name` | T, L | Job `name` | Official string human-readable task name | Keep; native Task signal / #52 object agreement |
| `cicd.pipeline.task.run.id` | T, L | Job `id` | Official string unique within Run | Keep decimal Job identity; missing ID remains explicit omission |
| `cicd.pipeline.task.run.url.full` | T, L | Job `html_url` | Official complete Task URL; Required on Task Span | Keep source value; missing URL still withholds that Task |
| `cicd.pipeline.task.run.result` | T, L | Job `conclusion` | Official enum with same six values above | Keep separate Job mapping, no fabricated Run result |
| `error.type` | P, T; applicable M | Error Run/Job conclusion | Official string, `error/registry.yaml`; conditional error rules apply | Keep for failure/error/timeout; preserve ERROR status and native error-policy findings |
| `cicd.pipeline.run.state` | M | Explicit phase intervals with Run/Attempt/source | Official enum pending/executing/finalizing | Keep; Job envelope never becomes a phase |
| `cicd.pipeline.run.duration` | Metric name, Histogram `s` | Proven phase interval end minus start | Official metric, `cicd/metrics.yaml`, duration per state | Existing Histogram/DELTA; absent phases stay `not_observable`; approximate Root never supplies duration |
| `cicd.pipeline.run.active` | No output | No active-run count source | Official metric, unobservable here | Keep `metrics_not_observable`, emit no invented datapoint |
| `github.repository` | Former R | Raw owner/repo | Unknown to pinned official catalog; actual Task-only native `missing_attribute` failure | Replace OTLP attribute with sourced URL; preserve raw repository in Candidate/Witness; old Capture remains immutable |
| `github.actions.run_attempt` | Former R/P/T/L, opt-in Metrics R | Raw Attempt | ECI/GitHub extension, unregistered; #74 historically has a narrowly scoped finding filter | Remove from new OTLP; keep facts, composite Run ID, Expected ID scheme and Source Witness; #74 candidate `94996d6` binds the composite suffix; pre-#83 main Terminal still requires the old extension |
| `eci.cicd.end_time_quality` | Former P | End precision decision | ECI extension, unregistered; no prior real Root native failure established | Keep `terminal` / `terminal_provenance.end_time_quality`; omit OTLP key |
| `eci.cicd.exact_run_duration` | Former P | Explicit numeric End vs observation | ECI extension, unregistered | Keep corresponding Candidate/Provenance boolean; no claim of GitHub-certified end |
| `eci.cicd.duration_valid_for_metrics` | Former P | Root time-quality decision | ECI extension, unregistered | Keep Candidate/Provenance boolean; existing phase-Metric eligibility unchanged |
| `eci.cicd.root_emission` | Former P/L | explicit_end / approximate_root / not_emitted | ECI extension, unregistered | Keep Terminal/Provenance and rootless log Candidate metadata |
| `eci.log.source` | Former L | github_job_terminal / github_attempt_terminal | ECI extension, unregistered | Keep `signals.logs[].source` in Candidate; no replacement OTLP attribute |
| `eci.cicd.time_role` | Former rootless L | terminal_observed_at | ECI extension, unregistered | Keep log Candidate `terminal_provenance.time_role` |
| `eci.cicd.end_lower_bound_ns` | Former rootless L | Completed Job max end / weak Run start | ECI extension, unregistered | Keep log Candidate and existing Terminal bounds; no exact End invented |
| `eci.cicd.end_upper_bound_ns` | Former rootless L | Observation after terminal verification | ECI extension, unregistered | Keep log Candidate and existing Terminal bounds |
| `eci.cicd.uncertainty_ns` | Former rootless L | Upper minus lower bound | ECI extension, unregistered | Keep log Candidate and existing Terminal uncertainty/budget |
| `eci.cicd.parent_span_exported` | Former rootless L | Root withheld | ECI extension, unregistered | Keep log Candidate boolean and `coverage.withheld_parent_span` |
| `vcs.repository.name`, `vcs.owner.name` | No output | Splittable owner/repo | Official strings: repository name excludes owner, owner identifies group | Catalog alternatives audited; unnecessary once canonical URL is sourced, so no extra attributes |
| `traceId`, `spanId`, `parentSpanId`, Span name/kind/start/end/status | Trace protocol fields | Existing stable-ID scheme, real Job bounds, gated Root bounds, result mapping | OTLP protocol fields, not Registry attributes | Encoding unchanged; P=SERVER, T=INTERNAL; success UNSET, errors ERROR; #52 reconciles independent objects |
| LogRecord body, time, severity, Trace/Span IDs | Logs protocol fields | Existing Job terminal / rootless Attempt observation | OTLP protocol fields; body is bounded event text, not full job output | Same event content/time/association; source and time semantics accompany Candidate rather than custom attributes |
| Histogram bounds/count/sum/min/max/start/end, scope name | Metrics / instrumentation protocol fields | Existing measured phase and producer label | OTLP protocol fields, not SemConv attribute declarations | Encoding unchanged; persisted scope label `eci_core.cicd_telemetry` has no schema version |

Repository URL validation accepts non-default GitHub Enterprise hosts and ports.
It rejects credentials, query/fragment-bearing repository origins, malformed
URLs, conflicting hosts, and a URL path for another repository/Run/Attempt.
An absent `server_url` never defaults to `github.com`. The raw Source Witness
is preserved before export and retains its original repository object.

Schema 9's `producer.attribute_contract` names this changed output contract.
Consumers must bind the Candidate/Terminal Provenance and Capture digests:
OTLP alone no longer carries ECI time-quality metadata and cannot prove an
exact Run duration. This introduces no extension Registry, parallel policy,
or finding filter. It does not change #52's independent Expected/Observed
reconciliation, #63 admission, or #61 Required authority.

The default report producer name remains `eci_core.cicd_telemetry` for persisted
evidence compatibility. This is an evidence label, not an import path; Python
callers use `engineering_ci.cicd_telemetry` (post-#78 / package unify).

`engineering-ci cicd-telemetry` writes:

- a **candidate/witness** JSON artifact (`kind=engineering-ci-cicd-telemetry`,
  `evidence_class=candidate_record`, `status=witness_only|partial|incomplete`);
- optional `--otlp-logs-json` / `--otlp-metrics-json` companion signal files;
- passing legacy trace flags (`--otlp-json`, `--end-time-mode exact|observed_upper_bound`,
  or `--max-end-uncertainty-ns`) exits `2` (`duplicate_trace_producer_retired`).

Caller `--facts` JSON is untrusted input, not authenticated GitHub provenance.
`claims.trace_producer_retired` is `true` and `claims.otlp_json_prepared` /
`otlp_json_exported` remain `false` for built-in synthesis. `--otlp-endpoint`
performs OTLP/gRPC export for prepared Logs/Metrics (or externally attached
`githubreceiver` trace payloads); `claims.rpc_accepted`, `otlp_logs_rpc_accepted`,
and `otlp_metrics_rpc_accepted` track per-signal RPC acceptance independently. An RPC ACK is **not** `receiver_observed`,
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
invent spans. CLI exit 0 for `partial` or `witness_only` is witness/companion
generator behavior — consumers must read `report.status` / coverage and
complete `#52 -> #74 -> #66-C -> #63 -> #61` verification before gate acceptance.

Post-run entry: `.github/workflows/cicd-telemetry-post-run.yml` listens for
`workflow_run` completion of `Engineering CI` (and supports
`workflow_dispatch`). It reads the source Run via Attempt API using the event
identity, never executes the source PR head, runs `--end-time-mode witness`
(auto-capturing host observation time when `--terminal-observed-at` is omitted),
and uploads witness/logs/metrics artifacts (`--otlp-json` retired). Live
Collector endpoint wiring remains optional via repository secrets / dispatch
inputs; absence of a receiver leaves RPC acceptance false. Code retirement in
`engineering_ci.cicd_telemetry` is distinct from live production cutover:
`report["production_handoff"]` defaults to `status=not_verified`
(`local_e2e_proves_production_webhook=false`), and the workflow records
`production_webhook_cutover` from `vars.ECI_GITHUBRECEIVER_PROD_VERIFIED`
(`not_verified` until repository Webhook subscription, HMAC secret, live
Collector ingress, OTLP delivery, and `#52` consumer gate evidence are verified).

On the `#52 -> #66-C -> #63 -> #61` consumer chain, `reconciliation` additionally
rejects `weaver_terminal` if `weaver/terminal-live-check` reports Exit `0`
while `gate_status != "passed"`, `gate_exit_code != 0`, or
`terminal_correlation_status != "passed"` (`terminal_correlation_unproven`),
aligning with native tool-contract enforcement.

### O2 Logs / O3 Metrics & Retired Producer Baseline

| Signal | Source → producer → OTLP | Status |
| --- | --- | --- |
| LogRecord | `terminal_job_records` / Attempt observation bound to `githubreceiver` Trace/Span IDs → `ExportLogsServiceRequest` (`signals.logs[].source=github_job_terminal|github_attempt_terminal`) | Companion LogRecords only; full Job stdout/stderr archives stay on GitHub Actions. gRPC `LogsService.Export` tracks `otlp_logs_*` separately |
| `cicd.pipeline.run.duration` | Caller-proven `pending` / `executing` / `finalizing` windows → Histogram, unit `s` | One Histogram datapoint per proven window (with `run_conclusion` fallback when trace synthesis is retired). A missing window is `not_observable` (`phase_interval_unproven`) |
| `cicd.pipeline.run.active` | Current active-run count | `not_observable` (`active_count_unproven`) |

Controlled live OTLP/gRPC receiver evidence (logs/metrics + external trace transport) and frozen schema 7/8/9 trace archives live under
[cicd-telemetry-otlp-grpc-evidence](../verification/cicd-telemetry-otlp-grpc-evidence/README.md),
[cicd-telemetry-real-run-receiver](../verification/cicd-telemetry-real-run-receiver/README.md), and
[cicd-telemetry-real-run-identity-v8](../verification/cicd-telemetry-real-run-identity-v8/README.md).

A complete Attempt fetch writes the raw Attempt payload and Job pages beside
the candidate (`*.api-pages.json`) before any OTLP file or network export, and
records only page digests, page count, and `completeness` on the candidate.
The output directory is created first. A witness write failure does not start
OTLP export. `github_authenticated` stays false. A page whose length matches
`total_count` is still refused when a list entry is not a Job object, or when
`status` is missing or not one of `queued`, `in_progress`, `completed`,
`waiting`, `requested`, or `pending` (`jobs_payload_invalid`). A real
noncompleted status stays in the source count and does not become a Job witness record.
Missing `gh` or API timeouts map to `github_cli_unavailable` /
`github_api_timeout` (no retry engine).

OTLP `InstrumentationScope` carries the producer name only. `schema_version`
stays on the candidate report and is not written as `scope.version`.
`producer.semconv_source` lists the companion groups (`log.cicd.pipeline.terminal`,
`metric.cicd.pipeline.run.duration`) with `group_ids_role=producer_mapping`.
`official_source_pinned` and `official_registry_verified` remain `false` on this
companion witness payload; native SemConv v1.45.0 verification runs against
`githubreceiver` OTLP captures via `engineering_ci.github_receiver` + Dagger Weaver.
`attempt_coverage=complete` means the completed Job set was recorded in
`terminal_job_records` or legally omitted; `task_coverage` stays partial while any Task was omitted.

Full `#75` (`Webhook -> githubreceiver -> OTTL -> SemConv v1.45.0 Weaver`) +
`#52 -> #66-C -> #63 -> #61` E2E verification lives in
[github-receiver configuration](../configuration/github-receiver.md),
[terminal-expected.md](terminal-expected.md), and
[Verification](../verification/cicd-telemetry.md). Existing `engineering_ci.cicd_telemetry`
continues to own Attempt API Witness acquisition, companion Logs/Metrics,
expected-trace reconciliation (`otel-githubreceiver-usecheckrunid-v1`), and
OTLP/gRPC transport receipts while duplicate GitHub Trace/Task Span emission is retired. Production Collector webhook deployment remains opt-in behind environment secrets.

## Related

- Authoritative requirements: [Issue #75](https://github.com/dymzz/engineering-ci/issues/75), [Issue #52](https://github.com/dymzz/engineering-ci/issues/52)
- [GitHub Receiver Configuration](../configuration/github-receiver.md)
- [Terminal Expected Binding](terminal-expected.md)
- [Verification](../verification/cicd-telemetry.md)
- Parallel checker: [Issue #74](https://github.com/dymzz/engineering-ci/issues/74)
