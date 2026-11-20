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
publish_check_run(..., publishing_authorization, publish_authority,
                  assessment_authority, github)
        |
        +--> production POST for CHECK_NAME only
             both conclusions require verified Provider/Workflow authorization
             success also requires the original R1 PASS
```

| Envelope | Check name | Production `publish_check_run` |
| --- | --- | --- |
| Live Assessment | `engineering-ci / required-assessment` | Requires publisher authorization for either conclusion |
| Contract sample | `engineering-ci / required-assessment (contract-sample)` | **Rejected** (zero POSTs) |
| Missing Assessment | production name, `failure` | Requires publisher authorization |

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

Production publication carries the governed `target_binding.assessment_scope`
with `context`, `action`, `target`, `phase` and the `refs` pairs
(`target_ref`, `ref_action`). Success must also carry a target tree matching
the original Assessment. The trusted publisher supplies the expected scope
from the governed event and target.

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

`publish_check_run` requires `publishing_authorization` for the exact
`publication_ref` and approved `source_app_id`. The trusted
`publish_authority(authorization, publication, github)` must accept the
publication and the supplied repository-scoped publishing client.

For success, `assessment_authority(assessment_ref, publication)` must retrieve
the original Assessment from a trusted source. The publisher validates it as
an R1 artifact, then rebuilds the expected publication and requires an exact
match before POST. For either conclusion, GitHub's Check Run response must
match the requested identity and approved App ID. That response is received
after POST, so the publishing authority must verify which credentials the
client will use before it authorizes the call.

Forged `trust_basis="approved_evaluator"` metadata alone cannot POST. A
disagreeing `repository=` override is rejected. `request.external_id` must
equal `assessment_ref` when an Assessment is present.

## Limits

- Does not enable or mutate Branch Protection / Rulesets (see
  [Required Assessment Protection](required-assessment-protection.md) for the
  #61 G3/R3 read-only observation contract).
- Does not replace Native Required Contexts.
- Does not wire a production CI Job that produces live universe/admission
  inputs; trusted Workflow `publish_authority` binding lives in
  [Required Assessment Publish](required-assessment-publish.md) (#61 G2), while
  live Universe/Admission materialization remains follow-up G2 wiring.
- Declarative construction is not publish authorization.

## Directed acceptance

`tests/test_required_assessment_check.py` covers production vs sample names,
authorization-gated success POST, forged evaluator refusal, target binding /
SHA / event / Run mismatches, repository override rejection, missing
Assessment failure, and skipped/neutral / forced-success refusals.
