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
`time_source_verified` stays false. Schema 9 keeps time quality in Candidate
`terminal` / `terminal_provenance`, rather than unregistered Span attributes.
`exact_run_duration` means the numeric interval is closed
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

`reconcile_expected_trace` rebuilds those IDs from `run_id`, `attempt` and
`job_ids`. Its `ok` flag means only `verification_scope=task_trace_identity`.
`source_completeness_verified` and `job_attributes_verified` stay false.
Duplicate, empty, or non-decimal Job IDs fail and remain visible. Job URL and
omission coverage stay on the producer candidate, not in this function.

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
| LogRecord | Attempt Job terminal mapped into Task Spans → `ExportLogsServiceRequest` (`signals.logs[].source=github_job_terminal` in Candidate) | Emitted for each Task Span; full Job stdout/stderr archives stay on GitHub Actions. gRPC `LogsService.Export` is a separate `otlp_logs_*` claim and does not inherit Trace delivery |
| `cicd.pipeline.run.duration` | Caller-proven `pending` / `executing` / `finalizing` windows → Histogram, unit `s` | One Histogram datapoint per proven window. A missing window is `not_observable` (`phase_interval_unproven`). The whole Run duration is not recorded as `finalizing`. `cicd.pipeline.run.id` is omitted unless `include_run_entity_metrics` |
| `cicd.pipeline.run.active` | Current active-run count | `not_observable` (`active_count_unproven`) |

Controlled live OTLP/gRPC receiver evidence (ACK + receiver-observed Span IDs,
logs/metrics artifacts) lives under
[cicd-telemetry-otlp-grpc-evidence](../verification/cicd-telemetry-otlp-grpc-evidence/README.md).
The schema 7 real-run receiver ACK stays in
[cicd-telemetry-real-run-receiver](../verification/cicd-telemetry-real-run-receiver/README.md).
Schema 8 identity fields for that same Run are a file check in
[cicd-telemetry-real-run-identity-v8](../verification/cicd-telemetry-real-run-identity-v8/README.md).

A complete Attempt fetch writes the raw Attempt payload and Job pages beside
the candidate (`*.api-pages.json`) before any OTLP file or network export, and
records only page digests, page count, and `completeness` on the candidate.
The output directory is created first. A witness write failure does not start
OTLP export. `github_authenticated` stays false. A page whose length matches
`total_count` is still refused when a list entry is not a Job object, or when
`status` is missing or not one of `queued`, `in_progress`, `completed`,
`waiting`, `requested`, or `pending` (`jobs_payload_invalid`). A real
noncompleted status stays in the source count and does not become a Task.
Missing `gh` or API timeouts map to `github_cli_unavailable` /
`github_api_timeout` (no retry engine).

OTLP `InstrumentationScope` carries the producer name only. `schema_version`
stays on the candidate report and is not written as `scope.version`.
`producer.semconv_source` lists the groups this producer maps
(`group_ids_role=producer_mapping`). It does not copy a second official
repository, version, or commit. `official_source_pinned` and
`official_registry_verified` stay false until the shared registry record from
the #74 fix is in this baseline and a native check has actually run.
`job_mapping.verification_scope` is `producer_objects`.
`source_independence_verified` and `observed_capture_verified` stay false.
`attempt_coverage=complete` means the completed Job set was emitted or legally
omitted. `task_coverage` stays partial while any Task was omitted.
`job_url_verified` covers only rows that carried a URL.

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
