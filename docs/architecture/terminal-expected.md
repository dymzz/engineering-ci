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
| Projection | Live `expected_checks.validate_projection(parent, git, plan=expected_plan, root=expected_root)` only. Caller `projection_proof` dictionaries are **untrusted**. Terminal Plan must not be supplied as `expected_plan`. |
| Phase + domain | Structurally sealed Phase Witness **plus** `phase_witness_authority` (stand-in for a future #66-B2 Phase/Obligation Producer verification). `seal_phase_witness` / matching `witness_ref` alone is **not** source authentication. |
| Expected entry | `expected_authority` (does not prove phase/domain/projection) |
| Provider terminal | `completed_run_authority` (Plan builder params alone insufficient) |
| Input closure | `validate_post_completion_binding(plan, root)` |

`expected_set_ref` is recomputed via `expected_checks.set_identity` over the
parent's actual `checks` / `non_check_obligations`; stale refs after tampering
cannot authorize.

## Frozen upstream contract

Declared phase projections remain `status=partial` /
`phase_status=unverified`. Parent `phase_status=verified` is rejected as
unsupported by the current B2 Validator.

## Directed acceptance

Tests cover forged sealed Phase Witness without authority, wrong Profile
revision, self-attested projection proofs, `expected_set_ref` recompute
tamper, live Expected v2 (`effective_profile` + `phase=post_completion`)
`validate_projection` positive versus Terminal Plan as wrong context, frozen
parent negatives, and Plan closure / Run identity guards.
