# Workflow and PowerShell analysis

actionlint and PSScriptAnalyzer are delivered as two separate execution
locations, chosen because the tools genuinely differ rather than for
convenience.

- **actionlint** is a Go binary that runs anywhere. It is exposed as a Dagger
  function alongside the other static checks, and the repository's own
  `workflow-analysis` Check is its strict form.
- **PSScriptAnalyzer** depends on a PowerShell environment and on rules that
  only mean something on one. It runs in a dedicated `windows-2025` job,
  invoked the same way the dependency-security job invokes its helper, rather
  than inside the Linux Dagger container.

There is deliberately no second PowerShell execution path. A Dagger-only
implementation would report Windows requirements it cannot honour, which is
worse than saying so.

## Verification status

1. **actionlint's exit-code semantics and text output format are observed.**
   The `fixture_workflow_analysis` Check runs the pinned tool against the
   committed clean and broken Workflows and asserts both outcomes, and the
   observed diagnostic format is the one this implementation parses:
   `<path>:<line>[:<column>]: <message> [<rule>]`. actionlint exposes no
   structured output, so `native_json_available` is legitimately always false
   for it. The pinned version is asserted from the tool's own `-version`
   output.
2. **No PSScriptAnalyzer run has been observed.** The committed fixtures assume
   that `PSAvoidUsingCmdletAliases`, `PSAvoidUsingWriteHost` and
   `PSUseDeclaredVarsMoreThanAssignments` fire on `violations.ps1`. Which
   default rules actually fire is a property of the analyzer.
3. **The Windows runner job has never executed.** The runner label, the pinned
   uv invocation, the `PYTHONPATH` wiring and the analysed script set are all
   unexercised.

Every classifier in both implementations fails closed: an unreadable envelope,
an empty script set, a missing module or a failed process produces a non-zero
exit and a failure bundle, never a clean result. A wrong assumption therefore
surfaces as a red check that prints the observed value, not as a silent pass.

## Workflow analysis

`actionlint_workflows` takes a source Directory, `scan_paths` defaulting to
`.github/workflows`, and an optional `config`. It returns a JSON report and
does not gate CI; callers raise on `outcome`.

`workflow-analysis` is the strict form of the same function. Both reference one
execution node, so the gate and the report never describe two different scans.
The Check requires `outcome == clean` *and* an applicable coverage: a scope
that holds no analysable Workflow is a failed required check, not a pass.

ShellCheck and pyflakes are passed as explicitly empty, so the analysed scope
never changes with whatever happens to be present in the image. Only Workflow
structure is checked: inline scripts that were not analysed are never reported
as passing.

## Required input coverage

A scanner that runs is not the same as a scanner that covered the inputs the
requirement names. Both implementations therefore state their coverage.

| Fact | Meaning |
| --- | --- |
| `coverage.scope` | The declared scope the scan had to cover |
| `coverage.inputs` | The files that were actually handed to the scanner |
| `coverage.input_count` | How many required inputs there were |
| `coverage.present` | Whether the scope exists in the analysed tree at all |
| `coverage.applicable` | Whether the scope holds anything to analyse |

Workflow inputs are enumerated from the declared scope by suffix, and the
enumeration is what the scanner receives, so a clean result means every
required Workflow was analysed. A scope that does not exist is reported as
`scope_absent`; a scope that exists but holds no Workflow is reported as
`no_applicable_workflows`. Neither starts a scan, because a scan over an empty
scope would report a clean result over nothing.

PowerShell inputs are enumerated the same way, and the report names the
analysed scripts by their path inside the checked tree. An empty script set is
`not_executed` and never a clean analysis.

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

## Optional for consumers, required here

For consumers, PowerShell analysis stays opt-in: `powershell_mode` defaults to
`off` and the job is skipped entirely in that case. For this repository it is
required: `ci.yml` runs the same analysis in `enforce` mode on a
`windows-2025` runner, and the release evidence names that Job and Step.

This repository analyses its own scripts with its own checkout, so the Job
selects the checkout as the helper instead of checking out a pinned helper ref.
The reusable Workflow keeps the pinned-helper arrangement, because a consumer
has no engineering-ci checkout of its own.

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

Issue #46 stated that reusable Workflow selection, gating, Runner scheduling and
Artifact delivery belong to the integration issue. That integration now exists:
the strict `workflow-analysis` Check, the required coverage it states, the
Windows Job in `ci.yml` and the release evidence entries for both are in place.
The reusable Workflow's own Windows Job is unchanged, so the consumer interface
did not move.

Bash and sh scripts remain out of scope, and Workflow inline scripts are never
extracted for a second scan.