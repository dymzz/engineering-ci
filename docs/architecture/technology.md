# Technology Inventory

Technologies engineering-ci uses directly, their adoption status and where each is integrated.
Source code, dependency manifests and configuration are authoritative; this page records
relationships only. Maintenance rules: [documentation conventions](../conventions.md).

## Status values

| Status | Meaning | Required evidence |
| --- | --- | --- |
| `adopted` | Used by current code, configuration or CI and supported by the project | Usage in source, configuration or CI, and a pin in a manifest or configuration |
| `experimental` | Implemented but opt-in, partially verified or without a compatibility promise | Usage in source, the open gap and the promotion or removal criterion |
| `planned` | Documented direction with no implementation | Link to the decision; never described as available |
| `removed` | No longer used; kept to record the replacement and retained compatibility | Removal confirmed in source and manifests |

## Inventory

| Technology | Status | Scope | Integration |
| --- | --- | --- | --- |
| Python | adopted | runtime | [python](integrations/python.md#python) |
| uv and uv_build | adopted | build, development, CI | [python](integrations/python.md#uv-and-uv_build) |
| transitions | adopted | runtime | [python](integrations/python.md#transitions) |
| filelock | adopted | runtime | [python](integrations/python.md#filelock) |
| PyYAML | adopted | runtime | [python](integrations/python.md#pyyaml) |
| pytest | adopted | development, CI | [python](integrations/python.md#pytest) |
| pytest-cov | adopted | development, CI | [python](integrations/python.md#pytest-cov) |
| Dagger engine and module | adopted | CI, PR check graph, container execution | [dagger](integrations/dagger.md#dagger-engine-and-module) |
| Dagger Python SDK and packaging | adopted | CI module runtime | [dagger](integrations/dagger.md#dagger-python-sdk-and-packaging) |
| Docker | adopted | container runtime | [dagger](integrations/dagger.md#docker) |
| GitHub Actions | adopted | CI, release workflow | [github-actions](integrations/github-actions.md#github-actions) |
| Reusable Python CI Workflow | adopted | CI orchestrator | [github-actions](integrations/github-actions.md#reusable-python-ci-workflow) |
| Ruff | adopted | development, CI | [tools](integrations/tools.md#ruff) |
| Git CLI | adopted | development, CI, release | [tools](integrations/tools.md#git-cli) |
| GitHub CLI (gh) | adopted | download, release, API | [tools](integrations/tools.md#github-cli-gh) |
| aria2c | adopted | optional download backend | [tools](integrations/tools.md#aria2c) |
| OSV-Scanner | adopted | dependency security gate | [tools](integrations/tools.md#osv-scanner) |

## Discrepancies

Open findings from targeted verification. Resolve them in a task that owns the change.

- **Ruff** — pinned in `config/pipeline.toml` (`[toolchain] ruff = "0.16.10"`) and
  `.dagger/pyproject.toml` (`dev` group), but not declared in root `pyproject.toml` dependency
  groups. Local PR check execution invokes `uvx` or ambient `ruff`. (2026-10-14 @ `963d809a295f`)
