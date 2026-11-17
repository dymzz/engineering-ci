# Required Assessment → Release 对账（#61 R4）

Status: additive #61 R4 **contract / reconciliation slice**
(`engineering_ci.required_assessment_release`). It maps one immutable #61 R1
Assessment onto the existing Release Manifest v1 / Run / Artifact chain and
checks whether a frozen Profile snapshot remains authorized for
`verified` → `publish`.

It does **not** re-run Assessment (#61 R1), publish a GitHub Check (#61 R2),
read Rulesets (#61 R3), evaluate post-publish/terminal stages (#61 R5), rewrite
Manifest `checks[].status` into trusted/#63 admitted facts, or invent a second
Release decisioner / looser Manifest validator.

## Position

```text
#61 R1 Assessment (immutable)
existing Manifest v1  --release.manifest.validate-->  identity + fixed checks
authorized Profile Artifact obligations + authorities
        |
        +--> reconcile_release_assessment
                |
                status: verified | rejected | unverified
                release_authorized: only provider_verified + verified + non-sample
```

## Canonical Manifest gate

`_load_manifest_v1()` **always** calls `release.manifest.validate()`. Invalid
tools, `required=false` fixed Checks, wrong Job/Step identity, wrong aggregate
`result`, or bad Artifact ID/digest/URL fail closed before any Release
authorization can be minted. R4 does not reimplement a parallel v1 schema.

## Manifest Mode / Run binding

`publish_context.manifest_binding`:

| Binding | Rule |
| --- | --- |
| `same_run` | Context `mode` / `run_id` / `run_attempt` equal Manifest |
| `validation_to_publication` | Manifest is validation identity; context is a **distinct Run ID** publication Run (same Run / different Attempt is **not** distinct); `handoff_authority` must accept the linkage |

## Validation vs publication authorization

| Stage | `status=verified` | `release_authorized` |
| --- | --- | --- |
| `mode=validation` | Allowed for structurally complete reconciliation | **Always false** — validation is not a publish grant |
| `mode=publication` + provider basis + passed Assessment + authorized Artifact set | Required | May be true when all other gates pass |

A future publish boundary must still recheck policy authorization and exact
current Run/Attempt/Artifact evidence; this module does not perform Tag/Release
publication.

## Manifest identity binding

R4 output records `manifest_identity` for the Manifest that passed
`release.manifest.validate`:

- `content_digest` — canonical `_digest` of the full Manifest v1;
- `mode` / `run_id` / `run_attempt` — Manifest workflow source identity;
- `artifacts[{name,id,digest}]` — Artifact ID/digest binding (not a second ledger).

Consumers compare with the same rule via `bind_manifest_identity` /
`require_reconciliation_manifest`. Changing a valid Artifact ID or Digest
changes both the Manifest identity and `reconciliation_ref`.

`validate_release_reconciliation` also requires `publish_context.validation_identity`
to equal top-level `validation_identity` for `validation_to_publication`, and
rejects a top-level Validation identity under `same_run`.

## Artifact obligations

Caller supplies an authorized Profile Artifact set:

- `completeness=empty` — no Profile-declared distribution artifacts (Manifest v1
  fixed artifacts still apply via `manifest.validate`);
- `completeness=complete` — nonempty `items[{name, obligation_ref}]` must all
  appear in the Manifest.

`artifact_obligation_authority` proves the set is the current authorized
Profile projection. A bare name list without that authority cannot authorize.

## Authorization basis

| `authorization_basis` | Meaning |
| --- | --- |
| `structural_fixture` | Contract shape only; **never** `release_authorized=true` |
| `provider_verified` | `manifest_provider_authority` accepted. Production adapters are
  `manifest_provider_from_verification(api)` /
  `manifest_provider_from_publication(api)`, which call existing
  `verification.evidence` / `publishing.published_evidence` for Artifact
  ID/digest/Run linkage — R4 does not invent a second remote verifier. |

Digest integrity is **not** authorization. `validate_release_reconciliation()`
recomputes `reconciliation_ref` **and** re-checks pure authorization-consistency
predicates (`release_authorized` ⇒ verified, non-sample, policy authorized,
provider_verified, manifest passed, no gaps/blocking reasons, Candidate alignment).

## Limits

- Does not mutate `release.yml` or replace Manifest v1 fixed check sets.
- Fixture authorities are not production trust roots.
- Post-publish / Terminal remain #61 R5.
- Real Provider verification is required only when claiming production
  publication authorization.

## Directed acceptance

`tests/test_required_assessment_release.py` covers genuine v1-valid success,
invalid v1 Manifest refusal, blocked Assessment, failed fixed checks without
trust rewrite, revoked/missing policy despite same SHA, same-run Mode/Run
mismatches, validation→publication handoff, Profile Artifact complete/empty/
missing sets, structural vs provider basis, contract-sample non-authorization,
Manifest identity binding / consumer mismatch, inconsistent serialized
Validation identity rejection, and recomputed-digest authorization-consistency
rejection.
