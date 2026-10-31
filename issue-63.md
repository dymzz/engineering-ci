# [ECI-CHECK-01] Required Engineering Checks and Release Evidence

**Source tracking:** [engineering-ci Issue #63](https://github.com/dymzz/engineering-ci/issues/63)  
**Evaluation input:** fixed functional-requirements snapshot, prepared for independent implementation comparison. The published Issue #63 contains additional design discussion; this file is **not** a verbatim copy of its GitHub body.

## Engineering problem

Engineering CI already runs core engineering checks in ordinary CI, and provides other analysis capabilities. However, Workflow and PowerShell analysis are not consistently executed and enforced for ordinary pull requests and changes to the main branch. Release verification must also correctly account for the checks required of the candidate.

Implement the required behavior in the existing Engineering CI repository. Deliver actual functioning changes, not only a proposal.

## Functional requirements

1. Applicable GitHub Actions Workflow analysis using actionlint and PowerShell analysis using PSScriptAnalyzer must be executed automatically during ordinary PR/main validation; genuine diagnostics or execution failures must affect the required CI result.
2. Successful execution, clean diagnostics, reported findings, missing or inapplicable inputs, execution failure and nonexecution must be distinguished. A required check must not be reported as passing without applicable successful execution.
3. Preserve the behavior of existing core checks. CI and Release conclusions must reflect the required checks applicable to their respective source revision and verification context.
4. Results from an earlier execution must not establish satisfaction of a check added or changed afterward if its actual coverage is insufficient. Previously collected evidence can still be used when its conditions remain applicable.
5. Correct the affected implementation, tests and configuration as needed. Provide actual positive and negative verification, identifying any behavior that could not be verified.

## Delivery

- Implement and verify the engineering change in the supplied repository checkout.
- Supply the code/configuration changes, relevant test and tool outputs, and any necessary updates to existing technical documentation.
- Explain significant design decisions, evidence reused, and unresolved implementation facts.

The technical solution, execution structure and change scope are to be determined through investigation of the actual implementation.
