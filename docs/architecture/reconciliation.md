# Expected → Result reconciliation (#66-C)

Status: additive #66-C contract and first implementation slice
(`engineering_ci.reconciliation`). It does not change Plan schema 2, the
Execution Report schema, or any pre-execution Expected identity. Scheduler and
Hook lifecycle remain #66/#72 work; this module consumes their evidence.

## Position

The module binds the three identities issued before execution —
`expected_set_ref`, `scope_id` and per-instance `expected_check_ref` — to
activated scheduler Job instances and genuine #52 Native Results. It produces
one versioned **Binding/Reconciliation Receipt**. It:

- consumes an already derived Expected projection (#66-B) and never rewrites it;
- accepts routing, rule/source/baseline resolution and Native Results as inputs;
- qualifies results by execution identity before calling
  `check_contract.evaluate_expected_satisfaction()` exactly once per Expected
  instance, and cites its `selected_result_ids`; it does not select a second
  witness;
- keeps `matched` / `missing` / `insufficient` / `unknown` / `unrouted` /
  `not_applicable` distinct, and reports `unexpected`, `duplicate` and
  rejected execution objects;
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
    "job_instances": [
      {
        "job_ref": "job/quality",
        "run_ref": "run/123",
        "attempt_ref": "attempt/1",
        "runner_ref": "execution:host",
        "report_ref": {
          "kind": "engineering-ci-execution-report",
          "schema_version": 1,
          "sha256": "…raw 64 hex…"
        }
      }
    ],
    "mapping_status": "routed",
    "scheduler_ref": {"name": "lefthook", "version": "2.2.2"},
    "effective_config_ref": {"name": "hook-job", "version": "1"},
    "reason": null
  }
  ```

  `job_refs` are Job names; `job_instances` are the actually activated
  identities `(job_ref, run_ref, attempt_ref)` for this routing decision. A
  `routed` binding without instances is `unknown`
  (`job_activation_identity_missing`); it cannot produce `matched`. One Job may
  map to many Expected instances and one Expected instance may map to many
  jobs/instances. `mapping_status` is `routed`, `unrouted`, `filtered`,
  `not_configured`, `skipped`, or `unknown`.

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

  The immutable Expected `input_refs` are a floor. Resolution `input_refs` may
  add native input evidence but may not omit or shrink an expected input
  (`expected_inputs_reduced`). The Expected `profile_ref` always enters the
  strict #52 instance; a Native Result must carry that same
  `check.profile_ref` for `matched`. A missing profile association is
  `expected_profile_binding_missing`; a different one is
  `expected_profile_mismatch`. This module does not re-authorize the Profile's
  organizational authority (#66-B/#63).

- `observations` — genuine Native Results under the exact execution identity:

  ```json
  {
    "job_ref": "job/quality",
    "run_ref": "run/123",
    "attempt_ref": "attempt/1",
    "runner_ref": "execution:host",
    "report_ref": {
      "kind": "engineering-ci-execution-report",
      "schema_version": 1,
      "sha256": "…raw 64 hex…"
    },
    "native_results": [ { "kind": "engineering-ci-native-check-result", "…": "…" } ]
  }
  ```

  The `(job_ref, run_ref, attempt_ref)` tuple is unique in one call. Results are
  only eligible when that tuple matches an activated `job_instances` entry. A
  routing instance that declares `runner_ref`/`report_ref` must equal the
  observation's value (`job_runner_mismatch` / `job_report_mismatch`, surfaced
  as `job_execution_identity_mismatch`). A result observed in one Job cannot be
  reattached to another Run/Attempt. An observation carrying results without a
  complete execution identity (`run_ref`, `attempt_ref`, `runner_ref`,
  `report_ref`) stays `unknown` (`job_execution_identity_unproven`), not
  `matched`. The declared `report_ref` is bound into the receipt; this module
  does not read the full report or re-run any Check.

  Every result is validated with `check_contract.validate_result()`. A Job may
  report no results; that is a real absence, not a failed Check. Duplicate
  `result_id` observations across identities are recorded and counted once.

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
      "job_instances": [
        {"job_ref": "job/quality", "run_ref": "run/123",
         "attempt_ref": "attempt/1", "runner_ref": "execution:host",
         "report_ref": {"…": "…"}, "observed": true}
      ],
      "mapping_status": "routed",
      "scheduler_ref": {"…": "…"},
      "effective_config_ref": {"…": "…"},
      "routing_reason": null,
      "binding_status": "matched",
      "reason": "native_witness_satisfied",
      "observed_result_ids": ["sha256:…"],
      "observed_results": [
        {"result_id": "sha256:…", "status": "passed", "result_origin": "host",
         "native_exit_code": 0, "tool": {"access_id": "tach/check"},
         "job_ref": "job/quality", "run_ref": "run/123",
         "attempt_ref": "attempt/1", "runner_ref": "execution:host",
         "report_ref": {"…": "…"}}
      ],
      "selected_result_ids": ["sha256:…"],
      "selected_results": [{"…": "same evidence shape…"}],
      "rejected_results": [
        {"result_id": "sha256:…", "reason": "source_mismatch",
         "job_ref": "job/quality", "run_ref": "run/123",
         "attempt_ref": "attempt/1"}
      ],
      "rejected_observations": [
        {"reason": "job_runner_mismatch", "job_ref": "job/quality",
         "run_ref": "run/123", "attempt_ref": "attempt/1",
         "observed_runner_ref": "execution:other"}
      ],
      "native_status": "passed",
      "resolved_expected": {"check_id": "eci/hygiene/tach", "…": "…"},
      "resolved_expected_ref": "sha256:…",
      "evidence_refs": {"rule_source_ref": {"…": "…"}, "source_receipt_ref": {"…": "…"}},
      "native_satisfaction": {"status": "satisfied", "selected_result_ids": ["sha256:…"], "…": "…"},
      "unknown_fields": []
    }
  ],
  "unexpected": [],
  "duplicate": [
    {"result_id": "sha256:…",
     "observations": [
       {"job_ref": "job/quality", "run_ref": "run/123", "attempt_ref": "attempt/1"},
       {"job_ref": "job/quality", "run_ref": "run/123", "attempt_ref": "attempt/2"}
     ]}
  ],
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
| `matched` | An activated Job instance produced the expected execution object and #52 satisfied the resolved Expected instance; `selected_result_ids` is exactly #52's selection. |
| `missing` | The Expected instance is routed to an activated Job instance but no matching native result was observed, or the routing evidence says the Job was filtered/not configured/skipped. |
| `insufficient` | Results were observed but do not prove the Expected constraints: native failure, missing required tool/result, uncovered input/coverage, a rule/source/baseline/phase object mismatch (`execution_object_mismatch`), or resolution inputs shrinking the Expected input floor. |
| `unknown` | Authoritative resolution or execution identity is absent/unresolved: rule/source/baseline/phase resolution, activated Job identity, Run/Attempt/report/runner binding, Expected Profile binding, or Check definition. Observed native facts are still recorded. |
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

`unexpected` records a result whose observation has no activated routing
instance (`job_not_routed`, or `job_instance_not_activated` when only the Job
name matches), whose execution identity is incomplete
(`job_execution_identity_unproven`), whose Check is not Expected for that Job
(`result_check_not_expected`), or whose only matching Expected instance is not
applicable (`expected_not_applicable`). Same-Check observations with the wrong
Source/Rule/Baseline/Phase are recorded in the entry's `rejected_results` and
cannot be selected by #52. `duplicate` records the same `result_id` observed in
multiple execution identities; it is counted once and never silently dropped.

## Invariants

- Entry, set and scope identities are recomputed; a changed Expected entry,
  `expected_set_ref`, or `scope_id` is refused before any reconciliation.
- Each Entry/Source/Profile/selection-rule field of a child instance must equal
  the scope that issued it.
- A non-null expected `rule_ref`, profile or comparison baseline is never
  replaced by resolution evidence; a conflict is `insufficient`. The immutable
  Expected `input_refs` floor is never removed or shrunk.
- The resolved source must cover the Expected snapshot, declared Tree, content
  digest, commit/index identities, and the Expected changed-path scope.
- Native Results enter the candidate group only from an activated Job instance
  whose Run/Attempt (and declared runner/report) match the observation, and
  only after the Check/Rule/Source/Baseline/Phase/Profile identity matches the
  resolved Expected instance.
- `evaluate_expected_satisfaction()` is called exactly once for that qualified
  candidate group. A failed, rejected or unselected result cannot complete an
  expected input or coverage reference.
- The Expected projection, its identities, and its completeness claims are
  never rewritten or upgraded.
- `trusted` / `required` are always `false` and
  `evidence_admission`/`required_gate` are `not_evaluated`.

## Limits

- The caller must pass a Plan-validated Expected projection. This module
  recomputes its immutable identities but does not re-authorize the engineering
  Profile or Plan (`expected_projection_authority_not_reverified`).
- Scheduler routing evidence is not produced by this slice; #72 owns Hook Job
  activation and lifecycle. A routed Expected without activated Job instances
  stays `unknown`.
- Cross-Run/Attempt historical reuse is not accepted as current execution
  evidence here (`cross_run_reuse_requires_upstream_admission`). It must be
  represented through the existing #63 admission path upstream; reattaching an
  old result to a new Job remains `job_instance_not_activated`/`unexpected`.
- Resolution evidence is separately sourced (controlled rule/config, #66-P0
  Source materialization, comparison baseline). The module verifies presence,
  shape, digest pins and consistency with the immutable Expected instance, not
  the external truth of those sources; #63 remains responsible for admission.
- Full byte/input closure remains a #66-P0 proof. This slice checks the
  Expected changed-path scope against the resolved verified scope, preserves
  the Expected input floor, and delegates native input/coverage equality to
  #52's Expected instance.
- No CLI, Hook or Report wiring is added in this slice. The receipt is consumed
  by #61/#63 producers and the eventual Hook final-reconciliation path.
