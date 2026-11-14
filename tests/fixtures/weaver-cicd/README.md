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

`registry/` is a byte-exact copy of the upstream `model/` tree: the directory
hashes to upstream git tree `b9a819cdbafe936394bd596bb27c3d929ba24ff4`, and
`registry/cicd/spans.yaml` hashes to upstream blob `ed203885c005ab2462bb0d72f5c28c8fbad19d15`
(SHA-256 `10e19a1811af95b033fb8a9e642865f55a1b66718d1935cfb8d6df060e2d7f86`).
`.gitattributes` marks the whole fixture `* -text` so checkout cannot rewrite
any of its bytes. `engineering_ci.semconv_registry` pins the same
identity for runtime verification; the pinned upstream tree digest is
`85e58bf616303da0488f2cf86f0bb8f9e91ae5fd45e5b38399fa97a288e10963`.

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
`advice-policies/cicd-error-type.rego` (plus vendored `otel.rego`).

Upstream SemConv v1.45.0 declares **no** entity association for
`span.cicd.pipeline.run.server`, so it defines **no** Resource Required
obligation for the Pipeline span, and native Weaver does not enforce
`cicd.pipeline.run.id`. ECI's own requirement that a capture identifies its
Pipeline Run is the correlation rule `eci_terminal_run_id_correlation`, enforced
by `weaver-terminal` (failures `terminal_observed_identity_missing`,
`terminal_run_id_mismatch`, `terminal_run_id_conflict`) and reported separately
from official SemConv obligations. Do not reintroduce a local `entity_associations`
block: it would misrepresent upstream SemConv as requiring the attribute.

```sh
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live export --path .artifacts/weaver-cicd-live
dagger api call weaver-enforce --source tests/fixtures/weaver-cicd --kind live
dagger api call weaver-report --source tests/fixtures/weaver-cicd --kind terminal
dagger api call weaver-report --source tests/fixtures/weaver-cicd --kind terminal --config-path ci-weaver-terminal-attempt-absent.toml
dagger api call weaver-report --source tests/fixtures/weaver-cicd --kind terminal --config-path ci-weaver-terminal-missing-resource-run-id.toml
# Negatives (one config each under negatives/):
dagger api call weaver-artifacts --source tests/fixtures/weaver-cicd --kind live --config-path ci-weaver-neg-missing-pipeline-result.toml export --path .artifacts/neg-missing-pipeline-result
```

`ci-weaver-terminal-attempt-absent.toml` replays the same Registry/matchers with
`capture-attempt-absent.json` and `expected-attempt-absent.json`. The Run ID
is `controlled-42` with no Attempt suffix, so Terminal fails
`terminal_attempt_correlation_unverified` while native Live can still pass.
The fixture does not whitelist `github.actions.run_attempt` or
`github.repository`.

`ci-weaver-terminal-missing-resource-run-id.toml` replays
`negatives/missing-resource-run-id.json` (Resource without `cicd.pipeline.run.id`)
so Terminal fails `terminal_observed_identity_missing`. Native Live passes for
that capture: upstream declares no Resource Required obligation for it.

Origin hashes: `registry-sha256.json`. Reports remain `trusted: false`.
