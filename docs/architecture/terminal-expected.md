# Terminal Expected → post_completion 绑定（#52-D4）

Status: additive #52-D4 **contract / binding slice**
(`engineering_ci.terminal_expected`). It binds one frozen #66-B2
`weaver_terminal` Check Expected onto an existing
`selection.post_completion_plan` / `validate_post_completion_binding`
consumption closure.

It does **not** invent Expected, rewrite B2 Schema (`phase_status=verified` on
the parent is rejected), implement a Phase Producer, confuse Span Expectations
with Check Expected, produce Native Results, mint #66-C Receipts, perform #63
Admission / #61 Required, or wire `release.yml`.

## Position

```text
frozen #66-B2 Expected v2 (partial + phase_status=unverified when phase set)
  └─ weaver_terminal Check Entry (no run_id / run_attempt)
projection_proof  (validate_projection receipt or live call)
phase_witness      (external Phase + domain attestation; does not mutate parent)
completed_run + completed_run_authority
post_completion_plan input closure
        |
        +--> bind_terminal_expected
                |
                authorized only when ALL independent proofs hold
```

## Frozen upstream contract

Declared phase projections from current `expected_checks` are
`status=partial` / `phase_status=unverified`. `validate_projection` rejects
`phase_status=verified` and `complete+unverified`. D4 consumes that shape
unchanged. Phase/domain completeness is a **separate** witness bound to
`scope_id` / `expected_set_ref` / phase — never by rewriting the parent.

## Independent authorization facts

| Fact | Source |
| --- | --- |
| Projection valid | live `validate_projection` or bound `projection_proof` |
| Phase + domain complete | external `phase_witness` (`domain_complete=true`) |
| Expected entry authority | `expected_authority` (does not prove phase/domain) |
| Provider terminal | `completed_run_authority` (Plan params alone insufficient) |
| Input closure | `validate_post_completion_binding(plan, root)` |

Any missing fact ⇒ `authorized=false`. Callbacks cannot skip deterministic
identity checks (`expected_set_ref`, scope, phase match).

## Limits

- Does not implement #66-B2 Phase Producer or change B2 Validator.
- Controlled witnesses in tests are hypothetical external attestations, not
  current production PASS claims.
- Digest integrity is not authorization.

## Directed acceptance

`tests/test_terminal_expected.py` covers frozen partial non-authorization,
independent-witness authorization without rewriting parent, rejection of
`phase_status=verified` / `complete+unverified`, domain-incomplete and wrong
`expected_set_ref` witnesses, missing `expected_set_ref`, self-reported
completed_run rejection, and helper Plan builder non-self-authorization.
