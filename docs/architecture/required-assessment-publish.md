# Required Assessment trusted publish authority (#61 G2)

Status: additive #61 G2 **trusted Workflow publish-authority contract**
(`engineering_ci.required_assessment_publish`). It supplies the production
Provider/Workflow boundary that R2 `publish_check_run` requires before POSTing
a `success` Required Assessment Check.

It does **not** produce live Universe/Admission/Receipt inputs, verify
Rulesets/Branch Protection (#61 G3/R3), or authorize Release (#61 R4/R5).

## Construction vs authorization

```text
publication_context_from_actions(GITHUB_*)
        |
        +--> R2 publication_context bound to live Actions identity
        |
workflow_publish_authority(authority_ref, GITHUB_*)
        |
        +--> publish_authority(authorization, publication) is True
             only when authorization ≡ publication ≡ live runtime
        |
authorize_and_publish(...)
        |
        +--> success: authorize then R2 publish_check_run
             failure: POST without Actions runtime authority
```

## Trusted runtime binding

`actions_runtime_identity()` requires `GITHUB_ACTIONS=true` and a well-formed
repository / event / SHA / Workflow Ref / Workflow SHA / Run / Attempt. The
Workflow path extracted from `GITHUB_WORKFLOW_REF` must be in the allowlist
(default: `.github/workflows/ci.yml`, `.github/workflows/required-assessment.yml`).

`workflow_publish_authority` returns a callable suitable for R2
`publish_authority`. It returns exactly `True` only when:

1. `authorization.authority_ref` matches the configured authority evidence ref;
2. authorization and publication both equal the live Actions identity fields;
3. publication uses a production trust basis and the production Check name;
4. contract-sample publications are refused.

A caller-asserted `trust_basis="protected_workflow"` string alone never
authorizes the POST.

## Limits

- Does not wire a CI Job that materializes live Assessment inputs.
- Does not enable or mutate Branch Protection / Rulesets (see G3).
- Does not replace Native Required Contexts.
- Failure publications may POST without Actions runtime authority; success may
  not.

## Directed acceptance

`tests/test_required_assessment_publish.py` covers Actions identity parsing,
unlisted Workflow path refusal, matching runtime acceptance, Run/authority-ref
mismatches, contract-sample refusal, success POST with FakeGitHub, and failure
POST without Actions.
