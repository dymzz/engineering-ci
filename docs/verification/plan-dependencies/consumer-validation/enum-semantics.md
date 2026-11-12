# Scope status and snapshot semantics

Review [5874742421](https://github.com/dymzz/engineering-ci/pull/88#pullrequestreview-5874742421)
identified two remaining cases where a well-typed unknown value could be
silently ignored or accepted. This change rejects those values in the existing
shared Plan validator, for both schema 1 and 2. It adds no execution stage or
Source attestation. Fixed implementation and artifact hashes are pinned in
[enum-receipt.json](enum-receipt.json).

The sets were checked against current production code at `5e63db5`:

| Field | Producer / existing semantics | Supported values |
| --- | --- | --- |
| `check_scope[*].status` | `selection._check_scope`, `select`, `_escalated`; applies at top level and nested selection | `selected`, `unknown`, `not_applicable`, `targeted`, `escalated`, `no_tests`, `no_changes` |
| `git.snapshot` | `selection.changeset`, multi-ref union and post-completion Plan | `worktree`, `index`, `commit`, `push`, `pr_merge`, `multi` |

Explicit full selection emits `escalated`, with `full_tests=true`; it does not
emit a new `full` status. CLI mode `pr` produces the materialized identity
`pr_merge`. Failed/unresolved CLI mode labels are not additional executable
snapshot identities. Missing/null legacy snapshot keeps the existing default:
commit when an execution Tree exists, worktree otherwise. Explicit unknown or
empty snapshot strings are refused. Multi-ref remains recognized but requires
per-ref execution; this patch does not enable it as a single Source.

Unknown scope status returns `check_scope_status_unsupported`; unknown
snapshot returns `git_snapshot_unsupported`. The binding helper independently
refuses unknown modes before Git access, preventing accidental bypass of the
public validator from falling through to success.

## Focused regression

**113 tests passed, one existing merge-tree skip.** Only the Plan validator
suite, the new actual-producer status calibration and five existing binding
tests ran. CLI loader/CLI, direct execution and both schema versions refuse a
Plan containing valid test targets and unknown semantics before consumers
start. Positive cases cover all seven current scope states, six snapshot
declarations, actual Selector output and legacy default behavior. Existing
index/worktree/push/commit refusal or binding behavior was retained; the
unchanged PR-merge case retained its existing skip. Ruff check/format passed
on the four changed Python files.

The [source archive](inputs/enum-source.tar.gz) preserves a dependency-free
controlled project, a native offline-generated lockfile and a valid stdlib
unittest target. Source file hashes and content identity are in the receipt.
The control ensures that malformed Plans are not merely failing because their
test target or project is unusable.

| Native case | Input | Returned Report | Observation |
| --- | --- | --- | --- |
| Supported control | [Plan](supported-control-plan.json) | [Report](supported-control-report.json) | passed / exit 0; native stderr reports one unittest passed |
| Valid target + future source scope | [Plan](unknown-scope-status-plan.json) | [Report](unknown-scope-status-report.json) | not_executed / exit 2 / `check_scope_status_unsupported`; no checks or actions |
| Valid target + future snapshot, no Tree | [Plan](unknown-snapshot-plan.json) | [Report](unknown-snapshot-report.json) | not_executed / exit 2 / `git_snapshot_unsupported`; no checks or actions |

[enum-native-cli.json](enum-native-cli.json) records all three actual API
requests and tool-return terminal chunks. API CLI exit 0 is separate from
Report status/exit. Returned Results and observed cache markers are recorded
without claiming fresh Origin Execution; #87 retains that attribution.
The refused Reports also record tests as not executed. No unmodified ODB,
Host-write, Weaver/OTLP or complete local matrix evidence was rerun.

Earlier validation, unknown Source-binding state, ODB/source-write safeguards
and cached Result evidence remain intact. Real PASS/data edges, #66 receipt
consumption and B/D still remain open under #86. This is only the first A
delivery; these enum fixes do not imply completion of the remaining phases.

Parent: [consumer validation](README.md).
