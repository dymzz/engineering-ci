# Engineering CI — Agent Instructions

所有访问其他目录的行为都需要手动批准

Engineering CI provides reusable Python CI/CD capabilities through Dagger and GitHub Actions, including checks, delivery, release evidence, workflow artifacts and engineering-domain Providers.

Vesryn provides the optional Agent Harness. vesryn-tool-runtime owns shared tool contracts, registration, discovery, binding and generic invocation coordination.

## Project Navigation

- [Documentation index](docs/README.md): technical documentation and maintenance conventions.
- [Repository overview](docs/architecture/repository-overview.md): architecture, responsibilities and module boundaries.
- [Technology inventory](docs/architecture/technology.md): adopted, experimental, planned and removed technologies.
- [Generated workflows](guides/generated-workflows.md): Harness capability usage and commands.
- [Common tasks](guides/common-tasks.md): development, CI/CD and recovery procedures.

Consult relevant documentation and interfaces when working across module boundaries.

## Architecture and Ownership

- engineering-ci owns CI/CD execution, quality policy, domain Providers, approval/feedback rules and release delivery.
- Vesryn owns portable Harness capabilities; vesryn-tool-runtime owns shared tool registration, discovery, binding and invocation coordination.
- Basic CI/CD must operate independently of the optional Vesryn integration.
- `config/pipeline.toml` defines registered local check settings.
- Dagger manages internal execution graphs, containers and caching.
- GitHub Actions manages remote workflow orchestration, permissions and release gates.

## Harness Integration

- Discover existing capabilities with `engineering-ci workflow capabilities`.
- Use `workflow select` to check activation and admission before `workflow call`.
- Prefer applicable registered capabilities over reconstructing their execution sequences.
- Capability approval and activation require explicit authorization.

## Delivery

- PR merging is authorized for this repository after task acceptance, code review and required CI checks on the current head.
- Before preparing a formal Release, review
  [Release Readiness](docs/delivery/release-readiness.md)
  and verify the applicable acceptance criteria.
  Readiness assessment does not authorize publication.

## Package APIs and cross-repository execution

- Installed Python packages expose reusable APIs directly. Cross-repository tool execution uses Dagger interfaces.
- Tool execution must not bypass its declared observation boundary.
