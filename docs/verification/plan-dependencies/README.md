# Plan dependency inventory and refusal boundary

Issue [#86 A](https://github.com/dymzz/engineering-ci/issues/86), first delivery
on `main` `95c70be310053ddb9fcbe12b78a285e56d62b542`. This records actual
instances and the versioned public handoff. It does not claim B's Dagger DAG,
pre-execution PASS scheduling, data materialization or D's cache observations.
The authoritative design remains [Execution Graph](../../../design/execution-graph/plan.md).

## Actual instances

[inventory.json](inventory.json) contains the per-instance Producer, revision,
action/parameter presence, selection basis, Consumer, edge kind, phase,
ODB/context/source/result origin, failure scope and unresolved reason. It is
an evidence inventory, not a runtime registry. Baseline input Git-blob hashes
are included. No confirmed production pre-execution PASS relation was found.

| Instance / source category | Actual selection and action | Consumer / current edge and execution | Unresolved facts / handoff |
| --- | --- | --- | --- |
| `tests/fixtures/selection-app/ci-selection.toml` / fixture: `identity_commit` | targeted intersection with `tests.test_sub`; full/escalated select all names; no action spec | Test selection relation only; no proven test startup condition | No Commit, ODB, actual identity action or production Consumer; cannot infer from name |
| Same fixture: `wheel_smoke` | targeted intersection with `tests.test_other`; full/escalated all; no action spec | Selection only; missing action remains unresolved and final Report blocks | No argv, wheel build, File/Directory or declared data Consumer |
| `docs/architecture/selection.md` / example: `identity_commit` | `git_commit_present`, literal placeholder `0123abcd...` | Existing Host post-check policy -> final Report; needs original Git ODB | Documentation example, not an executed production prerequisite |
| Same example: `wheel_smoke` | argv `python -m your_wheel_smoke`; mapped to `tests.test_pkg` | Existing post-check command -> final Report | Placeholder module; does not prove a wheel producer or wheel consumer |
| `tests/test_selection.py::test_plan_includes_typed_prerequisite_actions` / controlled fixture | Real disposable base Commit and `python -c 'raise SystemExit(0)'` parameters | Plan serialization only | Proves parameter production, not that either action ran |
| `tests/test_execution.py::test_execute_runs_identity_commit_prerequisite` / controlled execution | Actual fixture base Commit | Host `git cat-file -e`, post-check, Report policy | No production PASS-precondition claim; ODB path/native result now retained |
| `test_execute_fails_missing_identity_commit` / controlled rejection | `000…000` absent in correct fixture ODB | Native failure, final Report fails | Different from unavailable ODB; no production claim |
| `test_execute_runs_command_prerequisite` / test double | argv `python -c 'raise SystemExit(0)'` | Runner double, post-check policy | No wheel built, no artifact produced; not a real build execution |
| `.github/workflows/reusable-python-ci.yml` / caller parameter | `selection_config` defaults empty; existing select -> execute CLI | Real Actions checkout when opted in; no committed caller supplies a value | Concrete production config/Commit/action unknown; cannot count optional capability as actual invocation |
| Repository root / project config | No root `ci-selection.toml` exists | No project prerequisite declaration | No production action demonstrated |
| `repository.environment` -> `_repository_tests` / actual native data path | `pyproject.toml` + `uv.lock`, Source Directory -> installed container | Dagger data dependency; `_repository_tests` feeds Check/Artifact | `execute-plan` Pytest is a separate path; B result sharing pending |
| `weaver_artifacts` -> Report/Enforce/Checks / actual native data path | Source/config/runtime -> `/out` Directory and Native Report | Existing Dagger Function data/result path | No A claim of cross-session or cross-Run reuse; B owns Plan mapping |

Original Selector behavior is retained. It represents explicit full as
`escalated=True` with `full_tests=True`; the new handoff distinguishes full
using that existing flag, retaining the original escalation reason. Targeted
uses test intersection; full and escalated still select all declared names.

## Delivered public contract

New `selection.plan()` and `post_completion_plan()` produce **Plan schema 2**
with `dependency_contract.schema_version=1`. Config schema and Execution Report
schema remain 1. Existing schema-1 Plans remain readable only without the new
contract; a schema-1 Plan cannot smuggle new mandatory semantics past validation.

Each selected legacy prerequisite has one edge: action Producer -> final
Report prerequisite Consumer, `dependency_kind=policy_only`, phase
`post_check`, `requires_pass=false`, no data `output_ref`, explicit config
path/hash/section and Git binding reference, actual selected-by/reason and
resolved/unresolved status. This describes the current final policy
aggregation; it never means a failed policy is allowed to pass the final gate.
Unconfigured actions remain unresolved and retain the existing final blockage.

The only currently supported required semantic is
`prerequisite_policy_aggregation`. `data` and `pre_execution_pass` are rejected
until B supplies their actual consumers and versioned semantics. A true PASS
edge is not quietly converted to policy-only. Unknown Plan/edge version,
mandatory semantic, kind, Producer/Consumer, condition, binding, unsupported
action, missing parameter or missing/duplicate selected obligation refuses
before tools start. Action overrides cannot replace a versioned declaration.
Non-string framework/action types and malformed nested consumer structures
are rejected with stable reasons before either consumer entry dereferences
them. [Consumer validation](consumer-validation/README.md) records the focused
review regression and Source-binding negative case.

`execution.validate_plan()` delegates to the one shared contract validator.
CLI loader, direct Python `execute()` and Dagger `execute-plan` use it.
CLI retains native exit 2/stderr; Python and Dagger return
`not_executed`, exit 2, empty checks/prerequisites. Dagger's API CLI can exit 0
for this refused Report; that does not mean the plan passed.

Current Reports distinguish `plan_declaration_consistent` (true for supported
schema-2 declarations, false on rejection, null for legacy declarations) from
`source_binding_verified="unknown"`. Internal hash equality and
`original_git_snapshot_preserved` preserve declarations; they do not attest
actual config bytes, original ODB or a #66 Source/Prerequisite result. Existing
tree/input-closure checks are narrower observations. B must consume #66's
verified handoff, check actual config bytes/hash, snapshot/Tree and consumer
inputs, and reject config-A/actual-source-B drift before claiming source
binding. This A slice explicitly does not provide that attestation.

## Source/Host safety in this slice

- Host Git actions first establish a bound original ODB. An absent or enclosing
  unrelated ODB returns `not_executed/git_odb_unavailable_or_unbound`, not
  `identity_commit_missing`. A genuinely absent Commit in the correct ODB
  remains a native failure. Valid ODB/root/Commit and native exit/diagnostics
  are retained. Independent tests still run in their existing phase.
- Dagger marks the Host adapter `execution_context=dagger_export`; that adapter
  never queries some other Git ODB. Original Plan Git snapshot and actual
  exported content identity are retained in `source_handoff`, not relabeled
  as proof of a worktree checkout. An original ODB result/Source receipt from
  #66 is still needed; this PR creates no attestation or trust authority.
- Exported bytes are measured before and after Host actions. A write invalidates
  the old binding with `exported_source_changed`, exit 4, before remaining
  Weaver consumers start. Earlier native results remain in the Report. Generated
  outputs are explicitly not delivered; B still owns File/Directory handoff
  and replanning. No global scheduler or hash-based pass cache is added.

## Actual targeted verification

Implementation is pinned in [receipt.json](receipt.json). Only the three
affected suites ran: `test_plan_contract.py`, `test_selection.py`,
`test_execution.py` — **92 passed, 1 existing skip** (the controlled merge tree equals the
feature HEAD tree). Ruff check/format passed on the seven changed Python files.
Windows checkout had transformed the existing Weaver fixture's line endings;
tests used the unchanged Git blobs, then the normal checkout was restored.
No Registry/fixture edit is part of this PR.

The eight Dagger [native controlled cases](cases/README.md) use actual existing Dagger
`execute-plan`, on this fixed implementation, without tool/graph replacements:

| Case | Actual Report | Consumer observation |
| --- | --- | --- |
| Future Plan version | not_executed / exit 2 | No checks or prerequisite actions started |
| Future dependency version | not_executed / exit 2 | No consumer started |
| Declared PASS precondition | dependency_kind_unsupported / exit 2 | Unsupported precondition is not ignored or faked |
| Missing Producer reference | dependency_producer_invalid / exit 2 | No consumer started |
| Malformed JSON | plan_invalid / exit 2 | Structured refusal, no consumer started |
| Supported post-check policy | passed / exit 0 | Returned native command Result, exit 0; `executePlan` marked CACHED |
| Known main Commit but exported Source | blocked / exit 5 | Git requirement not_executed at export boundary, not missing Commit |
| Host writes `generated.txt` | exported_source_changed / exit 4 | Returned command exit 0 and changed-byte Result; `executePlan` marked CACHED |

Two additional controlled direct-Host invocations preserve actual Git ODB
root, expected Commit and raw native exits: the same fixed main Commit is
present (exit 0), while an all-zero Commit is absent in that correct ODB
(exit 128; failed). These are not production preconditions.

The returned controlled host-write Result's content ID equals an independently
reconstructed tree containing the exact generated bytes. The immutable Dagger
Directory retains its initial identity. The raw Reports, input Plans, Source,
CLI transcripts and hashes are preserved; API exit 0 is kept separate from
Report exit. These are controlled mechanism tests, not production prerequisites.

The [controlled inputs](inputs/README.md) preserve the exact source bytes and
separately label the expected generated bytes. [SHA256SUMS](SHA256SUMS) covers
this evidence bundle. CLI capture consists of the returned terminal chunks;
it is not an Engine-wide trace or an invocation-count proof.
In particular, supported-policy and host-write show cached `executePlan`
Results. They prove what Result the API returned, not that either command
started afresh in that API invocation. Invocation, returned Result, observed
cache markers and unattributed Origin Execution are separated in the receipt;
#87 owns actual Trace/Origin attribution. Original Plans/Reports/CLI captures
remain unchanged; their absent new binding fields are historical, not true.

Remaining #86 work is explicit: real Pre-execution PASS and data edges/outputs
from #66; local failure versus actual dependency scope; native B DAG/concurrency
and Result Sharing; output materialization; D cache/dynamic-input observations.
#63 Trusted/cross-Run admission and #61 Required/Release are not granted here.
B must also propagate Report `status/exit_code` to real strict Check/Enforce/CI
consumers and prove API exit 0 plus a refused Report cannot pass that entry.
The present read-only Report functions retain their normal API behavior.
This is a Draft first A slice, not completion of Issue #86 or all A.1–A.5.

Parent: [verification records](../README.md).
