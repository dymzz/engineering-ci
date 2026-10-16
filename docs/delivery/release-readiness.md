# Release Readiness

This document defines acceptance criteria for determining whether a software component is ready for formal release.

Release Readiness evaluates delivery readiness. It does not execute CI/CD, manage publication, or authorize a release.

## 1. Scope

- Routine development, PR checks, and ordinary testing do not require a complete readiness assessment.
- Before formal release, evaluate all applicable C1–C8 criteria.
- Identify the candidate version, source revision, deliverables, public interfaces, supported environments, and relevant changes.
- Mark non-applicable criteria `N/A` with justification. Unverified criteria must not be treated as passed.
- Reuse valid existing verification evidence where applicable.

Assessment has two scopes:

- **Preparation:** Verify the release candidate and delivery requirements before publication.
- **Publication:** Verify actual published identifiers, artifacts, and metadata against the approved candidate.

These scopes do not introduce a new release state machine.

## 2. Acceptance Criteria

### C1. Independent Consumption

- The component can be consumed through a supported installation or integration interface.
- Consumption does not depend on private development directories or temporary build state.
- Dependencies, environments, and external requirements are documented.
- Consumers can reference a fixed version or immutable revision.

### C2. Public Contract

- Public interfaces are distinguishable from internal implementations.
- Inputs, outputs, errors, and side effects are documented and match actual behavior.
- Deprecated or breaking interfaces are explicitly identified.
- Internal callable functions are not automatically public APIs.

### C3. Documentation

- A discoverable documentation entry point exists.
- Installation, usage, API references, configuration, and troubleshooting are documented where applicable.
- Documentation and examples correspond to the release candidate.
- Consumers do not need to inspect internal source code to understand supported usage.

### C4. Reproducible Examples

- Representative examples use supported public interfaces.
- Required dependencies, configuration, and execution conditions are specified.
- Examples run successfully in their declared environments.
- Observed results match documented behavior.

### C5. Failure Semantics

- Applicable success, failure, skipped, cancelled, unavailable, and uncertain outcomes have defined meanings.
- Transport success or artifact generation does not imply successful underlying execution.
- Missing or failed required operations must not be reported as successful.
- Errors and exceptions are identifiable through supported interfaces.
- Unsupported or unobserved outcomes must not be fabricated.

### C6. Versioned Delivery

- Version identifiers correspond to actual deliverables.
- Public interfaces, documentation, examples, and artifacts correspond to the same release.
- Consumers can reference immutable versions rather than mutable development branches.
- Released artifacts can be associated with their source revision.

### C7. Consumer Verification

- At least one supported independent consumption path is verified.
- Verification uses public interfaces and checks expected results or artifacts.
- Changes affecting public interfaces receive corresponding compatibility verification.
- Internal unit tests alone do not establish independent consumer compatibility.
- Provider-side installation checks and downstream integration tests are identified separately.

### C8. Compatibility & Evolution

- Changes affecting public interfaces or existing consumers are identified.
- Breaking changes, deprecations, and required migrations are documented.
- Upgrade guidance reflects actual release behavior.
- Known incompatibilities and unverified compatibility ranges are disclosed.

## 3. Verification Evidence

Acceptance must be supported by observable evidence from actual implementations, tests, documentation, artifacts, or consumer execution.

| Status | Meaning |
|---|---|
| `PASS` | Requirements are satisfied with supporting evidence. |
| `FAIL` | Requirements are confirmed to be unsatisfied. |
| `NOT VERIFIED` | Evidence is insufficient or verification is incomplete. |
| `N/A` | The criterion does not apply, with justification. |

Evidence rules:

- Associate evidence with its source version, revision, environment, and execution result where available.
- Reuse existing evidence when relevant implementations, contracts, dependencies, and execution assumptions remain applicable.
- Reassess evidence when relevant conditions change; do not repeat equivalent verification unnecessarily.
- Distinguish historical evidence from new executions.
- Do not invent missing results, identifiers, or verification coverage.
- Documentation, successful builds, and internal tests do not independently prove consumer compatibility.

## 4. Release Decision

**READY** requires:

- All applicable required criteria are `PASS`.
- All `N/A` decisions are justified.
- No known release-blocking issues remain.

**BLOCKED** applies when:

- An applicable required criterion is `FAIL` or `NOT VERIFIED`.
- Candidate identity, contracts, or verification evidence contain unresolved conflicts.

The assessment output should include:

- Candidate version and source revision.
- Public interface changes.
- C1–C8 statuses and evidence references.
- Known limitations and unverified scope.
- Final decision: `READY` or `BLOCKED`.

Preparation readiness does not authorize publication or prove publication success.

After publication, verify actual release references, artifacts, and metadata as applicable. Missing publication evidence must remain unverified.

Existing CI/CD, release controls, and publication mechanisms remain authoritative. Readiness assessment must not replace or weaken them.