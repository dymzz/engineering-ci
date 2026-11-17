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
- 三个集成方 authority callable：
  - `issuer_authority` — 独立认证签发身份；
  - `execution_authority` — 证明受控执行/采集边界与证据完整性；
  - `authorization_authority` — 证明当前有效的项目/组织生产者授权；
- `evaluated_at` 与可选 `required_check_ids`（核对授权 scope）。

它**不**执行 Check、不重写 Native Result、不调用 T1-a `admit_evidence`、
不完成 T2 历史复用、不判定 #61 Required/Release。

## 判定规则

仅当以下全部成立，交接才为 `verified` 并暴露可供 T1-a 消费的 `materials`：

1. **Issuer 允许且 authority 接受** — policy allowlist 不够；缺 verifier →
   `unverified`。
2. **执行边界受控** — `controlled=true`、`self_reported=false`，且 kind 不属于
   `uncontrolled_developer_workstation`；execution authority 必须接受。
3. **生产者授权有效** — 未撤销、在有效期内、覆盖所需 check_ids；authorization
   authority 必须接受。
4. **载荷摘要一致** — `payload_digest` / `handoff_ref` 可独立重算。

显式负例（不得提升 Trusted）：

- 本地自报 `declared_trusted=true` — 仅记录
  `local_declared_trusted_ignored`，**永不**充当 authority；
- 身份签名通过但执行不受控 / 证据自报；
- 授权过期、撤销或 Check Scope 未覆盖；
- 缺任一 authority、issuer 不在 allowlist、摘要被篡改。

## 输出语义

- `status`: `verified` / `rejected` / `unverified`。
- `admitted_for_t1a`: 仅在 `verified` 时为 `true`；表示交接材料可供后续 T1-a
  评估，**不等于** T1-a 已接纳。
- `materials`: 验证通过时的 reports / job_receipts / result_ids /
  source_digests；失败时为 `null`。
- 本地执行身份与本次 Actions Job 身份保持独立；不重写 GitHub Ruleset。

## 信任边界

- 三个 authority verifier 是集成边界；Artifact 上传成功、身份签名或自报 PASS
  不能单独证明未受控环境中的工具实际运行。
- 单元/夹具中的 issuer、policy 与 verifier **不是**生产信任根。
- 真实 T1-b 验收仍需要：一条获授权且实际执行可证的本地 PASS → Actions 取得
  原始证据 → 独立验真 → 生成受信 Covered/Remaining Work 的端到端路径。

## 与相邻组件的关系

- **T1-a**：消费 verified materials 做同次接纳；本模块不调用 `admit_evidence`。
- **T2**：本地→Actions 复用场景依赖本交接；PR→main 的 Actions 自源复用不要求
  T1-b 先完工。
- **#61**：消费受信覆盖/Remaining Work；T1-b 不发布最终 Gate。
