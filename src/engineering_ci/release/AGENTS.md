# Release Subsystem — Agent Instructions

Orchestrates release lifecycle, delivery verification, manifest generation, and publication. Extends [src/engineering_ci/AGENTS.md](../AGENTS.md).

## Human Stop Points

- **Mandatory Manual Gates**:
  1. **PR Review Gate**: Pauses after PR creation for human review and CI status checks.
  2. **Publication Gate**: Merging a PR must **never** auto-publish. Release publication requires an explicit, separate user command.

## Release Evidence & Manifest

- **Manifest Conformance**: Manifests must validate against [docs/architecture/release/manifest.schema.json](../../../docs/architecture/release/manifest.schema.json).
- **Exact Provenance Binding**: Manifests must bind the exact release commit SHA, GitHub Actions Run ID, attempt number, and artifact file digests (`sha256`).
- **No Inferred Success**: Never synthesize a release manifest from unverified historical passes or mismatched git trees.

## Delivery & Recovery Boundaries

- **Clean Working Tree**: Refuse release delivery if the local workspace has uncommitted changes or untracked files.
- **Undelivered Changes Guard**: If a PR is merged externally while local workspace files differ, halt immediately to protect local modifications.
- **Publish Idempotency**: Verify remote tag and release asset existence before uploading. Never overwrite published release assets silently.
