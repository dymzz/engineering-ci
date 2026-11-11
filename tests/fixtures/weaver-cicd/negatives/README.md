# Targeted CI/CD SemConv negatives

Mutations of the positive `../capture.json` for native Weaver Required /
Conditionally Required checks. Reuse the same Registry, matchers, and
`ci-weaver.toml`; copy one negative capture over `capture.json` (and adjust
`sample.json` / `expected.json` when span set changes) before replay.

| File | Case |
| --- | --- |
| `missing-pipeline-result.json` | Pipeline Span without `cicd.pipeline.result` |
| `missing-task-url.json` | Task Span without `cicd.pipeline.task.run.url.full` |
| `failure-missing-error-type.json` | Pipeline+Task `result=failure` without `error.type` (Rego FAIL) |
| `error-missing-error-type.json` | Pipeline+Task `result=error` without `error.type` (Rego FAIL) |
| `failure-with-error-type.json` | Pipeline+Task `result=failure` with `error.type` (PASS) |
| `missing-resource-run-id.json` | Resource without `cicd.pipeline.run.id` (entity Required FAIL) |
| `wrong-result-type.json` | Pipeline result as int `123` |
