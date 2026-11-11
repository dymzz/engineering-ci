# Controlled CI/CD SemConv v1.45.0 Weaver fixture

Offline materialization of OpenTelemetry Semantic Conventions **v1.45.0**
Registry bytes for native Weaver replay. Canonical upstream identity is locked
by **Knowledge #80** (and related #81 pin notes): repository
`https://github.com/open-telemetry/semantic-conventions`, commit
`fb30ca17193b551473b1f43c315482918ab6987c`, version `1.45.0`. This tree is a
**local test/consumption copy** — not a second SemConv authority. Prefer the
Knowledge Source record and `registry-sha256.json` digests when comparing
identity; treat digest drift as Source conflict, do not arbitrarily prefer this
copy over the locked upstream.

Native CEL matchers cover Pipeline (`cicd.pipeline.run.server`) and Task
(`cicd.pipeline.task.internal`). Signal binding enforces each Span Group's own
Required / Conditionally Required attributes. Resource identity attrs use
`[live-check] search_all_attributes = true` so they resolve against the packaged
catalog. After `registry package --v2`, SemConv `registry.*` attribute groups
are catalogued and are not matcher-addressable IDs (unlike the cart fixture's
custom `acme.step`); naming `span.*` or `registry.*` in `strict_attribute_groups`
makes Weaver exit before the OTLP receiver is ready.

This proves native Weaver Required enforcement for the selected CI/CD signals —
not the cart matcher fixture and not a GitHub producer. Conditionally Required
`error.type` on `failure`/`error` is enforced by Source-local Advice Policy
`advice-policies/cicd-error-type.rego` (plus vendored `otel.rego`). Pipeline
Span declares `entity_associations: [cicd.pipeline.run]` so Resource Required
`cicd.pipeline.run.id` is checked via Weaver's entity association chain.

```sh
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live export --path .artifacts/weaver-cicd-live
dagger api call weaver-enforce --source tests/fixtures/weaver-cicd --kind live
dagger api call weaver-report --source tests/fixtures/weaver-cicd --kind terminal
dagger api call weaver-report --source tests/fixtures/weaver-cicd --kind terminal --config-path ci-weaver-terminal-attempt-absent.toml
# Negatives (one config each under negatives/):
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-missing-pipeline-result.toml export --path .artifacts/neg-missing-pipeline-result
```

`ci-weaver-terminal-attempt-absent.toml` replays the same Registry/matchers with
`capture-attempt-absent.json` (no `github.actions.run_attempt`) so Terminal fails
`terminal_attempt_correlation_unverified` while native Live can still pass.

Origin hashes: `registry-sha256.json`. Reports remain `trusted: false`.
