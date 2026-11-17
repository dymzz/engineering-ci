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
completed_run (sourced Run/Attempt/Terminal proof)
existing post_completion_plan (Span Expectations + input closure)
        |
        +--> bind_terminal_expected
                |
                status: bound | rejected | unverified
                authorized: only authority_verified + non-sample
```

## Canonical gates reused

- Parent Expected v2 supplies `scope_id`, `phase`, completeness. Missing parent
  phase cannot authorize; wrong phase is rejected. `phase_status` may remain
  `unverified` until a Phase Producer exists.
- Check Entry keeps the pre-execution `expected_check_ref`
  (`expected_checks.entry_identity`). Missing refs are rejected; the binder
  never remints identity from Native Results or stuffed Run fields.
- `check_contract.validate_expected_instance()` runs on a view that may attach
  the parent-proven phase without mutating the stored entry.
- `selection.post_completion_plan()` / `validate_post_completion_binding()` —
  Run/Attempt/result and recomputable Weaver input closure.

Post-hoc `run_id` / `run_attempt` on the Expected entry are forbidden.
`completed_run` correlates with Plan identity separately.

Span Expectations JSON under `ci-weaver.toml` remain a separate object from the
Check Expected identity.

## Authorization vs input closure

Two independent facts:

| Field | Meaning |
| --- | --- |
| `authorization_basis=authority_verified` | Caller `expected_authority` accepted the Expected |
| `input_closure=verified` | `selection.validate_post_completion_binding(plan, root)` recomputed successfully |

Overall `authorized=true` requires **both**, a complete parent scope, matching
`completed_run`↔Plan identity, and recomputed `expected_check_ref`. Missing
`root` yields `input_closure=unverified` and cannot authorize. Authority alone
never proves live Terminal inputs.

## Limits

- Does not implement #66-B2 Phase Producer or mark `phase_status=verified`.
- Does not expand Task-only A (#82) into Terminal B.
- Digest integrity is not authorization.
- Production execute-plan Expected injection and #63/#61 remain subsequent work.

## Directed acceptance

`tests/test_terminal_expected.py` covers authorized bind on real B2-shaped
entries, missing/rejected authority, wrong/missing parent phase, incomplete
parent scope, post-hoc Run fields forbidden, missing `expected_check_ref`,
wrong completed Attempt, contract-sample non-authorization, Plan identity drift
rejection, Span Expectations independence, and `post_completion_with_expected`.
