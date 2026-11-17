# Source identity and generated inputs

[PR #117 review 5932472685](https://github.com/dymzz/engineering-ci/pull/117#pullrequestreview-5932472685)
requested two P1 fixes: retain available validated Plan Git identity in Dagger
Native Results, and bind verified generated-input references before Result ID
construction. Implementation: `eff476abffe892d606cea3f5575e22e247869c9b`.
This is a repair of those associations; it grants no stronger Source proof,
implementation approval, Run/Attempt authentication or Origin attribution.

The adapters now receive the Plan and the existing verified
`execution.generated_input_refs()` projection before calling the shared Native
Result constructors. The framework Result and every Weaver stage Result retain
the consumer's generated input kind/path/digest/Producer ref in `input_refs`.
The outer report mirrors that information. Existing Source, Run/Attempt, Tool
identity, native exits, stage predecessor references and saved-result readers
keep their separate meanings.

## Focused validation

- **95 producer/Plan-result tests passed**, including Worktree/Index/Commit
  through policy-only and DAG paths, both unittest/pytest, all available Git
  identity fields, preserved `dagger` origin, and no invented content binding.
- Generated Producer identity/path/digest changes alter the immutable Result
  ID. Valid data closures enter the corresponding Native Results; wrong
  digests block each affected consumer before startup while independent peers
  still finish. Existing file/directory, binary and empty-directory cases pass.
- Existing Host data-closure regression: **1 passed, 124 deselected**, using
  `tests/test_plan_contract.py -k v2_data_dependency_closure`. No Host
  implementation was rewritten.
- Ruff check/format passed on the six changed Python files. Existing
  `engineering-ci version check` passed at v0.7.0.

## Two actual native requests

Both requests use the existing locked controlled Source, a command prerequisite
that produces `ci-weaver.toml` and `tests/test_generated.py`, and two verified
File data edges into Registry and unittest respectively. Native inputs are
retained in the original bundle, including the actual produced files.

The Plan deliberately supplies **declared control Index/Tree/Head values**
without independent Git ODB/materialization proof. The Source is still a
Dagger Directory. This tests identity retention and refuses to claim that the
declared Index bytes were independently authenticated: `binding_status` remains
`identity_only`, `scope_kind=changed_paths`, `verified_input_scope=[]`, no
content digest, top-level `source_binding_verified=unknown`, and `trusted=false`.

| Case | Plan | Original Report | Inspection | Complete original bundle |
| --- | --- | --- | --- | --- |
| Matching generated digests | [Plan](passed-plan.json) | [Report](passed-report.json) | [Inspection](passed-inspection.json) | [Bundle](passed-bundle.tar.gz) |
| Wrong expected test-file digest | [Plan](wrong-digest-plan.json) | [Report](wrong-digest-report.json) | [Inspection](wrong-digest-inspection.json) | [Bundle](wrong-digest-bundle.tar.gz) |

In the positive case Registry executes its three native stages with exit 0,
unittest runs the generated test with exit 0, and top-level `passed/0` passes
Enforce. All four Native Results keep `snapshot=index`, declared Tree/Head/Index
identity, and `result_origin=dagger`. Each includes exactly its consumer's
verified generated-input reference, whose Producer, output path and SHA-256
match the returned produced artifact and data closure. The framework's
`tests`/`checks` views and saved `check-result.json` agree.

In the negative case the test-file output's declared SHA-256 is wrong. The
actual generated file and diagnostic closure remain available, but Tests are
`not_executed`, their exit is null, and no test Native Result or proved input
is invented. Independent Registry stages still pass with their valid generated
config reference. Top-level `blocked/data_artifact_digest_mismatch/5` causes
Enforce to reject that same saved object.

Both compound observations retain equal Report/artifact bytes and the same
Directory ID before/after Enforce. Export CLI exit 0 means evidence delivery,
not its verdict. [native-cli.json](native-cli.json) stores tool-return chunks;
separate requests/cache markers do not prove shared or fresh Origin execution.
Caller Run/Attempt references remain unverified; #87 retains attribution.

[receipt.json](receipt.json) lists exact file hashes, implementation source
snapshots matched to the observed implementation fingerprints, validation
commands and bundle summaries. The unchanged starting Source is reused from
[the existing locked input archive](../locked-source.tar.gz); its complete
file map was checked against the actual invocation input. [SHA256SUMS](SHA256SUMS)
protects this new evidence. All earlier raw Plans/Reports/CLI/bundles and source
snapshots retain their original bytes and revisions.

Parent: [Process Producer evidence](../README.md).
