# Real Attempt Task-only native rejection

Recorded 2026-11-12 for [#52 / PR #82](https://github.com/dymzz/engineering-ci/pull/82).
Runtime/official-source implementation: #74 / PR #83 candidate
`8bf82112fae00e63a796c3de1ca22d254076e631`, unmerged when this case ran.
The official Registry is SemConv 1.45.0 at
`fb30ca17193b551473b1f43c315482918ab6987c`.

## Observed outcome

**Blocked; technical E2E acceptance is incomplete.** Run `42821429094`, Attempt
`1` produced the three archived Task Spans. The checker received all three,
with the independent Trace/Span IDs and `eci.task` matcher. The receiver's
ready/Export/Stop/Report/Shutdown lifecycle completed. Official Registry bytes
were verified, but native validation failed:

```text
missing_attribute (violation)
Attribute 'github.repository' does not exist in the registry.
native exit_code=1; gate_exit_code=1
```

The attribute is an application-specific Resource field in the original #75
capture. This is a strict Registry/policy integration mismatch, not an OTLP
protocol error or a missing Pipeline Root. No capture fields or findings were
removed to obtain a pass. #74/#75 own the extension/Producer handoff; #82 remains
Draft. Original Producer `github_authenticated=false` and `otlp_delivered=false`
are unchanged; the checker's local replay ACK is a separate observation.

| Interface | Observed result |
| --- | --- |
| `weaver-artifacts` export | CLI 0; exported report failed |
| `weaver-report` | CLI 0; equal to exported original report |
| `weaver-enforce` | CLI 1, `weaver_live: findings` |
| `execute-plan`, same Source | CLI 0; report failed / exit 1, Source tree verified |
| `execute-plan`, changed Source | `not_executed`, `source_tree_mismatch`, exit 4 |
| Consumer inspection | CLI 1, preserves native rejection and per-Task inventory |

The Plan's embedded native report equals the original artifact report, including
commands, ports, native samples and Source digest; each contains one native
`live-check`. The Source-drift rejection happens before native execution.

## Files

- [Receipt](receipt.json): actual command exits, Task identities, lifecycle,
  source/runtime identity, blocking finding and incomplete acceptance fields.
- [ECI report](report.json), [native report](live_check.json) and
  [native stderr](live.stderr): original bytes from the actual export.
- `native-artifacts.tar.gz`: complete exported Directory, including all raw
  logs, Advice files and native Registry package.
- `source.tar.gz`: complete replay Source, excluding temporary `.git` metadata.
  Original capture SHA-256 remains
  `07aa61d36ea8e0a179302ad75d17b1a5ec836e6c8870dd551482b59a759245e5`.
- [Plan](plan.json), [execution report](execution-report.json),
  [changed-Source refusal](changed-source-execution-report.json), and
  [consumer rejection](consumer-result.json). The last is a normalized JSON
  inspection result; the native report and complete native archive are unmodified.
- `SHA256SUMS`: checksums for this bundle; the parent legacy checksum file does
  not cover this new bundle.

## Reproduce through existing APIs

Use a checkout containing the declared runtime candidate and this consumer.
Extract `source.tar.gz` into a **new** temporary Source directory. For example:

```powershell
python -m tarfile -e docs/verification/weaver-evidence/real-attempt-task-only/source.tar.gz .artifacts/task-replay-source
dagger api call weaver-artifacts --source .artifacts/task-replay-source --kind live export --path .artifacts/task-replay-artifacts
dagger api call weaver-enforce --source .artifacts/task-replay-source --kind live
dagger api call execute-plan --plan docs/verification/weaver-evidence/real-attempt-task-only/plan.json --source .artifacts/task-replay-source
```

Artifact/report CLI exit 0 is not gate success. This frozen policy/capture
combination is expected to remain a failure. Its native valid-positive and
isolated new native boundary negatives are not accepted from this run.

Up: [Weaver evidence](../README.md). Contract and targeted unit checks:
[Weaver verification](../../weaver.md#52-real-github-attempt--weaver-live-integration).
