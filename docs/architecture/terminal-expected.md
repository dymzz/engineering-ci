# Terminal Expected → post_completion 绑定（#52-D4）

Status: additive #52-D4 **contract / binding slice**
(`engineering_ci.terminal_expected`). It binds one authorized #66-B2
`weaver_terminal` Check Expected (`scope_id`, `expected_check_ref`,
`phase=post_completion`) onto an existing
`selection.post_completion_plan` / `validate_post_completion_binding`
consumption closure.

It does **not** invent Expected when B2/authority is missing, confuse Weaver
Span Expectations with Check Expected, produce Native Results, mint #66-C
Receipts, perform #63 Admission / #61 Required, or wire `release.yml`.

## Position

```text
#66-B2 authorized weaver_terminal Expected
existing post_completion_plan (Span Expectations + input closure)
        |
        +--> bind_terminal_expected
                |
                status: bound | rejected | unverified
                authorized: only authority_verified + non-sample
```

## Canonical gates reused

- `check_contract.validate_expected_instance()` — Terminal Expected must use
  `phase=post_completion` and `eci/weaver/terminal`.
- `selection.post_completion_plan()` / `validate_post_completion_binding()` —
  Run/Attempt/result and recomputable Weaver input closure. D4 does not replace
  these with a looser Plan builder.

Span Expectations JSON under `ci-weaver.toml` remain a separate object from the
Check Expected identity.

## Authorization vs input closure

Two independent facts:

| Field | Meaning |
| --- | --- |
| `authorization_basis=authority_verified` | Caller `expected_authority` accepted the Expected |
| `input_closure=verified` | `selection.validate_post_completion_binding(plan, root)` recomputed successfully |

Overall `authorized=true` requires **both**, plus deterministic Expected↔Plan
Run/Attempt identity (and recomputed `expected_checks.entry_identity`). Missing
`root` yields `input_closure=unverified` and cannot authorize. Authority alone
never proves live Terminal inputs.

Missing or rejected authority records gaps; the module never synthesizes a
replacement Expected. Terminal B cannot fake PASS when Root / phase / Expected
authorization is missing.

## Limits

- Does not implement #66-B2 Phase Producer or mark `phase_status=verified`.
- Does not expand Task-only A (#82) into Terminal B.
- Digest integrity is not authorization.
- Production execute-plan Expected injection and #63/#61 remain subsequent work.

## Directed acceptance

`tests/test_terminal_expected.py` covers authorized bind, missing/rejected
authority, wrong phase/check/scope, contract-sample non-authorization, Plan
identity drift rejection, Span Expectations independence, and
`post_completion_with_expected` helper.
