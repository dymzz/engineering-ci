# Required Assessment (#61 R1)

Status: additive #61 R1 contract and first implementation slice
(`engineering_ci.required_assessment`). It performs the staged Required
judgement for one Action x Target x Phase x Ref scope.

It does **not** publish a GitHub Check (#61 R2; see
[Required Assessment Check](required-assessment-check.md)), inspect
Rulesets/Branch Protection (#61 R3), map Assessment onto Release Manifest /
policy-snapshot publish gates (#61 R4; see
[Required Assessment → Release 对账](required-assessment-release.md)) or
evaluate a later stage by itself (#61 R5). It also does not re-derive policy
obligations (#66-B2), admit trusted evidence or prove historical reuse (#63),
or rebuild the structural Binding/Reconciliation receipt (#66-C).

## Position

```text
authorized Profile/Expected v2 (#66-B2)  ->  Required Obligations universe
#66-C structural receipt                   ->  current-execution facts
#63 trusted admission (contract)           ->  per-instance success/fail/conflict
domain Producer/Verifier                   ->  non-check evidence
                                               |
                                       #61 R1 Required Assessment
                                               |
                          passed | blocked, with four independent facts
```

The module consumes five already-produced facts and issues one immutable,
versioned judgement:

- `engineering-ci-required-obligations` v1 — the authorized universe
  projection (Profile/authority window, governance-domain closure, evaluation
  input, Check-domain identity and the obligation instances).
- `engineering-ci-expected-checks` **Schema v2** — the #66-B Check projection.
  The assessment consumes one single-Ref scope; a multi-Ref container needs an
  explicit `ref_scope_id` child, exactly like #66-C.
- `engineering-ci-evidence-admission` v1 — the #63 trusted admission
  projection for the same exact scope. This is the consumer contract R1
  requires; the base per-instance shape is the frozen L2 sample in
  extended with the authenticated failure/conflict and adjudication facts
  required by #61 G1.
- `engineering-ci-expected-result-reconciliation` v1 — the #66-C structural
  receipt. R1 validates it with `reconciliation.validate_receipt()` and only
  cites it; it never upgrades `matched` into trusted success.
- `engineering-ci-non-check-evidence` v1 — normalized non-check evidence from
  domain Producers/Verifiers. R1 verifies identity, independent verifier
  authorization, subject/digest binding, validity and the reported result; it
  does not implement the domain verification algorithm.

R1 never infers the Required set from executed Checks, successful Jobs, a
matched receipt or an old PASS. A verified empty universe is legal and passes;
an unproven universe is blocked even when every observed Check passed.

## Identity and instance rules

- Check instance key: **`(scope_id, expected_check_ref)`**. The assessment
  never aggregates by Check name. Different Refs or scopes with the same
  `tach` instance are distinct.
- Non-check instance key:
  `canonical digest(obligation_ref, kind, target_ref, action, phase,
  candidate_digest, source_digest)`, exposed as
  `required_assessment.obligation_instance_ref()`. The same `obligation_ref`
  across Pre-/Post-publish phases, two Target/Ref scopes or two candidate/source
  identities is a different instance.
- An obligation with several required Check instances (M:N) is satisfied only
  when **each** Check instance is satisfied. Check-level OR/alternative
  witnesses remain #52 `ToolRequirement` semantics inside one Check; they never
  become a cross-Check OR for an obligation.
- Evidence and failures are matched by the same identity. Out-of-scope facts are
  retained in the assessment but never counted against the current instance.

## Applicability and due point

The universe declares each obligation instance as `required`,
`not_applicable` or `not_yet_due`. The evaluation `phase` and the per-obligation
`phase` come from the authorized #66-B2 evaluation (approved EffectiveProfile
phase and obligation phase applicability); R1 does not infer a phase from Event
or Job names and does not keep its own policy table or built-in phase order.

A phase different from the assessed phase is **not** automatically a future
phase. The universe must supply the authorized `evaluation.phase_order`
(`ref` plus the ordered phase names) and R1 only derives `not_yet_due` when that
order proves the declared phase (or `due_phase`) comes after the assessed phase.
A phase before the assessed phase is an expired Required and stays
`unverified`; a missing/unknown order or a phase absent from it is also
`unverified` and blocks the scope.

- `not_applicable` requires an `applicability_basis_ref` **and** proof that the
  authorization covers this exact Required instance; a basis reference alone is
  never authorization.
  - For a Check obligation R1 recomputes the authorized waiver from the
    EffectiveProfile for the actual `(check name, obligation_ref, target_ref,
    ref_action, phase)` scope using the existing #66-B2
    `_parse_profile_obligations()` semantics. A waiver must be present in the
    Profile (`allowed_inapplicability` or obligation `allow_inapplicable`);
    otherwise `applicability_unauthorized`.
  - For a Non-Check obligation the universe must carry a controlled
    `applicability_authorization` (authority ref equal to the basis ref, rule
    ref equal to `verification_rule_ref`, scope equal to the obligation's
    Target/Action/Phase, plus validity/revocation). The authorization must also
    bind the concrete instance: either `obligation_instance_ref` equal to
    `obligation_instance_ref(obligation)`, or an explicit `scope.obligation_ref`
    (and, when the obligation declares Candidate/Source digests, matching
    digests). Shared Rule/Authority/Target alone never authorizes a different
    `obligation_ref` or Candidate. Missing, mismatched, unbound, wrong-scope,
    expired/not-yet-valid or revoked authorization keeps the instance
    `unverified`. R1 adds no domain Policy/Verifier/Authorization registry; the
    fact must come from the authorized #66-B2/domain contract.
- `not_yet_due` requires an `applicability_basis_ref` and either a provably
  future phase in `phase`/`due_phase` or a future `due_at`. A current/past
  phase, an elapsed due time, or no authorized order is `unverified`.
- A `required` obligation with a provably future phase is `not_yet_due`; a
  past phase is `unverified` (`phase_already_passed`); an unavailable or
  unknown order is `unverified` (`phase_position_unproven`). `not_yet_due` is
  never used for a currently-required obligation whose evidence is missing.

### Inapplicability authorization facts

A Non-Check inapplicability authorization takes this controlled shape in the
universe obligation:

```json
"applicability": "not_applicable",
"applicability_basis_ref": {"name": "…", "version": "…", "sha256": "…"},
"applicability_authorization": {
  "authority_ref": {"name": "…", "version": "…", "sha256": "…"},
  "rule_ref": {"name": "…", "version": "…", "sha256": "…"},
  "obligation_instance_ref": "sha256:…",
  "scope": {
    "target_ref": "refs/heads/main",
    "action": "publish",
    "phase": null,
    "obligation_ref": "o/sbom",
    "candidate_digest": null,
    "source_digest": null
  },
  "valid_from": null, "valid_until": null, "revoked_at": null
}
```

`authority_ref` must equal `applicability_basis_ref`; `rule_ref` must equal the
obligation `verification_rule_ref`; `scope` Target/Action/Phase must equal the
obligation's. Bind the instance with `obligation_instance_ref` and/or
`scope.obligation_ref` (plus Candidate/Source digests when the obligation
declares them). The fact is consumed, not issued, by R1.

### Phase authorization

Expected v2 for a phase-bearing authorized Profile carries `phase` (equal to
the Profile phase) and `phase_status`. R1 consumes that status:

- The current #66-B2 producer marks a phase-bearing projection
  `phase_status="unverified"` because the actual CI/Release lifecycle Phase is
  not yet independently proven; the parent cannot be `complete`. R1's Check
  Domain fact blocks with `expected_phase_unverified`.
- A multi-Ref child may keep a local `status="complete"`, but that is local
  Check-structure completeness only. When the parent `phase_status` is
  `unverified`, R1 blocks the staged Check Domain authorization while retaining
  every already-proven child Check row.
- No trusted lifecycle Phase Producer contract exists in this repository yet.
  While `"verified"` is retained as a schema evolution shape on `phase_status`,
  R1 never trusts a bare self-reported `phase_status="verified"` string without
  an authenticated #66-B2 / lifecycle Phase Producer proof contract, and fails
  the Check Domain closed with `expected_phase_proof_unverified`.
- A no-Phase Profile uses `phase_status="not_applicable"` (or omits the field);
  its complete Check Domain remains legal.

### Membership vs due applicability

Universe completeness and current due applicability are separate judgements.
Membership completeness is reconciled against the authorized Profile's
applicable `Action × Target × Phase` scope (`expected_checks._obligation_matches_scope`):

- Future-only deferral is allowed only when the authorized Profile explicitly
  constrains that obligation to a provably future phase (`phases[]` excluding
  the current phase, with a complete `phase_order`).
- Obligations whose Profile declaration covers the current phase (including
  obligations without `phases[]`) must appear in the universe with a
  current-phase instance; a future-phase instance (`phase="post_publish"`)
  with the same `obligation_ref` never substitutes for the missing current
  instance (`required_check_omitted_by_universe` /
  `non_check_obligation_omitted_by_universe`), and dual current/future instances
  for one `obligation_ref` remain independent so missing current evidence
  still blocks.
- For Check obligations the actual `(obligation_ref, expected_check_ref)` pair
  is checked, not the Check Ref alone: a different `obligation_ref` referencing
  the same Check Ref cannot substitute (`required_check_association_unresolved`).

## Evaluation scope and subject binding

Universe scope identity is not only a Profile/Check hash. R1 also binds the
evaluation to the authorized Profile, Expected Event and checked Source:

- `evaluation.context`, `action`, `target` must equal the authorized Profile's
  values (and the target must be allowed by the Profile `targets` when declared).
- When the authorized Profile declares `phase`, it must equal
  `evaluation.phase`. #66-B2 is the producer of that profile phase; without it
  R1 cannot independently prove the phase binding and production wiring must
  supply it.
- Every `evaluation.refs[].target_ref` must appear in the Expected Event refs
  (`base_ref`/`head_ref`/reference refs) when those are present.
- A selected multi-Ref child binds its real `(target_ref, ref_action)` to a
  single universe evaluation ref: the same target with `update` cannot borrow a
  `delete` evaluation (`evaluation_ref_action_mismatch`), and a multi-ref
  universe aggregate is not a valid child scope
  (`evaluation_ref_scope_mismatch`). A single-Ref Event that declares
  `ref_action` must also match every universe ref action.
- An obligation that declares `candidate_digest` requires an actual candidate
  identity: no candidate identity → `obligation_candidate_identity_missing`;
  a different candidate → `obligation_candidate_out_of_scope`.
- An obligation that declares `source_digest` requires both the evaluated
  candidate and the Expected checked Source to carry the same digest. A missing
  candidate/Expected Source digest → `obligation_source_identity_missing`; a
  mismatch → `obligation_source_out_of_scope`. Raw 64-hex and `sha256:` forms
  of the Expected `source_ref.content_digest` are normalized to the same
  identity.
- Legitimately candidate/source-less scopes are not forced to invent an
  Artifact or Source identity; only the requirement each obligation actually
  declares is enforced. No Source Store or policy evaluator is added.

## Assessment statuses

| Status | Meaning |
| --- | --- |
| `satisfied` | The applicable Required instance has genuine, in-scope evidence: #52 Native Result admitted by #63 (and, for a current Run, matched by #66-C), or a domain-verified non-check result bound to the authorized verifier authorization/rule and target subject. |
| `not_applicable` | The authorized policy declares the instance inapplicable with a verifiable basis. No evidence is required. |
| `not_yet_due` | The authorized phase order proves a future phase, or an authorized future due point exists. Recorded; not current evidence, not a failure of this phase. |
| `unmet` | A positive failure fact applies: an authenticated native failure/rejection, an unmet domain verdict, or a rejected admission coverage. |
| `unverified` | The Required fact cannot be proven: missing admission/receipt/non-check evidence, unauthenticated issuer or raw result, unresolved conflict, unusable/expired/revoked evidence, unbound verifier, out-of-scope/omitted instance, missing basis, past/unproven phase position, or a Check definition/rule that is not resolved. |

## Four independent verification facts

The gate is not one boolean deriving from another fact. R1 keeps these separate
because they have different owners:

| Fact | Owner | Verified only when |
| --- | --- | --- |
| `verification.authority` | authorized Profile/policy source | authority status is `verified`, the assessment time is inside the effective window, the profile is not revoked, and the approval source is authoritative. |
| `verification.check_domain` | #66-B Expected projection | the Expected scope is `complete`, has no conflicts, declares `authority_status="verified"`, uses the completeness label for its scope kind (single-Ref `expected_domain_complete`, per-Ref child `per_ref`), has no unverified or unauthenticated phase (`phase` must be `None` until an authenticated upstream lifecycle Phase Producer proof contract exists; `phase_status="unverified"` and bare self-reported `phase_status="verified"` both block), and its required Check definitions/rules are resolved. |
| `verification.obligations_universe` | #66-B2 governed evaluation | the universe is `complete`, its governance domain closes (all delegated domains included and closed), its Profile/Check-domain and evaluation scope identities match the Expected scope (for multi-Ref children matching upstream `remote_ref or local_ref` paired with `ref_action`), every Expected Required Check/Non-Check and current-phase Profile obligation has a matching current-phase instance (only Profile-restricted `phases[]` strictly in the future allow future-only enumeration), and declared candidate/source identities match the evaluated and Expected identities. |
| `verification.evidence_satisfaction` | #63 / domain verifiers / #66-C | every current-due obligation is `satisfied` or has an authorized `not_applicable` (Profile waiver for a Check, valid scoped authorization for a Non-Check), and no in-scope Check or obligation row is `unmet`/`unverified`. |

`status` is `passed` only when all four facts hold. Otherwise it is `blocked`
and `blocked_reasons` lists the failed facts per instance. A failed fact never
erases an independently proven row: an authority/closure failure leaves a
genuine admitted Check `satisfied`; it only blocks the aggregate gate.

## Conflict and adjudication consumption

For every Check instance whose instance scope matches the current
Target/Phase/Action and candidate identity, R1 reads:

- accepted success coverage (`admission_status="admitted"`) from #63;
- authenticated native failures/conflicts in the same instance;
- authoritative adjudications for those result IDs (retry/supersede/conflict
  resolution), including their authority/policy references, decision time,
  validity window and revocation.

A historical admitted PASS plus an in-scope current failure stays `unmet`
until an effective adjudication covers that failure; an admitted coverage plus
unresolved conflict stays `unverified`. A valid retry/adjudication yields
`satisfied` only together with admitted current coverage. An old failure whose
instance scope (Target/Ref, phase, rule, profile) does not match the current one
is reported as out-of-scope and never blocks unrelated success. #63 remains the
owner of the raw facts and adjudication basis; R1 does not re-run admission or
native witness selection.

## Input shape highlights

`engineering-ci-required-obligations` v1:

```json
{
  "kind": "engineering-ci-required-obligations",
  "schema_version": 1,
  "profile": {"profile_ref": {"name": "…", "version": "…"},
               "profile_revision": "…", "approval_source": "org-governance",
               "profile_sha256": "sha256:…", "approval_digest": "sha256:…",
               "phase": "pre_publish"},
  "authority": {"status": "verified", "source_ref": {"name": "…", "version": "…", "sha256": "…"},
                 "approved_at": "…", "effective_at": "…", "valid_from": null,
                 "valid_until": null, "revoked_at": null, "evaluated_at": "…"},
  "governance_domain": {"closure_status": "closed",
                         "closure_ref": {"name": "…", "version": "…", "sha256": "…"},
                         "scope": "organization", "delegation_domains": [],
                         "inheritance": [], "exclusions": []},
  "evaluation": {"context": "release", "action": "publish", "target": "candidate",
                  "phase": "pre_publish",
                  "phase_order": {"ref": {"name": "org/release-phase-order",
                                           "version": "1", "sha256": "…"},
                                   "phases": ["pre_publish", "publish", "post_publish"]},
                  "refs": [{"target_ref": "refs/heads/main", "ref_action": "update"}],
                  "candidate": {"commit": "…", "tree": "…"},
                  "evaluated_at": "…"},
  "check_domain": {"scope_id": "sha256:…", "expected_set_ref": "sha256:…",
                    "status": "complete", "consumer_completeness": "expected_domain_complete"},
  "obligations": [
    {"obligation_ref": "…", "kind": "check", "target_ref": "refs/heads/main",
     "action": "publish", "phase": null, "candidate_digest": null,
     "source_digest": null, "applicability": "required",
     "expected_check_refs": ["sha256:…"]},
    {"obligation_ref": "…", "kind": "non_check", "target_ref": "refs/heads/main",
     "action": "publish", "phase": "pre_publish", "candidate_digest": null,
     "source_digest": null, "applicability": "required",
     "required_subject_fields": ["artifact_digest"],
     "verifier_ref": {"name": "…", "version": "…", "sha256": "…"},
     "verifier_authorization_ref": {"name": "…", "version": "…", "sha256": "…"},
     "verification_rule_ref": {"name": "…", "version": "…", "sha256": "…"}}
  ],
  "universe_status": "complete",
  "universe_ref": "sha256:…"
}
```

The document identity is the SHA-256 of the canonical JSON payload excluding
`universe_ref`; `universe_ref` is `sha256:<64 hex>`. The admission projection
uses the same rule for `admission_ref`. `evaluation.phase_order` is optional for
a phase-agnostic scope and is the only authorized evidence R1 uses to classify
another declared phase as future. The universe `profile` may carry the
authorized `phase`; when present it must equal the evaluation phase. Candidate
and source digests are only required when the corresponding obligation declares
them, and must equal the evaluated/Expected identities. A currently-required
non-check obligation must declare `required_subject_fields`; otherwise its
evidence stays `unverified`.

`engineering-ci-evidence-admission` v1 requires
`phase="post_run_evidence_to_gate_projection"` (the frozen L2 order in
[reconciliation](reconciliation.md)), the exact Expected `scope_id` /
`expected_set_ref` and:

- `accepted_coverage_by_instance`: key
  `"<scope_id>/<expected_check_ref>"`, with `admission_status`, `source_kind`,
  `witness_result_id`, `raw_result_verified`, `issuer_verified`,
  `origin_trace`, `applicable_to` (Target/Phase/Action plus optional candidate
  digest, rule and profile SHA-256), and `equivalence_basis` for historical
  reuse;
- `authenticated_failures_by_instance`: per-instance result IDs and failure
  kind (`native_failure` / `conflict`);
- `adjudications`: instance identity, disposition, result IDs, authority and
  policy references, decision time and validity/revocation;
- `remaining_work`: per-instance unresolved work (must not contain an admitted
  instance).

`engineering-ci-non-check-evidence` v1 carries one entry per verified domain
result with the same `obligation_ref` / Target / Action / Phase /
candidate/source identity, the expected `verifier_ref`,
`verifier_authorization_ref` and `verification_rule_ref`, a `subject_ref`
(commit/tree/artifact/source digest) bound to the evaluated candidate, the
verdict (`satisfied` / `unmet` / `unverified`), its own validity window and an
evidence reference. The authorized obligation declares which candidate subject
fields the verifier must actually have checked in `required_subject_fields`;
R1 requires the candidate and the evidence to carry every declared field with
equal values. A source/commit-level obligation may require only `commit`, but an
artifact-level obligation must bind the real `artifact_digest`: a matching
commit alone never satisfies it. A missing requirement declaration, a missing
candidate identity field or a missing/mismatched evidence field is
`unverified`. A verifier identity that differs from the authorized one (for
example a PR-modified fixed-PASS verifier) is also `unverified`; it never
self-authorizes.

## Assessment shape

```json
{
  "kind": "engineering-ci-required-assessment",
  "schema_version": 1,
  "status": "blocked",
  "phase": "pre_publish",
  "action": "publish",
  "target": "candidate",
  "refs": [{"target_ref": "refs/heads/main", "ref_action": "update"}],
  "candidate": {"commit": "…", "tree": "…"},
  "evaluated_at": "…",
  "inputs": {"profile": {"…": "…"}, "authority": {"…": "…"},
              "governance_domain": {"closure_status": "closed", "…": "…"},
              "check_domain": {"scope_id": "sha256:…", "expected_set_ref": "sha256:…",
                                "status": "complete", "consumer_completeness": "…"},
              "universe_status": "complete", "universe_ref": "sha256:…",
              "admission_ref": "sha256:…", "receipt_ref": "sha256:…",
              "contract_sample": true},
  "verification": {
    "authority": {"status": "verified", "reasons": []},
    "check_domain": {"status": "verified", "reasons": []},
    "obligations_universe": {"status": "verified", "reasons": []},
    "evidence_satisfaction": {"status": "unsatisfied", "reasons": ["obligation_unverified"]}
  },
  "obligations": [
    {"instance_ref": "sha256:…", "obligation_ref": "…", "kind": "non_check",
     "target_ref": "refs/heads/main", "action": "publish", "phase": "pre_publish",
     "applicability": "required", "status": "unverified",
     "reasons": ["non_check_evidence_missing"], "check_instances": [],
     "evidence_refs": []}
  ],
  "check_instances": [
    {"scope_id": "sha256:…", "expected_check_ref": "sha256:…", "check_name": "tach",
     "status": "satisfied", "reasons": [],
     "admission": {"status": "admitted", "source_kind": "current_run",
                    "witness_result_id": "sha256:…", "…": "…"},
     "structural": {"status": "recorded", "binding_status": "matched", "…": "…"},
     "authenticated_failures": [], "evidence_refs": ["sha256:…"]}
  ],
  "summary": {"obligations": {"total": 2, "satisfied": 1, "unmet": 0,
                               "unverified": 1, "not_applicable": 0,
                               "not_yet_due": 0},
               "check_instances": {"total": 1, "satisfied": 1, "unmet": 0,
                                    "unverified": 0, "not_applicable": 0,
                                    "not_yet_due": 0}},
  "blocked_reasons": ["obligation:sha256:…:non_check_evidence_missing"],
  "limitations": ["assessment_does_not_publish_github_check", "…"],
  "assessment_ref": "sha256:…"
}
```

`validate_assessment()` recomputes `assessment_ref`, re-checks status/summary
counts, the four facts, and rejects a `passed` assessment containing
`unmet`/`unverified` rows or contradictory `blocked_reasons`.

## Contract samples and production claims

A synthetic universe/admission/evidence document may set
`contract_sample: true`. The assessment then labels the inputs
(`inputs.contract_sample=true`) and adds
`contract_sample_inputs_not_production_admission`. Such samples verify only the
R1 aggregation logic; they do not claim that #63 admission, a real trusted
chain, a GitHub Check, server protection or a Release authorization exists.

The `assessment_does_not_*` limitations record the remaining stage boundaries.
R2 must bind the assessment to a real trusted PR/main entry point and publish a
stable GitHub Check. R3 must verify the actual server-side Ruleset/Check source
behavior. R4 must map the assessment into the existing Release
Manifest/Run/Artifact chain. R5 must evaluate post-publish/terminal stages.

## Directed acceptance

`tests/test_required_assessment.py` covers:

- a complete current-run scope with a real #66-C matched receipt (all four
  facts pass), legal empty Check/obligation universes, historical reuse and
  non-check satisfaction;
- authority unverified/expired/revoked, governance closure incomplete or a
  delegated domain missing, incomplete universe/Check domains, and profile
  binding/version drift — all blocked while proven Check coverage is retained;
- the matched-receipt/trusted-boundary negatives, missing current-run receipt,
  receipt binding/witness mismatches, admitted-but-remaining contradictions and
  out-of-scope admission facts;
- historical PASS plus current failure, conflicting failures, authorized retry
  and expired/revoked adjudications, plus an old failure from another Ref that
  must not block the current success;
- same-obligation-ref instances across Pre-/Post-publish phases and across
  Target/Refs (including `source_digest` identity), M:N Check obligations, and
  `not_yet_due`/`not_applicable` basis enforcement; a past phase, a missing or
  incomplete authorized phase order and an unproven position are all blocked
  instead of being deferred;
- Profile-restricted future-only Check and Non-Check obligations (`phases[]`
  strictly after current phase) satisfy universe membership and evidence
  satisfaction (`not_yet_due`) without waiving any current-phase obligation,
  while unscoped current obligations cannot be substituted by a future-only
  instance (`required_check_omitted_by_universe` /
  `non_check_obligation_omitted_by_universe`), dual current/future instances of
  one `obligation_ref` stay independent, an unreliable phase order still
  BLOCKS, and a different `obligation_ref` referencing the same Check Ref does
  not close the Expected association;
- missing `authority_status`, `status=complete` with a non-matching
  completeness label, an evaluation context/action/target/phase/ref outside the
  authorized Profile/Expected Event, a declared candidate digest with no real
  candidate identity, and a declared source digest missing/mismatching the
  candidate or Expected Source all leave Check Domain / Universe unverified;
  correctly bound scopes (including per-Ref child labels and candidate/source
  identities) still PASS;
- a phase-bearing parent with `phase_status="unverified"` (`expected_phase_unverified`)
  or bare self-reported `phase_status="verified"` without a trusted lifecycle
  Phase Producer (`expected_phase_proof_unverified`) both block the staged
  Check Domain while keeping already-proven local Check witnesses; no-Phase
  complete scopes still PASS;
- a multi-Ref child prefers `remote_ref` with fallback to `local_ref` matching
  upstream `_multi_ref_scopes`, paired with its real `(target_ref, ref_action)`:
  `local != remote` pre-push passes on `remote_ref`, fails on `local_ref`-only
  target or mismatched `ref_action`, and legal `update`/`delete` samples PASS;
- a Check `not_applicable` with a plausible basis but no Profile waiver BLOCKs
  (`applicability_unauthorized`); a Profile `allowed_inapplicability`/
  `allow_inapplicable` waiver for the exact obligation/scope PASSes; a waiver
  for another obligation or target does not; an unauthorized inapplicability
  keeps an independently satisfied Check witness;
- a Non-Check `not_applicable` without `applicability_authorization` BLOCKs; a
  valid, instance-bound, current authorization PASSes; wrong authority/rule/
  target/phase/`obligation_ref`/Candidate, missing instance binding, expired,
  not-yet-valid and revoked authorizations BLOCK; reusing another obligation's
  authorization under the same Rule/Target BLOCKs;
- non-check missing/unmet/conflict evidence, PR-modified verifier/proof refs,
  artifact-level subject fields, commit-only evidence rejected for an
  artifact-level obligation, a legitimate commit-level obligation, missing
  requirement declarations, subject-digest mismatch, expired/revoked/late
  evidence and unbound verifiers;
- tampered Expected/universe/admission/receipt identities, wrong-scope
  admission, multi-Ref container handling and Schema v1 refusal.

## Limits

- **Phase order is upstream owned.** R1 consumes the universe's
  `evaluation.phase_order` and never hardcodes a lifecycle or infers one from
  Event/Job names. #66-B2 (or the authorized policy source) must supply that
  order; a missing or incomplete order keeps phase-scoped obligations
  `unverified` rather than letting a different phase be treated as future.
- **Inapplicability authorization is upstream owned.** A Check waiver is
  recomputed from the authorized EffectiveProfile using the existing #66-B2
  waiver semantics for the actual Target/Action/Phase; a basis reference alone
  is never authorization. A Non-Check waiver requires the controlled
  `applicability_authorization` fact from the authorized #66-B2/domain
  contract, bound to the concrete obligation/instance (and Candidate/Source
  digests when declared). Shared Rule/Authority/Target alone never authorizes
  a different `obligation_ref` or Candidate. R1 creates no parallel waiver
  Policy, Verifier registry or authorization store; if the upstream fact is
  absent or unverifiable, the instance stays `unverified`.
- **Lifecycle Phase authorization is upstream owned.** R1 consumes
  `Expected.phase`/`phase_status` and never issues Phase evidence or infers it
  from Event/Job names. The current #66-B2 producer marks phase-bearing
  projections `phase_status="unverified"`; R1 therefore blocks the Check Domain
  for them even when a per-Ref child is locally complete. Because no trusted
  lifecycle Phase Producer proof contract exists yet, self-reported
  `phase_status="verified"` strings also fail closed
  (`expected_phase_proof_unverified`) until #66-B2 supplies an authenticated
  versioned Phase proof contract.
- **Evaluation identity is upstream owned.** Context/action/target are bound to
  the authorized Profile and evaluation refs to the Expected Event, including
  the selected child's `ref_action`. Phase is bound when the authorized Profile
  declares it; #66-B2 must project that field (with its approval identity) for
  production phase binding. Candidate/Source digests are only checked when an
  obligation declares them, against the evaluated candidate and the Expected
  checked Source (raw 64-hex and `sha256:` forms are normalized). R1 adds no
  Source Store or Artifact schema.
- **Subject requirements are upstream owned.** R1 consumes
  `required_subject_fields` from the authorized obligation/domain contract. It
  does not decide that every domain needs an artifact or build an artifact
  schema; it only enforces that an artifact-level obligation binds the real
  artifact digest and that a commit-level obligation binds its declared subject.
- A multi-Ref operation needs one Assessment per Ref. R1 refuses the aggregate
  multi-ref Expected container, exactly like #66-C, so a single child PASS must
  not be presented as the whole operation's Required result; the caller owns the
  per-Ref aggregation.
- R1 consumes contract samples for pure-logic verification; it cannot prove
  that a trusted admission engine exists. Real #63 T1-a/T1-b/T2 delivery must
  fill `engineering-ci-evidence-admission` with genuine authenticated facts.
- R1 does not enumerate or complete policy domains. It verifies the supplied
  `closure_ref`/`closure_status` and the declared delegation closure; detecting
  an omitted delegated domain is #66-B2's enumeration duty.
- R1 does not authenticate issuers, adjudication authorities or verifier
  approvals. It verifies presence, controlled identity, scope and temporal
  validity, and consumes the upstream verdict. Unverifiable upstream authority
  stays blocked.
- Shared Job/Run/Attempt, source or prerequisite contamination is determined by
  #63 (including the L2 §3.1 propagation rule) and by the domain verifiers; R1
  consumes the resulting per-instance admission/evidence facts and does not
  re-derive contamination or native witness selection.
- R1 does not read GitHub Rulesets/Branch Protection or publish the final
  check. Those remain R2/R3.
- R1 does not rewrite any historical assessment or native fact; a later stage
  produces a new Assessment with its own `assessment_ref`.
