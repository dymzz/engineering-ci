# Terminal Expected → post_completion 绑定（#52-D4）

Status: additive #52-D4 **contract / binding slice**
(`engineering_ci.terminal_expected`). It binds one authorized #66-B2
`weaver_terminal` Check Expected (pre-execution `scope_id` /
`expected_check_ref`; phase from the parent Expected v2 / Profile projection)
onto an existing `selection.post_completion_plan` /
`validate_post_completion_binding` consumption closure, using a **separate**
completed Run/Attempt/Terminal proof.

It does **not** invent Expected when B2/authority is missing, confuse Weaver
Span Expectations with Check Expected, produce Native Results, mint #66-C
Receipts, perform #63 Admission / #61 Required, implement a Phase Producer, or
wire `release.yml`.

## Position

```text
#66-B2 parent Expected v2 (phase / scope_id / checks[])
  └─ weaver_terminal Check Entry (no run_id / run_attempt)
completed_run + completed_run_authority (Provider terminal proof)
existing post_completion_plan (Span Expectations + input closure)
        |
        +--> bind_terminal_expected
                |
                status: bound | rejected | unverified
                authorized: only when phase + run + closure + Expected authority hold
```

## Upstream Expected v2 phase contract

When an Effective Profile declares `phase`, real #66-B2 projections are
`phase_status=unverified` and parent `status=partial`.
`complete` + `unverified` is illegal (`expected_phase_complete_unproven` /
`terminal_expected_phase_complete_unproven`). That real shape may bind
structurally but **cannot** authorize until a Phase Producer fact sets
`phase_status=verified`. `expected_authority=True` alone never proves phase.

## Canonical gates reused

- Parent Expected v2 supplies `scope_id`, `phase`, completeness.
- Check Entry keeps the pre-execution `expected_check_ref`
  (`expected_checks.entry_identity`). Missing refs are rejected; post-hoc
  `run_id` / `run_attempt` on the entry are forbidden.
- `selection.validate_post_completion_binding()` — recomputable Weaver input
  closure.
- `completed_run` correlates with Plan identity, but Plan builder params alone
  are not Provider terminal proof; `completed_run_authority` (or a future
  sourced proof) is required for `authorized=true`.

Span Expectations JSON under `ci-weaver.toml` remain a separate object from the
Check Expected identity.

## Authorization facts

| Field | Meaning |
| --- | --- |
| `phase_verified` | Parent `phase_status=verified` (Phase Producer fact) |
| `authorization_basis=authority_verified` | Caller `expected_authority` accepted the Expected |
| `completed_run_verified` | `completed_run_authority` accepted the terminal claim |
| `input_closure=verified` | `validate_post_completion_binding(plan, root)` succeeded |

Overall `authorized=true` requires **all four**, plus matching identities and
no gaps.

## Limits

- Does not implement #66-B2 Phase Producer.
- Does not expand Task-only A (#82) into Terminal B.
- Digest integrity is not authorization.
- Production execute-plan Expected injection and #63/#61 remain subsequent work.

## Directed acceptance

`tests/test_terminal_expected.py` covers real B2 partial/unverified non-
authorization, Phase Producer-verified authorization, illegal
`complete+unverified`, self-reported completed_run rejection, missing/rejected
authority, wrong/missing parent phase, post-hoc Run fields forbidden, missing
`expected_check_ref`, Plan identity drift, Span Expectations independence, and
helper Plan builder non-self-authorization.
