# GitHub Actions Instructions

Applies to `.github/`. Inherit the repository's authorization and delivery boundaries.

- YAML owns the remote Job DAG, permissions and conditions. Shared check settings come from `config/pipeline.toml` through the existing Python entrypoints; reject incompatible bindings rather than silently ignoring configuration.
- Preserve required check identities, original exit propagation and reusable Workflow input/output contracts. Optional/skipped/unavailable results must not become passed.
- Keep PR/development verification separate from the human-initiated formal Release Workflow. Do not introduce publication on a main push or infer publication permission from an authorized PR merge.
- Bind Release evidence to the actual repository, commit SHA, Run ID, Run Attempt and native Artifact IDs. Never mix attempts or substitute an unrelated historical Run.
- Pass secrets only to the necessary operations; never embed credentials in YAML arguments, logs, reports or artifacts. Preserve existing private source authentication and cleanup boundaries.
- Use the existing Workflow helpers; do not move Actions business operations into `scripts/` or duplicate them in shell/YAML.

Contracts: [Pipeline bindings](../docs/configuration/pipeline.md),
[Release workflow](../docs/architecture/release/workflow.md),
[Release evidence](../docs/architecture/release/README.md).
