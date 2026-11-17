# 本地 → Actions 交接（T1-b 合同切片）

Status: **contract and negative-path first slice; not T1-b production acceptance.**
本模块实现 [#63](https://github.com/dymzz/engineering-ci/issues/63) T1-b 的本地
Hook/CLI 原生证据 → Actions/ECI 受控交接输入/输出形状。它要求独立的 issuer、
执行边界与生产者授权证明，但夹具 verifier **不是**生产信任根，因此本切片不能
宣称真实获授权本地→Actions 端到端交接已完成。

## 职责

`engineering_ci.local_actions_handoff` 提供：

- `build_local_handoff_package(...)` — 构造不可变候选交接包（status=`candidate`）；
- `validate_local_handoff_package(...)` — 重算 `handoff_ref` / `payload_digest`；
- `verify_local_handoff(...)` — 在 Actions/ECI 边界核验交接包。

核验输入：

- 候选交接包（issuer、execution_boundary、producer_authorization、payload、
  可选 transport_ref）；
- 显式 `handoff_policy`（允许的 issuer + 执行边界种类）；
- **独立的 `consumer_scope`**：当前 Actions Event / #66 Expected 的
  Action×Target×Phase 与非空 `check_ids`。生产者 grant 中的 scope **不得**被当作
  当前 consumer scope；
- 三个集成方 authority callable：
  - `issuer_authority` — 独立认证签发身份；
  - `execution_authority` — 证明受控执行/采集边界，并返回认证的
    `{"accepted": True, "executed_at": ...}`；裸 `True` 只证明边界、不证明时间；
  - `authorization_authority` — 证明生产者授权（接收含 consumer_scope /
    executed_at / evaluated_at 的上下文）；
- `evaluated_at` — 当前 Actions 接纳时点。

它**不**执行 Check、不重写 Native Result、不调用 T1-a `admit_evidence`、
不完成 T2 历史复用、不判定 #61 Required/Release。

## 判定规则

仅当以下全部成立，交接才为完整 `verified` 并释放原始 `materials`：

1. **Issuer 允许且 authority 接受** — policy allowlist 不够；缺 verifier →
   `unverified`。
2. **执行边界受控** — `controlled=true`、`self_reported=false`，且 kind 不属于
   `uncontrolled_developer_workstation`；execution authority 必须接受并给出认证
   `executed_at`（须与包内 `produced_at` 一致；`produced_at` 单独不作授权证明）。
3. **生产者授权覆盖当前 consumer** — Action/Target/Phase 必须与独立
   `consumer_scope` 一致；grant 的 `check_ids` 覆盖 consumer 的 Check 集合。
4. **时间双检** — 授权在认证的 `executed_at` **与** `evaluated_at` 均有效；
   撤销在执行时或当前消费时生效则拒绝；`executed_at > evaluated_at` 拒绝。
5. **载荷摘要一致** — `payload_digest` / `handoff_ref` 可独立重算。

部分授权（Action/Target/Phase 匹配，但仅部分 Check 被 grant）：

- `status=verified`，`materials` **仅**含 `authorized_check_ids`，不释放未受限的
  reports/job_receipts/result_ids；
- `remaining_work[]` 记录未授权 Check；
- 无 consumer Check 集合、或 Action/Target/Phase 不匹配、或无一 Check 被授权 →
  不释放 materials。

显式负例（不得提升 Trusted）：

- 本地自报 `declared_trusted=true` — 仅记录
  `local_declared_trusted_ignored`，**永不**充当 authority；
- 身份签名通过但执行不受控 / 证据自报；
- 授权在执行时尚未生效、在当前消费时已过期/撤销；
- 未来执行时间、或认证时间与 `produced_at` 不一致；
- `main` grant 用于不同 PR Target / 错误 Phase/Action；
- 缺任一 authority、issuer 不在 allowlist、摘要被篡改。

## 输出语义

- `status`: `verified` / `rejected` / `unverified`。
- `executed_at`: 由 execution authority 认证的执行/采集时间；未认证则为 `null`。
- `authorized_check_ids` / `remaining_work[]`: 相对独立 consumer Check 集合的授权子集。
- `admitted_for_t1a`: 完整或授权子集材料可供后续 T1-a 评估时为 `true`；
  **不等于** T1-a 已接纳。
- `materials`: 完整核验时含 raw bags + `authorized_check_ids`；部分授权时仅含
  `authorized_check_ids`；失败时为 `null`。
- 本地执行身份与本次 Actions Job 身份保持独立；不重写 GitHub Ruleset。

## 信任边界

- 三个 authority verifier 是集成边界；Artifact 上传成功、身份签名或自报 PASS
  不能单独证明未受控环境中的工具实际运行。
- 包内 `produced_at` 与生产者自报 scope 都不是信任根；时间与 consumer 范围必须由
  独立 authority / Actions+#66 上下文证明。
- 单元/夹具中的 issuer、policy 与 verifier **不是**生产信任根。
- 真实 T1-b 验收仍需要：一条获授权且实际执行可证的本地 PASS → Actions 取得
  原始证据 → 独立验真 → 生成受信 Covered/Remaining Work 的端到端路径。

## 与相邻组件的关系

- **T1-a**：消费 verified materials 做同次接纳；本模块不调用 `admit_evidence`。
- **T2**：本地→Actions 复用场景依赖本交接；PR→main 的 Actions 自源复用不要求
  T1-b 先完工。
- **#61**：消费受信覆盖/Remaining Work；T1-b 不发布最终 Gate。
