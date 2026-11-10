# Controlled CI/CD SemConv v1.45.0 Weaver fixture

Pinned OpenTelemetry Semantic Conventions **v1.45.0** Registry bytes
(`commit fb30ca17193b551473b1f43c315482918ab6987c`) with native CEL matchers for
Pipeline (`cicd.pipeline.run.server`) and Task (`cicd.pipeline.task.internal`).
Signal binding enforces each Span Group's own Required / Conditionally Required
attributes. Resource identity attrs use `[live-check] search_all_attributes =
true` so they resolve against the packaged catalog. After `registry package
--v2`, SemConv `registry.*` attribute groups are catalogued and are not
matcher-addressable IDs (unlike the cart fixture's custom `acme.step`); naming
`span.*` or `registry.*` in `strict_attribute_groups` makes Weaver exit before
the OTLP receiver is ready.

This proves native Weaver Required / Conditionally Required enforcement for the
selected CI/CD signals — not the cart matcher fixture and not a GitHub producer.

```sh
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live export --path .artifacts/weaver-cicd-live
dagger api call weaver-enforce --source tests/fixtures/weaver-cicd --kind live
dagger api call weaver-report --source tests/fixtures/weaver-cicd --kind terminal
dagger api call weaver-report --source tests/fixtures/weaver-cicd --kind terminal --config-path ci-weaver-terminal-attempt-absent.toml
```

`ci-weaver-terminal-attempt-absent.toml` replays the same Registry/matchers with
`capture-attempt-absent.json` (no `github.actions.run_attempt`) so Terminal fails
`terminal_attempt_correlation_unverified` while native Live can still pass.

Origin hashes: `registry-sha256.json`. Reports remain `trusted: false`.
