# Registered Producer Attempt acceptance

This is #52's actual **Task-only A acceptance of #74 and #75 together**.
It uses Consumer commit `9b7a1f76e6f5d72cd5538292bd35439ec54f3f6e`, #74
candidate `94996d6cc28e73a9862003b836a6ad3b3fe9996b`, and #75 PR #84 archive
`4a354c9db2506eacde5761e7ee3ede925d743b84` (Producer code `0e72f14`). These
are fixed candidates; their merges are not assumed. No API fetch or Producer
network export was performed by this consumer.

The original [strict failure](../real-attempt-task-only/README.md) remains
immutable. Its Capture SHA-256 is
`07aa61d36ea8e0a179302ad75d17b1a5ec836e6c8870dd551482b59a759245e5`.
This new case consumes #75's separately identified schema 9 files; it does
not remove attributes from the historical Capture to make it pass.

## Inputs and independent reconciliation

Run `42821429094`, Attempt 1, repository `dymzz/engineering-ci`, Head
`7ea85c24a62e65a9384a0abe9eb541ca54064cbc`. Original Expected Job IDs remain
`129847643854`, `129847644372`, `129847644459`. Expected Trace
`21ee8206fe06a4879580f60591a3014f`, reserved Pipeline Parent `093c00d57bcc226c`
and Task Span IDs are rebuilt with #75's existing `_stable_ids` function.
They are not copied from the Capture.

The fixed [input witness](../../../../tests/integration/real-attempt-registered-attributes.json)
binds the original Expected objects to five unchanged #75 files:
Candidate, OTLP Traces, independent API Attempt/Jobs Witness, Producer source
receipt and original exit. They are materialized in the
[consumer fixture](../../../../tests/fixtures/registered-attempt/README.md).
The input hashes are fixed outside the replay Source. Coordinated edits of
local data and its manifest cannot change that expected identity.

#52 checks raw repository against the canonical
`vcs.repository.url.full=https://github.com/dymzz/engineering-ci`, complete
`cicd.pipeline.run.id=42821429094/1`, Head, all Job objects, names, URLs,
results, timestamps, Trace/Span/Parent IDs and the complete Task multiset.
The same native receiver Resource/Task fields must agree. Candidate, API
Witness, receipt and Capture bytes remain fixed at result consumption.
Run/Attempt declarations in `expected.json` are rechecked against the
independent witness even when a forged Report agrees with them.

## Observed result

| Case | Native exit | Native gate / Enforce API | #52 Consumer exit | Actual rejection or acceptance |
| --- | --- | --- | --- | --- |
| Real three Tasks | 0 | 0 | 0 | Complete independent inventory, Native and Plan PASS |
| Missing Task | 0 | 1 | 1 | Native expected Span missing; Consumer missing Task |
| Extra Task with all three Expected present | 0 | 0 | 1 | Consumer unexpected Task; received=4 |
| Duplicate Task | 0 | 1 | 1 | Native duplicate Expected Span; Consumer duplicate Task |
| All Tasks share one wrong Trace | 0 | 1 | 1 | Native IDs mismatch; Consumer independent Trace mismatch |
| Wrong Attempt | 0 | 0 | 1 | Consumer Source/Run/Attempt provenance mismatch |
| Wrong Head | 0 | 0 | 1 | Consumer Source/Head mismatch |
| Wrong repository URL | 0 | 0 | 1 | Consumer independent repository mismatch |
| Zero Matcher hit | 0 | 1 | 1 | Native gate `coverage_missing`; Consumer preserves failure |
| Zero Span sample | 0 | 1 | 1 | Native coverage missing; Consumer three missing Tasks |
| Missing Task URL | 1 | 1 | 1 | Native `required_attribute_not_present` violation |
| Failure result without `error.type` | 1 | 1 | 1 | Native `cicd_conditional_error_type_required` violation |

Negatives are explicitly controlled mutations of the new fixed Capture or
Matcher, not new GitHub facts. The independent Expected three-Job set stays
unchanged. Every case uses zero Finding Filters. Native findings and ECI
identity/completeness decisions are distinct: #74 deliberately checks its
`declared_expected_set`; #52 supplies complete-source and observed-object
acceptance. A Native gate 0 for an extra Task or a well-typed wrong identity
is not a complete E2E PASS. The public strict API was actually called for
every case, including those that correctly return 0 at the Native layer.

All 12 executions completed ready / Export ACK / Stop / Report / Shutdown.
The official 269-file Registry was authenticated from #74's shared external
record, with canonical digest
`85e58bf616303da0488f2cf86f0bb8f9e91ae5fd45e5b38399fa97a288e10963`.
Native Required/Conditional rule failures above are actual findings from
these executions. Resource Required remains `not_applicable` for the official
Pipeline Group; this consumer does not restore the removed entity association.

## Same execution, Report and Plan

`weaver-artifacts`, `weaver-report`, `weaver-enforce` and existing
`execute-plan` reference the same fixed positive Source. The Report API and
Artifact JSON agree; the Plan embeds that exact Native report, including its
commands, ports, samples and lifecycle. There is one native `live-check`
command in each report. Dagger reused the execution node for reporting/gating.

- Selected Check: `weaver_live`; no unrelated Check selected.
- Source content ID: `3e8ce27c833dd29b0c7728b8586810ebab393d065117c925513b7f5a7bb94804`.
- Execution Git tree: `4ba50d6e74e44b48be9135425492785a4280441b`.
- Native Dagger Source digest is preserved in [receipt.json](receipt.json).
- `execute-plan`: API exit 0, report `passed`, report exit 0,
  `source_tree_verified=verified`; Consumer `plan_binding=verified`.
- Changed Candidate uncertainty metadata: the old Plan returns API exit 0,
  report `not_executed/source_tree_mismatch`, exit 4, with no Check invocation.

Policy byte identity uses the exact common file map and paths. Native
Advice aggregate SHA is `a34c5847293a32bde9c39fdce2352bc4c88dba48446a01a628aae888b808d718`;
Selection's compact-JSON aggregate is
`9c339d1fed010728ffdb44fbab89abfcc7dd69df89bb606e20ef6eef89298eea`.
Both are retained. Their different aggregate encodings are not represented
as equal hashes; individual file SHA-256 values agree. Native tool version
and asset hash, Policy loaded/executed coverage and its Native digest are
also checked at consumption. No Registry, Checker or canonicalizer is added.

The Windows checkout's five actual Checker source files have CRLF line
endings. Their bytes, hashes, canonical #74 Git-blob hashes and line-equivalence
proof are retained in the receipt and `checker-source.tar.gz`. The Registry's
`-text` files are exact upstream bytes. Native output was never rewritten.

## Artifacts and reproduction

- [Case index](cases/README.md): reports, complete raw native Artifact archives,
  original stderr, Consumer results, input/artifact hash receipts and Source deltas.
- [Native CLI transcripts](native-cli.json), [case summary](case-summary.json).
- [Consumer result](consumer-result.json), [Plan](plan.json),
  [execution report](execution-report.json), [API report](report-api.stdout.json),
  [Source drift refusal](changed-source-execution-report.json).
- `source.tar.gz`: full positive Source. A negative's `source-delta.tar.gz`
  overlays an empty extraction of this base; every reconstructed file was
  compared byte for byte with the actual Source. This avoids another full
  Registry copy for each negative. The changed-Source delta is separate.
- Each `artifacts.tar.gz` is the complete original export, including
  `live_check.json`, generated package, Advice and stdout/stderr. Its receipt
  records all original member hashes. Raw Consumer stdout is kept in gzip;
  `consumer-result.json` is normalized presentation of the same JSON.
- [SHA256SUMS](SHA256SUMS) binds the whole bundle.

Use the existing Consumer and fixed candidate Checker:

```powershell
python tests/integration/prepare_real_attempt_weaver.py --case registered-attributes --destination <source>
# Bind an existing Selection/Plan to this Source before execution.
$env:FORCE_DARK_MODE='1'
dagger api call weaver-artifacts --source <source> --kind live export --path <artifacts>
dagger api call weaver-enforce --source <source> --kind live
dagger api call weaver-report --source <source> --kind live
dagger api call execute-plan --source <source> --plan <plan>
python tests/integration/prepare_real_attempt_weaver.py --case registered-attributes --verify-source <source> --artifacts <artifacts> --plan-report <execution-report>
```

Exports stay outside Source. The unchanged default `--case original` retains
the historical input path. This is a fixed integration acceptance helper,
not a general scheduler, new Checker, Producer or exporter.

## Acceptance boundary

**Accepted:** #74 official-source / Native Live / rule and gate behavior;
#75's pinned schema 9 Trace/Source identity contract; #52's full Task
reconciliation and same-execution Check/Report/Artifact/Plan connection for
this real Attempt. Scoped tests: **78 passed** in
`tests/test_real_attempt_weaver_e2e.py`; Ruff check/format passed for the two
changed Python files. Synthetic result-contract tests are not Native evidence.

**Not accepted by this slice:** real Pipeline Root / Terminal time properties,
default-branch post-run Workflow, new direct Producer network receipt,
Logs/Metrics SemConv, #63 Trusted, #61 Required/Release, and #52's other
Runtime/Actions responsibilities. Three Tasks and four existing LogRecords
remain; Root is `not_emitted`, phases/active count remain `not_observable`.
Original Producer exit and per-signal transport claims are preserved, with no
upgrade of `otlp_delivered`, `github_authenticated`, `weaver_pass` or `trusted`.
The separate Native replay PASS does not change those Producer claims.

PR #82 remains the sole #52 OTel integration PR. #83/#84 candidate dependencies
and independent review/merge state remain explicit; no Issue is closed by this
technical acceptance. Up one level: [Weaver evidence](../README.md).
