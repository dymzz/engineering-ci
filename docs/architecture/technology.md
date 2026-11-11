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
| pytest | adopted | development, CI | [python](integrations/python.md#pytest) |
| pytest-cov | adopted | development, CI | [python](integrations/python.md#pytest-cov) |
| Dagger engine and module | adopted | CI, PR check graph, container execution | [dagger](integrations/dagger.md#dagger-engine-and-module) |
| Dagger Python SDK and packaging | adopted | CI module runtime | [dagger](integrations/dagger.md#dagger-python-sdk-and-packaging) |
| Docker | adopted | container runtime | [dagger](integrations/dagger.md#docker) |
| GitHub Actions | adopted | CI, release workflow | [github-actions](integrations/github-actions.md#github-actions) |
| Reusable Python CI Workflow | adopted | CI orchestrator | [github-actions](integrations/github-actions.md#reusable-python-ci-workflow) |
| Ruff | adopted | development, CI | [tools](integrations/tools.md#ruff) |
| Vulture | adopted | opt-in static analysis function | [static analysis](testing/python-static-analysis.md) |
| Pyright | adopted | opt-in static analysis function | [static analysis](testing/python-static-analysis.md) |
| deptry | adopted | opt-in static analysis function | [static analysis](testing/python-static-analysis.md) |
| Pylint | adopted | opt-in R0801 duplicate-code function | [static analysis](testing/python-static-analysis.md) |
| Git CLI | adopted | development, CI, release | [tools](integrations/tools.md#git-cli) |
| GitHub CLI (gh) | adopted | download, release, API | [tools](integrations/tools.md#github-cli-gh) |
| OSV-Scanner | adopted | dependency security gate | [tools](integrations/tools.md#osv-scanner) |
| OpenTelemetry CI/CD SemConv attributes | experimental | GitHub Run/Job API or facts → Pipeline/Task candidate records + optional OTLP/JSON file (`engineering_ci.cicd_telemetry`); registry pin tracked with Knowledge #80/#81; live OTLP/gRPC later | [cicd-telemetry](cicd-telemetry.md) |

## Verification notes

Ruff is locked in root dev dependencies and configured in standard `pyproject.toml`.
Vulture, Pyright, deptry and Pylint are pinned in the Dagger module and run through
`uvx` in their own environment, so they are not root dev dependencies and never
enter a consumer's lockfile. The removed host Runner, input receipt cache and YAML
mirror validator are not integration layers.
