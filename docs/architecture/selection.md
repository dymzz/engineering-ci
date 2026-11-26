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

[prerequisite_actions.identity_commit]
type = "git_commit_present"
commit = "0123abcd..."

[prerequisite_actions.wheel_smoke]
type = "command"
argv = ["python", "-m", "your_wheel_smoke"]
```

- `module_tests` is the only module → test mapping. A shared module without a
  mapping escalates to the full suite (`strict_modules = false` opts out).
- `non_code_rules` are ordered; `exact` or `prefix` selects one. Unknown paths
  never silently select zero tests.
- `ignored_*` removes documentation/meta changes; those produce `no_tests`, not
  a pass.
- `prerequisites` names type signals (for example an approved identity commit or
  a wheel smoke test). `prerequisite_actions` supplies consumer parameters so
  host/`execute-plan` can run them: `git_commit_present` or `command`. Missing
  parameters stay `not_executed` and block a pass; ECI never hardcodes Knowledge
  SHAs or CLI strings.
- An explicitly source-only v2 producer may use `dagger_command` with pinned
  image/platform and cache eligibility. Its declaration, Dagger-only execution
  and refusal boundaries are defined in the [Dagger integration](integrations/dagger.md#plan-and-link-observation-cache-boundaries).
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
| `worktree` | merge-base..HEAD plus staged/unstaged/untracked paths | `content_binding` path digests; drift → `worktree_snapshot_stale` |
| `index` | merge-base..index (`git diff --cached`), `index_tree` recorded | `execution_tree = index_tree`; live `write-tree` must match |
| `commit` | merge-base..HEAD only | `execution_tree` + `head_commit` must match checkout |
| `push` | explicit `before`/`after` commits; no merge-base | `execution_tree = after` tree + `head_commit = after` |
| `pr` | `git merge-tree --write-tree` merge tree | checkout must materialize `merge_tree` or `pr_merge_tree_unmaterialized` |
| `multi` | union of several push `before:after` pairs; `references[]` recorded | no single execution tree; per-ref execution required |

The plan records `git.snapshot`, `index_tree`, `merge_tree`, `execution_tree`,
`content_binding` (worktree) and `source_tree_id` (immutable trees). Host
`execute` verifies the binding before tests/checks and passes `expected_tree`
into `run_tests` when immutable. Dagger `execute-plan` compares the mounted
`source` Directory to `source_tree_id` when supplied. The byte comparison sets
`tree_verified`; a mismatch is `source_tree_mismatch`. Native test diagnostics
can also run with Index identity alone, in which case the result leaves the
byte comparison `not_requested`. `PlanExecution.enforce` interprets the native
verdict. A Git Hook using that result as a check of the captured Index also
requires an independently verified byte binding and same-Invocation
reconciliation. Evidence admission is a separate #63 decision.

PR merge-tree requires a Git version with `merge-tree --write-tree`; conflicts
or missing objects produce `complete: false`, never a narrower pass. Multi-ref
pre-push batches produce a union selection plus `references[]`, but the
executor refuses to attribute one run to them: `multi_ref_requires_per_ref_execution`.
Hook `pre-push` already implements per-ref planning, isolated execution, and
per-ref receipts in `engineering_ci.hook_checks` (`prepare_push()`,
`run_push_checks()`, `reconcile_push()`, `consume_push_result()`) invoked by
`lefthook.yml`'s `pre-push-checks`. The repository enables it via the top-level
`pre-push` job and tracks the local push policy in `ci-selection.toml`.
Push Profiles bind an action and exact Ref or a Ref prefix ending in `/*`.
Every captured Ref must match a Profile. `[push].select_tests = false` limits
test selection for this push policy. `[commit].select_tests = false` likewise
disables Index / pre-commit test selection so activating a root
`ci-effective-profile` for low-cost hygiene does not escalate ordinary commits
into full pytest / Dagger. Other snapshots and explicit full-suite selection
still use the configured framework. Generic non-Hook multi-ref execution
remains a per-ref caller responsibility.

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

The algorithms live once in `src/engineering_ci`. The root `engineering-ci` wheel
installs this single Python package, including selection, execution, hygiene,
sources and CI/CD telemetry. The former `eci_core` import namespace is removed;
Python callers migrate to `engineering_ci.<module>`. CLI subcommand names and
Plan/Report schemas retain their existing contracts.

The Dagger module project is `engineering-ci-module` and its Python package is
`engineering_ci_module`, avoiding the top-level `engineering_ci` collision with
the installed wheel. `dagger-module.toml` includes the root `pyproject.toml`,
README and `src/engineering_ci/**`; `.dagger/pyproject.toml` declares the root
project as a local path dependency. The module imports the installed
`engineering_ci` package without adding checkout directories to `sys.path`.
There is no copied selector. The package contains no link to external agent
hooks or automatic agent hook registrations. SDK dependencies stay in the
Dagger project.

Verified with the engine:

```text
dagger api call shared-identity
# 0.7.0:1:engineering-ci-selection-plan

dagger api call selection-self-test
# engineering-ci-selection-plan:targeted:tests.test_pkg
```

`selection-self-test` builds a disposable Tach/config/tests layout inside the
module runtime and calls `engineering_ci.selection.select`; it is an import/call
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
(exit 4). Empty targets with no selected/unknown checks and no prerequisites
are `not_executed` (exit 3), never a pass. When `check_scope` still selects
docs/tach/vulture/source, host `execute` runs those native checks even with
zero business tests (`--hygiene-config` / `--sources-config`, or plan
`inputs.*.path`). Index snapshots bind `execution_tree` to `index_tree` and
refuse a stale `git write-tree`. Test failure
keeps the native exit and is reported as `failed`.

The report (`engineering-ci-execution-report`) carries the plan's generator/Git
identity, actual targets and framework, JUnit path, native exit and status.
Selection status and execution status stay separate. This host executor is the
Actions-consumable path.

If the plan has selected/unknown checks or non-empty prerequisites, test
success alone is `blocked` / exit `5` with reason
`required_checks_not_executed`; `checks[]` and `prerequisites[]` record
`not_executed`, while the native test result is preserved. Only a plan with no
unexecuted required work can be `passed`.

If the consumer sets `coverage_required = true`, the plan carries it and
`execute` returns `blocked` / exit `5` with reason
`coverage_evidence_required`; no tests run. A subset run, and even a full test
run without coverage evidence, cannot satisfy the gate. Actual
coverage/min_coverage execution or #63 verified complete evidence is still
required before this can become a pass.

Dagger also has `execute-plan(plan: File, source: Directory)`: it validates the
plan kind, refuses multi-ref unions and empty targets, runs the exact targets
with `uv run` in a synced consumer environment, and returns native exit/stdout/
stderr plus pytest JUnit. Because the Dagger Source excludes `.git`, it cannot
verify `execution_tree`; the report sets `tree_verified: false` and
`trusted: false`. A trusted pass must use the host executor or a future
Source-identity binding.

Actions workflow wiring is not in this slice.

### Actions

`reusable-python-ci.yml` now has an opt-in `selection` job (default disabled by
empty `selection_config`):

- inputs: `selection_config`, `selection_source_ref`,
  `selection_artifact_name`;
- checks out the consumer and the pinned engineering-ci ref;
- installs pinned `tach==0.35.3` and runs `select --validate-tach`, so
  `tach_graph_validation` is bound to the same Tach file/tree;
- supports PR merge, push before/after and workflow_dispatch commit modes;
- syncs the consumer uv environment, installs the pinned CLI into it;
- runs `engineering-ci select` then `engineering-ci execute --plan`, uploading
  `selection-plan.json`, `selection-execution.json` and `selection-junit.xml`;
- output: `selection_artifact_id`.

This reuses the existing reusable workflow rather than adding a new runner.
`actionlint -shellcheck=` passes. Tach validation and dispatch modes are wired,
Host `execute` and Dagger `execute-plan` run selected docs/tach/vulture/source
checks and typed `prerequisite_actions` when parameters are present; missing
prerequisite parameters still return `blocked`. A real opt-in selection Run has
not succeeded yet.

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
| P1-2 source set reconciliation | Generic enumeration/diff/conflict/digest/attribute algorithm delivered in `engineering_ci.sources`; Knowledge config equivalence pending |
| P1-3 common package import in Dagger | Import and selector call delivered via `engineering_ci` + `engineering_ci_module`; Tach/Lychee acquisition and Source construction pending |
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
| P1-4 event snapshots and execution binding | Partial: worktree/index/commit/push/PR merge/multi-ref selection and execution tree refusal delivered; per-ref execution and Dagger/Actions consumption open |
| P2 framework identity | `check_scope` uses the configured framework name (`unittest`/`pytest`), not a fixed label |
| P2 pytest report/coverage | Local multi-target JUnit and explicit `blocked` coverage gate delivered; actual coverage execution / #63 evidence wiring open |
| Production Dagger/Actions plan execution | Partial: host `execute --plan` is tree-bound; Dagger `execute-plan` runs exact targets/JUnit but reports `trusted:false`; Actions job added but not yet run |
| Clean-environment install and non-Knowledge consumer | Delivered: wheel install outside checkout; independent fixture `select → execute` passed 3 tests |


## Plan dependency contract (schema 2)

New Plan producers emit schema 2 with `dependency_contract.schema_version=1`.
Configuration and Execution Report schemas remain 1. Version-1 Plans without
this contract retain legacy behavior. CLI, direct Python and Dagger use the
same public `execution.validate_plan` rules before starting consumers.

The currently delivered dependency semantic is
`prerequisite_policy_aggregation`: each selected action contributes its
post-check result to `report.prerequisites`. References bind the action name,
Report consumer, configuration path/hash/section and original Git Plan binding.
Missing action parameters stay unresolved and cannot make the final Report
pass. These declarations do not turn test selection relations into startup
preconditions. True `data` or `pre_execution_pass` edges are unsupported until
B supplies their consumers; all entry points reject them instead of ignoring
mandatory semantics. Unknown versions, missing references/conditions and
changed action overrides are likewise refused.

Check scope status accepts the current producer's seven values: `selected`,
`unknown`, `not_applicable`, `targeted`, `escalated`, `no_tests`, `no_changes`.
The last four describe the test-selection scope; explicit full selection is
represented as `escalated`. Unknown values refuse with
`check_scope_status_unsupported`, before a Check can be silently omitted.

Executable snapshot identities are `worktree`, `index`, `commit`, `push`,
`pr_merge`, `multi`; CLI mode `pr` produces `pr_merge`. Unknown explicit values
refuse with `git_snapshot_unsupported`. Missing/null legacy snapshot retains
the existing default: commit when `execution_tree` exists, worktree otherwise.
The binding helper also refuses unknown modes before querying Git. Accepting
the `multi` declaration does not enable single-source multi-ref execution.

`plan_declaration_consistent=true` records schema-2 declaration validation
only. Rejected Plans report false; legacy Plans without the dependency
contract report null. `source_binding_verified="unknown"` is a separate,
explicit state: comparing two hashes inside a Plan does not verify the
configuration bytes, original ODB or Source consumed by a Check. Existing
tree/input-closure checks retain their narrower meaning. B must consume #66's
verified Source/Prerequisite handoff and check actual configuration hashes,
snapshot identity and downstream inputs before claiming that binding.

For exported Source, `source_handoff` preserves original Git identity and the
actual exported content ID. Git-dependent actions require their real ODB:
missing/unbound context is not a missing Commit verdict. A Host write to
exported Source invalidates its old binding before downstream consumers; this
slice delivers no generated output or trust receipt. Detailed instance scope,
raw results and remaining handoffs are in the
[dependency inventory](../verification/plan-dependencies/README.md).

The [Forwarding handoff](hygiene.md#forwarding-across-host-and-dagger-new-053)
records the remaining #66-P0/#86 A.1 work to consume verified Host Results or
deliver verified historical inputs when Dagger must execute Forwarding.

The read-only Dagger API may exit 0 while returning a non-passed Report.
`PlanExecution.enforce` now consumes Report status and its original aggregate
exit code; an API exit-0/refused-Report fails that entry. This is a verdict gate
on the provided Source computation, not #66 target-Source verification, #63
evidence admission or #61 Required coverage. A returned Result, especially a
cached Result, is not proof of a new tool execution in this invocation.

### Native Plan result object (#86 B delivery & v2 dependency closure)

`plan-execution(plan, source)` returns a `PlanExecution` with one real
`artifacts` Directory. `report` reads its `report.json`; `enforce` checks the
Report kind/version, status, integer exit and matching `exit-code.txt`, raising
on failed/blocked/not-executed results. `execute-plan` remains the convenience
Report API and does not become a strict process gate.

In `selection.py`, optional `[[dependency_edges]]` tables activate active edges
for selected checks (`check_scope` non-test entries + `tests` when test targets
exist) and emit `dependency_contract.schema_version = 2`; configurations without
`[[dependency_edges]]` retain `schema_version = 1` (`policy_only`). Unresolved
declared actions stay `unresolved`.

In Dagger `plan-execution`, v2 `pre_execution_pass` and `data` edges run
concurrently before dependent consumers, while independent consumers proceed
without waiting on unrelated producers. `command` prerequisite actions execute
under a module-local `/scratch` directory (`execution_location =
"dagger_module_scratch"`, `result_origin = "module_local_subprocess"`,
`engine_cached = false`), materialize outputs into immutable Dagger Engine
`File`/`Directory` objects via `dag.current_module().workdir`/`workdir_file`
and `sync()`, verify consumer path conflicts/overlaps/digests, overlay them
onto immutable consumer `Directory` inputs (`tests`, `docs` with container
`git`/`lychee` fallback via `_exec_capture`, `weaver_*`), and publish them
under `produced/<kind>/<name>/<rel>`. Host source writes remain refused,
`#66` receipts, `#87` Trace/Origin attribution, and `#63`/`#61`
Admission/Required decisions remain separate; remaining applicable producers
and Native DAG edges belong to follow-up `#86 B`, while `#86 D` refines input
closure, cache hit/invalidation, and external URL/side-effect policy. See also
[native result-sharing verification](../verification/plan-result-sharing/README.md).

The delivered online-Lychee slice of #86 D.1 refreshes the external-read Exec
while retaining deterministic native layers. Plan entrypoints reevaluate each
request; readers of the saved result do not rerun tools. Cache policy and
observation fields are defined in the [Dagger integration](integrations/dagger.md#plan-and-link-observation-cache-boundaries).
The broader per-Check input audit, Module Include measurements and remaining
applicable production DAG edges stay open under #86 B/D.

The mixed-Plan review repair adds an explicit native prerequisite producer
inside the same v2 data closure. Ordinary `command` actions remain
module-local and execute for every new Plan; only declared source-only
`dagger_command` computation receives independent native Exec caching.
See the [mixed-Plan observations](../verification/dagger-input-cache/mixed-plan/README.md)
for actual request/reader counts and failure preservation.

### Repository boundary mapping & pytest target binding (#162 A2)

- Root `ci-selection.toml` registers `tach = "tach.toml"`, `strict_modules = true`,
  `[module_tests]` for all `src/engineering_ci` modules, explicit `[check_scope]`
  (`code_prefixes = ["src/", "scripts/", "tests/", ".dagger/src/"]`,
  `shell_prefixes = ["scripts/"]`), and `[[non_code_rules]]` mapping
  `.dagger/src/engineering_ci_module/*`, `scripts/*`, `config/*`, and test
  fixtures while retaining Index/push `select_tests = false` and #166 revision-3
  governed hook profiles (`declared_checks = ["whitespace", "forwarding", "actionlint", "shellcheck", "zizmor"]`).
- Schema 1 omitted `[check_scope].code_prefixes` keeps its historical default
  `("src/", "scripts/", "tests/")` across config parsing and identity
  reconstruction; explicit `.dagger/src/` inclusion lives in `[check_scope]`.
- When `framework = "pytest"`, `_test_modules`, `_tree_test_modules`, direct
  test matching, and per-ref source snapshots honor explicit list
  `[tool.pytest.ini_options].norecursedirs` entries in `pyproject.toml`
  (excluding `tests/fixtures/` from root collection while preserving standalone
  fixture project test discovery). This helper is scoped to `pyproject.toml`
  `norecursedirs` rather than acting as a full `pytest.ini` / `setup.cfg`
  parser; unreadable or malformed `pyproject.toml` falls back safely without
  claiming complete pytest discovery proof.
- Host `run_tests(..., framework="pytest")` and Dagger Plan-selected
  `plan_execution._tests` / `EngineeringCi._run_pytest` keep
  Selection's dotted test module identities in Plans and resolve them (plus
  optional `::nodeid` suffixes) **once** via `selection.resolve_pytest_targets`
  into bound repository-relative file or directory paths inside the bound Source
  without `--pyargs`, placing `--` after all pytest options (`-q`, optional
  `--junitxml`) before resolved targets. `plan_execution._tests` hands off the
  already-bound CLI targets via private `_resolved_targets` so `_run_pytest`
  neither re-exports the Source nor re-interprets explicit directory paths
  (such as `./chosen` alongside `chosen.py`, or `./sub.pkg` alongside
  `sub/pkg.py`) as dotted modules; direct `_run_pytest(..., _targets=...)`
  still resolves bound targets once on its own. Single-segment non-`.py` path
  targets and `@`-prefixed targets retain an explicit `./` prefix (`./chosen`,
  `./sub.pkg`, `./@args.py`, `./@dir/test_in_dir.py`) for idempotency and to
  disable `pytest 9.0.3` `fromfile_prefix_chars="@"` expansion after `--`.
  Legitimate `PYTHONPATH` is preserved while external packages and `@argsfile`
  expansion cannot shadow or substitute for bound repository test targets;
  out-of-source, escaping symlink, whitespace-padded, pre-/post-normalization
  Windows drive/UNC (`./C:/outside`, `./C:outside`), and option-like targets
  (`./--collect-only`, `./--help`, `./--version`, ` -q`) are refused before
  spawning pytest (`source_refusal` on Host, `not_executed` on Dagger Plan
  execution, or `ValueError` on direct `_run_pytest`), while missing non-option
  in-source targets fail natively with pytest exit `4`. Generic consumer
  `python_test` retains the `pytest_arguments` CLI shape while neutralizing
  leading `@` and rejecting post-normalization leading `-` and Windows drives
  in `test_policy`. `unittest` semantics stay unchanged. Any non-`tach.toml`
  `./tach.toml`) is refused fail-closed matching native `tach 0.35.3` discovery.
- **Adoption scope (R15 independent slice):** this delivers selection mapping,
  hygiene applicability, root `tach.toml` / `ci-selection.toml` artifacts, and
  host-native `tach check` / `tach check-external` evidence when those tools are
  invoked. Default Index/push Profiles and Verify Jobs do **not** enable Tach,
  Lychee, Vulture, or incremental `select_tests = true`; Hook/Verify default
  enablement is a later adoption slice.
- `selection._tach_check` and `hygiene._tach` remain independent native runs
  (and `tach_external` stays independent). Plan `selection.tach_graph_validation`
  does not skip native `tach check` in execution; `AC-06` equivalent
  deduplication remains open for a future production consumer under
  `#52` / `#66` / `#63` / `#162 B2/D`.
- `expected_checks.derive_expected_checks` / `validate_projection` forward
  `hygiene_config_path` and per-ref `base_commit` / `head_commit` so single-ref
  and per-ref projections agree on config-triggered hygiene applicability.
