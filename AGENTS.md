# Engineering CI — Agent Instructions

所有访问其他目录的行为都需要手动批准

Current `main` delivers reusable Python CI/CD through Dagger, GitHub Actions and local tools. **Target [#152](https://github.com/dymzz/engineering-ci/issues/152)** replaces all four GitHub Workflow execution, telemetry, Required and Release dependencies with Lefthook, existing ECI/Dagger native execution and an authorized non-Workflow CI Provider. Native Git/PR, Checks, Ruleset and Release APIs remain applicable; old Actions facts remain valid within their original historical scope.

## Project Navigation

- [Documentation index](docs/README.md): technical documentation and maintenance conventions.
- [Repository overview](docs/architecture/repository-overview.md): architecture, responsibilities and module boundaries.
- [Technology inventory](docs/architecture/technology.md): adopted, experimental, planned and removed technologies.
- [Common tasks](guides/common-tasks.md): development, CI/CD and recovery procedures.

Consult relevant documentation and interfaces when working across module boundaries.

## Binding Engineering Decisions — Git Hooks and Provider Time

- **Git Hook orchestration:** Use pinned **Lefthook 2.2.2** for Hook-file lifecycle (`lefthook install`) and native Job activation, filtering, grouping and concurrency. The ECI-owned `#66` implementation handles verified Git Event/Index/Push refs, Source/Selection/Plan, native Check/Report, Receipt and final reconciliation **using existing APIs**. Do not create or continue a parallel Hook Coordinator/Event Coordinator, scheduler, dispatcher, runner, hook-file installer, FSM or task graph, including under aliases such as a “logical coordinator” or “compatibility adapter”. PR #115's reusable validations may be retained in their existing domain modules; its extra control layer is not an implementation baseline. Lefthook **2.2.2 is an installed/verified baseline, not a full pre-push argv/stdin certification**; the selected native argument path or fixed release must prove full multi-Ref and space-containing argument fidelity and be pinned with the YAML. Server Required uses the **merged effective config** (`lefthook dump`, including local/extends/remotes), not only the main YAML. Real Checks must read target Index/Ref bytes including ordinary partial staging; rejecting every differing Worktree is not the completed #66-P0 goal. See [Issue #66](https://github.com/dymzz/engineering-ci/issues/66).
- **GitHub Workflow telemetry:** The GitHub API supplies Run/Attempt/Job identities, status, conclusion and native Job times; a post-run observation is not a measured Pipeline end. Do not infer a standard Pipeline Root end/duration from `updated_at`, last Job end, event/observer time, speculative time bounds or invented uncertainty budgets. Preserve a terminal event and valid Task spans with `root_emission=not_emitted` when no owner-proven full Pipeline boundary exists. Native Dagger Function/Check spans are valid for their own boundaries, **not** the enclosing GitHub Workflow. `terminal_event` is the **approved Draft PR #150 target**, not the current `main` post-run default (`observed_upper_bound`). #152's future controlled Provider must measure the **whole Pipeline** (possibly several Plans/platform Jobs), not relabel a local Plan or Dagger span. See [#75](https://github.com/dymzz/engineering-ci/issues/75), [#87](https://github.com/dymzz/engineering-ci/issues/87), [PR #150](https://github.com/dymzz/engineering-ci/pull/150) and [#152](https://github.com/dymzz/engineering-ci/issues/152).
- **Binding against regressions:** Historic Issue sections, merged design PRs, passing local tests and review approvals do not override these current owner/semantic decisions. Correct conflicting *active* directions at their actual source; retain genuine historical evidence and already verified unaffected contracts. Revalidate only the integration changes and risks that truly changed.

## Architecture and Ownership

- engineering-ci owns CI/CD quality policy, release evidence and delivery.
- Agent Harness, mining and generic Workflow execution are outside this repository. Do not add forwarding or compatibility layers for them.
- Native Dagger Checks define repository checks; standard Ruff settings and locked tools live in pyproject.toml. Do not recreate a host scheduler, input-hash receipt or check registry.
- Dagger manages internal execution graphs, containers and caching.
- **Current `main`:** GitHub Actions owns existing remote workflows, artifacts and Release dispatch. **Target #152:** a real controlled Provider handles CI ingress, Windows/Linux execution, Run/Attempt and native Artifact production; ECI/#63/#61 handles trusted evidence and Required/Release; GitHub App Checks and Ruleset enforce server-side decisions. No Actions Workflow dependency or fallback for the new chain.
- **Current `main`:** Release CLI observes the Actions Workflow and downloads selected native Actions artifacts. **Target #152:** preserve manual validate/publish, wait/download, R4 pre-write authorization and R5 final consumption via versioned actual Provider Run/Attempt/Manifest/Artifact IDs. Old Actions Runs remain explicitly selectable only for read-only historical checks; Git/PR use native Git/GitHub APIs.
- Reuse existing ECI/Dagger contracts and mature Provider/archival capabilities rather than duplicating Hook Coordinator/FSM/Gate logic. This does not remove #152's need for a real controlled CI ingress/executor and authorized, independently readable artifact storage, nor permit forged GitHub Workflow identities.
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
