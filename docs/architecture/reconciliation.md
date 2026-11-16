# Expected → Result reconciliation (#66-C)

Status: additive #66-C contract and first implementation slice
(`engineering_ci.reconciliation`). It does not change Plan schema 2, the
Execution Report schema, or any pre-execution Expected identity. Scheduler and
Hook lifecycle remain #66/#72 work; this module consumes their evidence.

## Position

The module binds the three identities issued before execution —
`expected_set_ref`, `scope_id` and per-instance `expected_check_ref` — to
activated scheduler Job attempts and genuine #52 Native Results. It produces one
versioned **Binding/Reconciliation Receipt**. It:

- consumes an already derived Expected projection (#66-B) and never rewrites it;
- accepts routing, rule/source/baseline resolution and Execution Reports under
  an upstream Job/Attempt receipt;
- qualifies results by activated attempt, Report membership and Check object
  identity before calling `check_contract.evaluate_expected_satisfaction()`
  exactly once per Expected instance, and cites its `selected_result_ids`;
- keeps `matched` / `missing` / `insufficient` / `unknown` / `unrouted` /
  `not_applicable` distinct, and reports `unexpected`, `duplicate`, rejected
  execution objects and inactive attempts;
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
        "attempt_ref": "attempt/2",
        "runner_ref": "execution:host",
        "report_ref": {
          "kind": "engineering-ci-execution-report",
          "schema_version": 1,
          "sha256": "…raw 64 hex…"
        },
        "attempt_eligibility": "eligible",
        "qualification_ref": {"name": "scheduler-attempt", "version": "1", "sha256": "…64 hex…"}
      }
    ],
    "mapping_status": "routed",
    "scheduler_ref": {"name": "lefthook", "version": "2.2.2"},
    "effective_config_ref": {"name": "hook-job", "version": "1"},
    "reason": null
  }
  ```

  `job_refs` are Job names; `job_instances` are the actually activated
  identities `(job_ref, run_ref, attempt_ref)`. A `routed` binding without
  instances is `unknown` (`job_activation_identity_missing`); it cannot produce
  `matched`. One Job may map to many Expected instances and one Expected
  instance may map to many jobs/instances.

  `attempt_eligibility` is the scheduler's qualification of an occurred
  attempt: `eligible`, `superseded`, `terminal`, or `unknown`. Only `eligible`
  attempts are passed to #52; `superseded`/`terminal` attempts remain recorded
  in `inactive_attempts` and cannot satisfy the current reconciliation. If any
  attempt is `unknown`, or no eligible attempt exists, the entry stays
  `unknown` (`effective_attempt_unqualified`); multiple eligible attempts of
  the same Job are `multiple_eligible_attempts` rather than a silent choice.
  Non-unknown eligibility requires a versioned `qualification_ref`. The module
  consumes this qualification; it does not infer it from result order, ID size
  or success. `mapping_status` is `routed`, `unrouted`, `filtered`,
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

- `observations` — genuine Native Results under one verified Execution Report
  and Job/Attempt receipt:

  ```json
  {
    "job_ref": "job/quality",
    "run_ref": "run/123",
    "attempt_ref": "attempt/2",
    "runner_ref": "execution:host",
    "report_ref": {
      "kind": "engineering-ci-execution-report",
      "schema_version": 1,
      "sha256": "…canonical digest of report…"
    },
    "report": { "kind": "engineering-ci-execution-report", "schema_version": 1,
                "checks": [ {"native_results": [ {"…": "…"} ]} ] },
    "job_receipt": {
      "kind": "engineering-ci-job-attempt-receipt",
      "schema_version": 1,
      "job_ref": "job/quality",
      "run_ref": "run/123",
      "attempt_ref": "attempt/2",
      "runner_ref": "execution:host",
      "report_ref": {"…": "…"},
      "issuer_ref": {"name": "hook-runner", "version": "1", "sha256": "…64 hex…"},
      "verification_status": "upstream_verified"
    },
    "native_results": [ {"…": "…"} ]
  }
  ```

  The `(job_ref, run_ref, attempt_ref)` tuple is unique in one call. The
  `report_ref` digest is recomputed over the submitted `report`; supplied
  `native_results` must exactly match the Native Results found in that report's
  `checks[]`. This is how the module proves a Result ID belongs to the Report,
  rather than merely copying a self-reported Result list. A report/result
  mismatch or digest mismatch is refused as invalid input.

  The `job_receipt` binds that `report_ref` to the same Job/Run/Attempt/runner
  and carries an upstream `verification_status`. Only
  `upstream_verified` is accepted for `matched`; a missing/unverified receipt
  leaves the entry `unknown` (`job_attribution_unproven`), with observed
  results still recorded. #66-C consumes this upstream proof state; it does
  not authenticate the issuer (that remains #63's admission boundary). A
  routing instance that declares `runner_ref`/`report_ref` must equal the
  observation's value (`job_runner_mismatch` / `job_report_mismatch`, surfaced
  as `job_execution_identity_mismatch`). An observation carrying results
  without a complete execution identity (`run_ref`, `attempt_ref`,
  `runner_ref`, verified report) stays `unknown`, not `matched`.

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
  "reconciled_scope": "declared_expected_instances",
  "all_observations_reconciled": false,
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
         "attempt_ref": "attempt/2", "runner_ref": "execution:host",
         "report_ref": null, "attempt_eligibility": "eligible",
         "qualification_ref": {"…": "…"}, "observed": true}
      ],
      "inactive_attempts": [
        {"job_ref": "job/quality", "run_ref": "run/123",
         "attempt_ref": "attempt/1", "attempt_eligibility": "superseded",
         "observed": true, "result_ids": ["sha256:…"]}
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
         "attempt_ref": "attempt/2", "runner_ref": "execution:host",
         "report_ref": {"…": "…"}, "job_receipt_ref": "sha256:…"}
      ],
      "selected_result_ids": ["sha256:…"],
      "selected_results": [{"…": "same evidence shape…"}],
      "rejected_results": [
        {"result_id": "sha256:…", "reason": "source_mismatch",
         "job_ref": "job/quality", "run_ref": "run/123",
         "attempt_ref": "attempt/2"}
      ],
      "rejected_observations": [
        {"reason": "job_runner_mismatch", "job_ref": "job/quality",
         "run_ref": "run/123", "attempt_ref": "attempt/2",
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
    "unexpected": 0, "duplicate": 0,
    "rejected_results": 1, "rejected_observations": 0,
    "inactive_attempts": 1, "selected_results": 1
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
| `matched` | An eligible activated Job attempt produced a Native Result proven to belong to its Execution Report and the expected Check/Rule/Source/Baseline/Phase/Profile object, and #52 satisfied the resolved Expected instance. `selected_result_ids` is exactly #52's selection. |
| `missing` | The Expected instance is routed to an eligible activated Job attempt but no matching native result was observed, or routing says the Job was filtered/not configured/skipped. |
| `insufficient` | Results were observed but do not prove the Expected constraints: native failure, missing required tool/result, uncovered input/coverage, a rule/source/baseline/phase object mismatch (`execution_object_mismatch`), or resolution inputs shrinking the Expected input floor. |
| `unknown` | Authoritative resolution, execution identity or attribution is absent/unresolved: rule/source/baseline/phase resolution, activated Job identity, effective attempt qualification, Run/Attempt/report/runner binding, upstream Job receipt, Expected Profile binding, or Check definition. Observed native facts are still recorded. |
| `unrouted` | No routing binding exists yet, or it explicitly reports `unrouted`. The Expected instance is retained. |
| `not_applicable` | The immutable Expected applicability says the Check does not apply; no result is required. An observed result for it is reported as `unexpected`. |

Receipt-level status is `matched` only when the Expected projection itself is
`complete`, every non-inapplicable instance is `matched`, no unexpected
observation exists, and no rejected result/observation remains
(`all_observations_reconciled=true`). Otherwise it is `partial`, `unrouted`
(all eligible instances unrouted), or `unknown` (all eligible instances unknown
/ no legal completeness proof for an empty set). A legal empty `complete`
Expected scope yields `matched` with `summary.expected=0`; this still does not
grant Required.

Test-framework Expected scopes carry their native Selection status
(`targeted`, `full`, `escalated`) rather than `selected`; all three are
applicable and are reconciled like `selected`. `no_tests`/`no_changes` are
treated as not applicable for reconciliation.

## Two-level semantics

`entry.binding_status="matched"` means the declared Expected instance has a
qualified native witness. It does **not** mean every observation in the input
was reconciled. Extra observations with a wrong execution object remain in
`rejected_results`/`rejected_observations`; unassociated observations remain in
`unexpected`; superseded/terminal attempts remain in `inactive_attempts`.

`reconciled_scope` is therefore always
`declared_expected_instances`, and `all_observations_reconciled` is true only
when no unexpected, rejected-result or rejected-observation remains. The
receipt-level `status` cannot be `matched` while
`all_observations_reconciled=false`. `summary.rejected_results`,
`rejected_observations`, `inactive_attempts`, `unexpected` and `duplicate`
expose the counts so #61/#63 can consume the narrower per-Expected facts without
mistaking them for a full observation ledger.

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
- Native Results enter the candidate group only from an `eligible` activated
  Job attempt whose Run/Attempt (and declared runner/report, plus any bound
  `invocation_ref`/`event_ref`/`scope_id`/`expected_set_ref` attribution) match
  the observation, whose Execution Report digest, per-Check admissibility
  (`plan_authorized`, `check_contract.status == "valid"`, `check_id`, `status`,
  primary `native_result` consistency) and result membership are verified,
  whose upstream Job receipt binds that report to the attempt, and whose
  Check/Rule/Source/Baseline/Phase/Profile identity matches the resolved
  Expected instance (including `changed_paths_content_digest` when expanding a
  `changed_paths` Expected scope to `check_input_closure`/`full_input_closure`).
- `evaluate_expected_satisfaction()` is called exactly once for that qualified
  candidate group. A failed, rejected, inactive-attempt or unselected result
  cannot complete an expected input or coverage reference. `validate_receipt()`
  recomputes and enforces internal consistency across `status`, `summary`,
  `all_observations_reconciled`, and `matched` <-> `selected_result_ids`.
- The Expected projection, its identities, and its completeness claims are
  never rewritten or upgraded.
- `trusted` / `required` are always `false` and
  `evidence_admission`/`required_gate` are `not_evaluated`.

## Limits

- The caller must pass a Plan-validated Expected projection. This module
  recomputes its immutable identities but does not re-authorize the engineering
  Profile or Plan (`expected_projection_authority_not_reverified`).
- Scheduler routing, Hook Dispatch/Collect, and Qualification evidence remain
  upstream in #72/#102 (`engineering-ci.hook.dispatch.v1`); this module verifies
  supplied routing and `job_receipt` / `invocation_ref` / `event_ref`
  attribution without generating Hook receipts itself. A routed Expected without
  activated Job instances stays `unknown`.
- The Job/Attempt receipt is consumed as an upstream proof state. #66-C verifies
  the Report digest, Report->Result membership, attribution fields, and receipt
  logical consistency, but does not authenticate the issuer; #63 remains
  responsible for admission.
- Cross-Run/Attempt historical reuse is not accepted as current execution
  evidence here (`cross_run_reuse_requires_upstream_admission`). Replaying a
  report from another `invocation_ref`, `event_ref`, `scope_id`, or
  `expected_set_ref` is rejected; legitimate reuse must go through the existing
  #63 admission path upstream.
- Resolution evidence is separately sourced (controlled rule/config, #66-P0
  Source materialization via `execution.build_source_resolution()`, which reads
  only Plan + disk configs + Git ODB + `expected_check_ref`/`scope_id` and never
  reads `native_result`, comparison baseline). The module verifies presence,
  shape, digest pins, `profile_ref` authority, and consistency with the immutable
  Expected instance, not the external truth of those sources; #63 remains
  responsible for admission.
- **Frozen `#63` L2 Consumer Interface & Acyclic Execution Phases**:
  - `CURRENT_EXECUTION_ATTRIBUTION_FIELDS`: `("invocation_ref", "event_ref", "scope_id", "expected_set_ref")` — when `invocation_ref` is bound on `reconcile_expected_results()`, all four must appear and match on `job_receipt` (`job_*_unproven` / `job_*_mismatch` otherwise).
  - `EVIDENCE_ADMISSION_HANDOFF_FIELDS`: `("invocation_ref", "event_ref", "scope_id", "expected_set_ref", "receipt_ref", "receipt_sha256")`.
  - `EXECUTION_PHASE_ORDER` (acyclic): `pre_run_history_to_remaining_work` (`#63` pre-run history -> Remaining Work) -> `current_execution_to_reconciliation` (`#66-C`) -> `post_run_evidence_to_gate_projection` (`#63` post-run admission -> `#61`).
- Full byte/input closure remains a #66-P0 proof (`whitespace`, `forwarding`,
  offline `docs` with succeeded `inventory`, local non-external `tach` on Host;
  open-world/external checks remain `changed_paths`/`identity_only`). This slice checks the
  Expected changed-path scope against the resolved verified scope, preserves
  the Expected input floor, and delegates native input/coverage equality to
  #52's Expected instance.
- No CLI, Hook or Report wiring is added in this slice. The receipt is consumed
  by #61/#63 producers and the eventual Hook final-reconciliation path.
