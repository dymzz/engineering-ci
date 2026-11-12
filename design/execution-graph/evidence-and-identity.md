# Evidence Resolution、Identity、Artifacts 与 Cache

**状态：目标契约；未交付的接口以 proposed 标注。** [总览](README.md) · [DAG](dagger-execution.md)

## 1. 复用顺序：D-01 与 D-09
`Source/Context → Authoritative Expected → Plan 对账 → 受信证据有效性 → Remaining Work + Artifact/Prerequisite Closure → Dagger/Host → Native Result → Full Coverage → Current Policy Assessment`。

`Reusable = OriginAccepted ∧ InputEquivalent ∧ ResultEligible ∧ ScopeCovered ∧ PolicyPermitsReuse`；判据由 #63 的已实际交付接口与当前有效策略实现。缺失受信证据不得将候选 JSON 变为受信 PASS。

- `passed` 仅在来源、输入、准则、环境、范围及策略允许时复用。
- 确定性 `failed` 可作为目标架构候选能力，但 #63 当前只明确授权原始成功结果参与 Required 复用；未取得策略裁决前**不得**据此提前跳过 Required。失败事实永远不构成 Required 成功。
- `blocked/cancelled/not_executed` 不作为已完成 Check Verdict 短路；`not_applicable` 必须重评当前适用性。
- 既有有效 PASS 的复用不能使 Expected 集合缩小；未选中、未覆盖与已验证 PASS 分离。

## 2. Verdict 复用 ≠ 产物物化
假设 A 的验证结果可复用，B 仍需消费 A 的 Directory：
- 若存在可核验、符合消费方文件/权限/路径/环境要求的持久产物，则恢复并绑定实际输入。
- 否则保留 A 所需真实数据生成节点，Dagger 自行复用计算缓存。
- 两者均不可行时 B blocked/not_executed，不得以 A 的历史 PASS 冒充 Directory。
- 如果 B 的真实执行前置是“A Check 必须 PASS”，还需独立核验前置 Verdict；若 B 只需要 A 的数据，不受无关 A 失败连坐。
- Preparation 由 Dagger 计算缓存或可核验 Artifact 满足，不作为 Check Verdict 的另一个证据缓存。

## 3. 身份（拟议的交接语义）
| 身份/引用 | 语义与生产者 | 缺失/拒绝 |
|---|---|---|
| `check_key` | ECI 按实际 Check、Subject、Input Closure、规则、工具和结果相关环境定义的**语义等价键**；不是 Dagger Cache Key | 缺少等价依据不得作跨 Run 结果复用 |
| `invocation_id` | 实际调用入口生产，仅代表本次发起原生 Check 请求 | 纯历史复用不创建虚构 Invocation |
| `origin_execution_id` | 原生工具/可核验执行来源，能证明才存在 | Cache Hit 不生成新的独立原生执行 |
| `native_result_ref` | Native Result/Artifact 生产者提供的不可变或可核验引用；Report/Gate 统一消费 | 缺失、冲突不得授予对应证据完整 PASS |
| `assessment_id` | ECI 当前 Policy Assessment 生成，引用本次消费的结果与有效 Profile | Assessment 数量不代表执行次数 |

现有 `selection.plan()` 与 `execution.execute()` **尚未全面交付上述字段**；保留现有 `git.execution_tree`、`source_tree_id`、`inputs`、`checks[]`、原生 status/exit_code/diagnostics，在真实契约版本化演进时才添加相应引用。

同一 Check Key 多个相反 Native Result 必须保留各自来源并裁决冲突，不能默选 PASS。不同 Assessment、Invocation 或共享缓存结果不自动算独立验证。

### 3.1 D-04 的结果共享范围（B 与 C 分开验收）

| 范围 | 可以断言的事实 | 不得断言的事实 / 交付 |
|---|---|---|
| **同一 Dagger 计算节点/物化 bundle** | 同一原生工具结果同时供 `@check`、Report、Artifact/Gate 读取；可用原生执行次数、Artifact/退出验证 | 不可由“再次请求同名 Function”直接推定跨会话 100% 共享；**B 验收** |
| **不同 Dagger Invocation/Session** | 引擎可能命中缓存，须按真实调用、缓存层及原生执行分别归因 | Cache Hit 不自动等于同一个 `origin_execution_id`，也不必然代表没有发生任何工具操作 |
| **跨 `dagger check` / `execute-plan` / 单独 Artifact Function / 历史 Run** | 通过 `native_result_ref`、来源、输入/Artifact/Bundle 及 #63 接纳建立**可验证时**的结果复用关系 | 两次真正运行的原生工具可以且应有不同 Native Result；不强制为它们伪造同一个结果 ID；**C 验收** |

当前 `tests()` 和 `repository_test_artifacts()` 均调用 `_repository_tests()`，Weaver 也有共享原生产物路径；但 `execute_plan()` 的 Pytest 是另一条 Container/Exec 路径。**分别调用这些入口，不等于共享同一次执行。** B 只保证本次具体共享计算/结果路径不重复扫描；跨入口复用要求 C 的来源/结果交接实际交付后才能声明。C 未交付前按各自真实运行及缓存状态记账，不凭名称合并。

## 4. D-02～D-08
- **D-02**：Check Input Closure 必须完整；按实际语义选 Dagger Directory.filter 或完整 Source，检查模块自身源码对 Function/Exec Cache 的影响。
- **D-03**：真实 Check FAIL 与环境/工具/报告失败分离，保留退出和诊断，复用现有 `expect=ANY`。
- **D-04**：在一次明确共享的原生计算/Bundle 内，由 `@check`、Report、Artifact 和 Gate 消费同一结果；跨调用、跨入口使用则必须按 §3.1 提供可核验结果引用与来源，不假定任何条件下 100% 去重。
- **D-05**：计算等价、调用、原始执行、结果、策略评价身份分离；每次变化的 Invocation/Trace 元数据不得无依据进入可缓存计算输入。
- **D-06**：缓存观察取值 `observed_hit / observed_execution / mixed / unknown / not_applicable`，必须注明**实际观测范围**。这些是设计值，不是现有 Report 字段。
- **D-07**：Host、Dagger、可核验外部 Check 原生结果可汇总，但保留平台来源，不将 Provider Job success 当原生 PASS。
- **D-08**：纯计算、外部动态输入、外部副作用分别决定缓存/复用条件；发布行为不经普通计算缓存宣称已发生。
- **D-09**：最终 Gate 独立对账 Expected/Selected/Executed/Accepted Reuse/Not Applicable，缺失 Required 不得通过。

Dagger Function、Container Exec 的缓存由 Engine 决定。Cache Hit **不等于** #63 Evidence Admission；原生 OTel 归因见 [Telemetry](telemetry.md)。

[现有 Selection](../../docs/architecture/selection.md) · [Pytest 原生结果共享](../../docs/architecture/testing/pytest-reporting.md) · [#63](https://github.com/dymzz/engineering-ci/issues/63)