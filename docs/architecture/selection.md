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
                "escalation_reason": null, "reasons": {"tests.test_sub": ["..."]}},
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

## CLI

```sh
engineering-ci select --root . --config ci-selection.toml \
  --base origin/main --head HEAD --json
engineering-ci select --root . --base origin/main --run
engineering-ci select --root . --full --list
```

`--json`, `--list` and `--run` are mutually exclusive. `--full` expands tests
only; it is not an "all checks passed" claim.

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
| P1-2 source set reconciliation | Not yet: next slice (`sources` module with enumeration/diff/conflicts/attributes) |
| P1-3 common package import in Dagger | Import and selector call delivered via `eci_core` + `engineering_ci_module`; Tach/Lychee acquisition and Source construction pending |
| P1-4 multi pytest targets | Local execution delivered as one native process with exit fidelity; Required full-coverage caveat documented; Dagger/Actions pending |
| P2-1 Git attributes host location | Pending with the source slice; must run where `.git` exists |
| P2-2 `--full` scope | Delivered: tests only, explicitly not an all-checks pass |
| P2-3 path fidelity | Delivered: no `.strip()`, real NUL-separated leading-space test |
