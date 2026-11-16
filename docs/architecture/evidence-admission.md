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
- **独立的 `authority` provenance proof**（issuer refs、authority ref、verification
  status）。Policy 本身不认证 issuer；没有 `authority` 时不会产生生产覆盖率。

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
- `issuer_status`: 由独立 `authority` proof 认证；只有 policy allowlist 时为
  `unverified`，不得当成生产受信。
- `implementation_status`: `approved` / `unapproved` / `unverified`。
- `origin_status`: 只有 integrity 与 issuer 都已证明时才为 `verified`；Raw FAIL 可保留
  真实 native fact，但不会成为成功覆盖。
- `obligation_status`: `satisfied` / `unsatisfied` / `unknown` / `not_applicable`。
- `admitted`: 仅当完整 selected Witness 集合被 #52 一次性选中、全部成员通过
  integrity/authority/implementation 检查、且均为原生 PASS。
- `verified_native_facts`, `rejected_evidence`, `unverified_evidence`,
  `remaining_work[]` 分开保存。

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
- 未选中（unrelated）的坏 observation 只进入 diagnostics；只有共享同一 Report/
  Job/Run/Attempt 的坏事实才污染 selected witness。
- 合法空 Expected 不会被自动升级为完整/受信 PASS。

## 信任边界

Policy 是 caller 提供的显式 allowlist；`authority` 是单独输入的 provenance proof。
本切片没有从项目/组织权限系统取得真实 authority，也没有真实 Producer 输出
`verification_implementation`。因此：

- 单元/夹具测试中的 issuer、authority、实现摘要和 policy **不是生产信任根**；
- 真实 T1-a 验收仍需要独立认证的 issuer、受控执行/采集边界与当前批准的实现注册；
- 在取得这些事实前，输出保持接口级和 non-production 状态，不宣称 T1-a 通过。

## 与 #66-C / #61 的关系

- 输入 Receipt 保持 `trusted=false`, `evidence_admission=not_evaluated`,
  `required_gate=not_evaluated`；Admission 不重写它。
- Admission 只输出当前 Expected 实例的可复核覆盖/未满足，供 #61 后续消费。
- 不创建第二套 Receipt、Result、Expected、Gate、Scanner 或证据账本。
