# Post-completion Plan → execute-plan E2E (#74 / PR #78)

Controlled CI/CD fixture Source: `tests/fixtures/weaver-cicd/`.
Plans built with `selection.post_completion_plan(...)` (closure from
`ci-weaver.toml` only). Commands (agent shells need `FORCE_DARK_MODE=1`):

```text
dagger api call execute-plan --plan docs/verification/weaver-evidence/head-current/post-completion/plan.json --source tests/fixtures/weaver-cicd --weaver-config ci-weaver.toml
dagger api call execute-plan --plan docs/verification/weaver-evidence/head-current/post-completion/plan-closure-mismatch.json --source tests/fixtures/weaver-cicd --weaver-config ci-weaver.toml
dagger api call execute-plan --plan docs/verification/weaver-evidence/head-current/post-completion/plan-attempt-mismatch.json --source tests/fixtures/weaver-cicd --weaver-config ci-weaver.toml
```

| Artifact | Case | Parent status | Notes |
| --- | --- | --- | --- |
| `plan.json` + `execution-report.json` | Full closure | `passed` | `input_closure_verified=verified`; nested `weaver_terminal` PASS; `trusted=false` |
| `plan-closure-mismatch.json` + `execution-report-closure-mismatch.json` | Tampered Advice/closure digest | `blocked` | Terminal `not_executed` / `post_completion_closure_digest_mismatch` (Native not invoked) |
| `plan-attempt-mismatch.json` + `execution-report-attempt-mismatch.json` | Attempt ≠ expectation | `blocked` | Terminal `not_executed` / `post_completion_evidence_mismatch` |

`execution-report.json` omits nested native sample bodies for size; keeps
statistics, coverage, digests, gate, and identity fields. Raw dagger stdout is
not committed.

The Registry closure digest recomputed here equals the pinned official upstream
SemConv tree digest
(`85e58bf616303da0488f2cf86f0bb8f9e91ae5fd45e5b38399fa97a288e10963`),
because the fixture Registry is byte-identical to the upstream `model/` tree. The
nested Terminal report records `official_registry_verified` and
`eci_policy_evaluated` separately, and `coverage.resource_required` is
`not_applicable` because upstream SemConv declares no Resource Required
obligation for the Pipeline span.
