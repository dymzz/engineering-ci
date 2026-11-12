# Native Plan result sharing

First B delivery for [#86](https://github.com/dymzz/engineering-ci/issues/86),
based on merged #88/main `7ed7e6792257bfa7a7584dbbc812d2987547c6f9`.
Result-sharing implementation: `e71f63eff9368b76016971caa8586c8148c7f350`;
shared existing test prerequisites: `3b222aac73db11d820b11814bbc88163f68218ba`.
This is the explicit result-sharing slice, not completion of B or D.

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

## Remaining work

New `data` and `pre_execution_pass` semantics remain unsupported and refused by
the shared validator. No production PASS relation was invented from legacy
prerequisite names. B still needs actual #66 producer/consumer binding, scoped
generic PASS startup failure, generated-output handoff, historical-Verdict/data closure
and broader native mapping. D cache/dynamic input, #63 cross-Run admission and
#61 Expected/Required/Release remain separate. Issue #86 stays open.

Parent: [verification records](../README.md).
