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
trigger_event + due_window
        |
        +--> reconcile_post_stage
                |
                status: verified | rejected | unverified | pending
                prior_* fields unchanged; never rewritten
```

## Lifecycle identity

Prior and post Assessments must share `context` / `action` / `target` / `refs` /
`candidate`. Phase may differ. Trigger `target` must match. Cross-Candidate
splicing cannot become `verified`.

## Due window

| `due_state` | Rule |
| --- | --- |
| `not_yet_due` | `evaluated_at` before `earliest_at` → envelope `pending` (not success) |
| `within_window` | Between earliest and due; stage result follows post Assessment |
| `overdue` | After `due_at`; missing/failed post evidence cannot be verified success |
| `not_applicable` | Derived only from a **passed** post Assessment whose rows are all `not_applicable` (or empty). A blocked post cannot claim N/A |

`trigger_authority` and `due_window_authority` are required for `verified`.
`run_completed` must be explicit (never defaulted to true). Timestamps are
UTC-aware.

## Triggers

`run_completed` / `publication_completed` / `artifact_available` /
`authorized_reevaluation`, bound to `run_id` / `run_attempt` / `target`
(optional `artifact_digest`). An incomplete Run (`run_completed=false`) must
not force the original Workflow to wait on its own Terminal.

## Lifecycle coverage

This **pair** reconciliation always records
`lifecycle_all_phases_passed=false` and `lifecycle_coverage=unverified`. Two
passing stage Assessments are not a complete authorized phase/obligation set.

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
pending, overdue failure, smoke FAIL with prior preserved, blocked prior,
authorized N/A, Candidate/Target mismatch rejection, incomplete-run non-wait,
missing trigger authority, authorized reevaluation, contract-sample
non-authorization, forged lifecycle-all-phases rejection, and validate
consistency rejection.
