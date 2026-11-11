# Weaver checker evidence summaries (#74 / PR #78)

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

- Code/unit coverage: `tests/test_weaver.py` (Resource Run ID, result conflict,
  Attempt gate, extensible result typing, `native_live_status` /
  `capture_attempt_matched`).
- Bounded CI/CD SemConv v1.45.0 Source: `tests/fixtures/weaver-cicd/`
  (Knowledge #80 identity; pinned Registry bytes, Pipeline+Task matchers,
  positive capture, attempt-absent Terminal variant, SemConv negatives under
  `negatives/`).

Replay (no extra engine env; use the machine's existing Dagger; agent shells need
`FORCE_DARK_MODE=1`):

```text
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live export --path docs/verification/weaver-evidence/head-current/cicd-live
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind terminal export --path docs/verification/weaver-evidence/head-current/cicd-terminal
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind terminal --config-path ci-weaver-terminal-attempt-absent.toml export --path docs/verification/weaver-evidence/head-current/cicd-terminal-attempt-absent
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-missing-pipeline-result.toml export --path docs/verification/weaver-evidence/head-current/negatives/missing-pipeline-result
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-missing-task-url.toml export --path docs/verification/weaver-evidence/head-current/negatives/missing-task-url
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-failure-missing-error-type.toml export --path docs/verification/weaver-evidence/head-current/negatives/failure-missing-error-type
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-wrong-result-type.toml export --path docs/verification/weaver-evidence/head-current/negatives/wrong-result-type
```

| Bundle | Case | `status` / notes |
| --- | --- | --- |
| `head-current/cicd-live` | Native Live on CI/CD fixture | `passed` (`gate_exit_code=0`; Pipeline+Task matchers hit) |
| `head-current/cicd-terminal` | Terminal PASS | `passed`; `native_live_status=passed`; `terminal_correlation_status=passed`; `capture_attempt_matched`; `trusted=false` |
| `head-current/cicd-terminal-attempt-absent` | Terminal FAIL without Attempt | `failed` / `terminal_attempt_correlation_unverified`; Live still `passed` |
| `head-current/negatives/missing-pipeline-result` | Required Pipeline result absent | `failed` / `findings`; native `required_attribute_not_present` violation; `gate_exit_code=1` |
| `head-current/negatives/missing-task-url` | Required Task URL absent | `failed` / `findings`; native `required_attribute_not_present` violation; `gate_exit_code=1` |
| `head-current/negatives/failure-missing-error-type` | `failure` without `error.type` | Live **`passed`**; native only information-level `conditionally_required_attribute_not_present` — **Conditionally Required not accepted** under Weaver 0.28.0 builtin policy |
| `head-current/negatives/wrong-result-type` | Non-string Pipeline result | `failed` / `result_type_invalid` (ECI type gate); native exit 0 |

Committed evidence keeps `report.json`, `live_check.json`, and native
stdout/stderr (plus digests in the report). Packaged Registry bytes are not
duplicated here. Reports remain unsigned / `trusted: false`. Do not treat
historical `terminal-passed-pre-bind` as proof of the current Terminal gate.
