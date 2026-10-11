# Engineering CI — Agent Instructions

## Project

Engineering CI provides reusable Python CI capabilities through Dagger and GitHub Actions.

## Documentation

Follow [docs/README.md](docs/README.md) for project
documentation structure and maintenance conventions.

## Development Workflow

- Use `D:\workspace\engineering-ci` as the primary working checkout for this workspace. Use branches in this checkout; do not create additional worktrees or staging checkout copies. Preserve existing changes before switching branches.
- Use one primary development PR per version increment.
- Continue development, fixes, and verification within the existing PR for that increment.
- Base new development on the appropriate current branch and repository state.
- PR merging requires human approval.
- Local changes must be committed and pushed before their PR checks or merge are treated as delivery evidence. An unrelated historical Run cannot imply that the current PR should be merged.
- If a PR was merged while local changes remain undelivered, report the actual merged SHA and pending files, preserve them, and ask the user to decide on a follow-up PR before continuing publication.
- Follow the current task's goals and acceptance criteria.

## Implementation

- Choose implementation details based on actual project requirements.
- Preserve existing public behavior unless a change is intentional and documented.
- Update affected documentation and the PR description whenever CLI or workflow behavior changes.
- Do not introduce additional requirements or success metrics that were not requested.
- Prefer mature, maintained libraries, tools, and established
  implementations over custom solutions when they meet the requirements.
- Evaluate existing project capabilities before introducing new
  dependencies or implementing equivalent functionality.

## Verification

- Prefer local execution for development feedback.
- Use the project's existing tools, including uv, pytest, Ruff, and Dagger, as appropriate.
- Select verification based on the changes and their potential effects.
- Use actual execution results as evidence of correctness.
- Preserve the original failure semantics of tests and quality gates.
- Distinguish successful, failed, skipped, and unavailable verification results.

## CI and Release

- Check Git and gh authentication, effective repository permissions, and actual operation errors in the primary checkout. Never expose authentication tokens.
- If an operation is blocked by additional authorization, manual repository/account selection, or a workspace-location change, stop that operation and report the exact obstacle and the decision needed from the user. Do not create a substitute workspace, change the project location, sign in, or switch accounts to work around it automatically.
- Follow the repository's current GitHub Actions workflows.
- Keep development verification separate from formal release validation.
- Formal releases require explicit human initiation.
- Preserve version consistency and validate the release commit.
- Do not merge PRs, publish releases, or overwrite existing release tags without authorization.

## Completion

A task is complete when its acceptance criteria are satisfied and the implementation is ready for review.

Provide a concise handoff containing:

- Summary of changes.
- Verification performed and results.
- Relevant PR or workflow links.
- Known limitations or unresolved issues.

Further improvements may be suggested separately; they are not automatically part of the current task.
