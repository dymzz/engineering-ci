# Required Assessment trusted publish authority (#61 G2)

Status: additive #61 G2 **trusted Workflow publish-authority contract**
(`engineering_ci.required_assessment_publish`). It supplies the production
Provider/Workflow boundary that R2 `publish_check_run` requires before POSTing
a Required Assessment Check.

It does **not** produce live Universe/Admission/Receipt inputs, verify
Rulesets/Branch Protection (#61 G3/R3), authorize Release (#61 R4/R5), or store
approvals itself.

## Construction vs authorization

```text
normalize_publisher_approval(claim)
approval_authority(context) is True   # independent controlled source
normalize_execution_identity(actual)  # executed workflow/evaluator/policy
actions_runtime_identity(GITHUB_*)
        |
        +--> verify_publisher_identity  (success and failure)
        |
workflow_publish_authority(...)       (success only)
        |
authorize_and_publish(...)
        |
        +--> failure: verified publisher, then R2 POST
             success: verified publisher + execution identity POST
```

## Real Workflow Ref

`GITHUB_WORKFLOW_REF` must be `OWNER/REPO/.github/workflows/name.yml@ref`.
Relative fixtures are rejected. Parsed workflow repository must equal
`GITHUB_REPOSITORY`.

## Independent approval verification

`normalize_publisher_approval` only validates claim structure/time. Production
trust requires an integration-supplied `approval_authority` callable (same
extension style as #63 admission verifiers) that independently confirms the
claim against a controlled source outside the evaluated PR. Caller-minted
`active` approvals that the authority rejects cannot POST.

## Execution provenance

`execution_identity` names the **actually executed** workflow SHA, evaluator
and policy. Success requires those executed revisions to be listed on the
verified approval and to equal the publication's declared refs. Declaring an
approved evaluator while executing a different revision is refused.

## Failure vs success

Both conclusions require Actions runtime + `approval_authority`-verified
publisher approval. Only `success` additionally requires `execution_identity`.

## Limits

- Does not wire a CI Job that materializes live Assessment inputs.
- Does not enable or mutate Branch Protection / Rulesets (see G3).
- Fixture `approval_authority` callables are not a production trust root.
- `GITHUB_SHA` under `pull_request` may be a merge SHA; live wiring must bind
  the evaluated Git object via event/#66 source identity (follow-up).

## Directed acceptance

`tests/test_required_assessment_publish.py` covers real workflow refs,
caller-minted approval rejection, mismatched actual evaluator, authorized new
revision acceptance, success POST, and failure POST with verified publisher
identity.
