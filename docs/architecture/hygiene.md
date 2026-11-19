# Repository hygiene checks

Status: second delivered slice of [ECI #66](https://github.com/dymzz/engineering-ci/issues/66).
Dagger/Actions execution and domain source-manifest validation remain follow-up
slices.

`engineering-ci hygiene` runs the configured native checks once against the same
Git change set the selector uses, and returns structured evidence. Missing
tools, failed commands and malformed output are failures; absent applicable
input is `not_applicable`; Vulture findings stay advisory.

## Consumer configuration

`ci-hygiene.toml` (default):

```toml
schema_version = 1
checks = ["whitespace", "forwarding", "docs", "vulture", "tach"]

[docs]
suffixes = [".md", ".html"]
excluded_prefixes = ["docs/archive/"]

[lychee]
executable = "lychee"
arguments = ["--offline", "--no-progress", "--files-from", "-"]

[forwarding]
source_prefix = "src/"

[vulture]
executable = "vulture"
paths = ["src"]
min_confidence = 60
advisory_exit_codes = [3]

[tach]
executable = "tach"
path = "tach.toml"
external = false
```

Unknown keys and unsupported check names are refused. Paths are
repository-relative; no Knowledge directory (`data/catalog/**`,
`content/**`, `sources/**`) is built into ECI.

## Checks and activity

| Check | Runs when | Pass | Failure |
| --- | --- | --- | --- |
| `whitespace` | Always | `git diff --check <base>` exits 0 | Nonzero; whitespace diagnostics retained |
| `forwarding` | A changed file under `forwarding.source_prefix` exists | No module became a pure re-export | Base content unreadable or module unparsable |
| `docs` | Documents changed, a path was deleted, or `--full` | Lychee exits 0 for the active affected documents | Lychee missing, broken links, other native failure |
| `vulture` | Code changed (src/scripts/tests or tach/pyproject) | Exit 0, or exit in `advisory_exit_codes` (status `advisory`) | Other exit or missing tool |
| `tach` | Code changed | `tach check` exits 0 | Config missing, check fails, tool missing |
| `tach_external` | `external = true` and code changed | `tach check-external` exits 0 | Nonzero or missing tool |

- `docs` targets only active documents affected by the change (or all active
  documents for `--full`/deletions); docs excluded by the consumer are not
  silently checked or reported as covered.
- `forwarding` reports changed `src` modules that became pure re-exports while
  the base version was substantive. This is an advisory review item, matching
  the original native diagnostic; it does not fail the run.
- Forwarding proves the Git worktree context and the base Commit and Tree before
  classifying a path as new. A listed but unavailable Blob fails the Internal
  check. Its observation records the Git probe command, exit code, diagnostics,
  and available object IDs. The Internal Result's own exit code stays empty.
  An export without the relevant Git context blocks the check before analysis.
- Vulture exit `3` remains `advisory`, not `passed`; the report preserves the
  exit code, bounded output and findings. Other exits are failures.
- Each check executes at most once; no summary re-runs a scanner.
- The `git` block records the same merge-base/head/tree/worktree identity as the
  selection plan.

## Statuses

| Status | Meaning |
| --- | --- |
| `passed` | All executed checks passed or were advisory/not applicable |
| `failed` | At least one check failed, was unavailable or could not run |
| `blocked` | A selected check could not start because a required input or Git context was unavailable |
| `not_applicable` | Every configured check had no applicable input |

A `not_applicable` report is not a success claim for a protected check: the
caller/config must decide whether the check is required. The CLI exits `1` for
`failed` or `blocked`. A `not_applicable` report exits `0`.

## Native tool acquisition (no image pulls)

The host CLI executes `lychee`, `tach` and `vulture` from `PATH` (observed
Lychee `0.24.2`, Tach `0.35.3`, Vulture `2.16`). Missing tools are failures,
never `not_applicable`.

Dagger reuses the already cached `ghcr.io/astral-sh/uv:python3.14-trixie-slim`
image; it does not pull a new image for either tool:

- `tach-check` installs `tach==0.35.3` inside that container with `uvx` and
  runs `tach check`.
- `lychee-check` downloads the pinned Lychee `0.24.2` Linux release asset,
  extracts it to `/usr/local/bin/lychee` in the same container, then runs it.
- Source construction excludes only `.git`, virtualenvs and caches. `docs/**`
  and source inputs are retained, unlike the Python-check `SOURCE_EXCLUDES`.
- Empty `files` returns `not_applicable` without executing; a real broken link
  or failed `tach check` returns `failed` with native output.

Evidence from the engine:

```text
dagger call tach-check --source tests/fixtures/tach-ok
# {"status": "passed", "exit_code": 0, ...}

dagger call lychee-check --source . --files docs/architecture/selection.md
# {"status": "passed", "exit_code": 0, "version": "0.24.2", ...}
```

Vulture continues to reuse the existing `python_vulture` function; no second
scanner is added. The Plan result adapter delegates missing Git/Lychee to
containers and retains their native results. Online and offline Lychee cache
boundaries are defined by the [Dagger integration](integrations/dagger.md#plan-and-link-observation-cache-boundaries).

## CLI

```sh
engineering-ci hygiene --root . --config ci-hygiene.toml \
  --base origin/main --json
engineering-ci hygiene --root . --full
```

The report is `engineering-ci-hygiene-report` schema 1 with `git`, `checks`
(name/status/exit_code/reason/stdout/stderr/findings) and the overall `status`.
Outputs are bounded to 8 KiB per stream; truncation is visible as a shorter
tail, not as an empty success.

## Handoff and remaining work

The report is native host evidence for local runs. Dagger Plan execution
retains the selected checks, inventory and original tool results in one
Report/Artifact bundle. #66 target Source verification, #63 admission and
#61 Required coverage remain separate. Source-manifest reconciliation is
implemented in `engineering_ci.sources`; its Knowledge configuration is
verified separately.

### Forwarding across Host and Dagger (NEW-053)

**Delivery status (2026-11-19).** [PR #132](https://github.com/dymzz/engineering-ci/pull/132)
merged as `b10dce6ae39595d3422182bf73dfc545f77ffc10` (reviewed HEAD
`6757c2e3f6c157be4db5222a3d4b8aff7723e5b8`), completing the
NEW-053 Host Git Source/ODB false-pass, deletion-applicability, and internal-link
source-diagnostic repair slice. This is not a claim that #66-P0 historical Blob
readiness, independent linked-input Source closure, #86 positive Dagger/Host
Result handoff, or #63 Trusted Admission has been delivered.

[PR #139](https://github.com/dymzz/engineering-ci/pull/139) is the separate
#66 Forwarding AST migration-semantics change: `__all__` assignment handling,
package `__init__.py` exclusion, Base AST substance, and Check Definition v2.
It is not a prerequisite to #132's completed Git Source slice. Integrate #139
against the merged #132 code, retaining both sets of behavior after resolving
conflicts and running targeted combined verification. Until that integration,
the Forwarding AST implementation below reflects the #132 merged baseline,
not the pending #139 semantics.


The Host path reads the Base Commit and Tree in the checkout's Git context.
It uses exact Tree membership to distinguish a new path from an existing path
whose Blob is unreadable. A substantive module that becomes a pure re-export
produces an advisory. The Host also uses the existing Plan Source checks for
Index, Commit and Push snapshots before running the selected checks.

For an immutable snapshot, applicability uses exact membership in the target
Tree. Deletion claims are checked against the actual worktree or target Tree
before a path is removed from Forwarding scope.
A failed Tree listing leaves the input selected for native diagnostics.
Forwarding verifies the target Blob can be read and records
`current_content_unavailable` when the lookup fails, including when the
checkout still contains the file. A proven deletion is inapplicable.

The [original Knowledge check](https://github.com/dymzz/engineering-knowledge/blob/main/scripts/check_repo_hygiene.py#L103)
and earlier ECI followed links to existing Python files. Forwarding now
checks every link component and reads an internal target with its actual link
and file identities recorded. In an immutable snapshot, the link and target
must match their Git Tree entries and readable Blob objects. Worktree bindings
retain the link chain, resolved path and target digest, so changing the link
invalidates the Plan even if the replacement target has identical bytes.

Links outside the Source, into Git metadata, dangling links, cycles and links
whose identities cannot be verified fail with Source evidence. A checkout that
represents Git mode `120000` as a plain file still preserves the link mode.
For a `SourcePathError` during link resolution, the observation retains the
traversed links and the actual failure kind and component. Cycles and depth
limits have distinct `forwarding_link_cycle` and
`forwarding_link_depth_exceeded` reasons.
Linked inputs remain in `unverified_paths`, so the Native Result does not
claim a complete Source closure and #66-C cannot treat them as fully resolved.
The Base link case still requires its own Git-object target binding.
The #66-P0 follow-up must establish the link chain, Git entries, target Blob
and inspected bytes as an input closure before an internal-link `passed`
Result can reach an independent Source Resolution and #66-C `matched` Receipt.
Unrelated unverified Tree members must still prevent full Source closure.
The immutable snapshot lists every selected Git member before inspecting the
filesystem. A missing file or dangling link remains in that list and prevents
Execution from issuing `full_input_closure` / `byte_tree` for the snapshot.

The [Host evidence in PR #132](https://github.com/dymzz/engineering-ci/pull/132#pullrequestreview-5969567897)
includes an actual Plan with a missing historical Blob. The Execution Report
retains the failed Internal Result. #66-C Reconciliation records the same
Result and its Report, Run and Attempt references with an `insufficient`
binding and `unsatisfied` Expected satisfaction. The fixture
supplies the routing and Job Receipt attribution. Forwarding has no tool
requirements, so the local tool-requirement summary can be `not_applicable`
alongside an actual failed Result. The Expected-scoped Receipt carries the
satisfaction decision. Trusted Admission remains [#63](https://github.com/dymzz/engineering-ci/issues/63).

The `worktree_root` observation comes from Git's worktree and empty-prefix
probe. Plan/Expected Source authority comes from the upstream Source binding.
`base_object_oid` identifies the Git object. `base_compared_text_sha256` covers
the text passed to the parser after decoding and re-encoding. Use the object
OID for raw Git Blob identity and the text digest for the comparison input.

In Dagger, `plan_execution(plan, source)` accepts the Plan and the current
Directory. Its `source_handoff` records the original Plan Git identity and the
exported Directory's content identity. The Git runner can query objects
available inside that view. The export fixture without a matching Git context
demonstrates the `not_executed`/blocked behavior. Positive Forwarding handoff
remains open under [#66-P0](https://github.com/dymzz/engineering-ci/issues/66)
and [#86 A.1](https://github.com/dymzz/engineering-ci/issues/86).

Choose the handoff according to the Check policy, the actual consumer, Engine
and Source availability, and the cost of execution and caching. Prefer the
verified result when it satisfies the consumer and an authorized producer can
deliver it. Run in Dagger when the consumer requires that execution context.

- **Consume an executed Forwarding Result.** This route requires a Result
  handoff contract between #66 and #86. An authorized Source/Host producer
  would deliver the Native Result, its `source_ref`, Baseline and Rule, and
  Report/Artifact with the actual Origin, Run and Attempt. Today a Plan Data
  edge accepts a `prerequisite_action` producer, and a PASS edge from a Check
  uses a selected Check in the same Plan. The Dagger Check node runs
  `execution.run_host_check()`. Consuming a prior Result requires a verified
  input at that boundary, with #66-C structural binding and any required #63
  Trusted Admission. Preserve the original execution identity when consuming it.
- **Execute Forwarding in Dagger.** If the consumer requires Dagger
  execution, #66-P0 provides verified historical objects or equivalent
  content bound to the selected paths, Ref, Candidate, Directory and Baseline
  Commit/Tree. #86 verifies and delivers that input through the existing Plan
  Source/Data handoff and captures the Native Result with its actual Origin.
  History that is unavailable or bound to a different Source blocks the
  dependent Check.

`dagger_export` records Host origin for Forwarding's Host AST analysis and
`dagger` as its parent execution context. A native container Check records
the container execution origin. Preserve both facts when handing off results.

For the selected route, verify the Candidate/Directory/Ref and Baseline,
the input bytes or the authenticated Native Result, its actual Origin, and the
required Report/Artifact, #66-C and #63 consumption. Exercise an advisory for
a substantive Base, a passing comparison for an already-forwarding Base,
and rejection of a mismatched binding. Check that native failure propagates
to the dependent Check or gate. The accepted Host failure Receipt can supply
the Host evidence for that case.

### Historical inputs in the pre-execution proof (#66-P0)

`build_pre_execution_source_proof()` can report `verified_candidate_inputs`
and `check_input_closure` for Forwarding while a selected historical Blob is
unreadable. In a disposable Git repository, removing only the loose Blob
left the proof identical while `git cat-file -e` exited 1. The
`baseline_source_ref` identifies the resolved Commit and Tree. Local read
readiness needs separate evidence for the selected Blob members.

The [#66-P0 pre-execution follow-up](https://github.com/dymzz/engineering-ci/issues/66)
owns that proof boundary. Readiness for a new native execution needs proof of
the selected historical entries and their readable contents or verified
absence. Record an unverified readiness state when a required object is
unavailable. Cross-run equivalence can use authenticated historical read
evidence and immutable Git object identities without re-reading Blob bytes
in the current ODB. #63 T2 must verify that evidence against the current
Expected, Source, Baseline, Check, Rule, Tool, Environment, policy and
freshness. The existing comparison helper evaluates one Result's input
equivalence.

## Verification

`tests/test_hygiene.py` covers each check with a fake native runner while Git
commands run against a disposable fixture repository: clean/dirty whitespace,
not-applicable documents, one Lychee invocation with the active file list,
missing/broken Lychee, Vulture advisory vs failure, Tach missing config/failure,
forwarding advisory, report identity and CLI exit propagation.
