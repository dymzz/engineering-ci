# Workflow/Shell Dagger Plan result evidence

Real Dagger Engine `v1.0.0-beta.16` bundle for PR #103 / Issue #58.

- Date: 2026-11-16 (Atlantic/Reykjavik)
- Code/evidence revision: `87b8e47` plus the archived bundle
- Dagger CLI/Engine: `v1.0.0-beta.16`
  (`runner-host: image://registry.dagger.io/engine:v1.0.0-beta.16`)
- Command:
  ```text
  dagger call fixture-plan-execution \
    --plan .tmp/eci58-evidence4/plan.json \
    --source .tmp/eci58-evidence4/repo \
    export --path .tmp/eci58-evidence4/bundle
  ```
- Fixture: clean workflow + independent shell script, checks
  `actionlint`, `shellcheck`, `workflow_shellcheck`, `zizmor`, `whitespace`.
- The generated host Plan was copied for Dagger with only
  `inputs.config.path` changed from the host absolute path to
  `ci-selection.toml`; the source tree, changed paths and snapshots are
  otherwise unchanged.
- Native Engine exit: `0`; strict Enforce: `passed`.

## Bundle contents

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `inspection.json` | 208 | `024a0e3cad0369ed511dc747f4f427f3afdbc3b8c9df16bd6496ab200fa19053` |
| `result/exit-code.txt` | 2 | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` |
| `result/plan.json` | 22341 | `5b031c26a7e4a3585e6bbf7e7c84cbad0da3fe6ce0f79e9479b5aaaeba413be5` |
| `result/report.json` | 46036 | `3eb2a6138420cfa9734255263c63655569478026a317518f56d5d274ca6e092d` |

`inspection.json`:

```json
{"origin_execution_attributed": false, "original_exit": "0", "report_equals_artifact": true, "same_bundle_id_after_enforce": true, "scope": "one_explicit_PlanExecution_object", "strict": {"status": "passed"}}
```

Report checks (same bundle):

- `whitespace`: passed, exit 0, contract valid, satisfied
- `actionlint`: passed, exit 0, contract valid, satisfied
- `shellcheck`: passed, exit 0, contract valid, satisfied
- `workflow_shellcheck`: passed, exit 0, contract valid, satisfied
- `zizmor`: passed, exit 0, contract valid, satisfied

This proves one real Engine Plan→Report/Artifact→Enforce relationship for the
clean fixture. It does not prove all #58 failure cases, #66-P0 Source Closure,
#63 trusted reuse, #61 Required/Release, or SBOM/DB freshness.
