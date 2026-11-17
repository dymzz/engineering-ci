# Required Assessment GitHub Check (#61 R2)

Status: additive #61 R2 **contract / publication slice**
(`engineering_ci.required_assessment_check`). It maps one immutable #61 R1
Assessment onto a stable GitHub Check Run request.

It does **not** verify Rulesets/Branch Protection (#61 R3), authorize Release
(#61 R4/R5), re-run Assessment (#61 R1), or prove that the evaluator/Workflow
path is production-trusted. Declared `trust_basis` is an explicit input, not a
server-side proof.

## Position

```text
#61 R1 Required Assessment
        |
        v
build_check_publication(assessment, publication_context)
        |
        +--> completed Check Run request
        |      name: "engineering-ci / required-assessment"
        |      conclusion: success | failure  (never skipped/neutral)
        |
optional publish_check_run(...)  -->  GitHub Checks API
```

Native domain Checks keep their own evidence. This Check is the staged Required
aggregate only. Independent Native Required Contexts remain in force until #61
R3 verifies any Ruleset change.

## Stable Check identity

| Field | Value |
| --- | --- |
| Check name | `engineering-ci / required-assessment` |
| Publication kind | `engineering-ci-required-assessment-check` v1 |
| Binding | `head_sha`, Workflow ref/sha, Run id/attempt, Job name, evaluator/policy refs, `assessment_ref` |

The YAML job name is not the Check identity. Consumers must use the Check Run
`name` (and Source App under R3), not a guessed Job label.

## Conclusion rules

| Assessment / context | Conclusion |
| --- | --- |
| Assessment `blocked` | `failure` |
| Assessment missing / upstream cancelled without Assessment | `failure` via `publication_for_missing_assessment` |
| Assessment `passed` + `trust_basis` in `{protected_workflow, approved_evaluator}` + not contract sample | `success` |
| Assessment `passed` + `trust_basis=untrusted_pr_path` | refused (`production_success_trust_unproven`) |
| Assessment `passed` + contract sample without explicit sample publication | refused |
| Any attempt to emit `skipped` / `neutral` / forced `success` on blocked | refused |

GitHub may treat skipped/neutral Required Checks as passable. R2 therefore
never uses those conclusions for the Required Assessment gate.

## Trust boundary

A PR can rewrite aggregator code, Workflow proposals or policy drafts. That
path is `trust_basis=untrusted_pr_path` and **cannot** mint production
`success`. Production success requires an explicit
`protected_workflow` or `approved_evaluator` declaration; R3 must still verify
the server-side Required Context and Source App. Contract-sample publications
are labeled and never claimed as production admission.

When the Assessment candidate declares a `commit`, the publication `head_sha`
must equal that commit.

## Limits

- Does not enable or mutate Branch Protection / Rulesets.
- Does not replace Native Required Contexts.
- Does not wire a production CI Job that produces live universe/admission
  inputs; that remains follow-up R2 wiring once producers exist.
- `publish_check_run()` needs `checks:write` and a validated publication; API
  success is not R3 server acceptance.

## Directed acceptance

`tests/test_required_assessment_check.py` covers:

- passed Assessment → success under approved evaluator / protected Workflow;
- blocked Assessment → failure;
- untrusted PR path cannot mint success;
- contract-sample success only with explicit sample trust;
- head_sha / candidate commit mismatch refused;
- missing Assessment completes as failure;
- forced success / skipped / neutral publications rejected;
- `publish_check_run` posts the validated request body.
