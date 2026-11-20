# Required Assessment 后置阶段（#61 R5）

Status: additive #61 R5 **contract / reconciliation slice**
(`engineering_ci.required_assessment_post`). It forms an independent post-stage
envelope after a prior (typically pre-publish) #61 R1 Assessment, applying an
observable trigger and due window while **preserving** prior facts.

It does **not** re-run obligation evaluation (#61 R1), publish a GitHub Check
(#61 R2), read Rulesets (#61 R3), reconcile Release Manifests (#61 R4), wait for
the same incomplete Workflow Run's own Terminal, or auto-revoke published
artifacts.

## Position

```text
prior R1 Assessment (immutable, typically pre_publish PASS)
post R1 Assessment (post_publish / terminal scope)
R1 evidence-shaped phase_order + phase_order_authority
trigger_event + due_window
        |
        +--> reconcile_post_stage
                |
                status: verified | rejected | unverified | pending
                prior_* fields unchanged; never rewritten
```

## Lifecycle identity and phase relation

Prior and post Assessments must share `context` / `action` / `target` / `refs` /
`candidate`. Trigger `target` must match. Cross-Candidate splicing cannot become
`verified`.

`phase_order` reuses the R1 evidence-ref shape (`name`/`version`/`sha256` +
ordered `phases`). It is bound to the Assessments' shared Profile
`profile_ref` / `profile_revision` / authority source, and must be accepted by
`phase_order_authority` — a caller-supplied list alone cannot verify. True
post-stage progression requires `post.phase` strictly after `prior.phase`.
`authorized_reevaluation` requires the **same** phase, and that phase must
still belong to the authorized order.

## Due window and temporal consistency

| `due_state` | Rule |
| --- | --- |
| `not_yet_due` | `evaluated_at` before `earliest_at` → envelope `pending` (not success) |
| `within_window` | Between earliest and due; stage result follows post Assessment |
| `overdue` | After `due_at`; missing/failed post evidence cannot verify. Overdue + PASS still needs `due_window_authority` to accept late evidence (`late_evidence_accepted=true`) |
| `not_applicable` | Derived only from a **passed** post Assessment whose rows are all `not_applicable` (or empty). A blocked post cannot claim N/A |

Trigger observation and both Assessments must be at or before envelope
evaluation. An explicit `occurred_at` records when the event happened
and must precede or equal `observed_at`, which records its receipt.
Older Trigger records use `observed_at` for both.
For progression and current reevaluation, the post Assessment must be at or
after both the prior Assessment and the trigger. Reevaluation references a
prior Assessment that exists at trigger time. Timestamps are normalized to UTC.

## Triggers

`run_completed` / `publication_completed` / `artifact_available` /
`authorized_reevaluation`, bound to `run_id` / `run_attempt` / `target`
(optional `artifact_digest`). An incomplete Run (`run_completed=false`) must
not force the original Workflow to wait on its own Terminal.

`authorized_reevaluation` may carry `historical_as_of`. For a historical
reevaluation, the post Assessment's evaluation time must equal this requested
instant, which must be at or before the trigger. The trigger authority must
authorize the exact event, Phase, and Assessment references and return
`historical_reevaluation_accepted=true` with its accepted result. The phase
authority receives the same event and Assessment times. A verified historical
envelope applies to the recorded instant. The due window is evaluated at the
envelope's own `evaluated_at`.

## Lifecycle coverage

This **pair** reconciliation always records
`lifecycle_all_phases_passed=false` and `lifecycle_coverage=unverified`. Two
passing stage Assessments are not a complete authorized phase/obligation set.
A later #61 integration must consume the versioned full Phase/Obligation set
and per-phase R1 Assessments for the same Candidate before a lawful
`lifecycle_all_phases_passed=true` is reachable. This pair API must not delete,
rename, or invent a parallel policy source for that field.

The envelope carries `contract_sample=true` if either Assessment or the caller
marks the input as a contract sample. Sample envelopes cannot be `verified`.

## Disposition

Optional `disposition_authority` may record authorized follow-ups
(`await_repair`, `reevaluate`, `notify`, `block_progression`, …). Default is
`none`. `auto_revoke_published` is always false in this contract.

## Published Release inspection

`release.verification.published()` and `cli.release.inspect()` return
the historical Release result and a separate `post_stage` observation.
For a protected caller supplying `post_stage_authority`, the read-side
adapter checks the final native Release ID and publication time, its
immutable Manifest, and the original completed Run and Attempt. It
binds the `publication_completed` event and any required Artifact ID
and digest to those facts. Native `published_at` supplies the event's
`occurred_at`. The independent Trigger authority verifies the receipt's
`observed_at`, which may be later. The verifier receives the
native publication identity and the applicable Artifact ID alongside
the governed R5 context. The caller must supply both original R1
Assessments and the governing Phase, Trigger and Due authorities.

The adapter runs R5 at read time. Its result may show a requested
disposition and does not execute one. The CLI continues to report
the historical publication even when current post-stage evidence
is pending, rejected or unverified. CLI exit 0 and `result=published`
establish successful historical inspection. A governed promotion write
must consume its own applicable authorization and record the action or
refusal. The default CLI has no governed
authority binding and reports that evidence gap explicitly. Other
trigger types and any policy-selected Promotion, Notification or
Reevaluation need their own native entry and outcome receipt.

## Limits

- Prior Assessment documents remain immutable references.
- Digest integrity is not authorization.
- The Release CLI only consumes publication-triggered R5 observations.
  Action consumers and server-protection changes are outside that read.

## NEW-001 / NEW-027 scope and evidence

This R5 contract repair addresses two defects tracked under [#61](https://github.com/dymzz/engineering-ci/issues/61)
and the [#129 defect ledger](https://github.com/dymzz/engineering-ci/pull/129):

- **NEW-001 — Contract Sample propagation:** the envelope's `contract_sample`
  is the logical OR of the explicit reconciliation flag and both validated
  R1 Assessments' `inputs.contract_sample`. In particular, a production-looking
  Prior and explicit `false` cannot hide a sample-marked Post. R1's own native
  and obligation verdicts are preserved; R5 does not promote a sample pair to
  production `verified`.
- **NEW-027 — Temporal and historical causality:** a Trigger, Prior Assessment
  and Post Assessment must not be later than envelope `evaluated_at`.
  Normal progression and current same-phase reevaluation require Post at or
  after Prior and Trigger. For a same-phase historical reevaluation, an explicit
  `historical_as_of` must equal the Post assessment time, be no later than
  Trigger observation, and be explicitly accepted for the same Trigger, Phase,
  Candidate and Assessment references by the Trigger Authority. The referenced
  Prior must already exist at the Trigger time. This permits authorized
  historical appraisal without silently accepting reversed current causality.

**What the directed tests establish:** R1→R5 reconciliation using real
evaluator code and controlled Authority callbacks, including Post-only sample,
future Prior/Post, reversed chronology, accepted/rejected historical instant
and an authority-rejected wrong Run Attempt. They demonstrate the **contract
and its refusal behavior**, not independently authenticated production Trigger,
GitHub Run/Attempt/Artifact, or Policy/Phase/Due issuer facts. A callback
returning `authorized` in a fixture is not a production trust credential.

**Remaining #61 acceptance:** authenticate and connect actual Trigger/Provider
and Artifact/Run/Attempt ownership; evaluate post-run/post-publish evidence at
the authorized due window, including delayed and missing evidence; and
aggregate an independently verified complete Phase/Obligation set before
claiming full lifecycle success. The pair envelope deliberately remains
`lifecycle_coverage=unverified` and
`lifecycle_all_phases_passed=false`, even for an otherwise `verified` pair.
No production publication, server gate or full lifecycle is established by
this code repair or by the passing unit/CI tests.

## Directed acceptance

`tests/test_required_assessment_post.py` covers in-window PASS, not-yet-due
pending, overdue failure, overdue PASS with/without late-evidence authority,
smoke FAIL with prior preserved, blocked prior, authorized N/A, Candidate/Target
mismatch rejection, same-phase and unknown-phase negatives, post-before-trigger
and forged phase_order negatives, incomplete-run non-wait, future trigger,
missing trigger authority, same-phase authorized reevaluation, contract-sample
non-authorization, forged lifecycle-all-phases rejection, and validate
consistency rejection.
