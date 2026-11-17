# Required Assessment trusted publish authority (#61 G2)

Status: additive #61 G2 **trusted Workflow publish-authority contract**
(`engineering_ci.required_assessment_publish`). It supplies the production
Provider/Workflow boundary that R2 `publish_check_run` requires before POSTing
a Required Assessment Check, bound to an **independently issued** publisher
approval.

It does **not** produce live Universe/Admission/Receipt inputs, verify
Rulesets/Branch Protection (#61 G3/R3), authorize Release (#61 R4/R5), or mint
approvals from the evaluated PR.

## Construction vs authorization

```text
normalize_publisher_approval(independent approval)
        |
actions_runtime_identity(GITHUB_* real OWNER/REPO/...@ref)
        |
        +--> verify_publisher_identity  (success and failure)
        |
workflow_publish_authority(...)          (success only)
        |
        +--> publish_authority is True only when
             approval ≡ runtime ≡ publication evaluator/policy
        |
authorize_and_publish(...)
        |
        +--> failure: verified publisher, then R2 POST
             success: verified publisher + approved evaluator/policy POST
```

## Real Workflow Ref

`GITHUB_WORKFLOW_REF` must be the real Actions form
`OWNER/REPO/.github/workflows/name.yml@ref`. Relative
`.github/workflows/...@ref` fixtures are rejected. The parsed workflow
repository must equal `GITHUB_REPOSITORY`.

## Independent publisher approval

`normalize_publisher_approval` requires a source not controlled by the
evaluated PR. It binds:

- repository + workflow repository/path/sha;
- allowed events;
- approved evaluator and policy evidence refs;
- authority evidence ref;
- status (`active` / `revoked` / `expired`) and optional validity window.

A caller-supplied `authority_ref` that merely matches itself is not approval.
PR-modified evaluator/policy digests that are absent from the approval cannot
POST `success`.

## Failure vs success

Both conclusions require a controlled publisher identity (Actions runtime +
approval). Only `success` additionally requires the publication's
evaluator/policy to be listed on that approval.

## Limits

- Does not wire a CI Job that materializes live Assessment inputs.
- Does not enable or mutate Branch Protection / Rulesets (see G3).
- Does not replace Native Required Contexts.
- `GITHUB_SHA` under `pull_request` may be a merge SHA; live wiring must bind
  the evaluated Git object via event/#66 source identity (follow-up).

## Directed acceptance

`tests/test_required_assessment_publish.py` covers real workflow refs,
wrong-repository refs, independent approved/unapproved evaluator/policy,
revoked approval, success POST, and failure POST that still requires publisher
identity.
