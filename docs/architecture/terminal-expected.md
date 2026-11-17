# Terminal Expected → post_completion 绑定（#52-D4）

Status: additive #52-D4 **contract / binding slice**
(`engineering_ci.terminal_expected`). It binds one frozen #66-B2
`weaver_terminal` Check Expected onto an existing
`selection.post_completion_plan` / `validate_post_completion_binding`
consumption closure.

It does **not** invent Expected, rewrite B2 Schema, implement a Phase
Producer, confuse Span Expectations with Check Expected, produce Native
Results, mint #66-C Receipts, perform #63 Admission / #61 Required, or wire
`release.yml`.

## Trust boundary

| Fact | Trusted source for `authorized=true` |
| --- | --- |
| Projection | Live `expected_checks.validate_projection(...)` against the **original Expected Plan/Git/Root**. Caller `projection_proof` dictionaries are untrusted. |
| Phase + domain | Sealed Phase Witness **plus** `phase_witness_authority`. Digest seal alone is not authentication. |
| Subject correlation | `completed_run_authority` must attest subject (`subject_verified` / `subject_status=authorized`), then Binder matches **Git** identity (`declared_target_tree` / commits / refs). Ref-only `expected_check_ref` / `scope_id` is insufficient. |
| Plan source | `validate_post_completion_binding` verifies Plan `git.source_tree_id` as directory **content** SHA-256. That value is **never** string-compared to Expected Git Tree OID; same-algorithm correlation uses `run_subject.source_content_id`. |
| Expected entry | `expected_authority` |
| Provider terminal | `completed_run_authority` (terminal status alone ≠ subject mapping) |
| Input closure | `validate_post_completion_binding(plan, root)` |

## Directed acceptance

Tests cover ref-only subject rejection, attested Git mismatch, Plan content id
distinct from Git Tree OID with correct `source_content_id` mapping, wrong
content-id mapping, forged Phase Witness, and live `weaver_terminal` Expected
v2 authorize/mismatch paths.
