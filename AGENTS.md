# Engineering CI — Agent Instructions

## Project and scope

Engineering CI owns reusable Python CI/CD through Dagger and GitHub Actions: checks,
delivery, Release evidence and Run downloads. Vesryn owns the portable Harness.

Use `D:\workspace\engineering-ci` as this workspace's primary checkout, with branches
in that checkout. Preserve existing changes; do not create worktrees/staging copies or
change project location to work around an obstacle.

## Instruction and documentation entry points

- Follow the shared development conventions in the user's global AGENTS.md.
- Start project documentation at [docs/README.md](docs/README.md); follow its maintenance conventions.
- Use the [repository overview](docs/architecture/repository-overview.md) as the factual basis for project instructions; keep task progress out of AGENTS.md.
- Before editing `.github/`, read [.github/AGENTS.md](.github/AGENTS.md).
- Before editing `.dagger/`, read [.dagger/AGENTS.md](.dagger/AGENTS.md).
- Root instructions own project boundaries; subtree instructions add their specialized constraints. Configuration and technical contracts stay in their existing authoritative files.

## Development and delivery

- Use one primary development PR per version increment and continue its fixes and verification in that PR. Choose the starting branch from actual Git state and the current task.
- PR merges require user authorization. The user has authorized automatic merging within the current task's repository scope after acceptance, code review and required CI pass for the exact head; guard that head and preserve repository protections. Conflicts, missing results or blocking findings stop the merge.
- Back up each accepted major phase using the shared immutable-branch rule; [operations and recovery](guides/common-tasks.md#大阶段分支备份) describe the Git commands. Backups are not release evidence.
- Local changes must be committed and pushed before PR CI or merge counts as their delivery evidence. If a PR merged with local changes still undelivered, preserve/report the files and actual merged SHA and ask the user to choose follow-up delivery before publication.
- Update affected documentation and the existing PR description when CLI or Workflow behavior changes. Follow current-task acceptance criteria; do not introduce unrequested checks or success metrics.

## CI/CD boundaries and verification

- Preserve basic CI/CD independently of the optional Vesryn dependency. Keep CI-specific check policy, approval/feedback and Release decisions in this repository.
- `config/pipeline.toml` owns registered local check settings; GitHub Workflow YAML owns its remote DAG, permissions and admission. Do not implement a second remote scheduler.
- Use existing uv, pytest, Ruff and Dagger checks for actual change risk. Retain original failure/skip/unavailable semantics; JSON/report delivery is not a passing test result.
- Preserve the version and lockfile contract and verify the release commit. Development/PR verification does not replace formal Release evidence.
- Check actual Git/gh authentication, effective permissions and operation errors without exposing credentials. Stop blocked operations requiring extra authorization/account selection or a location change; report the decision needed instead of signing in, switching accounts or making substitute checkouts.
- Formal releases require explicit human initiation. Automatic PR merge does not authorize Tag/Release creation, overwriting release tags or relaxing quality/security gates.

## Harness tools

- Discover an applicable existing capability through `engineering-ci workflow capabilities`; use `workflow select` to check activation and admission before `workflow call`.
- Prefer an applicable approved capability over reconstructing its sequence. Otherwise use existing development tools.
- Do not approve, activate or change capabilities without explicit user authorization. Report capability use/evidence or why none applied.
- Actual commands and recovery are in [generated workflows](guides/generated-workflows.md).

## Completion

The implementation must satisfy the current acceptance criteria and be ready for review.
Handoff changes, actual verification, PR/Run links, phase backup refs and remaining limits.
Upstream observations follow the global rules and belong in this project's
`.agent/upstream-observations.md`; additional improvements remain separate tasks.
