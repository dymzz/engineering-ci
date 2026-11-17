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

## Due window

| `due_state` | Rule |
| --- | --- |
| `not_yet_due` | `evaluated_at` before `earliest_at` → envelope `pending` (not success) |
| `within_window` | Between earliest and due; stage result follows post Assessment |
| `overdue` | After `due_at`; missing/failed post evidence cannot be verified success |
| `not_applicable` | Profile does not require post obligations |

## Triggers

`run_completed` / `publication_completed` / `artifact_available` /
`authorized_reevaluation`, bound to `run_id` / `run_attempt` / `target`
(optional `artifact_digest`). An incomplete Run (`run_completed=false`) must
not force the original Workflow to wait on its own Terminal.

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
profile N/A, incomplete-run non-wait, authorized reevaluation, contract-sample
non-authorization, and validate consistency rejection.
