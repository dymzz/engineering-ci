# Controlled native Plan consumers

All cases are explicitly controlled inputs. Native API output chunks are
preserved in [native-cli.json](../native-cli.json), with code revision and
expected/actual process exit. Whole bundles preserve native stderr/stdout,
JUnit, source Plan, native Reports, exit files and the compound observation.
Their file hashes are recorded without changing native bytes.

| Case | Plan | Raw Report | Compound observation | Whole native bundle |
| --- | --- | --- | --- | --- |
| unittest passed | [Plan](passed-plan.json) | [Report](passed-report.json) | [Inspection](passed-inspection.json) | [Archive](passed-bundle.tar.gz) |
| native configuration failure, independent test passed | [Plan](independent-check-failure-plan.json) | [Report](independent-check-failure-report.json) | [Inspection](independent-check-failure-inspection.json) | [Archive](independent-check-failure-bundle.tar.gz) |
| mixed pytest targets passed | [Plan](pytest-plan.json) | [Report](pytest-report.json) | [Inspection](pytest-inspection.json) | [Archive](pytest-bundle.tar.gz) |
| failed pytest plus independent native result | [Plan](pytest-failure-plan.json) | [Report](pytest-failure-report.json) | [Inspection](pytest-failure-inspection.json) | [Archive](pytest-failure-bundle.tar.gz) |
| refused Plan | [Plan](refused-plan.json) | [Report](refused-report.json) | [Inspection](refused-inspection.json) | [Archive](refused-bundle.tar.gz) |
| existing test prerequisite unsatisfied, independent Registry passed | [Plan](precondition-plan.json) | [Report](precondition-report.json) | [Inspection](precondition-inspection.json) | [Archive](precondition-bundle.tar.gz) |

The other five requests exercise the public serialized object: read-only
refused Report (API exit 0, Report exit 2), passed Enforce (API exit 0), refused
Enforce (API exit 1), failed Enforce (API exit 1), and prerequisite-blocked
Enforce (API exit 1; Report exit 5). The exact native reasons
are in the CLI capture; [read-only-refused-report.json](read-only-refused-report.json)
retains the returned Report. These are separate API invocations; no common
Origin or fresh tool execution is inferred from repeated Source or cache hits.

The compound fixture creates `PlanExecution` once, retains the Directory,
reads the Report, then calls its real Enforce and records the exception. Its
successful export of a failed bundle is evidence delivery, not a strict PASS.
The separate production Enforce calls prove the actual failing process path.

Input archives are linked from [receipt.json](../receipt.json). Unpack the
chosen Source archive, use its corresponding Plan and the recorded command;
the original invocations used `.artifacts/plan-sharing/`. This slice uses
minimal `snapshot=worktree` inputs and does not claim Git/Source attestation.

Parent: [native result sharing](../README.md).
