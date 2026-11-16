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
  `all_observations_reconciled`, and `matched` <-> `selected_result_ids` (plus,
  for every `matched` entry, internal consistency of `native_satisfaction` —
  `kind`/`schema_version`/`consumer_scope="supplied_expected_only"`,
  `required=false`/`trusted=false`, `expected_failures_complete=false`,
  `all_requirements_satisfied` vs tool-less `native_pass` reason, `result_ids`
  bounded to qualified non-rejected observed candidates — and `requirements[]`
  against controlled `CheckDefinition`, `resolved_expected.rule_ref`,
  `requirements[].reason="native_pass"`, `requirements[].observed`,
  `selected_results`, `rejected_results`, `after` prerequisites, tool match,
  and positional witness order).
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
  responsible for admission. Similarly, `validate_receipt()` operates strictly
  on the compressed Receipt representation (which summarizes `selected_results`
  without replaying raw `NativeResult.input_refs` or original Observation
  reports), so upstream cross-check_result prerequisite `input_refs` graphs are
  enforced during `reconcile_expected_results()` / `#52` evaluation rather than
  re-synthesized in compressed-receipt validation.
- **Frozen `#63` L2 Consumer Interface & Acyclic Execution Phases**:
  - `CURRENT_EXECUTION_ATTRIBUTION_FIELDS`: `("invocation_ref", "event_ref", "scope_id", "expected_set_ref")` — when `invocation_ref` is bound on `reconcile_expected_results()`, all four must appear and match on `job_receipt` (`job_*_unproven` / `job_*_mismatch` otherwise).
  - `EVIDENCE_ADMISSION_HANDOFF_FIELDS`: `("invocation_ref", "event_ref", "scope_id", "expected_set_ref", "receipt_ref")` — reuses the canonical Receipt digest `receipt_ref` (`sha256:<64-hex>` computed over the full payload by `reconcile_expected_results()` and verified by `validate_receipt()`, rejecting forged/mismatched digests with `receipt_ref_invalid` / `receipt_ref_mismatch`); no second unverified hash field is emitted.
  - `EXECUTION_PHASE_ORDER` (acyclic): `pre_run_history_to_remaining_work` (`#63` pre-run history -> Remaining Work) -> `current_execution_to_reconciliation` (`#66-C`) -> `post_run_evidence_to_gate_projection` (`#63` post-run admission -> `#61`).
- Full byte/input closure remains a #66-P0 proof (`whitespace`, `forwarding`,
  offline `docs` with succeeded `inventory`, local non-external `tach` on Host;
  open-world/external checks remain `changed_paths`/`identity_only`). This slice checks the
  Expected changed-path scope against the resolved verified scope, preserves
  the Expected input floor, and delegates native input/coverage equality to
  #52's Expected instance.
- No CLI, Hook or Report wiring is added in this slice. The receipt is consumed
  by #61/#63 producers and the eventual Hook final-reconciliation path.

## `#63` L2 消费与 Accepted Coverage 合同样例（非 `#63` 已实装代码）

> 本节为基于当前代码真实 Schema（`engineering-ci-expected-result-reconciliation` v1、`check-contract` v2、`expected_checks` v2）的**合同样例与消费映射说明**，用于冻结 `#66-C -> #63 -> #61` L2 交接语义；**不代表 `#63` 历史跨 Run 复用引擎或 `#61` 最终 Gate 已在本仓库实装。**

### 1. 三层状态职责分离与实例唯一键（`(scope_id, expected_check_ref)`）

| 层级与模块 | 负责输出字段 / 状态 | 精确语义与消费边界 |
| --- | --- | --- |
| `#66-C` Structural Receipt (`engineering_ci.reconciliation`) | `status` (`matched`/`partial`/`unrouted`/`unknown`)、`scope_id`、`entries[i].expected_check_ref`、`entries[i].binding_status`、`receipt_ref`、`trusted: false`、`required: false`、`evidence_admission: "not_evaluated"`、`required_gate: "not_evaluated"` | **`trusted=false` 与 `evidence_admission="not_evaluated"` 仅表示 `#66-C` 未执行外部来源签发认证**，**不是**对证据可信性的永久否决；`receipt_ref` 是唯一规范摘要。 |
| `#63` Independent Admission (上游消费端) | `accepted_coverage_by_instance["<scope_id>/<expected_check_ref>"]`（`admitted` / `rejected` / `unverified`）、`remaining_work[]`（每项含 `scope_id` + `expected_check_ref`） | **必须以 Expected 实例身份 `(scope_id, expected_check_ref)` 为唯一键**（`check_name`/`name` 仅用于展示），严禁以 Check 名称聚合，确保多 Ref（`Ref A` vs `Ref B`）同名 `tach` 实例互不串扰；不回写原 Receipt 字段。 |
| `#61` Required / Release Gate (下游决策端) | Final Gate Assessment（`passed` / `blocked`） | 汇总完整 Required Obligations、`expected_completeness` 与按实例独立的 `#63` Admission 结论决定最终放行；单实例 `matched` 或局部 `admitted` 不直接等于 Required Release PASS。 |

### 2. Receipt 压缩索引与原始完整 Result / Issuer 的取回验证路径

`reconciliation._result_evidence()`（`src/engineering_ci/reconciliation.py:1234–1250`）仅生成**压缩证据索引**（`result_id`、`result_instance`、`status`、`result_origin`、`native_exit_code`、`tool`、`job_ref`、`run_ref`、`attempt_ref`、`runner_ref`、`report_ref`、`job_receipt_ref`），**有意不包含**原始 `NativeResult` 的 `source_ref`、`check.rule_ref`、`check.profile_ref`、`input_refs`、`baseline_ref`、`coverage`、`evidence`；同时 `entries[i].resolved_expected` 是 `#66-B` 的预期对象，**不得**将预期对象当作实际执行证据形成循环证明。

`#63` 消费端的独立取回与验证步骤：

1. **定位原始载荷**：通过 `entries[i].selected_results[]`（或 `observed_results[]`）的 `(job_ref, run_ref, attempt_ref, report_ref, job_receipt_ref, result_id)`，从 Run Artifact / 归档存储（或本仓库现有 `gh` Run/Artifact 下载通道与 `#72/#102` 调度产物）获取完整的 **Raw Execution Report (`engineering-ci-execution-report`)** 与原始 **Job/Attempt Receipt (`job_receipt`)**。
2. **摘要与签发者独立验真**：
   - 重新计算 Raw Execution Report SHA-256 并核对等于 `report_ref.sha256`；
   - 重新计算 `job_receipt` SHA-256 并核对等于 `job_receipt_ref`；
   - 独立认证 `job_receipt.issuer_ref` 与 Run/Attempt 调度来源权威（外部签名/OIDC/信任链，不采信自声明）。
3. **提取并校验完整 Raw `NativeResult`**：
   - 从已验真 Raw Report 按 `result_id` 提取完整 `engineering-ci-native-check-result` (v2)，调用 `check_contract.validate_result(raw_result)` 验证其 `content_id`/`result_id`；
   - **直接读取** `raw_result["source_ref"]`、`raw_result["check"]["rule_ref"]`、`raw_result["check"]["profile_ref"]`、`raw_result["input_refs"]`、`raw_result["baseline_ref"]`、`raw_result["tool"]`、`raw_result["coverage"]`、`raw_result["evidence"]`，与当前 Check 需求比对语义等价性（可用 `entries[i].evidence_refs` 辅助核查规则/来源收据指纹）。
4. **缺失或不可验证的处理**：若 Raw Report、原始 `NativeResult` 或 `job_receipt` 任一缺失、SHA-256 不匹配或 `issuer_ref` 不可验真，**严禁**凭 Receipt 压缩记录或 `resolved_expected` 降级放行；消费端必须维持 `admission_status: "unverified"`（或 `"rejected"`），并将该 Check 保留在 `remaining_work`。

### 3. 逐 Check Accepted Coverage、完整性上限与同/跨 Run 等价规则

1. **逐实例覆盖 vs 整体完整性/Gate 解耦**：
   - 在 N 个适用 Expected 实例中（包含同一 Ref 下部分 Check 认证成功、或跨多 Ref 存在同名 Check 如 `Ref A: tach` 与 `Ref B: tach`），`#63` 以 `(scope_id, expected_check_ref)` 为唯一键在 `accepted_coverage_by_instance` 中记录已核实项并**仅将该实例**从 `remaining_work` 移除；`Ref B` 下尚未认证的同名实例 `tach` 必须保留在 `remaining_work` 中，`Ref A` 的接纳绝对不得消解 `Ref B`。
   - 上游 `expected_completeness != "complete"` 或 `all_observations_reconciled == false` 限制整体证明上限及 `#61` Required Gate 放行，**不得无条件抹消**无冲突实例已核实的逐项覆盖。
   - **共享前提/污染收敛**：仅当异常结果/rejected observation/失败 prerequisite 与某些 Expected 实例**共享**同一受污染 `job_ref`/`run_ref`/`attempt_ref`、`source_ref`、报告级 `plan_declaration_consistent == false` 或 `blocked_by` / `pre_execution_prerequisite_failed` 依赖边时，按实际传播范围使受影响实例的覆盖失效。
2. **同次执行归因 (`#66-C`) vs 跨次历史复用 (`#63`)**：
   - **同次执行 (`#66-C`)**：严格校验当前 `invocation_ref`、`expected_event_ref`、`scope_id`、`expected_set_ref`（不一致即在同次对账拒绝，并写入 `limitations: ["cross_run_reuse_requires_upstream_admission"]`）。
   - **跨次历史复用 (`#63`)**：历史 Run 具有不同的 `event_ref`（如不同的 `head_ref`/`head_commit`/`base_ref`）或不同 `invocation_ref` 属于**正常跨 Run 前提**，不得仅因 `expected_event_ref` 不同就否决复用；`#63` 保留历史原始 `(run_ref, attempt_ref, runner_ref, result_origin, event_ref)` 溯源，独立验证以下 **语义等价要件**：
     - 同一 `check_id`、`definition_version` 与受批准的 `rule_ref` / `profile_ref`（含当前生效窗与策略许可）；
     - 静态闭合的 `source_ref`（`scope_kind` 为 `check_input_closure` / `full_input_closure`，实际被检输入闭包摘要/材质一致性且满足当前 Target 实例语义）；开放范围（`changed_paths`/`identity_only`，如 `vulture`/`source`）不得冒充全闭包等价复用；
     - 若 Check 依赖对比基线或输入清单：实际消耗的 `baseline_ref.base_commit`、`tool` 版本/配置、`input_refs`、`coverage` 与环境约束必须完全等价。

### 4. `#63` L2 消费投影合同样例

#### 样例 A — 多 Ref 同名实例隔离 + 部分逐实例独立认证（`receipt_ref` 规范摘要）

```json
{
  "contract_sample": "63_accepted_coverage_projection",
  "phase": "post_run_evidence_to_gate_projection",
  "immutable_receipt_ref": "sha256:receipt_a_digest",
  "immutable_receipt_trusted_flag": false,
  "expected_set_ref": "sha256:expected_set",
  "expected_completeness": "per_ref_container_only",
  "all_observations_reconciled": true,
  "accepted_coverage_by_instance": {
    "sha256:scope_ref_a/sha256:exp_tach_ref_a": {
      "scope_id": "sha256:scope_ref_a",
      "expected_check_ref": "sha256:exp_tach_ref_a",
      "check_name": "tach",
      "admission_status": "admitted",
      "source_kind": "current_run",
      "witness_result_id": "sha256:raw_tach_a_result_id",
      "raw_report_ref": {"kind": "engineering-ci-execution-report", "schema_version": 1, "sha256": "..."},
      "raw_result_verified": true,
      "issuer_verified": true,
      "origin_trace": {"run_ref": "run/123", "attempt_ref": "attempt/2", "runner_ref": "execution:host", "result_origin": "host"}
    },
    "sha256:scope_ref_b/sha256:exp_tach_ref_b": {
      "scope_id": "sha256:scope_ref_b",
      "expected_check_ref": "sha256:exp_tach_ref_b",
      "check_name": "tach",
      "admission_status": "unverified",
      "reason": "raw_report_missing_or_issuer_unverified",
      "receipt_binding_status": "matched"
    },
    "sha256:scope_ref_a/sha256:exp_vulture_ref_a": {
      "scope_id": "sha256:scope_ref_a",
      "expected_check_ref": "sha256:exp_vulture_ref_a",
      "check_name": "vulture",
      "admission_status": "unverified",
      "reason": "source_scope_not_closed",
      "receipt_binding_status": "unknown"
    }
  },
  "remaining_work": [
    {"scope_id": "sha256:scope_ref_b", "expected_check_ref": "sha256:exp_tach_ref_b", "check_name": "tach"},
    {"scope_id": "sha256:scope_ref_a", "expected_check_ref": "sha256:exp_vulture_ref_a", "check_name": "vulture"}
  ],
  "required_gate_ready": false,
  "required_gate_block_reason": "remaining_work_non_empty_and_completeness_unproven"
}
```
> 注：`Ref A` 的 `tach` (`sha256:scope_ref_a/sha256:exp_tach_ref_a`) 已完成独立认证并移出 `remaining_work`；`Ref B` 同名 `tach` (`sha256:scope_ref_b/sha256:exp_tach_ref_b`) 在 Receipt 中即使呈现 `matched`，但因 `#63` 尚缺原始 Report/签发者认证而保持 `unverified` 并留在 `remaining_work`（`check_name` 仅作展示，`Ref A` 的接纳不消除 `Ref B`）；原始 Receipt 的 `trusted=false` 与 `receipt_ref` 摘要均保持不变。

#### 样例 B — 跨 Run 历史复用正反例（Event Ref 不同时的实例等价判断）

```json
{
  "contract_sample": "63_cross_run_reuse_decisions",
  "phase": "pre_run_history_to_remaining_work",
  "decisions": [
    {
      "scope_id": "sha256:scope_ref_a",
      "expected_check_ref": "sha256:exp_tach_ref_a",
      "check_name": "tach",
      "historical_event_ref": {"base_commit": "aaa...", "head_commit": "111...", "head_ref": "refs/heads/pr-old"},
      "current_event_ref": {"base_commit": "bbb...", "head_commit": "222...", "head_ref": "refs/heads/pr-new"},
      "admission_status": "admitted",
      "source_kind": "historical_reuse",
      "equivalence_basis": {
        "requires_baseline": false,
        "closed_source_scope_kind": "check_input_closure",
        "raw_native_source_digest_equal": true,
        "raw_native_rule_and_profile_equal": true,
        "issuer_verified": true
      },
      "origin_trace": {"run_ref": "run/099", "attempt_ref": "attempt/1", "result_origin": "dagger_export"},
      "remove_from_remaining_work": true
    },
    {
      "scope_id": "sha256:scope_ref_a",
      "expected_check_ref": "sha256:exp_ws_ref_a",
      "check_name": "whitespace",
      "historical_event_ref": {"base_commit": "aaa...", "head_commit": "111..."},
      "current_event_ref": {"base_commit": "bbb...", "head_commit": "222..."},
      "admission_status": "rejected",
      "reason": "baseline_mismatch",
      "equivalence_basis": {
        "requires_baseline": true,
        "target_tree_equal": true,
        "raw_native_baseline_commit": "aaa...",
        "current_expected_baseline_commit": "bbb..."
      },
      "remove_from_remaining_work": false
    },
    {
      "scope_id": "sha256:scope_ref_a",
      "expected_check_ref": "sha256:exp_dep_ref_a",
      "check_name": "tach",
      "admission_status": "rejected",
      "reason": "shared_prerequisite_or_source_conflict",
      "remove_from_remaining_work": false
    }
  ]
}
```
> 注：样例 B 每条决策均绑定到 `(scope_id, expected_check_ref)`；第一项说明不同 `event_ref` / `invocation_ref` 下，只要不依赖旧比较基线且 `#63` 基于完整原始 `NativeResult` 与签发链证明闭环等价，仍可合法复用；第二项说明差分型 Check 即使目标 Tree 相同但实际比较 `base_commit` 不同，必须拒绝复用；第三项说明存在共享前提或污染冲突的实例须连带保留在 `remaining_work`。
