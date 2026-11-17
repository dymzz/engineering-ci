# 历史复用接纳（T2 合同切片）

Status: **contract and negative-path first slice; not T2 production acceptance.**
本模块实现 [#63](https://github.com/dymzz/engineering-ci/issues/63) T2 的跨 Run /
PR→main 语义等价复用输入/输出形状。它消费历史 Result 与 #66-P0 执行前 Source 证明，
但 OriginAccepted、当前 Action×Target×Phase 授权和 P0 证明产地都依赖集成方提供的
verifier callable。夹具 verifier **不是**生产信任根，因此本切片不能宣称跨 Run
实际免重跑已完成。

## 职责

`engineering_ci.admission.admit_historical_reuse()` 接收：

- 当前不可变 #66-B Expected projection（**不要求**当前 #66-C Receipt、Job 或
  Native Result）；
- 独立取得的 #66-P0 `pre_execution` Source proofs（`execution_started=false`）；
- 历史候选：逐 Result 的 `result_origins` 信封、原始 Native Result、
  `verification_implementation`，以及历史 `(scope_id, expected_check_ref)`；
- 显式 `reuse_policy`（允许的 check + 已批准实现摘要）；
- 当前 `current_authorization`（Action×Target×Phase×Ref/Candidate）；
- 三个集成方 verifier：
  - `historical_origin_authority` — 认证历史 Scope/Expected、Result 成员、
    Report/Receipt/issuer 与已执行 Checker 实现；
  - `pre_execution_authority` — 认证当前目标/Scope 关联的 P0 证明产地；
  - `authorization_authority` — 认证当前阶段义务适用/到期，独立于历史义务状态。

它**不**执行 Check、不重算 Expected、不修改任何 Receipt、不生成新的 Native
Result、不判定 #61 Required/Release，也不完成 T1-b 本地→Actions 交接。

## 判定公式

仅当以下全部成立，当前实例才进入成功 Accepted Coverage：

`OriginAccepted ∧ InputEquivalent ∧ ResultEligible ∧ ScopeCovered ∧ PolicyPermitsReuse`

- **OriginAccepted**：`historical_origin_authority(context) is True`。候选上的自报
  PASS/admitted 字段已移除；没有 verifier 或 verifier 拒绝 → Remaining Work。
- **InputEquivalent**：对每个历史 Result 调用既有
  `execution.compare_pre_execution_to_historical_result()`；任一不等价则拒绝。
  比较器保持 `trusted=false`；等价本身不等于 T2 trust。
- **ResultEligible**：用不可变当前 Expected + 已鉴权 P0 闭包，经共用
  `reconciliation.derive_strict_expected()` 派生临时严格 Expected（与 #66-C
  Resolution 同一核心；#66-C 仍强制其 evidence refs，T2 不伪造 Receipt），再交给
  `#52 evaluate_expected_satisfaction()`。受信 Native FAIL 保留为
  `verified + unsatisfied`，永不进入成功覆盖。弱 Expected（`changed_paths`，
  digest X）提升到闭合 P0（digest Y，X≠Y）时，要求 P0 在 `closure_digest` 内
  绑定已验证的 `changed_paths_content_digest=X`；该子集 digest 取自与闭合
  SourceRef **同一**有效输入视图（含已验证 prerequisite overlay 的 consumer
  view）的 snapshot binding，而非 overlay 前的独立 worktree 重读。overlay
  若改写 Expected `changed_paths` 成员导致子集 digest ≠ X，则 P0 保持
  unverified。伪造/缺失子集 digest 或 P0 本身仍为 open/changed_paths 则
  Remaining Work。
- **ScopeCovered**：执行前 Source 必须为闭合
  `check_input_closure`/`full_input_closure` 且 `binding_status=content_digest`；
  另须核对 `closure_digest` 可独立重算，并由 `pre_execution_authority` 绑定当前
  目标/Scope。
- **PolicyPermitsReuse**：reuse policy 允许该 Check；`approved_implementations`
  必须匹配历史 `verification_implementation`；`authorization_authority` 必须确认
  当前 Action×Target×Phase×Candidate。历史义务适用性（`not_applicable`/
  `not_yet_due`）只作 provenance，不得单独否决已认证的历史执行事实，也不得
  把旧阶段 Required/不适用结论迁移为当前义务结论。

## 输出语义

- 覆盖键始终是当前 `"<scope_id>/<expected_check_ref>"`。
- 成功条目携带 `source_kind="historical_reuse"`、独立的
  `historical_scope_id` / `historical_expected_check_ref` /
  `historical_event_ref` / 逐 Result `result_origins` 与 `equivalence_basis`。
- 历史 Run/Attempt/Result/Report 身份**不得**改写到当前 Invocation/Job。
- `remaining_work[]` 保留精确原因（缺证明、Source 不等价、实现未批准、来源/授权
  verifier 拒绝、阶段义务不可迁移等）。
- `receipt_ref` 恒为 `null`：执行前复用不依赖当前 Reconciliation。

## 信任边界

- 三个 authority verifier 是集成边界；JSON 自报或仅 allowlist 不能产生生产覆盖。
- 单元/夹具中的 verifier、实现摘要和 policy **不是**生产信任根。
- 真实 T2 验收仍需要：可认证历史证据获取、真实 PR→main 免重跑路径，以及与
  #61 的逐实例交接。T1-b 本地→Actions 仍是独立交付。

## 与相邻组件的关系

- **#66-P0**：只证明当前执行前候选输入；`trusted=false`，不授予复用。
- **T1-a**：同次执行来源接纳；其已认证事实可由
  `historical_origin_authority` 消费，而不是候选自报字段。
- **#66-C**：结构性 Receipt `matched` 不等于 T2 admitted。
- **#61**：消费受信覆盖/Remaining Work；T2 不发布最终 Gate 或 GitHub Context。
