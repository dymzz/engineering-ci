# Required Assessment GitHub Check (#61 R2)

Status: additive #61 R2 **contract / publication slice**
(`engineering_ci.required_assessment_check`). It maps one immutable #61 R1
Assessment onto a GitHub Check Run request and optionally posts it.

It does **not** verify Rulesets/Branch Protection (#61 R3), authorize Release
(#61 R4/R5; see
[Required Assessment → Release 对账](required-assessment-release.md)), re-run
Assessment (#61 R1), or treat a caller-asserted `trust_basis` string as a
trusted issuer.

## Construction vs production publication

```text
build_check_publication / publication_for_missing_assessment
        |
        +--> declarative envelope + request body (no GitHub I/O)
        |
publish_check_run(..., publishing_authorization, publish_authority)
        |
        +--> production POST for CHECK_NAME only
             success requires verified Provider/Workflow authorization
```

| Envelope | Check name | Production `publish_check_run` |
| --- | --- | --- |
| Live Assessment | `engineering-ci / required-assessment` | Allowed; `success` needs authorization |
| Contract sample | `engineering-ci / required-assessment (contract-sample)` | **Rejected** (zero POSTs) |
| Missing Assessment | production name, `failure` | Allowed as completed failure |

## Target binding

Every publication context requires `target_binding` with `kind` in
`{candidate_commit, event_head, explicit_assessment_target}` binding the
assessed commit (equal to `head_sha`), repository, event name and
Run/Attempt. Candidate Commit is not mandated for every event, but absence of
any verified binding fails closed. When Assessment `candidate.commit` is
present it must equal that binding.

`validate_publication()` re-runs the same `normalize_target_binding()`
semantics against the serialized envelope (event / Run / Attempt / SHA /
repository), not only commit+repository. Production success authorization
also binds `event_name`, `target_binding` and optional `policy_ref`.

## Conclusion rules

| Assessment / context | Conclusion |
| --- | --- |
| Assessment `blocked` | `failure` |
| Assessment missing | `failure` via `publication_for_missing_assessment` |
| Assessment `passed` + declared production trust basis | build `success` (POST still needs authorization) |
| Assessment `passed` + `untrusted_pr_path` | refused at build |
| Contract-sample `success` | sample Check name only; production publisher rejects |
| `skipped` / `neutral` / forced `success` on blocked | refused |

## Production publish authorization

`publish_check_run` for `conclusion=success` requires:

1. `publishing_authorization` bound to the exact Assessment, Check name,
   conclusion, repository, head SHA, Workflow ref/sha, Run/Attempt and
   evaluator;
2. `publish_authority(authorization, publication) is True` from the trusted
   Provider/Workflow boundary.

Forged `trust_basis="approved_evaluator"` metadata alone cannot POST. A
disagreeing `repository=` override is rejected. `request.external_id` must
equal `assessment_ref` when an Assessment is present.

## Limits

- Does not enable or mutate Branch Protection / Rulesets.
- Does not replace Native Required Contexts.
- Does not wire a production CI Job that produces live universe/admission
  inputs; that remains follow-up R2 wiring.
- Declarative construction is not publish authorization.

## Directed acceptance

`tests/test_required_assessment_check.py` covers production vs sample names,
authorization-gated success POST, forged evaluator refusal, target binding /
SHA / event / Run mismatches, repository override rejection, missing
Assessment failure, and skipped/neutral / forced-success refusals.
