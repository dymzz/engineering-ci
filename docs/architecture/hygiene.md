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
| `not_applicable` | Every configured check had no applicable input |

A `not_applicable` report is not a success claim for a protected check: the
caller/config must decide whether the check is required. The CLI exits `1` only
for `failed`; `not_applicable` exits `0` but is labelled.

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
scanner is added. Full plan-driven hygiene orchestration (host change set +
acceptance of these native results) remains the next production step.

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

The report is native host evidence for local runs and #63 can consume its
identities/statuses. Dagger's `tach-check` / `lychee-check` are native building
blocks, but the plan-to-Dagger orchestration (source identity validation,
check list, aggregated evidence) is not wired yet. Source-manifest
reconciliation is implemented in `eci_core.sources`; its Knowledge
configuration is verified separately.

## Verification

`tests/test_hygiene.py` covers each check with a fake native runner while Git
commands run against a disposable fixture repository: clean/dirty whitespace,
not-applicable documents, one Lychee invocation with the active file list,
missing/broken Lychee, Vulture advisory vs failure, Tach missing config/failure,
forwarding advisory, report identity and CLI exit propagation.
