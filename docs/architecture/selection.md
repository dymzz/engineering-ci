# Git impact selection and execution plan

Status: first delivered slice of [ECI #66](https://github.com/dymzz/engineering-ci/issues/66);
Hygiene checks, Dagger execution and Actions wiring are not part of this slice yet.

`engineering-ci select` computes, once on the machine that has the real Git
objects, which tests and checks a change set affects. It emits a JSON plan that
Dagger and Actions consume. Selection exists only in the installable
`engineering_ci` package; no second selector is added to the Dagger module.
Execution results stay separate from selection: a plan never claims tests ran.

## Consumer configuration

Rules live in a consumer TOML file (default `ci-selection.toml`), not in ECI
source:

```toml
schema_version = 1
tach = "tach.toml"
tests_dir = "tests"
src_root = "src"
framework = "unittest"

escalation_files = ["pyproject.toml", "uv.lock", "tach.toml", ".gitattributes"]
escalation_prefixes = [".github/workflows/"]
ignored_files = ["README.md", ".gitignore"]
ignored_prefixes = ["docs/"]
checks = ["hygiene", "tach", "vulture"]

[module_tests]
"pkg" = ["tests.test_pkg"]
"pkg.sub" = ["tests.test_sub"]

[[non_code_rules]]
prefix = "data/"
tests = ["tests.test_pkg"]
reason = "data input"

[prerequisites]
identity_commit = ["tests.test_sub"]
wheel_smoke = ["tests.test_pkg"]
```

- `module_tests` is the only module → test mapping. A shared module without a
  mapping escalates to the full suite (`strict_modules = false` opts out).
- `non_code_rules` are ordered; `exact` or `prefix` selects one. Unknown paths
  never silently select zero tests.
- `ignored_*` removes documentation/meta changes; those produce `no_tests`, not
  a pass.
- `prerequisites` names type signals (for example an approved identity commit or
  a wheel smoke test). The plan reports them; execution remains the consumer's
  job and is not implied by a test pass.
- No Knowledge path (`data/catalog/**`, `content/**`) is built into ECI. Its
  domain rules belong in its own config.

## Git identity

`changeset(root, base, head)` records the actual commits and tree:

- `base_commit` is the merge base, not a branch name; `head_commit` and `tree`
  are the resolved objects.
- Local runs additionally include staged, unstaged, renamed and untracked paths
  and set `worktree_dirty`. Index and worktree content are not treated as the
  same snapshot as a commit.
- A missing base/head or failed diff produces `complete: false`. Selection then
  escalates and never reports zero changes.
- Push/PR event objects and simulated merge trees are not implemented in this
  slice; callers must pass explicit commits they actually hold.
- Paths from NUL-separated Git reports are preserved exactly (backslashes are
  normalised only); leading spaces are not stripped.

### Snapshot modes and execution binding

`changeset(..., mode=...)` supports:

| Mode | Snapshot | Execution binding |
| --- | --- | --- |
| `worktree` | merge-base..HEAD plus staged/unstaged/untracked paths | `worktree_dirty`; no immutable tree |
| `index` | merge-base..index (`git diff --cached`), `index_tree` recorded | index tree is not the execution tree; treated as mutable |
| `commit` | merge-base..HEAD only | `execution_tree = HEAD tree` |
| `push` | explicit `before`/`after` commits; no merge-base | `execution_tree = after` tree |
| `pr` | `git merge-tree --write-tree` merge tree | `execution_tree = merge_tree` |

The plan records `git.snapshot`, `index_tree`, `merge_tree` and
`execution_tree`. `--run` passes `execution_tree` to `run_tests`; if the
checkout's current `HEAD` tree differs, execution returns `4` and does not run
tests. Mutable worktree/index snapshots explicitly have no immutable binding and
must not be reported as a commit-bound pass.

PR merge-tree requires a Git version with `merge-tree --write-tree`; conflicts
or missing objects produce `complete: false`, never a narrower pass. Multi-ref
pre-push batches are not implemented; a caller must issue one push snapshot per
ref pair and must not aggregate them into one plan identity.

## Per-check impact (not a global ignore)

Ignoring a path for business tests does not ignore it for every check. The plan
emits `check_scope` for each check type:

| Check | Selected when | `not_applicable` when |
| --- | --- | --- |
| `unittest` / `pytest` targets | Selection status is `targeted`/`escalated` | `no_changes`, `no_tests` |
| `docs` (Lychee) | A document changed, or a path was deleted | No document change and no deletion |
| `tach` | Code/config inputs changed | Documentation-only change |
| `vulture` | Code/config inputs changed | Documentation-only change |
| `source` | Paths under configured source prefixes changed | No source input |

A documentation-only change therefore produces `unittest: no_tests` and
`docs: selected` with the affected files. A deletion sets `recheck_all: true` so
unchanged documents that referenced the deleted target are still checked.

## Selection statuses

| Status | Meaning |
| --- | --- |
| `targeted` | Exact test modules with reasons and prerequisites |
| `escalated` | Full discovered suite because of a global/unknown/unmapped input |
| `no_changes` | No paths in the change set; nothing executed |
| `no_tests` | Changes exist but are ignored/non-code without test coverage; not a pass |

`--run` executes the selected targets with their declared framework
(`unittest` dotted modules, or one native `pytest` process for multiple files,
preserving its exit code). `no_changes`/`no_tests` return `3` (not executed)
unless `--allow-empty` is explicit. `--full` expands **tests only** to the full
discovered suite; it does not claim Tach/Lychee/Vulture/source/Required checks
ran. A plan is not execution evidence: Dagger, Actions and #63 must record
actual execution separately. A subset pytest run cannot satisfy a Required
full-coverage gate; that gate must run fully or consume #63 verified complete
evidence.

## Plan handoff

`--json` emits the plan:

```json
{
  "schema_version": 1,
  "kind": "engineering-ci-selection-plan",
  "generator": {"name": "engineering-ci", "version": "0.7.0",
                "config_sha256": "...", "tach_sha256": "..."},
  "git": {"base_ref": "origin/main", "head_ref": "HEAD",
          "base_commit": "...", "head_commit": "...", "tree": "...",
          "worktree_dirty": true, "changed_paths": ["src/pkg/sub.py"]},
  "selection": {"status": "targeted", "tests": ["tests.test_sub"],
                "checks": ["hygiene", "tach", "vulture"],
                "prerequisites": ["identity_commit"], "escalated": false,
                "escalation_reason": null, "full_tests": false,
                "reasons": {"tests.test_sub": ["..."]}},
  "check_scope": [
    {"name": "unittest", "status": "targeted", "inputs": ["tests.test_sub"]},
    {"name": "docs", "status": "not_applicable", "inputs": [],
     "recheck_all": false, "reason": "no document changes"},
    {"name": "tach", "status": "selected", "inputs": [], "reason": "code changed"}
  ],
  "inputs": {"config": {"path": "...", "sha256": "..."},
             "tach": {"path": "tach.toml", "sha256": "..."}}
}
```

Consumers must reject a plan whose `generator.version`/digests do not match the
ECI version they run and whose `git` identity is not the object they execute.
Dagger receives the plan and the consumer source; it executes the selected
checks/tests in containers and records native results. It must not recompute
selection from a container without `.git`.

### Tach graph validation

The TOML graph is never trusted on its own. `plan.selection` carries
`requires_tach_check` and a bound `tach_graph_validation`:

- without `--validate-tach`: `{"status": "not_executed"}`;
- with it: native `tach check` exit/status plus `tach_sha256` and
  `source_tree`;
- if either identity differs at plan time: `{"status": "untrusted",
  "reason": "tach_config_mismatch" | "source_tree_mismatch"}`.

A narrow reverse-closure selection is coverage evidence only when validation is
`passed` for the same Tach file and execution tree. `failed`, `unavailable`,
`not_executed` and `untrusted` must not be reported as a trusted narrow run.

## Shared implementation and Dagger import (P1-3)

The algorithms live once in `src/eci_core`. The root `engineering-ci` wheel
packages both `engineering_ci` and `eci_core` (`module-name` list), so a normal
install carries the shared implementation.

The Dagger module project is `engineering-ci-module` and its Python package is
`engineering_ci_module`, avoiding the top-level `engineering_ci` collision with
the installed wheel. `dagger-module.toml` includes the root `pyproject.toml`
and `src/eci_core/**`; `.dagger/pyproject.toml` depends on the root project via
a path source (lockfile updated). The module imports `eci_core` directly; there
is no copied selector.

Verified with the engine:

```text
dagger call shared-identity
# 0.7.0:1:engineering-ci-selection-plan

dagger call selection-self-test
# engineering-ci-selection-plan:targeted:tests.test_pkg
```

`selection-self-test` builds a disposable Tach/config/tests layout inside the
module runtime and calls `eci_core.selection.select`; it is an import/call
proof, not a claim that Dagger already executes the full consumer plan.
Tach/Lychee tool acquisition, Dagger check execution and Source construction
remain follow-ups.

## Plan execution

```sh
engineering-ci execute --plan selection-plan.json --root . \
  --junit pytest.xml --json
```

`execute` loads exactly the plan's targets and framework; it does not re-select.
For immutable snapshots it passes `execution_tree` to the native runner. If the
checkout `HEAD` tree differs, no test runs and the report is `not_executed`
(exit 4). Empty targets are `not_executed` (exit 3), never a pass. Test failure
keeps the native exit and is reported as `failed`.

The report (`engineering-ci-execution-report`) carries the plan's generator/Git
identity, actual targets and framework, JUnit path, native exit and status.
Selection status and execution status stay separate. This host executor is the
Actions-consumable path; Dagger plan consumption is still open.

## CLI

```sh
engineering-ci select --root . --config ci-selection.toml \
  --base origin/main --head HEAD --json
engineering-ci select --root . --base origin/main --run
engineering-ci select --root . --full --list
```

`--json`, `--list` and `--run` are mutually exclusive. `--full` expands tests
only and sets `selection.full_tests`; `check_scope` keeps reporting whether
documents/Tach/Vulture/source checks are applicable. It is not an
"all checks passed" claim.

`engineering-ci hygiene --full` is the separate contract: it expands applicable
hygiene checks (all active documents and code checks), not tests.

## Verification

`tests/test_selection.py` exercises a disposable Git fixture: discovery, Tach
reverse closure, module mapping, non-code rules, ignored paths, escalation,
deleted tests, unmapped modules, renames/untracked paths including leading
spaces, plan identity and per-check scope, unittest pass/fail propagation,
multi-target pytest execution and CLI modes.

## Review resolution (2026-11-08)

| Review item | Status in this branch |
| --- | --- |
| P1-1 per-check impact | Delivered: `plan.check_scope` separates tests, docs, Tach, Vulture and source; deletions set `recheck_all` |
| P1-2 source set reconciliation | Generic enumeration/diff/conflict/digest/attribute algorithm delivered in `eci_core.sources`; Knowledge config equivalence pending |
| P1-3 common package import in Dagger | Import and selector call delivered via `eci_core` + `engineering_ci_module`; Tach/Lychee acquisition and Source construction pending |
| P1-4 multi pytest targets | Local execution delivered as one native process with exit fidelity; Required full-coverage caveat documented; Dagger/Actions pending |
| P2-1 Git attributes host location | Pending with the source slice; must run where `.git` exists |
| P2-2 `--full` scope | Delivered: `selection.full_tests` is explicit; tests only, not an all-checks pass |
| P2-3 path fidelity | Delivered: no `.strip()`, leading-space and rename/delete-with-spaces NUL tests |

## Code-review resolution (2026-11-08 19:11)

| Finding | Status |
| --- | --- |
| P1-1 empty escalation ran as success | Fixed: `run_tests(())` returns 3; `--run` refuses any empty selection unless `--allow-empty` for genuinely no-applicable changes |
| P1-2 Tach graph trusted without native check | Delivered: `--validate-tach` binds native result to Tach digest + source tree; stale/unexecuted is explicit |
| P1-3 incomplete Git treated as not_applicable | `check_scope` emits `unknown` for docs/Tach/Vulture/source when the change set is incomplete |
| P1-4 event snapshots and execution binding | Partial: worktree/index/commit/push/PR merge snapshots and execution tree refusal delivered; multi-ref batching and Dagger/Actions consumption open |
| P2 framework identity | `check_scope` uses the configured framework name (`unittest`/`pytest`), not a fixed label |
| P2 pytest report/coverage | Local multi-target JUnit XML and native exit delivered; Dagger/Actions artifact and coverage-gate wiring open |
| Production Dagger/Actions plan execution | Partial: host `execute --plan` validates tree and runs targets with JUnit; Dagger consumption/aggregation is next |
| Clean-environment install and non-Knowledge consumer | Clean wheel install outside the checkout delivered; a real non-Knowledge consumer execution is still open |
