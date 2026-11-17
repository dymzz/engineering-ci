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

Temporal checks reject a future trigger, a post Assessment after envelope
evaluation, progression with post before prior, and — for
`run_completed` / `publication_completed` / `artifact_available` — a post
Assessment that predates the claimed trigger event. `run_completed` must be
explicit. Timestamps are UTC-aware (`datetime.UTC`).

## Triggers

`run_completed` / `publication_completed` / `artifact_available` /
`authorized_reevaluation`, bound to `run_id` / `run_attempt` / `target`
(optional `artifact_digest`). An incomplete Run (`run_completed=false`) must
not force the original Workflow to wait on its own Terminal.

## Lifecycle coverage

This **pair** reconciliation always records
`lifecycle_all_phases_passed=false` and `lifecycle_coverage=unverified`. Two
passing stage Assessments are not a complete authorized phase/obligation set.
A later #61 integration must consume the versioned full Phase/Obligation set
and per-phase R1 Assessments for the same Candidate before a lawful
`lifecycle_all_phases_passed=true` is reachable. This pair API must not delete,
rename, or invent a parallel policy source for that field.

## Disposition

Optional `disposition_authority` may record authorized follow-ups
(`await_repair`, `reevaluate`, `notify`, `block_progression`, …). Default is
`none`. `auto_revoke_published` is always false in this contract.

## Limits

- Prior Assessment documents remain immutable references.
- Digest integrity is not authorization.
- No CLI / Actions / server-protection wiring in this slice.

## Directed acceptance

`tests/test_required_assessment_post.py` covers in-window PASS, not-yet-due
pending, overdue failure, overdue PASS with/without late-evidence authority,
smoke FAIL with prior preserved, blocked prior, authorized N/A, Candidate/Target
mismatch rejection, same-phase and unknown-phase negatives, post-before-trigger
and forged phase_order negatives, incomplete-run non-wait, future trigger,
missing trigger authority, same-phase authorized reevaluation, contract-sample
non-authorization, forged lifecycle-all-phases rejection, and validate
consistency rejection.
