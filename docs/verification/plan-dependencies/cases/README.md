# Controlled Plan consumer cases

These cases are acceptance inputs, not production configuration. The fixed
native implementation is `d396e076af8dbab2ed970a2a99764737e0ccfe97`.
The minimal source has only a comment-only `ci-selection.toml`; Plans are
explicitly constructed controlled API inputs. Their `git.snapshot=worktree`
does not prove Commit, Index or PR Merge Tree coverage.

| Case | Captured input | Unmodified Report | Actual outcome |
| --- | --- | --- | --- |
| Future Plan version | [Plan](plan-version-plan.json) | [Report](plan-version-report.json) | `not_executed`, Report exit 2, empty checks/actions |
| Future dependency version | [Plan](dependency-version-plan.json) | [Report](dependency-version-report.json) | `dependency_schema_unsupported`, exit 2 |
| PASS semantic unsupported in this slice | [Plan](pass-precondition-plan.json) | [Report](pass-precondition-report.json) | `dependency_kind_unsupported`, exit 2; cannot silently ignore the PASS obligation |
| Missing Producer reference | [Plan](missing-producer-plan.json) | [Report](missing-producer-report.json) | `dependency_producer_invalid`, exit 2 |
| Malformed JSON | [Input](invalid-json-plan.json) | [Report](invalid-json-report.json) | `plan_invalid`, exit 2 |
| Supported post-check policy | [Plan](supported-policy-plan.json) | [Report](supported-policy-report.json) | Actual `python -c` command, native exit 0; Report passed |
| Exported Source, original Commit exists | [Plan](exported-git-odb-plan.json) | [Report](exported-git-odb-report.json) | Git action not executed at the export boundary; Report blocked, exit 5 |
| Host writes source | [Plan](host-write-plan.json) | [Report](host-write-report.json) | Native command exit 0 retained; changed Source refused, Report exit 4 |
| Original ODB, Commit present | [Plan](host-odb-present-plan.json) | [Report](host-odb-present-report.json) | Direct Host API, native `git cat-file` exit 0 |
| Correct ODB, Commit missing | [Plan](host-odb-missing-plan.json) | [Report](host-odb-missing-report.json) | Direct Host API, native `git cat-file` exit 128 and stderr; Report failed |

[native-cli.json](native-cli.json) preserves the native command, actual CLI
exit and tool-return terminal chunks for all eight Dagger cases. The output
reports were copied byte for byte. CLI exit 0 means the API returned a Report;
its `status` and `exit_code` determine the controlled result. Terminal cursor
escapes and wrapping are retained. This is not a complete Engine Trace.

The native calls used this command, with `CASE` replaced by a table entry:

```powershell
$env:FORCE_DARK_MODE='1'
dagger api call execute-plan --plan .artifacts/issue86/CASE.json --source .artifacts/issue86/source --output .artifacts/issue86/CASE-result.json
```

To reproduce, copy the chosen archived Plan to that invocation path and the
[source file](../inputs/source/ci-selection.toml) into the source directory.
Do not add README files to that source: content identity covers actual bytes.
`invalid-json-plan.json` is intentionally not parseable JSON.

The two direct-Host calls used `execution.execute(plan, root=worktree_root)`
at implementation `7f397eb6ae5ed6e0f327960a51773571ac41a8fe`, with Git hooks
disabled. Both retain the actual ODB root, expected Commit and native result.
They refer to the original temporary invocation config path; reproduce by
copying the source file there, without silently rewriting the captured Plan.
Only the Dagger report shape changed at `d396e07`; the Host implementation is
unchanged. These are not production or trusted prerequisite receipts.

The supported Plan edge maps to the actual native `executePlan` Function,
then the existing Host adapter's post-check action; its native exit is in
`report.prerequisites[0]`, and the same declaration and original Git binding
are retained in the outer Report. There is no new data-edge/Artifact node
claimed by this example. Unsupported PASS declarations do not execute their
Producer or Consumer. B still owns real PASS scheduling and data mapping.

No test targets or other tools are selected in the eight native cases. The
affected Host tests separately prove that ordinary independent tests remain
executed when a post-check Git context is unavailable; the supported policy
test preserves tests-before-policy order. No live Weaver scan is repeated.

Parent: [dependency inventory and boundaries](../README.md).
