# 证据接纳（T1-a 合同切片）

Status: **contract and negative-path first slice; not T1-a production acceptance.**
本模块实现 [#63](https://github.com/dymzz/engineering-ci/issues/63) T1-a 的逐实例
Admission 输入/输出形状，但没有可认证的生产 issuer、受控传输或当前批准的实现注册表。
因此本切片只能证明接口与拒绝行为，不能宣称“真实受信来源已完成 T1-a”。

## 职责

`engineering_ci.admission.admit_evidence()` 接收：

- 当前不可变 #66-B Expected projection；
- 同一 scope 的 #66-C Receipt；
- 独立取得的 Raw Execution Report 与 Job/Attempt Receipt；
- Report check 中的 `verification_implementation` 身份；
- 显式 Admission Policy（可信 issuer + 已批准 Check/definition 实现摘要）。

它**不**执行 Check、不重算 Expected、不修改 #66-C Receipt、不生成新的 Native Result、
不判定 #61 Required/Release。

## 输出语义

逐 `(scope_id, expected_check_ref)` 输出：

- `origin_status`: `verified` / `rejected` / `unverified`
  - `verified`：Raw Report、Job Receipt、issuer、runner、实现身份及原生结果事实已按当前
    输入核对；原生 FAIL 也可以是 `verified`。
  - `rejected`：存在可识别的伪造/篡改/错误身份（Report digest、Job Receipt digest/
    identity、issuer、runner、原生 satisfaction 输入）。
  - `unverified`：缺少原始对象或无法证明实现/证据。
- `obligation_status`: `satisfied` / `unsatisfied` / `unknown` / `not_applicable`
- `admitted`: 仅当该实例的 selected #52 Witness 是真实 PASS、实现已批准、所有引用一致；
  成功才进入 `accepted_coverage_by_instance`。
- `verified_native_facts`, `rejected_evidence`, `unverified_evidence` 分开保存。
- `remaining_work[]` 按当前实例键保留未覆盖原因。

语义不变量：

- `verified` 的 Native FAIL 是 `verified + unsatisfied`，绝不进入 Accepted Coverage，也不被
  旧 PASS 自动覆盖。
- structural `binding_status="matched"` 不等于 `admitted`。
- 未批准实现（例如 PR 自改 Checker 固定 PASS）保留真实执行事实为 `verified`，但义务为
  `unsatisfied`，原因是 `verification_implementation_unapproved`。
- 缺失原始 Report/Receipt 为 `unverified`，不是 clean。
- 合法空 Expected 不会被自动升级为完整/受信 PASS。

## 信任边界

Policy 是 caller 提供的显式配置；本切片没有从项目/组织权限系统取得 issuer 或实现批准。
`verification_implementation` 来自 Report check，当前真实 Host Producer 尚未提供该字段。
因此：

- 单元/夹具测试中的 issuer、实现摘要和 policy **不是生产信任根**；
- 真实 T1-a 验收仍需要独立认证的 issuer、受控执行/采集边界与当前批准的实现注册；
- 在取得这些事实前，输出保持接口级和 non-production 状态，不宣称 T1-a 通过。

## 与 #66-C / #61 的关系

- 输入 Receipt 保持 `trusted=false`, `evidence_admission=not_evaluated`,
  `required_gate=not_evaluated`；Admission 不重写它。
- Admission 只输出当前 Expected 实例的可复核覆盖/未满足，供 #61 后续消费。
- 不创建第二套 Receipt、Result、Expected、Gate、Scanner 或证据账本。
