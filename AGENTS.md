# Engineering CI — Agent Instructions

所有访问其他目录的行为都需要手动批准

Engineering CI provides reusable Python CI/CD capabilities through Dagger and GitHub Actions, including checks, delivery, release evidence and workflow artifacts.

## Project Navigation

- [Documentation index](docs/README.md): technical documentation and maintenance conventions.
- [Repository overview](docs/architecture/repository-overview.md): architecture, responsibilities and module boundaries.
- [Technology inventory](docs/architecture/technology.md): adopted, experimental, planned and removed technologies.
- [Common tasks](guides/common-tasks.md): development, CI/CD and recovery procedures.

Consult relevant documentation and interfaces when working across module boundaries.

## Architecture and Ownership

- engineering-ci owns CI/CD quality policy, release evidence and delivery.
- Agent Harness, mining and generic Workflow execution are outside this repository. Do not add forwarding or compatibility layers for them.
- Native Dagger Checks define repository checks; standard Ruff settings and locked tools live in pyproject.toml. Do not recreate a host scheduler, input-hash receipt or check registry.
- Dagger manages internal execution graphs, containers and caching.
- GitHub Actions manages remote workflow orchestration, permissions and release gates.
- engineering-ci may depend on vesryn-log for CI event recording and vesryn-tool-runtime for actual CI tool contracts and invocation.
- engineering-ci must not depend directly or transitively on vesryn or vesryn-workflow, including through Log or Tool Runtime.
- Keep any CI instrumentation minimal; generic log storage, archiving and querying belong to the logging component.
- Vesryn and Workflow may consume engineering-ci capabilities;
  reverse dependencies are forbidden.
- If a forbidden dependency appears necessary, reassess ownership
  and component boundaries instead of adding compatibility layers.

## Delivery

- PR merging is authorized for this repository after task acceptance, code review and required CI checks on the current head.
- Before preparing a formal Release, review
  [Release Readiness](docs/delivery/release-readiness.md)
  and verify the applicable acceptance criteria.
  Readiness assessment does not authorize publication.

## Package APIs and cross-repository execution

- Installed Python packages expose reusable APIs directly. Cross-repository tool execution uses Dagger interfaces.
- Tool execution must not bypass its declared observation boundary.
