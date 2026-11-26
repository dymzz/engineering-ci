# Engineering CI — Agent Instructions

所有访问其他目录的行为都需要手动批准

Engineering CI provides reusable Python CI/CD capabilities through Dagger and GitHub Actions, including checks, delivery, release evidence and workflow artifacts.

## Project Navigation

- [Documentation index](docs/README.md): technical documentation and maintenance conventions.
- [Repository overview](docs/architecture/repository-overview.md): architecture, responsibilities and module boundaries.
- [Technology inventory](docs/architecture/technology.md): adopted, experimental, planned and removed technologies.
- [Common tasks](guides/common-tasks.md): development, CI/CD and recovery procedures. Before running repository verification, decide the actual obligation scope, then use the matching standard entry in [Explicit local full verification](guides/common-tasks.md#显式本地综合验证); reuse still-valid native evidence instead of expanding scope or re-running for a receipt.
- [Agent task continuity](docs/architecture/agent-instructions.md#task-continuity-and-evidence-reuse): locate relevant knowledge relations, existing work, evidence and handoff before implementation or review.
- [Authorization dependency review](docs/verification/authorization-closure/README.md): pinned dependency and evidence coverage snapshots, not current execution or authorization state.

Consult relevant documentation and interfaces when working across module boundaries. Before starting or resuming work, follow the task continuity procedure: locate the existing owner and applicable evidence, inspect the actual revision delta, and resume from the authoritative task record. Do not duplicate task, defect, dependency or evidence records for relationships already represented by their authoritative resources.

## Architecture and Ownership

- engineering-ci owns CI/CD quality policy, release evidence and delivery.
- Agent Harness, mining and generic Workflow execution are outside this repository. Do not add forwarding or compatibility layers for them.
- Native Dagger Checks define repository checks; standard Ruff settings and locked tools live in pyproject.toml. Do not recreate a host scheduler, input-hash receipt or check registry.
- Dagger manages internal execution graphs, containers and caching.
- GitHub Actions manages remote workflow orchestration, permissions and release gates.
- Release CLI requests, inspects and waits for that Workflow; Git/PR operations use native Git/gh. Downloads use native gh for explicitly selected Runs.
- Do not recreate a generic execution FSM, StateStore, download backend or host delivery lifecycle. Preserve only concrete dispatch uncertainty and release evidence safeguards.
- engineering-ci may depend on vesryn-log for CI event recording and vesryn-tool-runtime for actual CI tool contracts and invocation.
- engineering-ci must not depend directly or transitively on vesryn or vesryn-workflow, including through Log or Tool Runtime.
- Keep any CI instrumentation minimal; generic log storage, archiving and querying belong to the logging component.
- Vesryn and Workflow may consume engineering-ci capabilities;
  reverse dependencies are forbidden.
- If a forbidden dependency appears necessary, reassess ownership
  and component boundaries instead of adding compatibility layers.

## Delivery

- **Maintain engineering knowledge graph relations and task handoff with every submission** (commit, PR update, Review, or acceptance). Before submitting, reconcile affected relations and evidence against the actual change; update their authoritative records in the same submission or a linked companion update. Record the scope and outcome, including why no relation changed when applicable. Follow [graph maintenance on every submission](docs/architecture/agent-instructions.md#graph-maintenance-on-every-submission). Preserve pinned historical graphs; update original authorities and refresh applicable projections using adopted tools. Derive mechanical facts from Git/GitHub and native tool outputs instead of transcribing them. Tool adoption and projection automation belong to [#162 C](https://github.com/dymzz/engineering-ci/issues/162); use existing sources until those capabilities are available, without duplicating ledgers or unrelated revalidation.
- PR merging is authorized for this repository after task acceptance, code review and required CI checks on the current head.
- Before preparing a formal Release, review
  [Release Readiness](docs/delivery/release-readiness.md)
  and verify the applicable acceptance criteria.
  Readiness assessment does not authorize publication.

## Package APIs and cross-repository execution

- Installed Python packages expose reusable APIs directly. Cross-repository tool execution uses Dagger interfaces.
- Tool execution must not bypass its declared observation boundary.
