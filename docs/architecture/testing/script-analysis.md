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

## NOT VERIFIED

Nothing in this issue has been executed against the real tools yet. The
following are recorded as open, and none of them may be described as verified
until a real run says so:

1. **actionlint's exit-code semantics and output format are assumed.** This
   assumes `0` means clean, `1` means reported problems, and that a diagnostic
   line reads `<path>:<line>[:<column>]: <message> [<rule>]`. actionlint may
   also expose no structured output at all, in which case
   `native_json_available` is legitimately always false for it.
2. **No PSScriptAnalyzer run has been observed.** The committed fixtures assume
   that `PSAvoidUsingCmdletAliases`, `PSAvoidUsingWriteHost` and
   `PSUseDeclaredVarsMoreThanAssignments` fire on `violations.ps1`. Which
   default rules actually fire is a property of the analyzer.
3. **The Windows runner job has never executed.** The runner label, the pinned
   uv invocation, moving the helper checkout out of the workspace and the
   `PYTHONPATH` wiring are all unexercised.

Every classifier in both implementations fails closed: an unreadable envelope,
an empty script set, a missing module or a failed process produces a non-zero
exit and a failure bundle, never a clean result. A wrong assumption therefore
surfaces as a red check that prints the observed value, not as a silent pass.

## Workflow analysis

`actionlint_workflows` takes a source Directory, `scan_paths` defaulting to
`.github/workflows`, and an optional `config`. It returns a JSON report and
does not gate CI; callers raise on `outcome`.

ShellCheck and pyflakes are passed as explicitly empty, so the analysed scope
never changes with whatever happens to be present in the image. Only Workflow
structure is checked: inline scripts that were not analysed are never reported
as passing.

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

Scripts are parsed, never executed and never automatically fixed. The analyzer
module is materialised into a temporary directory outside the consumer's
checkout, so analysis never writes into the tree it inspects.

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