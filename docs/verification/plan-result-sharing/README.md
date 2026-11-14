# Native Plan result sharing

First B delivery for [#86](https://github.com/dymzz/engineering-ci/issues/86),
based on merged #88/main `7ed7e6792257bfa7a7584dbbc812d2987547c6f9`.
Result-sharing implementation: `e71f63eff9368b76016971caa8586c8148c7f350`;
shared existing test prerequisites: `3b222aac73db11d820b11814bbc88163f68218ba`.
This is the explicit result-sharing slice, not completion of B or D.

The [aggregation review regression](aggregation/README.md) records the later
fix for blocked tests combined with an actually failed independent native Check.
Earlier raw evidence below is retained at its original code revision.

`PlanExecution.artifacts` retains one native Directory. Report and strict
Enforce only read it. Selected pytest uses the existing execution and bundle
implementation, replacing the former second Plan-specific pytest runner;
Weaver retains its original `/out` Directory. Fixed independent native branches
are offered concurrently to Dagger and joined before verdict enforcement;
there is no ECI topology scheduler, queue, registry or pass cache. Host checks
and legacy post-check policies keep their observation boundary.

## Delivered scope

| B requirement | Actual delivery / remaining boundary |
| --- | --- |
| Source identity | Preserve existing original Git/export/tree/closure facts and refusal guards; `source_binding_verified=unknown` remains. #66 verified Source/prerequisite receipts remain pending |
| Native computations | Existing selected tests and Weaver results are retained as Container-derived Files/Directories. Other Host checks keep their existing execution path |
| Independent failure | A native branch failure or Engine error does not cancel its peer. Existing test-only Tach/coverage prerequisites block dependent tests; independent checks continue. Generic PASS edges remain unsupported |
| Data closure | Report/Enforce consume the real retained native artifact Directory; no Artifact is inferred from a historical Verdict. Host-generated outputs and new data-edge mapping remain pending |
| Shared consumers | One compound function holds one result object; report bytes equal artifact bytes and Directory ID stays identical across Enforce. Separate API calls are not claimed as one Origin |
| Local interface | No new default root Check or Host/Dagger requirement; native Enforce is an explicit result-verdict API |

Enforce rejects non-passed status, nonzero/mismatched exit, malformed or unknown
Report schema. It does not admit target Source, Expected coverage, trusted
historical evidence or Release. `execute-plan` remains a read-only Report API.
An enforcement call that raises does not also return an Artifact: hold/export
the result first, or use a compound caller that retains it. The fixture here
catches strict failure only to archive the observation; it is not a gate that
ordinary CI should substitute for `enforce`.

## Focused validation

- **160 tests and 48 subtests passed** across the new result contract and
  existing Plan/pytest report/delivery/quality suites. Three additional Report
  schema counterexamples and the final shared-prerequisite regression give
  **167 distinct tests total** (final gate subset: 129 passed). New unit tests
  use explicitly labelled Dagger doubles; their barrier proves concurrent
  requests and their counters constrain one invocation of each producer.
- Ruff check/format passed on the five changed Python files.
- [Eleven native API requests](native-cli.json), with each actual code revision,
  retain API exit separately from Report verdict. [cases/README.md](cases/README.md)
  links exact Plans, returned Reports, compound observations and full original
  bundles; [receipt.json](receipt.json) records hashes and input archives.
- The real compound caller reads Report, retains artifacts and calls Enforce
  on one object. All six compound cases reported identical Directory ID
  before/after Enforce and equal Report/artifact bytes. This proves result
  reference sharing, not a new Origin Execution or a fresh scanner count on
  a cached invocation. Native Trace/Origin attribution stays with #87.

The initial native mixed-target probe exposed pytest's console-script module
import failure in a virtual project. Plan targets now use `python -m pytest`;
the fixed native case ran both a module target and a file target (two passed).
The failing case preserves pytest exit 1, the failed assertion and JUnit.
The existing fixture's manifest/lock are used unchanged; an offline attempt
with a different locally uncached pytest index entry was not counted as a tool
or graph success. No host tool or project dependency was added.

Independent Weaver cases intentionally omit `ci-weaver.toml`. The actual
existing Weaver runtime returns its configuration failure and `/out` Report;
the unittest branch still passes. These are native configuration diagnostics,
not claims of a full Registry/live/OTLP scan or production prerequisites.
One additional controlled case consumes the unchanged Git-blob Registry
fixture: missing Tach graph validation prevents test startup while the
independent Registry returns native PASS. Report remains blocked/exit 5 and
public Enforce exits 1. This is the one newly required Registry integration
probe for scoped prerequisite failure; it does not rerun live/OTLP or a matrix.
No full local matrix or unchanged Weaver/OTLP scan was repeated. Root CI is
left to the PR's normal automatic workflow.

## PR #90 review revision (v2 DAG, `/scratch` `workdir` & `[[dependency_edges]]`)

Following Reviews 22–24 on PR #90 and merging `main`:

- Removed the nonexistent `dag.host()` / `dagger_host_import_unsupported` bypass.
  Prerequisite `command` outputs now materialize under the module `/scratch`
  workdir via `dag.current_module().workdir`/`workdir_file` + `.sync()` before
  cleanup, preserving binary files and empty/nested directories in one immutable
  Dagger `File`/`Directory` closure shared across consumers and exported under
  `produced/<kind>/<name>/<rel>`.
- Module-local `command` prerequisites explicitly carry
  `execution_location="dagger_module_scratch"`,
  `result_origin="module_local_subprocess"`, and `engine_cached=false`.
- `docs` check inside Dagger delegates missing `lychee` and read-only `git`
  (`ls-files`, `diff`, `show`, `cat-file`) to Dagger containers via
  `_exec_capture`, capturing full exit codes `0-255` (including git exit `128`).
- `selection.py` wires optional `[[dependency_edges]]` into `load_config`,
  `select`, and `plan`, emitting `dependency_contract.schema_version = 2` for
  configured active edges and preserving `policy_only` (`schema_version = 1`)
  otherwise.


## PR #90 R3 evidence provenance (2026-11-14)

**Evidence status: reported Engine executions; new R3 raw artifacts not yet
independently archived. Do not treat the statements below as a new verified
native receipt, or as a Ready/merge approval.**

- The [PR #90 description](https://github.com/dymzz/engineering-ci/pull/90)
  reports nine real Dagger Engine `v1.0.0-beta.16` R3 scenarios at
  `5d274fa8c47681e06af5211d4047ac1b773be9b9`: binary File and nested
  empty Directory delivery/export, configured `[[dependency_edges]]` with
  real Git/Lychee Docs success and broken-link failure, missing `.git` with
  Git exit 128 and no fabricated PASS, missing output, output-digest mismatch,
  producer nonzero exit, overlapping paths, and distinct failing consumers
  while an independent Docs check completes. These are **PR-author reported
  outcomes**; original per-scenario Plans, stdout/stderr, Reports, enforced
  exits, exports and digests are not linked in this repository.
- The same PR description reports a subsequent real Engine Docs/Lychee
  pass/fail pair using the corrected `selection-app/ci-hygiene.toml` at
  `83d00ecd07c2f995e9bc853a060c77739797c730`. The subsequent
  `cab8c4fc51bd5a1c2a67eedb3527e29459ba39ce` commit changes **only**
  the two independent test status/exit-code assertions in
  `tests/test_plan_result.py`, not the production Engine implementation.
- [GitHub Actions run 43612468175](https://github.com/dymzz/engineering-ci/actions/runs/43612468175)
  completed its four jobs successfully at `83d00ec`, but these are
  repository checks, **not** a per-scenario R3 receipt. Its sole uploaded
  artifact is `dagger-native-telemetry-a1-offline`, which is not the R3
  Plan/Artifact bundle.
- The historical [native-cli.json](native-cli.json),
  [receipt.json](receipt.json), [cases](cases/README.md), and
  [aggregation](aggregation/README.md) are retained as genuine earlier
  Result Sharing evidence **at their original revisions**, not relabeled
  as the new scratch-workdir / binary / Directory R3 observations.

**Still required for independent R3 acceptance:** preserve the *already
executed* native per-scenario invocation/CLI and engine version, exact source
and Plan inputs, Report JSON, native process exit and Enforce exit,
producer/consumer diagnostics, exported bundle structure and binary/Directory
hashes, and a clear execution-SHA-to-result mapping. Archive existing raw
records without reconstructing output from the expected results or rerunning
unaffected tools. If the original local `run_r3.py` outputs are unavailable,
report them as unavailable; these provenance notes do not substitute for
the missing records.


## Remaining work

- **#66**: authenticated Source/Prerequisite receipts and config/baseline proof
  (`source_binding_verified` remains `"unknown"` in `source_handoff`).
- **#86 B (remaining)**: remaining applicable production dependencies mapped to
  Native Dagger Function/Container compute nodes (this PR is only the Result
  Sharing, v2 Data Edge, and Module-local File/Directory Closure slice).
- **#86 D**: input closure refinement, cache hit/invalidation, and external
  URL/API/side-effect policy for those real Native computations/Sources.
- **#87**: read-only Trace/Origin attribution over generated Native Results and
  Artifacts.
- **#63 / #61**: historical Verdict/cross-Run evidence admission and Expected /
  Required / Release policy gates.

Issue #86 stays open.

Parent: [verification records](../README.md).
