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

来源认证（issuer / execution / authorization / 时间）与 **consumer 覆盖度**
彼此独立：

| `provenance_status` | `consumer_coverage` | `status` | `admitted_for_t1a` / `materials` |
| --- | --- | --- | --- |
| `authenticated` | `complete` | `verified` | raw Reports/Receipts 可交 T1-a |
| `authenticated` | `partial` | `partial` | **否**；仅保留授权元数据 |
| `authenticated` / 其他 | `none` | `rejected` 等 | 无 materials |
| `unverified` | * | `unverified` | 无 materials |

完整 `verified` 另需：

1. **Issuer 允许且 authority 接受** — policy allowlist 不够；缺 verifier →
   `unverified`。
2. **执行边界受控** — `controlled=true`、`self_reported=false`；execution
   authority 返回认证 `executed_at`（须与包内 `produced_at` 一致；`produced_at`
   单独不作授权证明）。
3. **生产者授权覆盖当前 consumer 与 Context×Action×Target 主体绑定** —
   Action/Target/Phase 与独立 `consumer_scope` 一致，grant 覆盖全部 consumer
   `check_ids`，且必须由 Producer Grant、native `job_receipts` 或结构化
   `authorization_authority` 回执（回显匹配 `handoff_ref`、`consumer_scope_ref`
   与 `bound_subjects`）绑定当前上下文必需的主体：
   - 对 PR / push / `merge_group` / release 或 `event_head` 等事件驱动范围绑定
     `event_ref`、`scope_id`、`expected_set_ref`（及 `consumer_scope` 显式声明的主体）；
   - 仅当 Action/Target 实际指向 `candidate` / `artifact` / `release`（非
     `event_head`）或范围显式声明时要求 `candidate_digest`，不对合法无
     Candidate 的 PR/`event_head` 场景强加伪造 Candidate 摘要。
   `authorization_authority` 仅返回裸布尔 `True` 不得补齐缺失的必需主体。
4. **时间双检** — 授权在 `executed_at` **与** `evaluated_at` 均有效。
5. **载荷摘要一致** — `payload_digest` / `handoff_ref` 可独立重算。

部分授权（Action/Target/Phase 匹配，仅部分 Check 被 grant）：

- `provenance_status=authenticated`，`consumer_coverage=partial`，
  `status=partial`（**不是**整包 `verified`）；
- `authorized_check_ids` / `remaining_work[]` 仅作元数据；
- `materials=null`，`admitted_for_t1a=false` — Check ID 白名单**不是** T1-a
  可消费的 raw Report/Job Receipt；若将来要部分交给 T1-a，须按授权实例投影并
  独立核验真实 raw 对象，不得从 Check ID 推断 Native PASS。

显式负例（不得提升 Trusted）：

- 本地自报 `declared_trusted=true` — 仅记录
  `local_declared_trusted_ignored`，**永不**充当 authority；
- 身份签名通过但执行不受控 / 证据自报；
- 授权在执行时尚未生效、在当前消费时已过期/撤销；
- 未来执行时间、或认证时间与 `produced_at` 不一致；
- `main` grant 用于不同 PR Target / 错误 Phase/Action；
- 缺任一 authority、issuer 不在 allowlist、摘要被篡改。

## 输出语义

- `status`: `verified` / `partial` / `rejected` / `unverified`。
- `provenance_status`: `authenticated` / `rejected` / `unverified` — 来源认证，
  独立于 consumer 覆盖度。
- `consumer_coverage`: `complete` / `partial` / `none`。
- `executed_at`: 由 execution authority 认证的执行/采集时间；未认证则为 `null`。
- `authorized_check_ids` / `remaining_work[]`: 相对独立 consumer Check 集合的元数据。
- `admitted_for_t1a`: 仅在 `consumer_coverage=complete` 且 `materials` 含 raw
  Reports/Receipts 时为 `true`；**不等于** T1-a 已接纳。
- `materials`: 完整核验时含 raw bags + `authorized_check_ids`；部分/失败时为
  `null`（不得用仅含 Check ID 的 dict 冒充可接纳材料）。
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
