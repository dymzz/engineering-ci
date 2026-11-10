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
`live-check.matchers` and `policy.finding_filters`. External advice files,
resolver overrides, and exporter settings are rejected rather than read from
unbound locations. Native built-in policies and explicit CLI options remain
part of the pinned tool execution.

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
    "status": "completed", "conclusion": "success"
  }
}
```

Each expected span must occur exactly once, match its declared signal/matcher,
and have valid completed timestamps. Empty expected sets, missing spans,
duplicates, zero matching samples and Matcher errors fail. Coverage is scoped
to this declared expected set; the producer/authorized caller owns its
completeness. Run/Attempt metadata is retained, not independently authenticated
by the checker as GitHub provenance.

`weaver-terminal` additionally requires a Pipeline Run signal and completed
terminal evidence. The matched Pipeline Span itself must observe
`cicd.pipeline.run.id`, `github.actions.run_attempt`, and
`cicd.pipeline.result`, and must be a Root Span (no parent). Those observed
values are compared to the declared source and terminal facts and to the
SHA-identified OTLP capture for the same Trace/Span IDs. Matching caller
assertions alone cannot pass when the observed Span belongs to a different
Run/Attempt/result or is not a Root. Missing observable identity fails the
terminal coverage claim without inventing attributes. External GitHub
provenance authentication remains #75/#63; #61 owns Required/Release.

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

A passing result covers the configured native violation policy and declared
expected set. Conditional advice and informational findings remain visible;
the checker does not claim to prove every normative SemConv condition.

In `ci-selection.toml`, explicitly include applicable `weaver_registry`,
`weaver_live`, or `weaver_terminal` in `checks`, and set
`check_scope.weaver_prefixes` to the actual Registry/config/capture paths.
Incomplete Git scope stays `unknown`. Dagger `execute-plan` executes these
entries and appends native reports to its existing `checks` collection. The
host executor leaves them `not_executed` and blocks rather than bypassing the
Dagger tool boundary. `execute-plan --weaver-config` selects the Source-relative
configuration path. Terminal selection belongs in the post-completion plan.

During this integration, identical Windows and Linux Source bytes exposed an
existing snapshot-id ordering bug: native `Path` ordering case-folds on Windows.
The shared Source-id function now sorts POSIX relative-path strings explicitly.
Regenerate plans created with the previous ordering; do not edit their digests
or bypass `source_tree_mismatch` refusals.

## Observed upstream limits

With Weaver 0.28.0 and SemConv 1.45.0, integer `123` for
`cicd.pipeline.result` produced information-level `undefined_enum_variant` and
native exit 0. ECI conservatively returns `enum_variant_unverified` for that
native finding; it does not rewrite its severity. Ordinary string attribute
type errors produced `type_mismatch` and native exit 1.

Dagger v1.0.0-beta.16 emitted `core.repositoryformatversion does not support
extension: worktreeconfig` in this managed worktree while calls still completed.
Git-metadata effects of that warning are unverified; Git configuration was not
changed. In this Windows host, terminal-mode `dagger version` returned the
version; `--version` is unsupported. Connection startup sometimes took over a
minute, independently of the native Weaver execution time.

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
