# Workflow and PowerShell analysis

actionlint and PSScriptAnalyzer are delivered as two separate execution
locations, chosen because the tools genuinely differ rather than for
convenience.

- **actionlint** is a Go binary that runs anywhere. It is exposed as a Dagger
  function alongside the other static checks and is selected by
  `dagger_checks`.
- **PSScriptAnalyzer** depends on a PowerShell environment and on rules that
  only mean something on one. It runs in a dedicated `windows-2025` job inside
  the reusable Workflow, invoked the same way the dependency-security job
  invokes its helper, rather than inside the Linux Dagger container.

There is deliberately no second PowerShell execution path. A Dagger-only
implementation would report Windows requirements it cannot honour, which is
worse than saying so.

## Evidence limits

The pinned Actionlint 1.7.12 was run against the committed clean and broken
Workflow fixtures: JSON mode returned `[]` with exit 0 for the clean fixture
and a diagnostic array with exit 1 for the broken fixture. Text mode returned
empty output and exit 0 for the clean fixture, and diagnostic lines with exit
1 for the broken fixture. The repository also retains a
[real Dagger clean-run bundle](../../verification/workflow-security/dagger-clean/README.md).
These observations do not prove that malformed output occurs in production.

The following PowerShell assumptions remain open:

1. **No PSScriptAnalyzer run has been observed.** The committed fixtures assume
   that `PSAvoidUsingCmdletAliases`, `PSAvoidUsingWriteHost` and
   `PSUseDeclaredVarsMoreThanAssignments` fire on `violations.ps1`. Which
   default rules actually fire is a property of the analyzer.
2. **The Windows runner job has never executed.** The runner label, the pinned
   uv invocation, moving the helper checkout out of the workspace and the
   `PYTHONPATH` wiring are all unexercised.

An unreadable envelope, an empty script set, a missing module or a failed
process cannot produce a clean check. The original process exit and bounded
output remain available in the report.

## Workflow analysis

`actionlint_workflows` takes a source Directory, `scan_paths` defaulting to
`.github/workflows`, and an optional `config`. The direct Dagger functions for
actionlint, ShellCheck and zizmor resolve each target in the mounted Directory
before invoking the native tool. Their reports include the Directory digest,
logical targets, resolved paths and SHA-256 digests. Callers gate on `outcome`.

Selection keeps rejected discovery candidates in the affected Check scope.
During discovery for a full or rule-triggered scan, an explicit file prefix
must resolve to a file. An empty directory prefix contributes no targets.
Host execution reports a refusal before the native process starts if a selected
source is unavailable. It stages the verified target bytes under the current
project and maps diagnostics back to the logical paths. Native output and exit
codes retain their original values.

Host runtimes can pass `admitted_external_targets` and a
`workflow_source_authority` to both execution and `build_source_resolution()`.
Each path authenticates the handoff for the original Plan digest, Git event and
Check. External claims name the logical path, resolved path and SHA-256.
Git-free materialization claims name the Git Tree and Directory digest. The
resolver verifies the supplied bytes independently and binds the observed
`scan_target` inputs to its receipt. The CLI and direct Dagger functions use
their supplied source without an external grant issuer.

A verified complete Git materialization can establish `full_input_closure`.
If it is supplemented by authorized external scan targets, the repository
digest and those target digests establish `check_input_closure` instead.
Worktree observations keep `changed_paths` Source coverage. Native results
retain the actual scan outcome, and strict reconciliation still requires a
closed Source.

Data edges can supply generated files and directories to a native scan through
the verified Producer output and Consumer View. When execution runs only with
temporary directories (`produced_artifacts_dir=None`), the result retains
`changed_paths` Source scope and records the generated input and scan target
digests while `build_source_resolution()` refuses with
`source_resolution_generated_inputs_unproven`.

When the host runner supplies `produced_artifacts_dir` (or Dagger `PlanExecution`
exports `artifacts`), verified Data-edge Files and Directories are retained under
`produced/<kind>/<name>/<rel>` with re-verified SHA-256 digests and recorded in
`report["producer_artifact_handoff"]` bound to the Plan digest, Git event and
candidate, Producer/Consumer/Output Ref, original Prerequisite Result and
Artifact Closure digests, and Run/Attempt identity when supplied.
After temporary execution directories are cleaned up, `build_source_resolution()`
(and the non-raising `evaluate_source_resolution()` helper for `#66-C`
reconciliation) requires `producer_artifact_handoff`, `producer_artifacts_dir`,
and runtime `"producer_artifact"` authorization via `producer_artifact_authority`
(for example `producer_artifact_authority_from_execution()`) or
`workflow_source_authority`. Verification validates internal Producer execution
consistency (`type`, `status`, integer `exit_code == 0`, `reason is None`,
`execution_ref`, and original Prerequisite/Closure digests) and rejects omitted
or mismatched `expected_run_ref`/`expected_attempt_ref` (or
`execution_context_identity`) whenever the Handoff or consuming Job context
carries a Run/Attempt while keeping identity-free local execution working. It
independently re-reads and verifies the retained File/Directory bytes,
reconstructs the Consumer View via `build_consumer_view()`, establishes
`check_input_closure` on verified immutable snapshots while keeping
`changed_paths` (`expected_source_scope_insufficient`) on open `worktree`
workflow scans, and separates missing/unverified proof (`unknown`) from
tampered/mismatched/truncated evidence (`insufficient`) without erasing native
PASS/FAIL results or tainting adjacent Checks without Data edges.
See [#86](https://github.com/dymzz/engineering-ci/issues/86) and
[#66](https://github.com/dymzz/engineering-ci/issues/66).

Actionlint's inline ShellCheck integration is opt-in. The actionlint command
sets the configured ShellCheck behavior explicitly and disables pyflakes.
When JSON mode is requested, an empty, malformed, non-array or over-limit
response is an invalid result rather than a text-format fallback. In text mode,
nonempty output without recognizable diagnostics is also invalid. If the
process exits 0 but the response cannot establish a verdict, its Native Result
is `unknown` with the
original exit and output, and Execution blocks the check. The inline ShellCheck
check cannot use that response as a clean verdict. Actionlint and inline
ShellCheck definition version 2 require these rules; version 1 results remain
readable but cannot satisfy the version 2 Checks.

### NEW-049 acceptance — Actionlint verdict provenance

[PR #140](https://github.com/dymzz/engineering-ci/pull/140) repairs the
[Issue #58 NEW-049](https://github.com/dymzz/engineering-ci/issues/58)
path where a JSON-requesting Actionlint run with exit 0 could fall back to
unrecognized text and be misreported as clean.

- **Producer:** Host and Dagger retain the native exit and bounded stdout /
  stderr. In JSON mode only a complete Actionlint array can establish a
  structured verdict; over-limit output remains invalid. Text mode accepts
  valid native diagnostics and truly empty clean output, not arbitrary text.
- **Validator:** exit 0 with parsed findings is inconsistent. Invalid output
  cannot become a valid clean verdict, even when the tool process succeeds.
- **Consumer:** Host Execution retains the actual native exit with a
  non-passing `unknown` Result, blocks the Report, and exposes
  `limitations`/`native_truncated`. Shared inline ShellCheck must not
  inherit an invented clean verdict. #66-C does not select the Result as a
  passing witness; current `actionlint` and `workflow_shellcheck`
  Check Definitions are version 2, while version 1 evidence remains readable
  but cannot satisfy those new definitions.

**Evidence and limits:** Actionlint 1.7.12 was exercised on real clean and
broken Workflow fixtures, and the unrecognized-output failure was reproduced
with a controlled anomalous Runner through Native Result → Source Resolution →
Expected/Reconciliation. [PR CI run #44686859634](https://github.com/dymzz/engineering-ci/actions/runs/44686859634)
completed successfully (four jobs). This supports the contract fix, **not**
a claim that the pinned native Actionlint produced such malformed output in
production. The Dagger API returns a Report and its caller must gate on
`outcome`; a successful Dagger function invocation is not itself a passed
Check. This fix does not complete unrelated Issue #58 integrations.

## PowerShell analysis on Windows

| Input | Default | Meaning |
| --- | --- | --- |
| `powershell_mode` | `off` | `off`, `report` or `enforce` |
| `powershell_paths` | `.` | Repository-relative files or directories |
| `powershell_settings` | empty | Native PSScriptAnalyzer Settings file |
| `powershell_severity` | empty | Native severity filter |
| `powershell_source_ref` | `v0.4.7` | Pinned helper ref |
| `powershell_artifact_name` | `powershell-analysis` | Evidence artifact name |

`off` analyses nothing. `report` keeps rule violations without blocking CI.
`enforce` blocks on anything that is not a clean analysis, which deliberately
includes an empty script set: an unanalysed scope is not a pass.

Scripts are parsed, never executed and never automatically fixed.

## Optional, and not a dependency of this repository

PowerShell analysis is opt-in and nothing in this repository turns it on:
`powershell_mode` defaults to `off`, the job is skipped entirely in that case,
and this repository's own Workflow does not enable it.

The analyzer is resolved rather than assumed. PowerShell's own discovery runs
first, and an installed module whose version equals the pin is used as-is.
Only when no matching module is present is one fetched from the PowerShell
Gallery into a temporary directory outside the consumer's checkout, so analysis
never writes into the tree it inspects. `module_source` in the report and the
Job Summary records which of `preinstalled`, `cached` or `downloaded` was
actually used, so the evidence shows whether anything was fetched at all.

There is no PyPI distribution of PSScriptAnalyzer, so it cannot share the uvx
route the Python scanners and actionlint use. actionlint itself does use it:
`uvx --from actionlint-py==...` fetches from PyPI through uv.

The Windows job checks out the consumer repository and a pinned engineering-ci
helper, moves the helper out of the workspace so only the consumer's own
scripts are reachable, and runs
`python -m engineering_ci.workflow_actions powershell`. It publishes a Job
Summary and uploads `report.json` and `summary.md`; uploading evidence never
changes the check result.

## Distinguishing the signals

PowerShell exits zero even when rules were violated, so the process status only
proves the invocation survived. The diagnostic objects decide findings, an
empty scope, and execution failure. Separately, preparing the pinned module is
its own step, so a failed download stays distinguishable from a rule violation.

An unsupported scope is stated rather than skipped: `analyze_script_scope`
routes by explicit file suffix and reports anything it cannot check as
`not_executed` with the reason, including PowerShell paths that belong to the
Windows runner.

## Boundary crossing to record

Issue #46 states that reusable Workflow selection, gating, Runner scheduling and
Artifact delivery belong to the integration issue. The Windows job deliberately
crosses that line so GitHub Actions can actually exercise the analyzer, which
was an explicit instruction. The integration issue now owns Linux-side
selection and Report/Enforce for the Python scanners; this issue owns the
Windows job.

Bash and sh scripts remain out of scope, and Workflow inline scripts are never
extracted for a second scan.
