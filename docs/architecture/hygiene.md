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

The Host path reads the Base Commit and Tree in the checkout's Git context.
It uses exact Tree membership to distinguish a new path from an existing path
whose Blob is unreadable. A substantive module that becomes a pure re-export
produces an advisory. The Host also uses the existing Plan Source checks for
Index, Commit and Push snapshots before running the selected checks.

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
available inside that view. The #66-P0 Host proof records a Baseline reference
from the Commit and Tree, and `git_commit_present` checks presence in the Host
ODB. The Dagger entry still needs a producer to supply historical Blob bytes.
The existing [#86 Data edges](selection.md#native-plan-result-object-86-b-delivery--v2-dependency-closure)
can deliver verified artifacts when a producer exists. The repository currently
has no Forwarding producer that delivers authorized historical inputs bound
to the current Candidate, Directory and Baseline. The export fixture without a
matching Git context demonstrates the `not_executed`/blocked behavior.

The positive Dagger path remains tracked under [#66-P0 Source](https://github.com/dymzz/engineering-ci/issues/66)
and [#86 A.1 Git ODB and Source handoff](https://github.com/dymzz/engineering-ci/issues/86).

- [#66-P0](https://github.com/dymzz/engineering-ci/issues/66) owns the Source
  producer in the authorized Git context. Its handoff must bind the selected
  scope and target Candidate/Directory to the Baseline Commit and Tree, the
  exact selected paths, and verified Blob identities and bytes or proven
  absence. The consumer needs the historical input itself and its provenance.
- [#86 A.1](https://github.com/dymzz/engineering-ci/issues/86) owns delivery of
  that prerequisite to the Forwarding consumer through the existing Plan
  Source/Data handoff. Verify the received payload and its scope, Candidate,
  Directory and Baseline bindings before comparison. The selected Forwarding
  check remains blocked when the required history is unavailable or mismatched.
- To accept the positive path, run a Dagger Plan with an authorized historical
  input and a current file whose Base was substantive. Verify the advisory,
  exact historical Blob and current-file identities, and the Native Result
  carried through Report/Artifact and #66-C. Also verify that an already
  forwarding Base can pass, and that a mismatched Candidate, Directory or
  Baseline cannot supply historical input for a passing Check.

## Verification

`tests/test_hygiene.py` covers each check with a fake native runner while Git
commands run against a disposable fixture repository: clean/dirty whitespace,
not-applicable documents, one Lychee invocation with the active file list,
missing/broken Lychee, Vulture advisory vs failure, Tach missing config/failure,
forwarding advisory, report identity and CLI exit propagation.
