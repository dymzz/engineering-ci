# Weaver checker evidence

## Real Attempt Task-only integration (#52)

[Task-only bundle](real-attempt-task-only/README.md): Run `42821429094`, Attempt
`1`, three received/matched Task identities, official Registry verified, but
strict native `missing_attribute` on Resource `github.repository`.
`weaver-enforce` exits 1; the same failure is preserved by Report/Artifact/Plan.
The Source, complete exported native output, original reports and Source-drift
refusal are retained with their own checksums. This is a blocked integration,
not a new positive or #63/#61 admission. The controlled #74 cases below remain
separate evidence.

## Historical (pre observed-Span bind)

Unsigned local summaries from `.tmp/weaver74/verified-*` against older HEAD
`bd3ebe17` (pre observed-Span terminal binding). **Not** GitHub-signed and not
#63/#61 trust.

| File | Case | Result |
| --- | --- | --- |
| `positive-live.summary.json` | Completed OTLP capture / live | passed |
| `terminal-passed-pre-bind.summary.json` | Terminal under prior assertion-only bind | passed |
| `terminal-missing-evidence.summary.json` | Terminal without completed evidence | failed |
| `negative-non-otlp.summary.json` | Candidate-record JSON as OTLP | failed |

Checksums: `SHA256SUMS`.

## Current HEAD fixture (unit + native replay)

- Code/unit coverage: `tests/test_weaver.py` (Terminal Run-ID correlation, result
  conflict, Attempt gate, extensible result typing, `native_live_status` /
  `capture_attempt_matched`).
- Bounded CI/CD SemConv v1.45.0 Source: `tests/fixtures/weaver-cicd/`
  (Knowledge #80 identity; pinned official Registry bytes, Pipeline+Task
  matchers, Advice Policies `otel.rego` + `cicd-error-type.rego`, positive
  capture, attempt-absent Terminal variant, SemConv negatives under
  `negatives/`).
- Post-completion Plan: `selection.post_completion_plan` +
  `execute-plan` binding gate (incremental selection never selects Terminal).

Replay (no extra engine env; use the machine's existing Dagger; agent shells need
`FORCE_DARK_MODE=1`):

```text
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live export --path docs/verification/weaver-evidence/head-current/cicd-live
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind terminal export --path docs/verification/weaver-evidence/head-current/cicd-terminal
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind terminal --config-path ci-weaver-terminal-attempt-absent.toml export --path docs/verification/weaver-evidence/head-current/cicd-terminal-attempt-absent
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-missing-pipeline-result.toml export --path docs/verification/weaver-evidence/head-current/negatives/missing-pipeline-result
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-missing-task-url.toml export --path docs/verification/weaver-evidence/head-current/negatives/missing-task-url
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-failure-missing-error-type.toml export --path docs/verification/weaver-evidence/head-current/negatives/failure-missing-error-type
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-error-missing-error-type.toml export --path docs/verification/weaver-evidence/head-current/negatives/error-missing-error-type
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-failure-with-error-type.toml export --path docs/verification/weaver-evidence/head-current/negatives/failure-with-error-type
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind terminal --config-path ci-weaver-terminal-missing-resource-run-id.toml export --path docs/verification/weaver-evidence/head-current/negatives/missing-resource-run-id
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-wrong-result-type.toml export --path docs/verification/weaver-evidence/head-current/negatives/wrong-result-type
```

| Bundle | Case | `status` / notes |
| --- | --- | --- |
| `head-current/cicd-live` | Native Live on CI/CD fixture | `passed` (`gate_exit_code=0`; Pipeline+Task matchers; Advice Policies loaded) |
| `head-current/cicd-terminal` | Terminal PASS | `passed`; `native_live_status=passed`; `terminal_correlation_status=passed`; `capture_attempt_matched`; `trusted=false` |
| `head-current/cicd-terminal-attempt-absent` | Terminal FAIL without Attempt | `failed` / `terminal_attempt_correlation_unverified`; Live still `passed` |
| `head-current/negatives/missing-pipeline-result` | Required Pipeline result absent | `failed` / `findings`; native `required_attribute_not_present`; `gate_exit_code=1` |
| `head-current/negatives/missing-task-url` | Required Task URL absent | `failed` / `findings`; native `required_attribute_not_present`; `gate_exit_code=1` |
| `head-current/negatives/failure-missing-error-type` | `failure` without `error.type` | `failed` / native violation `cicd_conditional_error_type_required` ×2 (Pipeline+Task) |
| `head-current/negatives/error-missing-error-type` | `error` without `error.type` | `failed` / native violation `cicd_conditional_error_type_required` ×2 |
| `head-current/negatives/failure-with-error-type` | `failure` with `error.type` | `passed` |
| `head-current/negatives/missing-resource-run-id` | Resource missing `cicd.pipeline.run.id` | `failed` / `terminal_observed_identity_missing` (ECI correlation rule `eci_terminal_run_id_correlation`); native Live passes |
| `head-current/negatives/wrong-result-type` | Non-string Pipeline result | `failed` / `result_type_invalid` (ECI type gate); native exit 0 |

Coverage matrix fields are recorded on each `report.json` under `coverage`
and are **derived from matched signals** (Registry pin, matchers, Required,
Rego Advisor digest, Result type, Terminal correlation). `official_registry_verified`
records whether the Registry bytes reproduce the pinned upstream SemConv tree,
together with the certified identity (`registry_version`, `registry_commit`,
`official_registry_tree_sha256`) taken from the pin rather than from the
Source's own declaration; a Source that declares a version the pinned release
does not carry is refused with `official_source_version_mismatch`.
`eci_policy_evaluated` records whether ECI's own policies ran. A Registry
**declaration** is not proof that a rule was **executed**, so the two are
reported separately: `resource_required_entities` is what the Registry declares
and `resource_required` is whether the native check actually executed the rule
in this run (`not_applicable` / `not_verified` / `executed`), with
`resource_required_native_evidence` carrying the native evidence. The basis
distinguishes the two `not_applicable` cases: no association declared at all,
versus an association declared while the Pipeline signal was not matched this
run (`pipeline_signal_not_matched`). Native evidence must belong to the target
Pipeline signal — a finding on another matched signal does not prove this rule
ran. For this fixture it is `not_applicable`, because upstream SemConv 1.45.0
declares no entity association for the Pipeline span; the Run-ID requirement is
reported under `eci_run_id_correlation`, and `terminal_gate_failure_reasons`
lists every Terminal gate failure reason. `time_provenance` records the
observed capture and native-sample time sources and precisions; it claims no
exact end time and no duration. Non-CI/CD fixtures report those obligations as
`not_applicable`. `passed` applies only to executed applicable obligations.

### Post-completion Plan → `execute-plan` E2E

See [head-current/post-completion](head-current/post-completion/README.md):
explicit Plan + Dagger `execute-plan` parent Report with nested Terminal
(PASS / closure-mismatch BLOCK / attempt-mismatch BLOCK). This is distinct
from direct `weaver-artifacts` replays.

Committed evidence keeps `report.json`, `live_check.json`, and native
stdout/stderr (plus digests in the report). Packaged Registry bytes are not
duplicated here. Reports remain unsigned / `trusted: false`. Do not treat
historical `terminal-passed-pre-bind` as proof of the current Terminal gate.
