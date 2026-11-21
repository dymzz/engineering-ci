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
and [prior 404 reports later confirmed working on 2026-11-12](https://github.com/github/rest-api-description/issues/7369)
show that historical deployment and token access differed. The endpoint's
successful availability for this Release Attempt cannot be inferred from
documentation, third-party examples or controlled tests. An unreadable Step log remains
`unverified/unavailable`, not a proven Artifact mismatch and never a release
grant. Any production-readiness claim for this native Provider path requires
successful observed access to the exact Job/Step/Artifact identity.

## D-G04 production consumer acceptance

The production status at `main@a99efd5` is `not_verified`. This baseline
builds on the official Weaver Registry and Checker (#83), Schema 9 Producer
(#84), Terminal Expected binding (#124), R4 Manifest and Provider contracts
(#121/#136), and R5 pair and chronology contracts (#123/#134). The open
[#82 Task-only A PR](https://github.com/dymzz/engineering-ci/pull/82) has
its own acceptance. Terminal B, R4 and R5 can proceed independently of
its merge. [D-G04 review](https://github.com/dymzz/engineering-ci/pull/144#pullrequestreview-5995833721)

### Observed Runs and Artifact bytes

Default-branch Post-run `44767235384/1` successfully uploaded Artifact
`14646306762` (`cicd-telemetry-44766709012-1`). The GitHub connector
downloaded the original ZIP and the computed SHA-256 matched GitHub's
`4d8ad5a93dfaf6e93a0746caf91d537c08768b40016206803bf5809c13dc34d7`.
The prior native `gh` HTTP 401 remains an observation about that client and
credential. The readable ZIP contains the Candidate, API witness, OTLP
traces and logs, CLI output, exit record and end-time policy.
[Producer Run and Artifact](https://github.com/dymzz/engineering-ci/actions/runs/44767235384) ·
[Source Run](https://github.com/dymzz/engineering-ci/actions/runs/44766709012)

The archived Attempt and separately read GitHub Attempt 1 Jobs agree on
the four completed, successful Jobs for source Run `44766709012` and HEAD
`a99efd5b59dde41de3ccea82c4d89c2b0e962669`. The comparison matched each
Job's ID, name, status, conclusion and SHA. The raw API witness matches its
Candidate digest
`4c7f21d10297c0bda83d499f1e8c14d28476acb9624920d694aea66a028ddf9c`.
`reconcile_expected_trace` matched all four Task Span IDs and their trace
and parent references against the independent Job IDs. The OTLP
`otel.json` digest is
`a9c0a4caa333ee69b87598746d32b10febce0de294fc22ea968e95f45ded3f67`.

The archive records `producer.exit=0`, `status=partial`,
`evidence_class=candidate_record` and `reason=end_time_uncertain`. The
expected Pipeline Root is absent from `otel.json`. Terminal provenance
records `root_emission=not_emitted` and `30,853,911,040 ns` uncertainty
with an empty `max_end_uncertainty_ns` budget. Claims
`github_authenticated`, `weaver_pass`, `bundle_delivery_complete`
and `otlp_delivered` are false. This verifies a real no-Root input and
Task trace identity. Native Weaver exit and findings, independent Issuer
authentication, Receiver ACK and Terminal execution remain unverified.

Four additional default-branch Post-run archives were readable and matched
their GitHub SHA-256 values. Artifact `14648469807` came from Post-run
`44775634689/1` for source `44775264598/1`, `14648877313` from
`44775134954/1` for `44774736338/1`, `14648263310` from `44773808079/1`
for `44773475745/1`, and `14645279469` from `44769863530/1` for
`44769458290/1`. Each recorded `end_time_uncertain`, zero SERVER
spans, and no confirmed bundle delivery. The default Workflow supplies
`observed_upper_bound` with no approved uncertainty budget. Its GitHub
Attempt adapter does not assign an exact completion time. Terminal positive
acceptance still needs a separately sourced authoritative end time or a
governed observation budget and a matching live capture.
[Post-run Workflow](../../.github/workflows/cicd-telemetry-post-run.yml) ·
[GitHub Attempt adapter](../../src/engineering_ci/cicd_telemetry.py)

Historical Release Run `39614205390/1` has successful `verified` and
`publish` Jobs. Its current Actions Artifact listing returns zero entries.
This establishes that the native Release path ran on that earlier candidate.
The native Release API reports `v0.7.0` as Release ID `419641784`,
published at `2026-10-21T14:12:08Z`, with Manifest asset `662727226`.
A current candidate still needs its own native Provider and lifecycle
authority evidence.
[Historical Release Run](https://github.com/dymzz/engineering-ci/actions/runs/39614205390) ·
[v0.7.0 Release](https://github.com/dymzz/engineering-ci/releases/tag/v0.7.0)

Current-candidate Release Run `44843103748/1` ran with `publish=false` at
`a99efd5b59dde41de3ccea82c4d89c2b0e962669`. The `verified` Job succeeded
and `publish` was skipped. The native read verified `release-manifest`
Artifact `14681683214` from `verified` Job `136657199935`, upload Step 7
(`Preserve validation Manifest`).
The downloaded ZIP matched GitHub's SHA-256
`f2e2a084e301a105e3494cd19fe6ab75c0b2e39dc213a7a92f6c0c867e2126fd`.
The canonical Manifest v1 digest was
`sha256:11f4e1dea85790308f8e67e08a3c1107b9a902a92f8fa418a9491b6ebb9a9a5c`.
The native R4 adapter completed `verification.evidence` against the four
original Artifacts and their upload Step identities and digests. The CLI with
the explicit Run returned `validated` and `publication.status=not_published`.
The existing `v0.7.0` Tag still points to `9d03ade4f2af89538c9a0362beae55c9a0835e18`.
[#149](https://github.com/dymzz/engineering-ci/pull/149) separates validation
of this candidate from the historical Release lookup. This Run establishes
native Provider evidence for validation mode.
[Validation Run](https://github.com/dymzz/engineering-ci/actions/runs/44843103748) ·
[Manifest Artifact](https://github.com/dymzz/engineering-ci/actions/runs/44843103748/artifacts/14681683214)

### Producer, verifier and consumer evidence

| Requirement and implementation owner | Producer and observed identity | Scope, time and evidence | Verifier and current evidence level | Final consumer and acceptance result |
| --- | --- | --- | --- | --- |
| [#75](https://github.com/dymzz/engineering-ci/issues/75) / [#74](https://github.com/dymzz/engineering-ci/issues/74), reuse #83/#84 | `cicd-telemetry-post-run.yml` and `cicd_telemetry`, Runs `44767235384/1` → `44766709012/1`, Artifact `14646306762` and the digests above | Four completed Jobs, four Task Spans, Root withheld, producer exit 0 and no observed Receiver ACK | Bytes and Task trace identity verified. Pinned Registry, applicable Native Live/Terminal result and required delivery await independent verification | #74 Native Checker into #52 Report. Real no-Root input confirmed. Need a genuine Task-only Native positive and a Terminal positive with an authorized Root and valid time quality |
| [#52 D4](https://github.com/dymzz/engineering-ci/issues/52), [#66-C](https://github.com/dymzz/engineering-ci/issues/66) and [#63](https://github.com/dymzz/engineering-ci/issues/63), reuse #124 | Authorized #66-B2 Check Expected plus completed Run/Attempt and #74 Native Result | `scope_id`, `expected_check_ref`, current Profile/Phase, Rule/Source, Candidate and original capture inputs | Terminal Plan validation, #66-C Receipt and #63 Admission. [#145](https://github.com/dymzz/engineering-ci/pull/145) at `b72e620` fixes the Native Correlation handoff and passed its contract review | R1 per-check Coverage, Native FAIL and Remaining Work. The examined archives supply real no-Root inputs. A qualified native positive and authenticated handoff still need evidence |
| [#61 R4](https://github.com/dymzz/engineering-ci/issues/61), E065/E066/E067/E072, reuse #121/#136 | Release Run `44843103748/1` provides the current candidate's validation Manifest. Publication needs current non-sample R1 and authorized Profile/Policy/Handoff | Repository/Tag/HEAD, action/target/ref/phase, approved validity, exact Manifest bytes, Run/Attempt/Job/upload Step and Artifact ID/digest | Native Provider verified the exact validation Manifest and original Artifacts. [Draft #147](https://github.com/dymzz/engineering-ci/pull/147) at `72408d6` checks the write request and any different retained Manifest under current authority. Controlled tests cover cross-Attempt recovery, future R1, required Check mappings and zero-write refusals | Release CLI consumed the same validation Run and returned `validated`. For publication, `workflow_actions release publish` → `publish_release` enforces R4 before mutation and records both reconciliation identities when recovering. The default Workflow lacks a protected `release_authority`. Authorized production writes and receipts remain unverified |
| [#61 R5](https://github.com/dymzz/engineering-ci/issues/61), E068, reuse #123/#134 | Independently authenticated Run/Release/Artifact event, ordered Phase/Due authorities and two same-Candidate R1 assessments | Trigger occurrence and receipt, Run/Attempt, target/ref, Artifact digest when applicable, evaluation time and authorized due window | [Draft #148](https://github.com/dymzz/engineering-ci/pull/148) at `1904aa0` binds native publication as the occurrence and verifies receipt separately. Controlled tests cover delayed and forged receipt, refusal, due and late outcomes | The Draft consumes the pair in Release inspection and retains the historical publication. Governed live R1/Trigger inputs and any Policy-selected Promotion/Disposition receipt remain unverified |

On base `a99efd5`, the two code patches applied together and passed
237 targeted tests and 25 subtests. These tests use a recording API and
controlled authorities.

### Terminal Source and Expected handoff

1. Use the original `workflow_run.completed` event or explicitly authorized
   dispatch with GitHub Attempt and complete Jobs records. Keep the Producer
   workflow identity separate from the source Run's Candidate SHA and bind
   the original API pages, Candidate and Capture bytes. Obtain independent
   authorization of GitHub source membership before forwarding a trusted
   Receipt. The preserved no-Root Artifact remains the negative input.
2. Exercise the existing pinned #74 Native Live against a legitimate
   Task-only capture, retaining tool version, official Registry SHA,
   capture hash, actual Native exit, Findings, matched signals and Report.
   For Terminal, require a real completed Run with a unique correlated Root,
   acceptable time provenance and Result. Exercise missing Root, wrong
   Run/Attempt or Result and unusable clock bounds. Missing Root evidence
   stays unverified or blocks the applicable Required obligation even if
   Native Live succeeds. For applicable delivery obligations retain the
   actual Receiver ACK and protocol and distinguish preparation, export,
   RPC acceptance and Receiver observation.
3. Bind the pre-authorized #66-B2 `eci/weaver/terminal` Check Expected
   through `terminal_expected` and `selection.post_completion_plan()` /
   `validate_post_completion_binding()`. Pass the completed Run and
   Capture/Registry/Config/Advice bytes as post-completion inputs.
   Preserve independently governed Span Expectations as the Checker input.
   Feed the actual Native Result to `reconciliation.reconcile_expected_results`,
   then to #63 Issuer/Source/Implementation/Receipt admission and R1.
   Assert that Native FAIL and missing inputs reach R1 per instance.
   Future post-completion obligations leave the source Workflow free to
   finish. A current Required obligation may depend on an already completed
   historical Run when the authorized scope requires it.

### R4 at the existing publication boundary

1. In Release `verified` / `publish` and `release.publishing`, obtain the
   same candidate's current non-sample R1, complete Profile Artifact
   obligations, and independently verified Policy revision, scope,
   approval and validity at publication time. Reuse the native
   `manifest_provider_from_verification` / `manifest_provider_from_publication`
   adapters. Preserve the exact Job/upload Step and Artifact ID/digest
   evidence under the Manifest's Run and Attempt.
2. Produce R4 with `reconcile_release_assessment`, using `same_run`
   for the same Run/Attempt or a governed `validation_to_publication`
   handoff for two distinct Run IDs. Validate with
   `validate_release_reconciliation` and bind the exact Manifest and
   Release identity with `require_reconciliation_manifest`. A validation
   reconciliation may be verified with `release_authorized=false`.
3. Consume the current authorization in `publish_release` before any
   Tag, Release or asset mutation, including asset recovery. Require
   `status=verified`, `release_authorized is True` and
   `contract_sample is False` with the native and independent evidence
   above. Draft recovery requires current authorization and validation
   of the saved Manifest against its original Run and Attempt. Before
   promoting a Draft that will retain a different Manifest, require
   fresh R4 for that original identity and explicit authority linking
   it to the current write request. Continue
   to verify historical final Release identity and the saved Manifest
   on the read side. Policy expiry or revocation changes eligibility for
   future writes or promotion and preserves the recorded publication
   fact. Propagate write refusal through the actual `workflow_actions`
   output and exit status.
4. Exercise authorized first publish and recovery with a recording API.
   Require zero writes for absent or blocked R1, expired/revoked/wrong
   scope policy, Manifest substitution, wrong Artifact ID/digest/Attempt
   or upload Step, unavailable Provider, wrong Handoff, validation-only
   and sample evidence. Production acceptance needs a separately approved
   candidate, production credentials and the actual native receipts. A
   validation-only Run proves the no-write route. A publication claim
   requires an authorized publication receipt.
   [Release Workflow](../../.github/workflows/release.yml) ·
   [Publishing entry point](../../src/engineering_ci/release/publishing.py)

### R5 at the existing Verification and Disposition boundary

1. Bind the Trigger to the original completed Run, publication or Artifact
   event and its independent authority. Verify event type, repository,
   Run/Attempt, candidate SHA, target/ref, applicable Artifact ID and
   digest, and observation time. Preserve native publication time as
   the occurrence and authenticate any delayed receipt separately.
   A post-run Workflow's own HEAD must
   remain separate from the source HEAD.
2. Use the exact Prior and Post R1 Assessments of the same Candidate and
   governing Profile/Phase Order. Reconcile via `reconcile_post_stage`
   and validate via `validate_post_stage`. Verify actual evaluation time
   and authorized Due Window, including any approval of specific late
   evidence or a historical reevaluation. Preserve prior results and
   pair-level `lifecycle_all_phases_passed=false`.
3. Apply the existing governed Profile or Policy to select the destination
   for each duty. Record the actual entry point and outcome for the
   applicable duties.
   - **Release inspection** uses `release.verification.published` and
     `cli.release.inspect`. Record the historical Release identity and
     current post-stage status independently. This receipt proves that
     the read-side consumer received and exposed the result.
   - **Promotion** consumes the result at the specific promotion write
     boundary designated by the Policy. When the obligation blocks
     progression, require that consumer's refusal and zero promotion
     writes. Pending or blocked evidence conveys no progression grant.
   - **Notification or reevaluation** uses the action named by the
     Profile. For notification retain the destination and native action
     receipt. For reevaluation retain the triggering input and the
     resulting evaluation Run/Attempt or governed receipt. A
     `disposition` value records a requested outcome. An execution
     receipt establishes that the action occurred.

   The repository currently exposes the Release inspection entry points.
   A governed production Profile and its selected Promotion,
   Notification or Reevaluation destinations are still needed for
   those duties. Leave a disposition unverified until its actual
   consumer and result can be identified.
4. Require original Run/Attempt and consumer evidence for a valid
   same-candidate trigger. Exercise missing or forged event, mismatched
   Attempt/Artifact/Ref/Candidate, future Trigger, reversed chronology,
   unauthorized Phase, not-yet-due pending, overdue missing evidence,
   accepted and rejected late evidence, post R1 failure and authorized
   historical reevaluation. Check the consumer result and prior state in
   each case. [R5 contract](required-assessment-post.md) ·
   [Release Verification](../../src/engineering_ci/release/verification.py)

Declare D-G04 end-to-end verified only with the scoped Terminal positive,
current R4 authorization consumed before writes, and real R5 Trigger and
Due decisions consumed by their destination. Record each implementation PR,
exact commit, Run/Attempt, Artifact ID/digest, original native receipt and
final verification, disposition or release result in the table. Keep open
links `not_verified` and use `failed` for an observed native failure of
the specific check. The [#125 evidence index](https://github.com/dymzz/engineering-ci/issues/125)
tracks cross-chain acceptance.

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
