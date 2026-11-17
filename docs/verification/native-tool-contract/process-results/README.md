# Process Producer and Source handoff

This is the #52 D2/D3 Producer/consumer slice in [PR #117](https://github.com/dymzz/engineering-ci/pull/117).
The [public Check contract](../../../architecture/check-contract.md) owns the
semantics. These are observed native facts, not Trusted/Required acceptance,
Runtime registration, complete Source closure or #52-D4 Terminal authorization.

Initial implementation: `e337cbe1afcf4ebdb825e8a0b85e8e54d89006aa`.
Earlier observations retain their actual `c096432` / `348f088` revisions in
[receipt.json](receipt.json). No earlier raw result was rewritten to claim the
final implementation ran it.

The later [Source identity and generated-input repair](source-and-generated-inputs/README.md)
records the two P1 fixes from review 5932472685. The raw evidence below remains
at its original implementation revisions.

## Actual native observations

Eleven Dagger API requests are preserved in [native-cli.json](native-cli.json):
ten bundle exports and one public serialized Enforce rejection. Eight compound
consumers hold one PlanExecution object, read its Report/Artifact and enforce
that same object. All eight retain equal Report/artifact bytes and the same
Directory ID before/after Enforce. Export CLI exit 0 means delivery of evidence,
including failed/blocked evidence; it is not a PASS verdict.

| Case | Observed outcome | Original Report | Complete bundle |
| --- | --- | --- | --- |
| Final mixed PASS | unittest 0; Registry stages 0/0/0; real Host Source passed; top passed/0; Enforce passed | [Report](current-passed-report.json) | [Bundle](current-passed-bundle.tar.gz) |
| Final setup refusal | actual `uv sync --locked` preparation exit 1 and stderr; Tests not executed/null; Registry and Host Source still passed; top blocked/5 | [Report](setup-diagnostic-report.json) | [Bundle](setup-diagnostic-bundle.tar.gz) |
| Registry native FAIL + blocked tests | Tests not executed/null, `tach_graph_unvalidated`; actual Registry check 1; only that executed stage has a Native Result; top failed/checks_failed/1 | [Report](registry-failed-report.json) | [Bundle](registry-failed-bundle.tar.gz) |
| Blocked tests + Registry PASS | Tests not executed/null; Registry stages 0/0/0; top blocked/5; Enforce rejected | [Report](blocked-report.json) | [Bundle](blocked-bundle.tar.gz) |
| pytest PASS | original pytest 0, JUnit and environment retained; top passed/0; Enforce passed | [Report](pytest-passed-report.json) | [Bundle](pytest-passed-bundle.tar.gz) |
| pytest FAIL + Registry PASS | original pytest 1, stdout/stderr/JUnit retained; Registry passed; top failed/tests_failed/1; Enforce rejected | [Report](pytest-failed-report.json) | [Bundle](pytest-failed-bundle.tar.gz) |
| Live PASS | Registry chain and live-check native exits all 0; original gate passed | [Report](live-report.json) | [Bundle](live-bundle.tar.gz) |
| Terminal missing evidence | actual native exits all 0; semantic gate failed/1, `terminal_evidence_missing_or_mismatched`; no Terminal PASS claimed | [Report](terminal-report.json) | [Bundle](terminal-bundle.tar.gz) |
| Earlier mixed PASS | same successful types, at the recorded earlier revision | [Report](passed-report.json) | [Bundle](passed-bundle.tar.gz) |
| Earlier setup refusal | original ExecError diagnostic retained; no test Native Result or invented test exit | [Report](setup-refused-report.json) | [Bundle](setup-refused-bundle.tar.gz) |

The public serialized Plan Enforce request returned CLI **1**, with
`Plan failed: tests_failed (exit 1)`. It is an independent API request; it is
not claimed to share one Origin with the exported failure case. Within each
compound consumer, readers use the retained object without another tool call.

Native process satisfaction and the Check's semantic gate remain separate:
Terminal's zero native exits do not override its failed gate. Report consumers
must retain the enclosing failed Check, not use the process-chain decision as
a Terminal acceptance. The live/terminal inputs are the small upstream Weaver
cart fixture; they do not claim Task-only A/#82 or CI/CD Terminal B acceptance.

## Inputs, implementation and handoff

The Plans are [mixed pass](passed-plan.json), [pytest failure](failed-plan.json),
[test precondition](blocked-plan.json) and [pytest pass](pytest-passed-plan.json).
Exact Source files are archived in [source.tar.gz](source.tar.gz) (initially no
lock), [locked-source.tar.gz](locked-source.tar.gz),
[pytest-source.tar.gz](pytest-source.tar.gz),
[failure-source.tar.gz](failure-source.tar.gz) and
[registry-failure-source.tar.gz](registry-failure-source.tar.gz).
The Registry fault changes one real scalar type to an invalid enum and updates
the manifest to the actual changed bytes, so failure comes from the original
Weaver `registry check`, not a manifest/config precheck. The pytest fault is an
intentional assertion. The initial input has no uv.lock; adding the existing
locked metadata produced the successful test case without weakening sync.

The receipt gives each bundle/input file's SHA-256, plus exact implementation
source archives matched against the `implementation_sources` observed inside
Dagger. Each source archive is reconstructed from matching working/Git-blob
bytes (including Windows checkout line endings where actually observed), then
verified against every reported source hash. It does not invent a signed
implementation authorization. [SHA256SUMS](SHA256SUMS) covers all archived files.
Git attributes preserve these evidence bytes. Captured Windows Plan inputs
retain their original CRLF; their whitespace attribute recognizes CR as the
line ending while continuing to reject trailing spaces.

The mixed case records actual Container Source/output Directory identities,
the exported Source content ID, matching before/after Host content IDs, original
Host Report bytes/hash and Host Result IDs. The Host Result remains `host` with
`execution:dagger_export`; framework/Weaver Results are `dagger`. Optional
`run/controlled` and `attempt/1` references remain unverified. Tool, adapter,
rule/config, environment, raw artifact and parent execution facts are retained;
unknown source closure and approval remain unknown. Directory IDs/cache markers
do not prove fresh Origin execution, and `origin_execution_attributed=false`
is preserved for #87.

## Focused checks and incidental observations

- Final producer/Plan result tests: **70 passed**, including real Host
  unittest/pytest PASS/FAIL with a selected framework definition and one runner
  invocation; partial Weaver stages; native/gate exits; implementation changes;
  original Host report linkage; and metadata/operation failure preservation.
- Earlier six related suites: **218 passed, 1 skipped, 5 failed** locally on
  Windows. The five failures are unchanged `weaver-cicd` CRLF checkout bytes
  against canonical official Registry hashes. No Pin or fixture was weakened.
  Native controlled inputs above use Git-blob fixture bytes. Final Linux
  current-head CI status is reported on the PR, separately from this local run.
- Scoped Ruff check/format passed on the ten changed Python files. Existing
  `engineering-ci version check` passed with v0.7.0.
- Dagger beta.16's `ExecError.__str__` exposes its message, while the original
  operation command/exit/stdout/stderr are separate SDK attributes. The final
  adapter preserves those attributes without treating a preparation failure
  as the test's native exit. A metadata query failure likewise preserves a
  test that already completed.
- The CLI emitted `core.repositoryformatversion does not support extension:
  worktreeconfig` while inspecting this linked worktree; these API requests
  nevertheless completed. The warning and cache markers remain in raw CLI
  chunks. They are observations, not a diagnosed upstream regression or a
  substitute for native Result/Source authentication.

Parent: [Native tool contract evidence](../README.md).
