# [ECI-CHECK-01] Required Engineering Checks, Evidence Integrity and Change Propagation

## Background

Engineering CI currently provides native Dagger Checks, reusable GitHub Actions workflows, script analysis capabilities and Release evidence verification.

The repository already executes its core Ruff, Pytest, version and lock checks through CI. Additional Workflow and PowerShell analysis capabilities exist, but their execution and coverage are not consistently required in ordinary PR validation.

The objective is to make required engineering checks reliably executable and enforceable, ensure that CI and Release results accurately represent what was actually verified, and preserve consistency when engineering requirements change.

The problem spans execution, result interpretation, evidence and their dependencies. Correcting only the immediately visible failure may leave the system inconsistent.

## 1. Required Engineering Checks

Existing Workflow and PowerShell analysis capabilities are not consistently integrated into ordinary PR and main-branch CI.

Making a scanner available is different from requiring its execution. A report-producing function is not necessarily a strict check, and a successful invocation does not establish that the intended inputs were covered.

The engineering problem includes determining which checks apply, how they execute and how their actual results affect CI.

### Design questions

**Q1.** What is the appropriate mechanism for identifying and executing the complete set of required checks across Dagger Checks, native tools and GitHub Actions?

**Q2.** How should required input coverage be established? When is an empty or excluded input set genuinely not applicable, and when must it cause a failed required check?

**Q3.** How can an existing report-producing function also support strict CI enforcement without executing the underlying scanner twice or interpreting its native diagnostics inconsistently?

**Q4.** How should Windows-specific checks participate in ordinary PR validation while preserving their actual execution environment and avoiding unrelated duplicate work?

## 2. CI Results and Gate Semantics

Successful execution, successful validation and successful evidence delivery are different claims.

The current CI integration must correctly distinguish actual scanner findings, execution failures, omitted operations, unsupported scopes and clean results.

A passing Job or successfully generated Artifact is not, by itself, sufficient evidence that every applicable required check was completed.

### Design questions

**Q5.** How can the system establish that every applicable required operation actually ran, rather than relying solely on the success of an enclosing Job?

**Q6.** When an execution succeeds but its evidence cannot be delivered, which conclusions remain valid, and which must be withheld?

**Q7.** What is the correct boundary between Dagger's execution result, GitHub Actions Job status and GitHub's server-side Required Status enforcement?

## 3. Release Evidence Integrity

Release verification currently relies on established relationships between the release candidate, required checks, GitHub Actions execution and collected evidence.

When required checks are added or their coverage changes, these relationships may no longer establish complete verification.

The system must distinguish evidence that remains valid from historical results that do not cover the candidate's applicable requirements. It must not report a release candidate as fully verified without adequate original execution evidence.

### Design questions

**Q8.** When required checks are added or their execution scope changes, which parts of the existing Release evidence contract must change, and which can remain untouched?

**Q9.** How should the implementation distinguish an unchanged valid check result from a historical result that no longer covers the current requirements?

**Q10.** How can Release verification establish completeness using the existing execution and evidence mechanisms? Which existing relationships are sufficient, and which require correction?

## 4. Change Propagation and Architectural Integrity

Changes to check definitions, execution scope, diagnostic interpretation or evidence requirements can affect multiple components.

The implementation must account for those dependencies rather than treating each visible failure as an isolated defect.

Existing functionality, interfaces and evidence relationships should be examined critically. Necessary architectural correction is part of solving the engineering problem; preserving an existing implementation is not inherently more correct than revising it.

Likewise, unaffected execution and verification results should remain reusable when their original validity conditions still hold.

### Design questions

**Q11.** Which existing interfaces and evidence relationships actually depend on the modified check definitions?

**Q12.** How should those dependencies be identified and revised without assuming that every implementation detail or historical result is affected?

**Q13.** Where should shared behavior be factored to avoid duplicated interpretation, and where are separate execution paths necessary because their environments differ?

## Engineering Objective

Resolve the identified problems as a coherent engineering change to the existing system.

The implementation should demonstrate correct execution, valid results, reliable Release evidence and consistent propagation of affected changes.

**System-level correctness takes precedence over minimizing modifications.** Local patches, compatibility workarounds or passing isolated tests are not sufficient when they leave relevant architectural inconsistencies unresolved.

Reuse established capabilities where appropriate, but allow necessary restructuring, replacement or removal of existing implementations. Neither minimal modification nor architectural expansion is an objective in itself.

The engineering investigation, implementation choices, affected dependencies and verification approach are to be determined from the actual system and its requirements.
