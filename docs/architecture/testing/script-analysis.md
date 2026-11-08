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

Observed against the real tools on 2026-10-31:

1. **actionlint 1.7.12 was run.** Exit 0 with no output on a clean Workflow
   set; exit 1 with diagnostics reading
   `<path>:<line>:<column>: <message> [<rule>]` for a broken one; exit 3 with
   `could not read "<path>"` for a path that does not exist. The rules it
   emitted were `syntax-check` and `expression`, and it prints a source
   excerpt below each diagnostic. The committed unit test now asserts the
   observed rules instead of an invented one.
2. **PSScriptAnalyzer 1.9.0 was run.** The process exits 0 even when it
   reports violations, so the diagnostics decide the result; the analyzer
   reports the number of files it scanned and its own version. The committed
   fixtures do fire `PSAvoidUsingCmdletAliases`,
   `PSAvoidUsingWriteHost` and `PSUseDeclaredVarsMoreThanAssignments`.
3. **That observation exposed a real defect, now fixed.** The invocation
   script consumed one path per `-Path` flag while both callers pass every
   path after a single `-Path`, so analysing more than one script failed with
   `Unknown argument`. The script now consumes every value that follows the
   flag, and the two-script case was re-run and observed clean.
4. **The committed PowerShell fixtures were not what they claimed.** The
   "clean" fixture used `Export-ModuleMember` in a `.ps1` file and declared
   `SupportsShouldProcess` without calling `ShouldProcess`; the module
   fixture used `Export-ModuleMember` as well. Observed against PSScriptAnalyzer
   1.9.0, a script that calls `Export-ModuleMember` can make the analyzer
   fail with an execution error rather than report diagnostics, and the
   `SupportsShouldProcess` declaration is reported as `PSShouldProcess`. Both
   fixtures are corrected, and all three now analyse in one invocation with
   three candidates, three analysed and findings only where intended.

   The general lesson is the one this check already follows: an analyzer
   execution error is a failure, not a clean result, and never a pass. It
   cannot be relied upon to occur for a given script, so it must not be
   depended upon in either direction.

Still open, and none of it may be described as verified until a real run says so:

1. **The Windows runner job has never executed.** The runner label, the pinned
   uv invocation, moving the helper checkout out of the workspace and the
   `PYTHONPATH` wiring are all unexercised.
2. **The Dagger container has not been run for these tools.** The pinned
   actionlint wrapper's Linux behaviour is inherited from the host observation
   above rather than observed directly.

Every classifier in both implementations fails closed: an unreadable envelope,
an empty script set, a missing module or a failed process produces a non-zero
exit and a failure bundle, never a clean result. A wrong assumption therefore
surfaces as a red check that prints the observed value, not as a silent pass.

## 输入覆盖与强制判定

一次成功的调用不构成输入被覆盖的证明，因此报告同时记录覆盖事实：

| 事实 | 含义 |
| --- | --- |
| `coverage.scope` | 声明的检查范围 |
| `coverage.candidates` | 范围内实际存在的目标文件数 |
| `coverage.analysed` | 工具实际处理的文件数 |

由此得到五种结论，判定只有一个来源：

| 结论 | 条件 | 是否通过 |
| --- | --- | --- |
| `clean` | 全部候选文件被处理且无诊断 | 是 |
| `findings` | 全部候选文件被处理且存在诊断 | 否 |
| `not_applicable` | 范围内没有任何目标文件 | 否 |
| `not_executed` | 存在候选文件但未被处理，或处理数量不足 | 否 |
| `failure` | 工具本身或调用失败 | 否 |

空范围与未执行是两件事：空范围是范围为空的证明，未执行是覆盖缺口。
两者都不是通过，但原因不同，记录也不同。

actionlint 无法报告逐文件处理结果，因此先枚举范围、再把枚举出的文件
逐个传入：actionlint 读到无法读取的文件时以 3 退出，所以退出 0 或 1 即
证明这些文件都被读过。

`script.enforce(report)` 把同一份报告转成门禁结论：只有 `clean` 通过，
其余结论都失败并打印报告记录的原因。它不重新运行扫描器，也不重新解释
原生诊断，因此报告函数与强制检查共用一次执行和同一套判定。

## Workflow analysis

`actionlint_workflows` takes a source Directory, `scan_paths` defaulting to
`.github/workflows`, and an optional `config`. It returns a JSON report and
does not gate CI; callers raise on `outcome`.

`workflow_analysis` is the required native Dagger check. It runs the same
execution and the same classifier, then enforces the result. The report is
produced once and read twice, so enforcing never runs the scanner again and
never re-interprets its diagnostics.

ShellCheck and pyflakes are passed as explicitly empty, so the analysed scope
never changes with whatever happens to be present in the image. Only Workflow
structure is checked: inline scripts that were not analysed are never reported
as passing.

The scope is enumerated before the scan and the enumerated files are passed
explicitly. The covered input set is therefore known in advance, and the same
invocation behaves the same on every platform.

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

PowerShell analysis stays opt-in for consumers: `powershell_mode` defaults to
`off`, the job is skipped entirely in that case, and the reusable Workflow
enables it only when a caller asks for it.

This repository is itself the helper, so it does not use the reusable
Workflow's helper checkout. Its own `ci.yml` runs the analysis in a
`windows-2025` Job against the declared paths, and the release Run reaches
the same Job through `core-tests`. The capability is therefore required here
without changing the reusable Workflow's public default and without running
the analysis twice.

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