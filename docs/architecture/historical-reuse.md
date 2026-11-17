# 历史复用接纳（T2 合同切片）

Status: **contract and negative-path first slice; not T2 production acceptance.**
本模块实现 [#63](https://github.com/dymzz/engineering-ci/issues/63) T2 的跨 Run /
PR→main 语义等价复用输入/输出形状。它消费已 OriginAccepted 的历史候选与
#66-P0 执行前 Source 证明，但没有生产受信 issuer、受控跨 Run 检索或真实
PR→main 免重跑部署。因此本切片只能证明接口、拒绝行为与可复验的等价判定，
不能宣称“跨 Run 实际免重跑已完成”。

## 职责

`engineering_ci.admission.admit_historical_reuse()` 接收：

- 当前不可变 #66-B Expected projection（**不要求**当前 #66-C Receipt、Job 或
  Native Result）；
- 独立取得的 #66-P0 `pre_execution` Source proofs（`execution_started=false`）；
- 历史候选：调用方声明的 OriginAccepted 状态、原始 Native Result 集合、历史
  `(scope_id, expected_check_ref)`、Event/Run/Attempt 身份；
- 显式 reuse policy（允许的 check、是否要求闭合 Source、是否禁止阶段义务迁移等）。

它**不**执行 Check、不重算 Expected、不修改任何 Receipt、不生成新的 Native
Result、不判定 #61 Required/Release，也不完成 T1-b 本地→Actions 交接。

## 判定公式

仅当以下全部成立，当前实例才进入成功 Accepted Coverage：

`OriginAccepted ∧ InputEquivalent ∧ ResultEligible ∧ ScopeCovered ∧ PolicyPermitsReuse`

- **OriginAccepted**：历史候选 `admitted=true`、`origin_status=verified`、
  `issuer_verified=true`、`obligation_status=satisfied`。夹具声明不是生产信任根。
- **InputEquivalent**：对每个历史 Result 调用既有
  `execution.compare_pre_execution_to_historical_result()`；任一不等价则拒绝。
- **ResultEligible**：完整 Result 集合经 `#52 evaluate_expected_satisfaction()`
  对**当前** Expected 满足；受信 Native FAIL 保留为 `verified + unsatisfied`，
  永不进入成功覆盖。
- **ScopeCovered**：默认要求执行前 Source 为闭合
  `check_input_closure`/`full_input_closure` 且 `binding_status=content_digest`；
  `changed_paths` 证明不足以跨 Run 复用。
- **PolicyPermitsReuse**：reuse policy 允许该 Check；禁止把历史
  `not_applicable`/`not_yet_due` 义务结论迁移到当前阶段。

## 输出语义

- 覆盖键始终是当前 `"<scope_id>/<expected_check_ref>"`。
- 成功条目携带 `source_kind="historical_reuse"`、独立的
  `historical_scope_id` / `historical_expected_check_ref` /
  `historical_event_ref` / `origin_trace` 与 `equivalence_basis`。
- 历史 Run/Attempt/Result 身份**不得**改写到当前 Invocation/Job。
- `remaining_work[]` 保留精确原因（缺证明、Source 不等价、规则/工具变化、
  未接纳来源、策略禁止等）。
- `receipt_ref` 恒为 `null`：执行前复用不依赖当前 Reconciliation。

## 信任边界

- 历史 OriginAccepted 由调用方供给；本切片不重新跑完整 T1-a raw Report/Job
  认证，也不从 GitHub 检索 Artifact。
- 单元/夹具中的 issuer、OriginAccepted 和 policy **不是**生产信任根。
- 真实 T2 验收仍需要：可认证历史证据获取、真实 PR→main 免重跑路径，以及与
  #61 的逐实例交接。T1-b 本地→Actions 仍是独立交付。

## 与相邻组件的关系

- **#66-P0**：只证明当前执行前候选输入；`trusted=false`，不授予复用。
- **T1-a**：同次执行来源接纳；其成功输出可成为 T2 历史候选的 OriginAccepted 输入。
- **#66-C**：结构性 Receipt `matched` 不等于 T2 admitted。
- **#61**：消费受信覆盖/Remaining Work；T2 不发布最终 Gate 或 GitHub Context。
