# Plan consumer validation regression

Review [5872984551](https://github.com/dymzz/engineering-ci/pull/88#pullrequestreview-5872984551)
identified an unhandled JSON type path and asked that declaration validation
stay separate from real Source binding. Implementation is pinned in
[receipt.json](receipt.json); it preserves schema 2, legacy readability and
the previously delivered Git-context/Host-write safeguards.

The validator now checks string types before set membership and checks nested
consumer structures before field access. This includes top-level and nested
`check_scope`, fallback check names, selected prerequisites, Git fields, input
config paths and post-completion input objects. Bad input returns a stable
diagnostic before source verification or tool invocation.

## Focused local regression

- The validator suite passed **70 tests**, including JSON null/bool/number/
  array/object categories, nonempty containers and the contract producer.
  Loader, direct execution and CLI assert the same refusal reason, empty
  checks/actions and no consumer start. No new test library was added.
- Host/Selector regression passed **70 distinct tests**, with one existing
  merge-tree skip and three unchanged post-completion/closure cases excluded.
  Three initial sandbox temporary-directory permission failures passed on a
  targeted retry with TEMP/TMP inside this worktree; no code changed for it.
- Total: **140 distinct tests passed, one existing skip**. Ruff check/format
  passed on the four changed Python files. Existing native ODB, source-write
  and initial CI evidence is retained without repeating those tools.

## Actual Dagger API invocations

[native-cli.json](native-cli.json) retains exact commands and tool-return
terminal chunks. Each invocation returned API CLI exit 0; the Report controls
the result below. These are API requests and returned native Results, not an
Origin Execution attribution or proof that a tool ran afresh.

| Input | Plan | Report | Result |
| --- | --- | --- | --- |
| `framework=[]` | [Plan](framework-array-plan.json) | [Report](framework-array-report.json) | `framework_invalid`, Report exit 2 |
| `framework={}` | [Plan](framework-object-plan.json) | [Report](framework-object-report.json) | `framework_invalid`, Report exit 2 |
| action `type=[]` | [Plan](action-type-array-plan.json) | [Report](action-type-array-report.json) | `dependency_action_unsupported`, exit 2 |
| action `type={}` | [Plan](action-type-object-plan.json) | [Report](action-type-object-report.json) | `dependency_action_unsupported`, exit 2 |
| non-list check scope | [Plan](check-scope-object-plan.json) | [Report](check-scope-object-report.json) | `check_scope_invalid`, exit 2 |
| non-object scope entry | [Plan](check-scope-entry-plan.json) | [Report](check-scope-entry-report.json) | `check_scope_invalid`, exit 2 |
| nested scope status array | [Plan](check-scope-status-plan.json) | [Report](check-scope-status-report.json) | `check_scope_invalid`, exit 2 |
| Plan config A / actual Source config B | [Plan](config-drift-plan.json) | [Report](config-drift-report.json) | declaration consistent; Source binding unknown |

The seven refused Reports have empty checks/prerequisites and did not start
affected consumers. The last case uses the exact [source B config](inputs/source-drift/ci-selection.toml),
which differs from the [declared config A](inputs/source/ci-selection.toml).
The two hashes inside the Plan agree with A; actual Source bytes are B. The
policy Report can pass, while `source_binding_verified` remains `unknown`.
This negative case proves no upgrade to verified binding; it does **not**
claim the Source drift has already been rejected by a #66 receipt consumer.

## A to B handoff

`plan_declaration_consistent` is true for supported schema-2 declarations,
false on validation refusal, and null for legacy declarations. It records
internal consistency only. `source_binding_verified="unknown"` cannot be
treated as true by a strict consumer. Original Git metadata preservation,
export content identity and narrower tree/closure checks do not supply #66's
verified Source/Prerequisite handoff.

B must check actual config bytes/hash, Source snapshot/Tree and each Consumer's
data against that handoff, then refuse config-A/source-B before claiming
binding. It must also propagate Report `status/exit_code` into real strict
Check/Enforce/CI failure: API exit 0 plus a refused Report must fail that entry.
This patch leaves those B interfaces explicit rather than creating a second
attestation system or changing all read-only APIs to throw.

The original cached supported-policy/host-write Results are described as
returned Results. Their raw captures are unchanged; the parent receipt now
separates invocation, observed cache markers and unattributed Origin Execution.
#87 owns actual Trace/Origin attribution. The known Git worktree warning is
retained without speculative Git configuration or Dagger-version changes.

Parent: [dependency inventory and boundaries](../README.md).
