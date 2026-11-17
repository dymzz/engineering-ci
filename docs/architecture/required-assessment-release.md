# Required Assessment → Release 对账（#61 R4）

Status: additive #61 R4 **contract / reconciliation slice**
(`engineering_ci.required_assessment_release`). It maps one immutable #61 R1
Assessment onto the existing Release Manifest v1 / Run / Artifact chain and
checks whether a frozen Profile snapshot remains authorized for
`verified` → `publish`.

It does **not** re-run Assessment (#61 R1), publish a GitHub Check (#61 R2),
read Rulesets (#61 R3), evaluate post-publish/terminal stages (#61 R5), rewrite
Manifest `checks[].status` into trusted/#63 admitted facts, or invent a second
Release decisioner.

## Position

```text
#61 R1 Assessment (immutable)          existing Manifest v1 (fixed checks)
        |                                         |
        +------------------+----------------------+
                           |
              #61 R4 reconcile_release_assessment
                           |
         verified | rejected | unverified
         release_authorized true only when verified and non-sample
```

## Inputs

- Validated R1 Assessment (`required_assessment.validate_assessment`);
- Manifest v1 document (repository/tag/commit/mode/result/checks/artifacts);
- `publish_context` — repository, tag, head SHA, mode, phase, `evaluated_at`;
- `policy_snapshot_authority` callable — proves the Assessment's frozen
  Profile revision is still authorized at publish time;
- optional `check_bindings` — Manifest check key → Assessment
  `expected_check_ref` (never invents coverage);
- optional `artifact_requirements` — Profile-declared Artifact names that must
  appear in the Manifest.

## Decision rules

1. **Identity** — Assessment candidate commit, Manifest commit and publish
   `head_sha` must agree; Assessment `phase` must equal the Release stage.
2. **Assessment** — `status=passed` required; blocked reasons become gaps.
3. **Manifest fixed checks** — original `checks[].status` retained with
   `trusted=false` / `evidence_admission=not_evaluated`. A failed Manifest
   result or required fixed check blocks Release; Job success is never rewritten
   as #63 admission.
4. **Policy snapshot** — same Candidate SHA does **not** preserve Required
   authorization. `policy_snapshot_authority` must accept the frozen Profile at
   publish time; missing → `unverified`; revoked/expired/superseded →
   `rejected`. Targeted re-assessment of changed obligations remains outside
   this module.
5. **Artifacts** — only Profile-declared requirements are enforced; absence of
   undeclared wheel/Container obligations is not invented.
6. **Contract samples** — may reach structural `verified` shape but
   `release_authorized=false`.

## Outputs

- `status`: `verified` / `rejected` / `unverified`
- `release_authorized`: production publish gate (false for samples / gaps)
- `policy_snapshot_status`: `authorized` / `rejected` / `unverified`
- `fixed_manifest_checks`: original statuses, never trusted rewrite
- `gaps[]` / `reasons[]`: assessment blocks, fixed-check failures, binding
  misses, missing artifacts, policy rejection
- `reconciliation_ref`: digest of the immutable envelope

## Limits

- Does not mutate `release.yml`, `registry.py` fixed check sets, or publish.
- Does not claim production Release acceptance; fixture authorities are not
  production trust roots.
- Post-publish / Terminal obligations remain #61 R5.
- Real Producer→digest→published-byte closure for newly declared distribution
  artifacts remains a follow-up when the authorized Profile requires them.

## Directed acceptance

`tests/test_required_assessment_release.py` covers passed Assessment+Manifest
authorization, blocked Assessment, failed fixed Manifest checks without trust
rewrite, revoked/missing/unverified policy snapshots despite same SHA, commit
and phase mismatch, missing required artifacts, unsatisfied check bindings,
and contract-sample non-authorization.
