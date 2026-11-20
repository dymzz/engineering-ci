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

Data edges can supply generated files to a native scan through the verified
Producer output and Consumer View. The result records the generated input and
scan digest. Independent Resolution reports
`source_resolution_generated_inputs_unproven` for the affected Check until it
can verify the original Producer output. The #86 Data Dependency handoff
supplies the retained File or Directory and its Producer, Consumer and output
identity. The #66-P0/#66-C Source and Resolution handoff must verify those
bytes and reconstruct the Consumer View for that Check. The new proof needs
to match a real native scan and reject missing or mismatched Producer output.
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
