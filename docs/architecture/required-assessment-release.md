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

## Release policy scope

`policy_snapshot_authority` receives the R1 Profile snapshot with its actual
`assessment_scope` and the requested `publish_context`, including Repository,
Tag, mode, Head, Phase, Run and Attempt. An accepted response must supply an
`assessment_scope` independently derived from the authorized Profile for that
Release. It must match the R1 `context`, `action`, `target`, `refs` including
Ref actions, `phase`, and full `candidate` identity. The authority can map an
authorized Release Target or Ref representation to the R1 representation.
Reconciliation compares the result exactly.

The envelope records both scopes. Existing envelopes without that binding
require fresh reconciliation before they can establish verified scope.

## Manifest identity binding

R4 output records `manifest_identity` for the Manifest that passed
`release.manifest.validate`:

- `content_digest` — canonical `_digest` of the full Manifest v1;
- `mode` / `run_id` / `run_attempt` — Manifest workflow source identity;
- `artifacts[{name,id,digest}]` — Artifact ID/digest binding (not a second ledger).

Consumers compare with the same rule via `bind_manifest_identity` /
`require_reconciliation_manifest`. Changing a valid Artifact ID or Digest
changes both the Manifest identity and `reconciliation_ref`.

## Provider evidence availability

The verification and publication adapters reuse the existing Release Provider.
`provider_evidence.availability` records which kind of proof that read produced:

| Availability | Meaning | Reconciliation status |
| --- | --- | --- |
| `live` | `manifest_provider_from_verification` or `manifest_provider_from_publication` completed the existing native read | May be `verified` |
| `accepted` | A caller callback returned true. This is not a native Job/Step/Artifact read and cannot set `release_authorized` | Structural `verified` only |
| `contradicted` | The existing verifier observed a failed or missing upload step, or an ID/Attempt/digest disagreement | `rejected` |
| `unavailable` | The API or step log could not be read (401/403/404, empty log, expired origin no longer accessible) | `unverified` |
| `not_consulted` | No provider authority was supplied | `unverified` |

An unavailable step log is not a producer mismatch and is not accepted from Artifact name, HEAD, and Run alone. A still-readable expired Artifact remains proof only when that same Attempt's upload step log still binds the exact ID. Green CI and a validation-only Run do not by themselves prove a full publish or historical recovery cycle.

`validate_release_reconciliation` also requires `publish_context.validation_identity`
to equal top-level `validation_identity` for `validation_to_publication`, and
rejects a top-level Validation identity under `same_run`.

## Serialized envelope and provider trust boundary

`reconciliation_ref` binds the serialized envelope's contents; recomputing it
does **not** authenticate the writer or prove that a native Provider read
occurred. `validate_release_reconciliation()` checks structure, digest and
authorization consistency, rather than independently re-reading GitHub.

In particular, for `release_authorized=false` a deliberately modified
`status=verified` validation-mode envelope with
`authorization_basis=provider_verified` and
`provider_evidence.availability=accepted` may pass structural validation after
its `reconciliation_ref` is recomputed. The normal
`reconcile_release_assessment()` producer does not emit that combination.
It does not turn `release_authorized` true: that stronger claim requires a
native `live` Provider basis and the remaining publication conditions.
Consumers must not derive permission to publish from `status=verified`,
`authorization_basis`, or the content digest alone. This false-authorization
serialization edge remains a nonblocking follow-up observation under #61,
not evidence of a demonstrated production publication bypass.

## Native execution evidence and remaining integration scope

The real [validation-only Run 44455217421](https://github.com/dymzz/engineering-ci/actions/runs/44455217421)
on earlier candidate `f659429` completed two Attempts with a successful
`verified` Job and a native `release-manifest` upload. Both Attempts skipped
`publish`. The historical v0.7.0 Release artifacts provide partial provenance,
but some original Artifact metadata is no longer readable after retention.
These facts establish neither a live dynamic Required Release decision nor a
real Tag/Release publication followed by Draft recovery.

For the R4 code at `d9020b5`, [PR CI 44490530299](https://github.com/dymzz/engineering-ci/actions/runs/44490530299)
completed four Jobs successfully and the targeted Release tests passed. Those
are code/test evidence, not a new native publish-and-recover execution. Issue #61
continues to own the larger real Provider → Required assessment → publishing
and recovery end-to-end acceptance; merging this slice does not close #61.

GitHub [documents a per-step Job log endpoint](https://docs.github.com/en/rest/actions/workflow-jobs#download-step-logs-for-a-workflow-run-job),
but [public reports of 404 for otherwise readable Jobs](https://github.com/github/rest-api-description/issues/7369)
mean its availability for an actual Release Attempt cannot be inferred from the
API description or controlled tests. An unreadable Step log remains
`unverified/unavailable`, not a proven Artifact mismatch and never a release
grant. Any production-readiness claim for this native Provider path requires
successful observed access to the exact Job/Step/Artifact identity.

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
  ID/digest linkage to the upload step of the Manifest's Run and Attempt. |

Digest integrity is **not** authorization. `validate_release_reconciliation()`
recomputes `reconciliation_ref` **and** re-checks pure authorization-consistency
predicates (`release_authorized` ⇒ verified, non-sample, policy authorized,
provider_verified, manifest passed, no gaps/blocking reasons, Candidate alignment).

## Limits

- Does not mutate `release.yml` or replace Manifest v1 fixed check sets.
- Fixture authorities are not production trust roots.
- Post-publish / Terminal remain #61 R5
  (`required-assessment-post.md`).
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
