# Native tool contract: real Host execution

Second #52 slice ([#52](https://github.com/dymzz/engineering-ci/issues/52),
Native Tool contract extension). It records one real Host execution of the
`docs` Check, whose declared tool chain is `git ls-files` (document inventory)
then `lychee` (link scan). It is not a Dagger or Actions parity claim, not a
Runtime ToolAsset binding and not a Required/trusted decision.

The contract itself is described in
[Check identity and Native Result contract](../../architecture/check-contract.md).
Raw output is in [host-docs.json](host-docs.json).

## Environment and command

Recorded 2026-11-13 on Windows 11 (26200), Python 3.14.3, Git
2.56.0.windows.4, Lychee 0.24.2 (the same version pinned by the Dagger module).
The disposable repository contains `docs/guide.md`, a one-check
`ci-hygiene.toml` (`checks = ["docs"]`, Lychee `--offline --no-progress`) and one
commit. The plan declares the `docs` Check as selected for
`docs/guide.md` with `snapshot=worktree`, `base_commit=head_commit=HEAD`.

```console
python -m engineering_ci.execution --plan plan.json --root . \
  --hygiene-config ci-hygiene.toml --json
```

No test double is used: the report below comes from the real `git` and `lychee`
processes. The planning helper was a local, uncommitted script; the plan and
repository inputs above are the complete reproduction.

## Cases

| Case | Native tools and exits | Satisfaction | Report |
| --- | --- | --- | --- |
| `docs-pass` (valid relative link) | `git/ls-files` 0, `lychee/docs` 0 | `satisfied` | `passed` |
| `docs-fail` (`[missing](missing.md)`) | `git/ls-files` 0, `lychee/docs` 2 | `unsatisfied`, `links: result_not_passed` | `failed`, `checks_failed` |

Both cases produce two Native Results with distinct `result_id`s for the same
`check_id` (`eci/hygiene/docs`), `definition_version` 1 and the same
`rule_ref`. The `lychee/docs` result carries the `git/ls-files` `result_id` in
`input_refs` (`kind=check_result`); the `docs` requirement declares
`after=("inventory",)` and is satisfied only because that consumption is
evidenced. `tool_id` is `null` in both results: no verified Runtime ToolAsset
identity exists for these host tools, so the contract records the native entry
(`git/ls-files`, `lychee/docs`), the executable and the exit, not an invented
asset id.

## Real defect found and fixed

Before this slice the Host `docs` Check invoked Lychee with no inputs and sent
the document list on stdin. Real Lychee rejects that invocation:

```console
$ printf 'docs/guide.md\n' | lychee --offline --no-progress
error: the following required arguments were not provided:
  <inputs>...
```

Exit 2, before checking any link. The Check now passes the target documents as
arguments (the Dagger path already did), so the real native result above is the
first reproduction of the Host `docs` tool chain.

## Limits

- Only the Host `engineering-ci execute` path is recorded. Dagger
  `execute-plan` reuses the same Host function inside the module but does not
  pin a tool identity; the separate Dagger `tach-check`/`lychee-check` functions
  still return their own ad-hoc JSON.
- No content digest is claimed as a complete Check input closure. The
  `source_ref` remains `identity_only` (or changed-path `content_digest`) as
  recorded in the JSON.
- `native_satisfaction` is a local, single-Check fact over the supplied results
  (`consumer_scope=supplied_results_only`, `required=false`, `trusted=false`).
  ExpectedSet completeness is #66-B/C work; trust and Required remain #63/#61.
- No `all-required` multi-result instance beyond `git` + `lychee` for `docs`,
  and no production `allowed-alternative` instance exists yet. The alternative
  path is covered by controlled contract tests only.
