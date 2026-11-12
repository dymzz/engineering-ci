# Weaver Registry and completed-capture checks

ECI #74 provides three Dagger Checks: `weaver-registry`, `weaver-live`, and
`weaver-terminal`. They have different inputs and execution times. They are
skipped in this repository's default Check selection; a consumer selects them
explicitly for its own source and policy. These names do not configure GitHub
Required status checks.

The checker uses Weaver 0.28.0, Linux amd64, release archive SHA-256
`9878e2d56ad82ae441b0b527377a4f6001ae19f4af362fb0a4d951884f59ab61`.
The container verifies that archive before extracting its executable. Python
dependencies for OTLP replay are pinned in the module's
`weaver-requirements.txt`. Dagger owns materialization and execution caching.

For a native Check, bind the `weaverSource` Directory setting explicitly when
the module's default Source is not the capture directory. Directory addresses
are resolved by Dagger; selecting `--workspace` does not implicitly substitute
that directory for every Source input. For example, the local consumer fixture
uses `[modules.validation.settings] weaverSource = "tests/fixtures/weaver"`
when invoked from the ECI checkout. The report/artifact/enforce APIs take an
explicit `--source` Directory and do not depend on this setting.

## Source input

Supply one Directory containing a fixed Registry, its expected file hashes,
the native Matcher configuration, and applicable capture inputs. For example:

```toml
# ci-weaver.toml, paths relative to the supplied Source Directory
schema_version = 1
registry = "registry"
registry_manifest = "registry-sha256.json"
matcher_config = ".weaver.toml"
otlp = "capture.json"
expectations = "expected.json"
# Replaces Weaver defaults: vendored otel.rego + cicd-error-type.rego.
advice_policies = "advice-policies"

# Optional native sample-JSON diagnostics, separate from OTLP.
sample = "sample.json"
expected_matchers = ["eci.pipeline"]

[origin]
repository = "https://github.com/open-telemetry/semantic-conventions"
commit = "fb30ca17193b551473b1f43c315482918ab6987c"
version = "1.45.0"
```

`registry-sha256.json` maps every Registry-relative file path to its lowercase
SHA-256. Missing, additional, modified, or symbolic-link inputs fail before
native validation. The expected hashes and origin must themselves come from
the consumer's authorized Source; a self-consistent manifest is not proof of
upstream authenticity. Reports retain `trusted: false`, exact file hashes and
the supplied Dagger Source digest for #63 to assess.

For a Source that claims the pinned official SemConv identity, the Dagger
module injects that authority from outside the Source Directory
(`engineering_ci.semconv_registry`: repository, commit, version and the
canonical upstream `model/` tree digest). The runtime recomputes the same
canonical digest over the Registry it actually read and reports
`official_registry_verified`. Editing the Registry and its manifest together
still fails, because neither can reproduce the pinned upstream digest:
`official_registry_bytes_mismatch` — or `official_source_commit_unverified` for
a different commit — stops the run before native validation. A Source that
declares a different upstream repository is not verified and says so
(`origin_not_pinned_official_source`); it makes no official claim.

The declared `origin.version` is part of that claimed identity and is verified
against the pinned release, so a Source may not present a version the pinned
upstream release does not carry: `official_source_version_mismatch` (also for a
missing version) stops the run before native validation. On a verified Source
the Report carries the certified identity — `coverage.official_registry_verified`,
`coverage.registry_version`, `coverage.registry_commit` and
`coverage.official_registry_tree_sha256` — taken from the pin rather than from
the Source's own declaration. Consumers must read that identity instead of
treating a plain `weaver-enforce` PASS as proof of official provenance.

`coverage.official_source_reason` names every outcome: `official_registry_bytes_mismatch`
and `official_source_commit_unverified` and `official_source_version_mismatch`
and `official_source_pin_unavailable` are refusals that stop the run;
`origin_not_pinned_official_source` (a different upstream repository) and
`origin_repository_undeclared` (a Source with no `[origin]`) make no official
claim and let the native Check run.

The current input contract accepts self-contained definition registries and
local resolved publications. A manifest declaring dependencies is rejected
with `registry_dependencies_require_local_materialization`; the caller must
first provide a local resolved publication and its fixed bytes. Runtime
resolution does not fetch arbitrary remote Registry dependencies. Symlinks and
paths escaping the Source are rejected. ECI has no runtime dependency on the
Knowledge repository or service.

In a supplied local publication, `resolved_registry_uri` is Source-relative
and must point inside the verified Registry directory. Registry/package/diff
commands use an explicit empty config so project config discovery cannot
introduce unbound resolver inputs.

The Registry Check runs native `check --v2`, `package --v2`, then checks the
resulting local publication. The publication points at the local resolved file.
Live and terminal checks use that same local package. Original Registry bytes
are preserved; the generated package and its digest are separate artifacts.
To request native version comparison, add `baseline` and `baseline_manifest`
with the same path/hash contract. Native `diff --v2` output is exported in
`diff/`; a diff is evidence of changes, not a compatibility approval policy.

## Matcher and expected coverage

`.weaver.toml` is a real native configuration, with CEL matchers and applicable
`strict_attribute_groups`. The bounded configuration supports
`live-check.matchers`, optional `live-check.search_all_attributes`,
`live-check.finding_filters`, and `policy.finding_filters`. External advice
files, resolver overrides, and exporter settings are rejected rather than read
from unbound locations. Native built-in policies and explicit CLI options remain
part of the pinned tool execution. CI/CD SemConv fixtures that carry
Resource-level `cicd.pipeline.run.id` (and related identity attrs) set
`search_all_attributes = true` so those attributes resolve against the packaged
catalog; Pipeline/Task Required attributes are still enforced by signal binding.
The CI/CD fixture does not whitelist unknown attributes. `github.repository`
and `github.actions.run_attempt` remain native `missing_attribute` violations.
Attempt correlation does not need that extension.

For the fixed SemConv candidate, the raw Group
`span.cicd.pipeline.run.server` resolves to v2 signal type
`cicd.pipeline.run.server`. Inspect the native package when selecting a signal;
raw Group IDs and resolved signal types are not interchangeable.

```toml
[[live-check.matchers]]
id = "eci.pipeline"
sample_type = "span"
when = 'name == "eci pipeline"'
signal = "cicd.pipeline.run.server"
```

This example signature is for a controlled checker fixture. A production
signature must match the actual producer's stable scope/attributes and cover
missing Required fields without using those fields as its sole discriminator.

`capture.json` is an OTLP/JSON ExportTraceServiceRequest with `resourceSpans`,
`scopeSpans`, hexadecimal IDs and string nanosecond timestamps. It is not
Weaver's sample JSON. Replay converts OTLP hexadecimal byte fields to the
protobuf representation before calling the native gRPC TraceService.

```json
{
  "schema_version": 1,
  "source": {"run_id": "controlled-42", "attempt": 1},
  "spans": [{
    "trace_id": "11111111111111111111111111111111",
    "span_id": "2222222222222222",
    "matcher": "eci.pipeline",
    "signal": "cicd.pipeline.run.server"
  }],
  "terminal_evidence": {
    "run_id": "controlled-42", "attempt": 1,
    "status": "completed", "result": "success"
  }
}
```

`terminal_evidence.result` is the normalized SemConv `cicd.pipeline.result`
value (`success` / `failure` / `cancellation` / `skip` / `timeout` / …).
Raw GitHub `conclusion` belongs to #75 provenance, not this equality.

Each expected span must occur exactly once, match its declared signal/matcher,
and have valid completed timestamps. Empty expected sets, missing spans,
duplicates, zero matching samples and Matcher errors fail. Coverage is scoped
to this declared expected set; the producer/authorized caller owns its
completeness. Run/Attempt metadata is retained, not independently authenticated
by the checker as GitHub provenance.

`weaver-terminal` requires a Pipeline Run signal and completed terminal
evidence. Canonical Run identity is read from the enclosing OTLP **Resource**
`cicd.pipeline.run.id`; Span-level Run ID is optional and only checked for
consistency. Normative result is the Span's `cicd.pipeline.result`, compared to
`terminal_evidence.result`. When capture and Weaver sample both present that
result and disagree, the terminal bind fails (`terminal_result_conflict`).
The capture must contain exactly one matching Trace/Span; only then is an
absent `parentSpanId` accepted as Root.

Live semantics, Terminal correlation and the time source are recorded
separately, in `coverage.time_provenance`: the capture time source
(`otlp_span_startTimeUnixNano_endTimeUnixNano`), the native sample time source
(`weaver_live_check_start_time_end_time_rfc3339`), and the observed fractional
digits of the native timestamps. A Terminal PASS proves the declared
Run/Attempt/result binding and a completed Root span — it does **not** prove an
exact pipeline end time or a duration, and no such claim is made. #75 produces
the time facts; #52 consumes the error budget and source it actually uses and
binds that provenance.

Attempt correlation reads the trailing positive decimal of Resource
`cicd.pipeline.run.id` (for example `42821429094/1`). The whole string is
still the Run identity. The suffix is a separate Attempt claim and must equal
the declared `source.attempt` and `terminal_evidence.attempt`. A Run ID with
no such suffix stays `terminal_attempt_correlation_unverified` and cannot
PASS the terminal gate. The same Run number with a different Attempt is a
different Run ID (`terminal_run_id_mismatch`) or, when the full ID matches
and the declared Attempt does not, `terminal_attempt_mismatch`. A SemConv-only
Live Check may PASS without an Attempt suffix. `native_live_status` covers
only the declared Matcher / finding-policy set for that run.

`attempt_correlation.status=capture_attempt_matched` means the suffix agreed
with the declared Attempt (`authenticated=false`,
`source=cicd.pipeline.run.id`). That is in-capture consistency with the
caller-supplied expected Attempt. It is not agreement with an independent
GitHub witness, and it is not #63 trusted admission. A leftover
`github.actions.run_attempt` is not required. If one is present and disagrees
with the suffix, or is not a positive decimal, the bind fails
(`terminal_attempt_mismatch` or `terminal_attempt_extension_invalid`). #75
still emits that extension and `github.repository`; this checker does not
whitelist either. A later producer change has to stop emitting them before a
strict Live gate can accept that capture. Top-level `trusted` stays `false`.

Matching caller assertions alone cannot pass against a different observed
Resource Run ID or SemConv result. External GitHub provenance authentication
remains #75/#63; #61 owns Required.

Bounded CI/CD SemConv v1.45.0 Pipeline+Task fixture:
`tests/fixtures/weaver-cicd/` (pinned Registry bytes + native matchers). Cart
fixture remains Weaver plumbing only.

## Execution and artifacts

```sh
dagger api call weaver-artifacts --source ./telemetry-source --kind live export --path .artifacts/weaver
dagger api call weaver-report --source ./telemetry-source --kind terminal
dagger api call weaver-enforce --source ./telemetry-source --kind live
```

Keep exports outside the supplied Source Directory. `weaver-artifacts` and
`weaver-report` preserve failures while returning their evidence; strict Check
functions raise for every non-passing result. They reference the same Dagger
execution node, so report/artifact export does not rescan identical input.
The function/trace completing successfully means its report was generated;
consumers must inspect `status`/`gate_exit_code`, use `weaver-enforce`, or run
the native Check through `dagger check`. Calling a Check-returning function
through `api call` can return a Check reference with CLI exit 0 even when its
displayed Check result failed; that is not the gating invocation.

The OTLP lifecycle is finite and runs inside an isolated checker container:
native gRPC listener → `/health` ready → Export RPC acknowledgement →
`POST /stop` → `GET /report` → `POST /shutdown` → process exit. All waits are
bounded, incomplete export or partial rejection fails, and cleanup terminates
the child on errors. Existing Dagger or application exporter routes are not
modified. This replay acknowledgement proves receipt of the supplied capture;
it does not prove an external producer previously flushed a complete Run.

Artifacts include `report.json`, native stdout/stderr and exit codes, original
Weaver `live_check.json`, the local package and optional diff. The ECI report
keeps native `exit_code` separate from `gate_exit_code`: zero native exit with
zero coverage still fails the gate. Original native findings remain intact.

A passing result covers only the executed applicable obligations recorded in
report `coverage` (Registry pin, Pipeline/Task Required, Conditionally
Required Rego Advisor `cicd_conditional_error_type_required`, Result type gate,
and — for Terminal — identity correlation). Official SemConv obligations and
ECI-owned obligations are reported separately: `official_registry_verified`
records whether the Registry bytes reproduce the pinned upstream source, and
`eci_policy_evaluated` records whether ECI's own policies ran. Informational
findings remain visible; `passed` is not a claim of full SemConv compliance.
Reports stay `trusted:false`.

Upstream SemConv 1.45.0 declares no entity association for
`span.cicd.pipeline.run.server`, so it defines no Resource Required obligation
for the Pipeline span. Two propositions are reported separately, because a
Registry **declaration** is not proof that the rule was **executed**:

- `coverage.resource_required_entities` — what the Registry that was read
  declares, keyed by the matched Pipeline span.
- `coverage.resource_required` — whether the applicable rule was actually
  executed by the native check in this run. The four cases are distinct and
  `coverage.resource_required_basis` names which one applies:
  - `not_applicable` + `pinned_semconv_declares_no_entity_association_for_pipeline_span`
    / `registry_declares_no_entity_association_for_pipeline_span` — the
    Registry declares no association for the Pipeline span, so there is no
    rule to execute.
  - `not_applicable` + `pipeline_signal_not_matched` — the Registry **does**
    declare the association, but this run did not match the Pipeline signal,
    so no rule was applicable. The declaration is kept as read in
    `resource_required_entities`.
  - `not_verified` + `entity_association_declared_without_native_execution_evidence`
    — an association is declared and the Pipeline signal matched, but this run
    produced no native evidence that the rule was applied.
  - `executed` + `native_entity_required_finding_observed` — native evidence
    from the same run shows the Entity Required rule ran.
- `coverage.resource_required_native_evidence` carries that native evidence
  (`signal`, `entity_type`, `attribute_key`, `level`, `message`). A finding
  proves the rule ran **for the target Pipeline signal**: a finding on another
  matched signal says nothing about this rule, even when both carry the same
  entity.

Weaver reports an Entity Required rule only when it is violated, so that
finding is the native evidence; its absence proves nothing, and ECI does not
re-implement the SemConv Required judgement in Python. The native finding and
exit code are preserved, so an executed-but-failed rule still fails the gate.

The native finding `entity_required_attribute_not_present` must not be reported
as an upstream Required attribute. The ECI requirement that a capture
identifies its Pipeline Run is the correlation rule
`eci_terminal_run_id_correlation` (`coverage.eci_run_id_correlation`), enforced
by `weaver-terminal` with failures `terminal_observed_identity_missing`,
`terminal_run_id_mismatch` and `terminal_run_id_conflict`;
`coverage.terminal_gate_failure_reasons` lists every reason the Terminal gate
itself can fail with. Do not re-add a local `entity_associations` block to the
pinned Registry to satisfy an older expectation: that would present a local
edit as an upstream SemConv requirement.

In `ci-selection.toml`, explicitly include applicable `weaver_registry`,
`weaver_live`, or `weaver_terminal` in `checks`, and set
`check_scope.weaver_prefixes` to the actual Registry/config/capture paths.
Incomplete Git scope stays `unknown`. `weaver_registry` / `weaver_live` select
when Weaver inputs under those prefixes change. Incremental selection never
claims Terminal readiness from filenames: `weaver_terminal` stays
`not_applicable` with `requires post-completion plan`. Callers build an
explicit `selection.post_completion_plan(...)` from a single `ci-weaver.toml`
(phase `post_completion`, Run/Attempt/result, and the recomputable closure of
Registry manifest+bytes, Matcher, Advice Policies, OTLP, Expectation, Weaver
tool pin). Dagger `execute-plan` recomputes that closure before Native
Terminal; digests or identity drift yield `not_executed` / `blocked` with
non-zero gate — never PASS. Parent reports expose `input_closure_verified`
and `source_tree_verified` separately from legacy `tree_verified`.
Evidence: [post-completion E2E](weaver-evidence/head-current/post-completion/).
`execute-plan --weaver-config` selects the Source-relative configuration path.

During this integration, identical Windows and Linux Source bytes exposed an
existing snapshot-id ordering bug: native `Path` ordering case-folds on Windows.
The shared Source-id function now sorts POSIX relative-path strings explicitly.
Regenerate plans created with the previous ordering; do not edit their digests
or bypass `source_tree_mismatch` refusals.

## Observed upstream limits

With Weaver 0.28.0 and SemConv 1.45.0, integer `123` for
`cicd.pipeline.result` produced information-level `undefined_enum_variant` and
native exit 0. ECI does **not** globally fail on that advice count (extensible
SemConv result strings may be custom). Instead it applies a targeted type check
on `cicd.pipeline.result` / `cicd.pipeline.task.run.result`: non-string values
yield `result_type_invalid`; correctly typed custom strings may PASS. Ordinary
string attribute type errors still produce native `type_mismatch` / exit 1.

Native CI/CD negatives under `tests/fixtures/weaver-cicd/negatives/` show:
missing Pipeline `cicd.pipeline.result` and missing Task
`cicd.pipeline.task.run.url.full` produce native `required_attribute_not_present`
violations (`gate_exit_code=1`); wrong JSON type for `cicd.pipeline.result` is
rejected by ECI as `result_type_invalid` while native exit stays 0. A Resource
without `cicd.pipeline.run.id` produces no native finding, because upstream
SemConv 1.45.0 binds no entity to `span.cicd.pipeline.run.server`; that capture
fails `weaver-terminal` with `terminal_observed_identity_missing` under ECI's
own correlation rule instead.
Conditionally Required `error.type` when Pipeline/Task result is `failure` or
`error` is enforced by the Source-local Weaver Advice Policy
`cicd-error-type.rego` (`policy_id=cicd_conditional_error_type_required`,
level=violation); the same failure result with a present `error.type` PASSes,
as does success without `error.type`. Builtin Weaver information-only
`conditionally_required_attribute_not_present` is retained but no longer the
gate.

Bounded CI/CD SemConv v1.45.0 native Live/Terminal positives, Attempt-absent
Terminal FAIL, Run-ID correlation FAIL and Required/type/conditional negatives
are archived under [weaver-evidence/head-current](weaver-evidence/README.md).

## Official Registry source identity

The CI/CD fixture Registry is a byte-exact copy of the upstream SemConv `model/`
tree at commit `fb30ca17193b551473b1f43c315482918ab6987c` (version `1.45.0`):

| Fact | Value |
| --- | --- |
| Upstream `model/` git tree | `b9a819cdbafe936394bd596bb27c3d929ba24ff4` |
| Local fixture Registry git tree | `b9a819cdbafe936394bd596bb27c3d929ba24ff4` |
| Upstream `model/cicd/spans.yaml` blob | `ed203885c005ab2462bb0d72f5c28c8fbad19d15` |
| `model/cicd/spans.yaml` SHA-256 | `10e19a1811af95b033fb8a9e642865f55a1b66718d1935cfb8d6df060e2d7f86` |
| Pinned canonical upstream tree digest | `85e58bf616303da0488f2cf86f0bb8f9e91ae5fd45e5b38399fa97a288e10963` |

All 269 Registry files match the upstream tree byte for byte. An earlier
revision of this fixture carried a local `entity_associations:
[cicd.pipeline.run]` block on `span.cicd.pipeline.run.server`; that block is
not upstream SemConv and was removed to restore the official bytes. The
`entity.cicd.pipeline.run` entity in `cicd/entities.yaml` is upstream and stays.

Because the fixture must keep those bytes, `tests/fixtures/weaver-cicd/.gitattributes`
marks the whole fixture as `-text`. Checkout then reproduces the published
bytes on every platform, so the Registry stays equal to the upstream tree and
the Plan closure digests (`source_tree_id`, per-file digests, closure digest)
stay reproducible instead of depending on the generating machine's line
endings.

## Package integration with #75 / PR #77

PR #79 unified the installable business package as **`engineering_ci` only**.
#74 / PR #78 must not restore `eci_core`. After #78 merges, rebase #77 onto the
unified tree and port its SCHEMA_VERSION=4 Attempt/OTLP producer increments onto
`src/engineering_ci/cicd_telemetry.py` (and `engineering_ci.cli` / tests). Keep a
single `engineering-ci cicd-telemetry` entrypoint; do not reintroduce an
`eci_core` import path or compatibility shim.

Dagger v1.0.0-beta.16 emitted `core.repositoryformatversion does not support
extension: worktreeconfig` in this managed worktree while calls still completed.
Git-metadata effects of that warning are unverified; Git configuration was not
changed. In this Windows host, terminal-mode `dagger version` returned the
version; `--version` is unsupported. Connection startup sometimes took over a
minute, independently of the native Weaver execution time.

## #52 real GitHub Attempt → Weaver Live integration

[ECI #52](https://github.com/dymzz/engineering-ci/issues/52) owns the consumer-side
Check/Report/Artifact integration; #74 owns the Checker and #75 the Producer.
[PR #82](https://github.com/dymzz/engineering-ci/pull/82) is the **A / Task-only**
slice. Its original archived execution inherited the repair in #74 / PR #83 at
fixed candidate
`8bf82112fae00e63a796c3de1ca22d254076e631`; that candidate was not merged when
the execution below was recorded. The case consumes #75's existing Run
`42821429094`, Attempt `1` archive, rather than fetching a new GitHub Run.

### Original archived strict failure

**Original recorded result: blocked, not a technical E2E PASS.** All three Task identities
were received and matched, but Weaver rejects the Resource attribute
`github.repository`, which is absent from the official Registry. The exact
native finding is `missing_attribute`, level `violation`; native exit and
`gate_exit_code` are both 1. The original capture and strict policy are retained.
Handling that Producer/Checker extension belongs to #74/#75. The consumer PR
was kept Draft for this recorded failure. This is independent of
the missing Pipeline Root: A does not select Terminal or claim exact Run time.

### Original fixed input and independent coverage

`tests/integration/real-attempt-42821429094.json` contains the separately
committed Job witness, its #75 archive commit and exact file hashes, and the
expected Trace/Span mapping. The adapter reuses
`engineering_ci.cicd_telemetry._stable_ids`, checking its output against the
witness. It never obtains Expected Trace IDs from the replay capture. All Tasks
moving to the same wrong Trace therefore fail even when the count stays three.
Raw OTLP identities and Run/Attempt/Head/Repository/Job URLs are compared with
per-object `matched`, `missing`, `unexpected`, `duplicate` and `mismatched`
diagnostics.

The Registry is checked against #74's `engineering_ci.semconv_registry` pin,
outside the supplied Source, as well as its file manifest. A coordinated edit
of Registry bytes and the local manifest is refused. The same independent
witness also prevents a coordinated edit of the archive and its own
`SHA256SUMS`. Archive JSON and witness bytes are protected against checkout
newline conversion; the original recorded hashes remain unchanged.

The temporary Source preserves the original OTLP bytes and Advice files,
includes the witness and original Candidate, and selects `eci.task` as its
expected matcher. Matcher selection uses `name.startsWith("task:")`, so missing
Required attributes cannot exclude the very spans that must be checked.
Official Registry identity, ECI policy execution, Producer claims and Check
outcome are separate facts. No field is stripped or filtered to turn this case
into a pass.

### Execute and inspect the existing interfaces

From the ECI checkout root on a branch containing this #52 integration:

```powershell
uv run --locked python -m pytest tests/test_real_attempt_weaver_e2e.py -q
uv run --locked python tests/integration/prepare_real_attempt_weaver.py --destination .artifacts/real-attempt-weaver-source
dagger api call weaver-report --source .artifacts/real-attempt-weaver-source --kind live
dagger api call weaver-enforce --source .artifacts/real-attempt-weaver-source --kind live
dagger api call weaver-artifacts --source .artifacts/real-attempt-weaver-source --kind live export --path .artifacts/real-attempt-weaver-evidence
```

The destination must be new and exports must remain outside it. Preparation
returns `prepared_not_executed` with `weaver_pass=false`; it does not invoke
Weaver. Ordinary preparation does not require a Pipeline Root.

`weaver-report` and artifact export can exit 0 while their Check report fails.
`weaver-enforce` is the native blocking entry point. The shared Dagger execution
is reused: the archived API report, exported `report.json` and the native Check
inside `execute-plan` were equal, including native samples, commands, ports and
Source digest; each contains one `live-check` command. The Plan was generated
through existing Selection APIs from a real temporary Git Source snapshot.
Its selected Check is `weaver_live` and its Source tree was verified, but the
failed native Check still made the Plan fail. Changing the Source yielded
`source_tree_mismatch` / `not_executed`, exit 4, before any native scan.

The consumer can inspect these existing outputs without executing a scanner:

```powershell
uv run --locked python tests/integration/prepare_real_attempt_weaver.py --verify-source .artifacts/real-attempt-weaver-source --artifacts .artifacts/real-attempt-weaver-evidence --plan-report .artifacts/real-attempt-plan-result.json
```

The optional Plan report must bind the same native report and the current
Source bytes. The consumer checks the exported sample identities, Job fields,
Matcher/Signal, explicit official-source result and input hashes, rather than
accepting a successful API response or self-reported coverage count. For this
blocked case it exits 1 and preserves native failure facts in its rejection.

### Recorded verification and remaining acceptance

2026-11-12: the changed consumer's tests passed (**32 passed**), including wrong
Trace/Attempt/Head/Job, duplicate/missing/unexpected Task, coordinated byte and
manifest tampering, absent artifacts, policy/Source drift, forged coverage and
unsupported trust upgrades. Synthetic result fixtures are unit tests, not
native execution evidence. Ruff passed on the two changed Python files.

The actual strict native rejection, complete input/output archives and Plan
results are in [the Task-only evidence bundle](weaver-evidence/real-attempt-task-only/README.md).
No broad tool/engineering matrix, Root/Terminal, Logs or Metrics check was run.
No valid-positive is claimed for that original input. The separate registered
Producer case below completes new native positive and negative acceptance;
it does not substitute a passing result into the original archive.

B remains a later #52 slice consuming #75's eligible Root/time provenance and
the existing `post_completion_plan` interface. A does not wait for B's precision
budget, and does not turn a network ACK into Weaver PASS. Original Producer
claims stay unchanged (`github_authenticated=false`, `otlp_delivered=false`);
the checker's replay ACK is recorded separately. #63 owns trusted admission and
#61 owns Required/Release. Neither is granted by this technical integration.

### Registered Producer contract and joint acceptance

The [registered Attempt bundle](weaver-evidence/registered-attempt/README.md)
records #52's joint Task-only acceptance of #74 candidate `94996d6` and #75
PR #84 archive `4a354c9` / Producer code `0e72f14`. The original #52 Expected
Job identities are cross-checked with #75's already archived independent API
Witness, source receipt and original exit. No new API call or encoder is used.

```powershell
uv run --locked python tests/integration/prepare_real_attempt_weaver.py --case registered-attributes --destination .artifacts/registered-attempt-source
dagger api call weaver-artifacts --source .artifacts/registered-attempt-source --kind live export --path .artifacts/registered-attempt-artifacts
dagger api call weaver-enforce --source .artifacts/registered-attempt-source --kind live
uv run --locked python tests/integration/prepare_real_attempt_weaver.py --case registered-attributes --verify-source .artifacts/registered-attempt-source --artifacts .artifacts/registered-attempt-artifacts --plan-report <execution-report>
```

The existing Selection/Plan must bind that same Source before execution. The
actual archived Plan selects only `weaver_live`, passes, and embeds the exact
Report/Artifact execution. Candidate uncertainty drift invalidates its Source
binding before a Check is invoked. The Consumer also rejects post-execution
Registry/Advice drift through `selection.compute_weaver_input_closure`, checks
the actual Native tool pin and Policy coverage, and matches received Resource,
Task identities, timestamps and result against the independent source.

The real three-Task positive passed. Eleven isolated native cases cover missing,
extra and duplicate Tasks, a common wrong Trace, wrong repository/Attempt/Head,
zero Matcher/zero sample, Required Task URL and Conditional `error.type`.
Native Live legitimately accepts an extra Task while its declared Expected
three remain present; #52's complete-source inventory rejects the overall case.
It likewise rejects well-typed Source identity mismatches that Native SemConv
does not authenticate. Native exit, native gate and Consumer exit are separate.

The two changed Python files pass Ruff; **78 targeted tests passed**. The
bundle preserves original native diagnostics, complete Artifact archives,
one shared full Source plus exact negative deltas, CLI exits and all hashes.
Native and Selection Advice aggregate hashes use different JSON encodings;
their exact common path/file hashes are compared and both aggregates retained.
The old strict-failure archive is unchanged. Default `--case original` remains
available. These fixed candidate results do not assume #83/#84 have merged.

The Consumer result marks `scope="A / Task-only"` and
`terminal_check="not_executed"`, retaining Producer terminal and per-signal
facts. Real Root time properties, default-branch post-run execution, new
Producer receiver delivery, Logs/Metrics semantic checking, Trusted/Required
and the rest of Issue #52 remain separately unaccepted.

## Verification on 2026-11-10

Dagger v1.0.0-beta.16 executed the fixed SemConv candidate and the pinned
upstream matcher model through the actual Linux checker container. Verified:

| Input/case | Native result | ECI result |
| --- | --- | --- |
| Registry check/package/local load | Exit 0 in all three phases | Passed |
| Completed OTLP capture | Export acknowledged; one expected identity matched | Passed |
| Completed Pipeline Root plus matching terminal evidence | Exit 0, complete lifecycle | Passed |
| Terminal evidence missing | Native exit 0 | Failed |
| Expected span missing | Native exit 0 | Failed |
| Zero samples | Native exit 0 | Failed, coverage missing |
| Unmatched samples / no matchers | Native findings and zero matching coverage | Failed |
| Strict-group Required attribute missing | Native exit 1, Required finding | Failed |
| Wrong ordinary attribute type | Native exit 1, `type_mismatch` | Failed |
| Invalid Matcher signal | Receiver exits 1 before ready | Failed, actual child exit retained |
| Registry byte mismatch | No Registry scan executed | Failed, native exit null |
| Registry and manifest edited together | No Registry scan executed | Failed, `official_registry_bytes_mismatch` |
| Registry equal to the pinned upstream SemConv tree | Exit 0 in all three phases | Passed, `official_registry_verified` |
| Baseline versus derived fixture with one event removed | Native diff records `acme.checkout.abandoned` removed | Diff artifact retained |
| Real Git Tree Plan, same Source bytes | `tree_verified=true` and native Weaver report passed | Passed |
| Same Plan with different Source | `source_tree_mismatch`, exit 4 | Not executed |

`execute-plan` reports `tree_verified` for Source-byte equivalence and keeps
top-level `trusted: false`. Embedded Weaver Check reports also retain
`trusted: false`. Tree binding must not be read as #63 Check-evidence
admission.

Both the native `dagger check` and `weaver-enforce` API gate were observed to
exit 0 on a valid Source and 1 for the strict Required negative. The relevant
host tests passed (74 passed, one existing platform skip). All five repository
Checks passed: version/lock, then lint/format/tests after correcting local
temporary-input exclusion and canonical example byte preservation.

The full local evidence directories are under `.tmp/weaver74/verified-*`,
including raw native reports, logs, packages, diff and input identities. They
are local execution evidence, not GitHub-signed or Required-policy admission.
Checked-in summaries and checksums for review are under
[weaver-evidence](weaver-evidence/README.md). The controlled captures do not
replace #75's real producer evidence or #52's cross-component integration smoke.

At integration baseline `a520765f0aa942a6c9f5a76566f5d65fc1a6e7ba`, #75's
merged slice emits `engineering-ci-cicd-telemetry` candidate-record JSON. That
is not an OTLP ExportTraceServiceRequest and is not accepted as a flushed OTLP
capture by this checker. The actual producer/export and #52's real-source
integration smoke retain their separate acceptance requirements.
