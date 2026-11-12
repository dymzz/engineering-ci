# Failure and blocked-result aggregation

Review [5876378443](https://github.com/dymzz/engineering-ci/pull/90#pullrequestreview-5876378443)
identified one P1: an already-written top-level `blocked` could conceal the
failure of an independent native Check. Enforce still rejected it, but the
top-level failure attribution was wrong. The implementation at
`88790cdd316a6e755069463f56beac14594d1c8f` collects Native and Host child results
first, then derives the aggregate verdict from the facts, without using the
previous top-level status as a latch.

Actual failure takes precedence over local blocked/unexecuted obligations.
For coexisting blocked tests and failed Registry, the top level is
`failed/checks_failed` and uses the retained gate/native failure exit. The test
remains `not_executed` with its original prerequisite reason. Child reasons,
raw native exits, gate exits and artifact bytes are not rewritten. Advisory
findings and failed context probes of an unexecuted ODB requirement are not
misclassified as failed Check executions. Existing shared-Source refusals
remain outside this final aggregation boundary and retain their earlier exit.

## Focused validation

**31 result-contract tests passed.** These include both Tach/coverage
prerequisite failures with independent PASS/FAIL, differing native/gate exits,
all previous top-level statuses, Host Check/prerequisite failures added after
Native collection, and Source-write refusal with a collected failed Check.
The labelled Dagger doubles also verify no test producer starts, retained
artifacts remain readable, and Enforce rejects. Ruff check/format passed on
the two changed Python files. No unrelated local matrix was rerun.

Three actual native API requests are preserved in [native-cli.json](native-cli.json):

| Native case | Plan | Raw Report | Compound observation | Full original bundle |
| --- | --- | --- | --- | --- |
| blocked tests + Registry FAIL | [Plan](precondition-plan.json) | [Report](native-failure-report.json) | [Inspection](native-failure-inspection.json) | [Archive](native-failure-bundle.tar.gz) |
| blocked tests + Registry PASS | [same Plan](precondition-plan.json) | [Report](native-pass-report.json) | [Inspection](native-pass-inspection.json) | [Archive](native-pass-bundle.tar.gz) |

The failure Source changes one controlled Registry group type to an invalid
enum and updates its manifest to the actual changed bytes. Thus the original
Weaver `registry check` really returns **exit 1**, rather than stopping at a
hash/config precheck. Its status, `registry-check_failed` reason, native exit,
gate exit and stdout/stderr remain in the bundle. Tests do not start; their
exit is null and their reason stays `tach_graph_unvalidated`. The top-level
result is `failed/checks_failed/1`. The public serialized object's Enforce
returns API process exit 1 with `Plan failed: checks_failed (exit 1)`.

The unchanged PASS Source returns `blocked/tach_graph_unvalidated/5`, with
Registry passed and tests still unexecuted. Both compound observations show
equal Report/artifact bytes and the same Directory ID before/after Enforce.
Exports returning API exit 0 are evidence delivery, not a PASS verdict.

[receipt.json](receipt.json) contains exact input/bundle file hashes and links
the [failure Source archive](failure-source.tar.gz). The PASS Source is the
unchanged [earlier controlled input](../inputs/precondition-source.tar.gz),
whose archive file hashes were rechecked against the actual invocation input.
Returned Results and cache markers remain separate from fresh Origin Execution;
#87 retains attribution. Earlier raw Plans/Reports/CLI/bundles remain unchanged.
This is only the requested aggregation repair; remaining B/D and #66/#63/#61
interfaces are not expanded or represented as complete.

Parent: [native result sharing](../README.md).
