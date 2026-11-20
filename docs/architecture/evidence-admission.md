# 证据接纳（T1-a 合同切片）

Status: **contract and negative-path first slice; not T1-a production acceptance.**
本模块实现 [#63](https://github.com/dymzz/engineering-ci/issues/63) T1-a 的逐实例
Admission 输入/输出形状，但没有可认证的生产 issuer、受控传输或当前批准的实现注册表。
因此本切片只能证明接口、拒绝行为与可复验的 raw-object 一致性，不能宣称“真实受信来源
已完成 T1-a”。

## 职责

`engineering_ci.admission.admit_evidence()` 接收：

- 当前不可变 #66-B Expected projection；
- 同一 scope 的 #66-C Receipt；
- 独立取得的 Raw Execution Report 与 Job/Attempt Receipt；
- Report check 中的 `verification_implementation` 身份；
- 显式 Admission Policy（issuer allowlist + 已批准 Check/definition 实现摘要）；
- **集成方提供的 `authority` verifier callable**，接收 issuer、Report/Job Receipt、
  Job/Run/Attempt、Result 与 `verification_implementation` 上下文并返回最终验证结果。
  任意 JSON `{"verification_status":"verified"}` 不是信任边界；没有 verifier 时不产生
  生产覆盖率。
- **独立来源的 Resolution 及其 `resolution_authority` verifier**：Admission 使用原始
  Expected entry 和该 Resolution 重新调用现有 `reconciliation._resolve_expected()`
  语义，检查 re-derived strict Expected 的摘要等于 Receipt 的
  `resolved_expected_ref`。verifier 上下文使用已由 Admission 验证的权威
  `scope_id`（不是 Receipt Entry 上不存在的字段）、`expected_check_ref` 和
  Resolution；不能只相信 Receipt 内部自洽的 `resolved_expected`；Source/
  Rule/Input/Baseline/Profile 约束必须来自当前 Expected 与受权 Resolution。
- 重复 `expected_check_ref` 的 Receipt entries 在 Receipt/Admission 边界直接拒绝，不静默
  去重。

它**不**执行 Check、不重算 Expected、不修改 #66-C Receipt、不生成新的 Native Result、
不判定 #61 Required/Release。

## 输出语义

`accepted_coverage_by_instance` 是**实例键 mapping**，键严格为
`"<scope_id>/<expected_check_ref>"`；同一 Ref 下同名 Check、不同 Ref 的同名 Check
互不聚合。每个值含 `admission_status`、`admitted`、`origin_status`、
`integrity_status`、`obligation_status`、原始 Result/Report/Receipt 引用和原因。

逐实例分别记录：

- `integrity_status`: raw Report digest/membership、Job Receipt digest/identity 等
  raw-object 一致性。
- `issuer_status`: 由集成方 authority verifier 对具体 Report/Job Receipt/Run/Attempt/
  implementation 上下文验证；只有 policy allowlist 时为 `unverified`，不得当成生产受信。
- `implementation_status`: `approved` / `unapproved` / `unverified`。批准摘要最终必须由
  verifier 绑定到真实 executable/checker；Report 内字符串本身只是待验证输入。
- `origin_status`: 只有 integrity 与 issuer 都已证明时才为 `verified`；Raw FAIL 可保留
  真实 native fact，但不会成为成功覆盖。
- `obligation_status`: `satisfied` / `unsatisfied` / `unknown` / `not_applicable`。
- `resolution_status`: Expected→Resolution→Receipt 的派生证明；不匹配时不得 admitted。
- `admitted`: 仅当完整 selected Witness 集合被 #52 一次性选中、全部成员通过
  integrity/authority/implementation 检查、且均为原生 PASS。
- `verified_native_facts` 只含 authenticated origin（`origin_status=verified`）；
  `integrity_validated_native_facts` 另存 raw-object 验证通过但 origin 未认证的事实。
  `rejected_evidence`, `unverified_evidence`, `remaining_work[]` 分开保存。

语义不变量：

- **Multi-Result Witness 必须整体评估**：每个原始 Result 先独立核对 Report/Job/
  Attempt/实现；随后将完整 selected Result 集合一次性交给
  `check_contract.evaluate_expected_satisfaction()`，不得逐个 Result 判定后取“任一 PASS”。
- `verified` 的 Native FAIL 是 `verified + unsatisfied`，绝不进入 Accepted Coverage，也不被
  旧 PASS 自动覆盖。
- structural `binding_status="matched"` 不等于 `admitted`。
- 未批准实现（例如 PR 自改 Checker 固定 PASS）保留真实执行事实为 `verified`，但义务为
  `unsatisfied`，原因是 `verification_implementation_unapproved`。
- 缺失原始 Report/Receipt 为 `unverified`，不是 clean。
- 未选中（unrelated）的坏 observation 只进入 diagnostics；只有共享同一具体
  Report/Job Receipt（或其他实际共同依赖）的坏事实才污染 selected witness；仅共享
  Job/Run/Attempt 标签不构成污染。
- **Typed `check_result` 前置依赖传递闭包（NEW-014）**：`input_refs` 引用的上游
  `check_result`（及其传递前置结果）不得信任 raw Report 自报 `status="passed"`，
  必须独立通过完整 #63 证据认证（`JobReceipt`、受信任 Issuer、已批准实现、Resolution
  与有效 `(run_ref, attempt_ref)` / `source_ref` / `profile_ref` / Receipt 作用域一致），
  且不得被兄弟/上游 `rejected`、`not_applicable` 或 `resolution_status!=verified`
  结果绕过；Report `blocked_by` 亦仅在共享已观测 Report/Job Receipt 或匹配前置
  `result_id` 上传播，不做跨全量 Reports 的同名 Check 匹配。
- 合法空 Expected 不会被自动升级为完整/受信 PASS。

## 信任边界

Policy 是 caller 提供的显式 allowlist；`authority` 是单独输入的 provenance proof。
本切片没有从项目/组织权限系统取得真实 authority，也没有真实 Producer 输出
`verification_implementation`。因此：

- 单元/夹具测试中的 issuer、authority、实现摘要和 policy **不是生产信任根**；
- 真实 T1-a 验收仍需要独立认证的 issuer、受控执行/采集边界与当前批准的实现注册；
- 在取得这些事实前，输出保持接口级和 non-production 状态，不宣称 T1-a 通过。

## 与 #66-C / #61 / T2 的关系

- 输入 Receipt 保持 `trusted=false`, `evidence_admission=not_evaluated`,
  `required_gate=not_evaluated`；Admission 不重写它。
- Admission 只输出当前 Expected 实例的可复核覆盖/未满足，供 #61 后续消费。
- 不创建第二套 Receipt、Result、Expected、Gate、Scanner 或证据账本。
- 本地→Actions 受控交接见 [本地 → Actions 交接](local-actions-handoff.md)
  （`verify_local_handoff()`）；verified materials 可供后续 T1-a 评估，本模块不调用
  Admission。
- 跨 Run / PR→main 执行前复用见 [历史复用接纳](historical-reuse.md)
  （`admit_historical_reuse()`）；T1-a 的同次接纳不升级为 T2 输入等价。
