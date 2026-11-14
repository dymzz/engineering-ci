# Producer attribute mapping evidence

Refs [#75](https://github.com/dymzz/engineering-ci/issues/75). This bundle is
the new Producer fix after merged PR #81, based on `main`
`88e5173e2e911ccfe1ed674176188a4db5504f0a`. Producer source commit is
`0e72f14e3bdd13ae6b6a96c41cf2659a85dfeede`, package version 0.7.0,
Candidate schema 9. The code/hash and exact CLI arguments are in
[receipt.json](receipt.json). The complete field classification, upstream
definitions, constraints and consumer decisions are in the
[Producer attribute contract](../../architecture/cicd-telemetry.md#attribute-contract-schema-9).

## Fixed source and new files

The existing Producer CLI freshly read the Attempt API and all Jobs pages for
`dymzz/engineering-ci`, Run `42821429094`, Attempt 1, Head
`7ea85c24a62e65a9384a0abe9eb541ca54064cbc`. It checked the requested Attempt,
repository and fixed Head, wrote the independent
[API Witness](candidate.json.api-pages.json) before export, and generated new
[Candidate](candidate.json), [Traces](otel-traces.json) and
[Logs](otel-logs.json). It did not configure an OTLP endpoint.

The original all-completed source archive is commit
`f8c31251ac6b4b3277579b40b78827e3bced6c85`; its three original file hashes are
recorded in the receipt. The original #52 failure at `ebb2704` and its OTLP
SHA-256 `07aa61d36ea8e0a179302ad75d17b1a5ec836e6c8870dd551482b59a759245e5`
remain unchanged. The schema 7 [receiver archive](../cicd-telemetry-real-run-receiver/README.md)
and schema 8 [file archive](../cicd-telemetry-real-run-identity-v8/README.md)
also retain their original bytes and claims. The schema 8 test now checks
its frozen Git-blob hashes rather than regenerating history with new code.

| New file | SHA-256 |
| --- | --- |
| Candidate | `88d4af44974b1619eb4a7c75909256fad308ed1ad404f17c2d30f60bfa3e2b14` |
| Independent API Witness | `e62d425dbf2b6ec6919ea77e78e10a97793eaf3762da081f9ac45e3a96710f32` |
| Traces | `50bcf5cfae3050c1a303ae1881a5c0aedb38151e897fcac3da72388d08665d8f` |
| Logs | `ec5c12a1e784d174b2ebdd8363a7068a0d3e7410b8f8d1802677aea44b6c802a` |

`github.repository=owner/repo` is replaced in the new Resource by
`vcs.repository.url.full=https://github.com/dymzz/engineering-ci`. The raw
API `repository.full_name` and `repository.html_url`, Run browser URL and
derived server origin agree; `repository_identity` records their provenance.
No host was assumed. Targeted tests cover a different GitHub Enterprise
host/port and conflicting repository, host, Run and Attempt URLs.

The existing composite `cicd.pipeline.run.id=42821429094/1` replaces the need
for the unregistered Attempt attribute. Raw Run/Attempt remain in facts and
the independent Witness. Trace `21ee8206fe06a4879580f60591a3014f` and reserved
Pipeline Span ID `093c00d57bcc226c` are unchanged. Expected IDs below are
rebuilt from Witness Job IDs through the existing stable-ID scheme, never
copied from the new Capture.

| Completed Job | Job ID | Task Span ID |
| --- | --- | --- |
| sync-tool-tests | `129847643854` | `7d2f4e7f18a603cd` |
| version-metadata | `129847644372` | `a7263c2e65a30498` |
| python-ci / checks | `129847644459` | `7d98cbfec3f44c1a` |

Every Task's identity, parent, name, kind, start/end, result and status were
compared to the original archive; only the unregistered Attempt attribute
was removed. There are still three Tasks and four LogRecords. Root is
`not_emitted/end_time_uncertain`; no uncertainty budget or exact completion
instant was invented. Pipeline phases and active count remain
`not_observable`. ECI time-quality and log-source metadata remain in Candidate
Terminal/Provenance/Coverage or `signals.logs[]`, outside OTLP attributes.

## Actual native validation

Used existing #74 PR #83 candidate
`94996d6cc28e73a9862003b836a6ad3b3fe9996b`, not the old `main` Checker.
All temporary module blob bytes matched that candidate; its original Git
tree is `d9d9576e6dc4845b5c9038e1455f151ca97c0d78`. The shared
`engineering_ci.semconv_registry` record is read from that candidate, without
copying it into Producer runtime. Existing Weaver 0.28.0 / Dagger interfaces
verified the exact official 269-file Registry. No Registry YAML or policy
was modified, no Checker was added, and every new Source has **zero finding
filters**. Only the existing matcher's name selector was bound to the actual
Producer's `task:` / `RUN ` names, with the same native signals.

| Case | Native exit | Gate / strict API exit | Actual result |
| --- | --- | --- | --- |
| Real three Tasks | 0 | 0 | Live PASS; received=matched=3; full receiver lifecycle |
| Add `github.repository` | 1 | 1 | Native `missing_attribute` violation |
| Add `github.actions.run_attempt` | 1 | 1 | Native `missing_attribute` violation |
| URL attribute as int | 1 | 1 | Native `type_mismatch` violation |
| Controlled Mapping Root | 0 | 0 | Terminal PASS using composite ID suffix, no Attempt extension |
| Controlled declared Attempt 2 vs Capture `/1` | 0 | 1 | Native Live has no violation; ECI Terminal rejects `terminal_attempt_mismatch` |

The controlled Root is generated by the revised Producer from explicit
fixture timestamps and an example Enterprise URL. It proves the changed
Root output path and bounded Attempt correlation. It does not prove a real
GitHub completion instant, a verified time source, or precise Run duration.
The real Run continues to withhold Root. Native release-candidate stability
improvements remain in raw reports; PASS does not mean zero native advice.

See [native evidence](native/README.md), [summary](native-summary.json) and
[Dagger CLI completion transcripts](native-cli.json). Each case preserves
the full Source and raw Artifact archives plus readable native Report,
`live_check.json` and stderr. Native input digests bind Capture/Expected/
Matcher/Registry/Advice. `weaver-report` returned the same real-Task report
as Artifact. `weaver-enforce` was actually called for every case; an
Artifact API exit 0 on a failed check is never counted as PASS.

Reproduce with the referenced existing #74 module and one extracted Source:

```powershell
$env:FORCE_DARK_MODE='1'
dagger api call weaver-artifacts --source <source> --kind live export --path <out>
dagger api call weaver-report --source <source> --kind live
dagger api call weaver-enforce --source <source> --kind live
```

Use `--kind terminal` for the two controlled Root cases. Keep output outside
Source. The archives are execution inputs, not a second product Registry or
an alternative policy. The CLI dependency/ignored-module diagnostics and
workaround are recorded in the existing
[upstream observations](../../../.agent/upstream-observations.md).

## Targeted tests and consumer handoff

Ruff check/format passed for the four changed Python files. Only the affected
Producer, attribute-contract and existing three-signal receiver suites ran:

```text
uv run --locked pytest tests/test_cicd_telemetry.py tests/test_cicd_telemetry_attributes.py tests/test_otlp_grpc_live.py -q
109 passed
```

These cover repository/host/URL errors before export, wrong API repository,
Attempt and Head, duplicate/missing Job identities and pages, stable Trace
reconciliation, explicit/approximate/withheld Root, no-job and clock/budget
boundaries, unchanged Traces/Logs/Metrics ACK/failure propagation and honest
O3 unobservability. No unrelated engineering matrix was run locally.

- #74: candidate `94996d6` already binds the complete composite Run ID and
  its Attempt suffix to declared Source/Terminal evidence, and leaves
  unknown GitHub attributes as native violations. That candidate is an
  explicit integration dependency until PR #83 merges. The old `main`
  Terminal still expects the removed extension; this PR does not duplicate
  the Checker change.
- #52: consume schema 9 as a **new** case with the fixed Producer commit,
  Capture digest and independent API Witness. Reconcile raw repository to
  the sourced canonical URL, compare complete `Run/Attempt` values, Head and
  all Job objects, and rebuild IDs with the existing rule. Update consumers
  of log `source` / Terminal metadata to the Candidate fields. Do not alter
  the old failing Capture/Report or substitute Producer ID self-audit for
  independent Expected/Observed E2E. This Producer PR does not execute that
  E2E or replace #82's archived result.

The new Producer files have `rpc_accepted`, `receiver_observed`,
`otlp_delivered`, `github_authenticated`, `weaver_pass` and `trusted` all
false. The native pass belongs to the separate Checker replay, whose
`trusted` is also false. This is no new direct Producer receiver receipt,
Logs/Metrics semantic check, default-branch post-run execution, #63 admission
or #61 Required result. Issue #75 stays Open for its remaining post-run and
artifact/receiver acceptance; this fix PR uses `Refs #75`.

Original Windows CRLF Producer stdout is preserved byte for byte in
[producer.stdout.json.gz](producer.stdout.json.gz); the receipt records its
decoded SHA-256. Compression keeps raw diagnostic line endings separate
from authored text formatting checks. All files are listed in
[SHA256SUMS](SHA256SUMS). Up one level:
[CI/CD verification](../cicd-telemetry.md).
