# Git impact selection — local verification

Scope: first slice of [ECI #66](https://github.com/dymzz/engineering-ci/issues/66).
Date: 2026-11-08; host: Windows 11, CPython 3.14.3, Git for Windows.

Contract: [selection and plan](../architecture/selection.md).

## What ran

```sh
pytest -q tests/test_selection.py
# 26 passed

pytest -q tests \
  --deselect "tests/test_security_policy.py::TestPolicyDocument::test_example_policy_is_a_valid_publish_input"
# 473 passed, 8 skipped, 1 deselected, 187 subtests passed
```

The deselected policy-fixture test is a Windows `core.autocrlf` artifact
unrelated to this slice; it passes with an LF fixture and on Linux CI and was
not modified.

`tests/test_selection.py` builds a disposable Git repository from
`tests/fixtures/selection-app` for every case, so nothing depends on a consumer
checkout or the Knowledge repository:

| Behaviour | Observed |
| --- | --- |
| Empty change set | `no_changes`; no tests; plan is not a pass |
| Tach reverse closure | changing `pkg.sub` selects `test_pkg`, `test_sub`, `test_other` |
| Leaf module | changing `pkg.other` selects only `test_other` |
| Non-code rule | `data/input.json` selects the configured `test_pkg` with its reason |
| Ignored paths | `docs/guide.md` yields `no_tests`, never `no_changes`/pass |
| Escalation | `pyproject.toml` or an unknown path selects the full discovered suite |
| Deleted test | escalated, not a narrower suite |
| Unmapped module | declared `pkg.new` without a mapping escalates with its name |
| Full mode | all tests and all prerequisites |
| Git identity | plan records base/head commits, tree, dirty worktree and changed paths |
| Rename/untracked | both rename sides and an untracked file appear in the change set |
| Missing base | escalated because Git input is incomplete |
| Native runner | selected `unittest` modules pass; an injected failure propagates |
| Multi-target pytest | two file targets run in one native pytest process; one failure propagates |
| Per-check scope | Markdown-only change: `unittest: no_tests`, `docs: selected`; code change: Tach/Vulture selected, docs not applicable |
| Deletion scope | `docs.recheck_all = true`, so unchanged referencing documents remain in scope |
| Path fidelity | a leading space in a renamed path survives the NUL-separated Git report |
| Rename/delete with spaces | exact `docs/space name.md` identity kept; deletion sets `docs.recheck_all` |
| `--full` scope | `selection.full_tests = true`; docs/Tach/Vulture remain `not_applicable` |
| CLI | JSON plan shape and `--json`/`--run` exclusion; `--run` with no tests returns 3 |

## Plan identity observed

A plan from the fixture records `kind = "engineering-ci-selection-plan"`,
`schema_version = 1`, `generator.config_sha256`, `generator.tach_sha256`,
resolved Git commits/tree and the sorted `selection.tests` plus prerequisites.
The fixture plan is disposable and is not committed.

## Dagger shared implementation

`uv build --wheel` produced one wheel containing both `engineering_ci/` and
`eci_core/`. The Dagger module project is `engineering-ci-module` (package
`engineering_ci_module`) and depends on that wheel via a path source; its
`dagger-module.toml` includes the root metadata and `src/eci_core/**`.

```text
dagger call shared-identity
# 0.7.0:1:engineering-ci-selection-plan

dagger call selection-self-test
# engineering-ci-selection-plan:targeted:tests.test_pkg
```

The second call runs `eci_core.selection.select` against a disposable
Tach/config/tests layout inside the Dagger runtime. This proves the Dagger side
imports and invokes the installed shared selector, not a copy or a plan-only
claim.

## Limits

- Local `unittest` and pytest execution are wired; Dagger check execution of a
  consumer plan is not. No second selector exists.
- Push/PR event objects and simulated merge trees are not implemented yet;
  callers must pass commits they actually hold.
- A subset pytest run is real execution for its targets, but it cannot satisfy a
  Required full-coverage gate; that gate must run fully or consume #63 verified
  complete evidence. `--full` expands tests only.
- Source-manifest validation, Tach/Lychee acquisition and Dagger Source
  construction are the remaining review items.
- Windows checkout line endings can change byte-level policy fixtures; the
  selection fixture is content-only and unaffected.
