# Dagger Instructions

Applies to `.dagger/`. Inherit the repository's authorization and quality boundaries.

- Dagger owns reproducible container execution, task dependencies and its cache. The full development/PR/Release lifecycle remains outside the Dagger module.
- Engine/CLI/SDK bindings must match the repository's actual pinned configuration. Generate SDK/lock outputs through existing Dagger and uv commands; do not manually rewrite generated SDK code.
- Reuse the current pytest execution result for JSON/Summary/Artifacts. Reporting success must never turn a failed pytest or quality gate into success; strict checks propagate the original failure.
- Dagger check functions use the actual SDK's check contract. Preserve repository/container check evidence and source bindings; cache reuse alone does not prove current input passed.
- Supply private installation credentials through Dagger Secret only where necessary. Remove secret variables through the SDK's secret API before probes/tests, and preserve the actual absence assertion. No token in files, ordinary arguments or reports.
- Preserve reusable module APIs and domain gates. Change implementation only within the current task; do not duplicate GitHub Actions' remote orchestration here.

Contracts: [Dagger configuration](../docs/configuration/dagger.md),
[local check evidence](../docs/architecture/local-checks.md),
[pytest reporting](../docs/architecture/testing/pytest-reporting.md).
