# Expected → Result reconciliation (#66-C)

Status: additive #66-C contract and first implementation slice
(`engineering_ci.reconciliation`). It does not change Plan schema 2, the
Execution Report schema, or any pre-execution Expected identity. Scheduler and
Hook lifecycle remain #66/#72 work; this module consumes their evidence.

## Position

The module binds the three identities issued before execution —
`expected_set_ref`, `scope_id` and per-instance `expected_check_ref` — to actual
scheduler Job references and genuine #52 Native Results. It produces one
versioned **Binding/Reconciliation Receipt**. It:

- consumes an already derived Expected projection (#66-B) and never rewrites it;
- accepts routing, rule/source/baseline resolution and Native Results as inputs;
- calls `check_contract.evaluate_expected_satisfaction()` for each coherent
  candidate group and cites its `selected_result_ids`; it does not select a
  second witness;
- keeps `matched` / `missing` / `insufficient` / `unknown` / `unrouted` /
  `not_applicable` distinct, and reports `unexpected` and `duplicate`
  observations;
- never admits trusted evidence (#63) or decides Required/Release (#61).

## Inputs

`reconcile_expected_results(expected, *, ref_scope_id=None, routing=(),
resolutions=(), observations=())`

- `expected` — the #66-B projection. A multi-ref Schema v2 container is not a
  runnable scope: pass `ref_scope_id` to reconcile one child `per_ref` entry.
  A Schema v1 `snapshot=multi` container is refused with
  `expected_multi_ref_requires_per_ref`; aggregate per-Ref receipts remain the
  caller's responsibility.
- `routing` — one binding per Expected instance:

  ```json
  {
    "expected_check_ref": "sha256:…",
    "job_refs": ["job/quality"],
    "mapping_status": "routed",
    "scheduler_ref": {"name": "lefthook", "version": "2.2.2"},
    "effective_config_ref": {"name": "hook-job", "version": "1"},
    "reason": null
  }
  ```

  `mapping_status` is `routed`, `unrouted`, `filtered`, `not_configured`,
  `skipped`, or `unknown`. `job_refs` may describe configured-but-filtered
  Jobs; a `routed` binding requires at least one Job. One Job may map to many
  Expected instances and one Expected instance may map to many Jobs.

- `resolutions` — separately sourced evidence that turns the pre-execution
  projection into a strict #52 Expected instance:

  ```json
  {
    "expected_check_ref": "sha256:…",
    "rule_ref": {"name": "tach", "version": "1", "sha256": "…64 hex…"},
    "source_ref": {"snapshot": "worktree", "declared_target_tree": "…",
                   "content_digest": "…", "verified_input_scope": ["…"],
                   "scope_kind": "check_input_closure",
                   "binding_status": "content_digest"},
    "baseline_ref": {"base_commit": "…"},
    "baseline_source_ref": {"name": "baseline", "version": "1", "sha256": "…"},
    "rule_source_ref": {"name": "hygiene-rule", "version": "1", "sha256": "…"},
    "source_receipt_ref": {"name": "p0-source-receipt", "version": "1", "sha256": "…"},
    "input_refs": [],
    "coverage": null,
    "coverage_by_requirement": null,
    "phase": null
  }
  ```

  `rule_source_ref` and `source_receipt_ref` are mandatory for a proven
  resolution; `baseline_source_ref` is mandatory when a baseline is supplied.
  Evidence references use `name`, `version`, and a raw 64-hex `sha256`. A
  resolution may not override a non-null rule or comparison baseline that the
  immutable Expected instance already declared: when its own `rule_ref` or
  resolved `baseline_ref` is omitted, the Expected declaration stands; a
  differing value is `insufficient`.

- `observations` — genuine Native Results grouped by Job:

  ```json
  {
    "job_ref": "job/quality",
    "runner_ref": "execution:host",
    "report_ref": {"kind": "engineering-ci-execution-report", "sha256": "…"},
    "native_results": [ { "kind": "engineering-ci-native-check-result", "…": "…" } ]
  }
  ```

  Every result is validated with `check_contract.validate_result()`. A Job may
  report no results; that is a real absence, not a failed Check. Duplicate
  `result_id` observations across Jobs are recorded and counted once.

## Receipt

```json
{
  "kind": "engineering-ci-expected-result-reconciliation",
  "schema_version": 1,
  "expected_set_ref": "sha256:…",
  "scope_id": "sha256:…",
  "expected_schema_version": 1,
  "expected_status": "partial",
  "expected_reason": "consumer_profile_not_resolved",
  "expected_completeness": "not_claimed",
  "expected_event_ref": {"…": "…"},
  "expected_source_ref": {"…": "…"},
  "ref": null,
  "status": "partial",
  "entries": [
    {
      "expected_check_ref": "sha256:…",
      "name": "tach",
      "check_id": "eci/hygiene/tach",
      "definition_version": 2,
      "applicability": {"status": "selected", "reason": "…"},
      "job_refs": ["job/quality"],
      "mapping_status": "routed",
      "scheduler_ref": {"…": "…"},
      "effective_config_ref": {"…": "…"},
      "routing_reason": null,
      "binding_status": "matched",
      "reason": "native_witness_satisfied",
      "observed_result_ids": ["sha256:…"],
      "observed_results": [
        {"result_id": "sha256:…", "status": "passed", "result_origin": "host",
         "native_exit_code": 0, "tool": {"access_id": "tach/check"}}
      ],
      "selected_result_ids": ["sha256:…"],
      "selected_results": [
        {"result_id": "sha256:…", "status": "passed", "result_origin": "host",
         "native_exit_code": 0, "tool": {"access_id": "tach/check"}}
      ],
      "native_status": "passed",
      "resolved_expected": {"check_id": "eci/hygiene/tach", "…": "…"},
      "resolved_expected_ref": "sha256:…",
      "evidence_refs": {"rule_source_ref": {"…": "…"}, "source_receipt_ref": {"…": "…"}},
      "native_satisfaction": {"status": "satisfied", "selected_result_ids": ["sha256:…"], "…": "…"},
      "candidate_decisions": [],
      "unknown_fields": []
    }
  ],
  "unexpected": [],
  "duplicate": [],
  "summary": {
    "expected": 1, "matched": 1, "missing": 0, "insufficient": 0,
    "unknown": 0, "unrouted": 0, "not_applicable": 0,
    "unexpected": 0, "duplicate": 0, "selected_results": 1
  },
  "trusted": false,
  "required": false,
  "evidence_admission": "not_evaluated",
  "required_gate": "not_evaluated",
  "limitations": ["…"],
  "receipt_ref": "sha256:…"
}
```

`resolved_expected` is the strict #52 instance built from separately sourced
evidence. It is an additional judgement argument; it never replaces the
original `expected_check_ref`. `receipt_ref` binds the complete receipt
content. `validate_receipt()` recomputes it and refuses authority claims.

## Entry status

| Status | Meaning |
| --- | --- |
| `matched` | A routed Job produced a coherent Native witness group that #52 satisfied for the resolved Expected instance; `selected_result_ids` is exactly #52's selection. |
| `missing` | The Expected instance is routed but no native result was observed, or the routing evidence says the Job was filtered/not configured/skipped. |
| `insufficient` | Results were observed but do not prove the Expected constraints: native failure, missing required tool/result, unexpected conditional result, uncovered input/coverage, or a rule/source/baseline conflict with the immutable Expected instance. |
| `unknown` | Authoritative resolution is absent or unresolved (rule, source closure, comparison baseline, post-completion phase, routing, Check definition), or the routing evidence leaves multiple incoherent candidate groups. The observed native facts are still recorded. |
| `unrouted` | No routing binding exists yet, or it explicitly reports `unrouted`. The Expected instance is retained. |
| `not_applicable` | The immutable Expected applicability says the Check does not apply; no result is required. An observed result for it is reported as `unexpected`. |

Receipt-level status is `matched` only when the Expected projection itself is
`complete`, every non-inapplicable instance is `matched`, and no unexpected
observation exists. Otherwise it is `partial`, `unrouted` (all eligible
instances unrouted), or `unknown` (all eligible instances unknown / no legal
completeness proof for an empty set). A legal empty `complete` Expected scope
yields `matched` with `summary.expected=0`; this still does not grant Required.

Test-framework Expected scopes carry their native Selection status
(`targeted`, `full`, `escalated`) rather than `selected`; all three are
applicable and are reconciled like `selected`. `no_tests`/`no_changes` are
treated as not applicable for reconciliation.

`unexpected` records observations whose Job has no routing binding
(`job_not_routed`), whose Check is not Expected for that Job
(`result_check_not_expected`), or whose only matching Expected instance is
not applicable (`expected_not_applicable`). `duplicate` records the same
`result_id` observed in multiple Jobs. Duplicates are counted once for
satisfaction but never silently dropped.

## Invariants

- Entry, set and scope identities are recomputed; a changed Expected entry,
  `expected_set_ref`, or `scope_id` is refused before any reconciliation.
- Each Entry/Source/Profile/selection-rule field of a child instance must equal
  the scope that issued it.
- A non-null expected `rule_ref` or comparison baseline is never replaced by
  resolution evidence; a conflict is `insufficient`.
- The resolved source must cover the Expected snapshot, declared Tree, content
  digest, commit/index identities, and the Expected changed-path scope.
- `evaluate_expected_satisfaction()` is called exactly once for the single
  coherent candidate group (same Check/definition/rule/profile/source/
  baseline/phase). Multiple incoherent groups mean the routing did not
  qualify one Run/Attempt instance; the entry stays `unknown`
  (`ambiguous_native_candidate_groups`) instead of guessing. A failed or
  unselected result cannot complete an expected input or coverage reference.
- The Expected projection, its identities, and its completeness claims are
  never rewritten or upgraded.
- `trusted` / `required` are always `false` and
  `evidence_admission`/`required_gate` are `not_evaluated`.

## Limits

- The caller must pass a Plan-validated Expected projection. This module
  recomputes its immutable identities but does not re-authorize the engineering
  Profile or Plan (`expected_projection_authority_not_reverified`).
- Scheduler routing evidence is not produced by this slice; #72 owns Hook Job
  activation and lifecycle. Without routing, Expected stays `unrouted`.
- Resolution evidence is separately sourced (controlled rule/config, #66-P0
  Source materialization, comparison baseline). The module verifies presence,
  shape, digest pins and consistency with the immutable Expected instance, not
  the external truth of those sources; #63 remains responsible for admission.
- Full byte/input closure remains a #66-P0 proof. This slice checks the
  Expected changed-path scope against the resolved verified scope and delegates
  native input/coverage equality to #52's Expected instance.
- No CLI, Hook or Report wiring is added in this slice. The receipt is consumed
  by #61/#63 producers and the eventual Hook final-reconciliation path.
