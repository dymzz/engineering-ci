# Issue #87 拟议修订：A 段的关联回执字段与拒绝路径

关联 Issue：[#87](https://github.com/dymzz/engineering-ci/issues/87)；实施 PR：
[#89](https://github.com/dymzz/engineering-ci/pull/89)。本文是按 P0 与复审要求整理的**拟议交接**，
用于评审；已交付的实现事实见
[Dagger 原生 OpenTelemetry 导出与接收](../architecture/dagger-native-telemetry.md)。


按本 Issue P0 的修订要求（「先列当前 Dagger CLI/SDK/Engine 固定版本确实提供的原生可关联数据和调用入口」「给出一份拟议最小交接……逐项写清生产者、消费入口、存在条件、唯一性/1:N、原始出处、拒绝原因」）与复审建议（「先把 P0 的字段/关联与明确拒绝路径落入 Issue，再按 beta.16 的真实能力收敛最小接口」），把已交付的关联回执结构写回本 Issue。实施 PR：#89。

### 谁在何时形成回执

**调用端**（跑 `dagger check` 的那一方）在**调用结束、拿到原生结果之后**形成一次只读回执：

```text
engineering-ci dagger-telemetry \
  --capture <接收端捕获的 OTLP/JSON> \
  --native-result <原生结果> \
  --engine-config <Engine 配置> --engine-endpoint <端点> --engine-protocol <协议> \
  --check-name <Check 名> --invocation-ref <调用描述> \
  --output <回执.json> --json
```

Engine 不产生这份回执，接收端也不产生；它只是把"调用端知道的事实"与"接收端确实收到的事实"放在一起判定。回执**不产生 Span**，不建第二个 Telemetry Store。

### 拟议最小交接（逐字段）

| 字段 | 生产者 | 消费入口 | 存在条件 | 唯一性 | 原始出处 | 拒绝原因 |
| --- | --- | --- | --- | --- | --- | --- |
| `actual_invocation_ref` | 实际发起调用的 CLI / ECI 调用端 | 回执消费者 | 有本次真实调用事实时；历史 Verdict 复用不生成新调用 | 1:1（本次请求） | 调用端写入的调用描述 | 无真实调用事实时缺省 |
| `source_binding_ref` | ECI Plan / Source 生产方 | 回执消费者、被检对象核对 | 有实际 Source 绑定时 | 1:N | 既有 `git.execution_tree` / `source_tree_id` / `content_binding` | 绑定不一致时不作 Check 归因 |
| `source_binding.observed_dag_digests` | 引擎（绑定到的调用 Span） | 回执消费者 | 绑定到带内容身份的调用 Span 时 | 1:N | `dagger.io/dag.digest` / `dagger.io/dag.content_preferred_digest` | 没观察到 digest 时为空数组，不推测 |
| `check_or_function_ref` | 真实 Check / Function 入口 | 回执消费者 | 观察到被检的 Check 或 Function 时 | 1:N | 本次捕获的 Span 名 | 未观察到对应 Span 时不填 |
| `native_result_or_bundle_ref` | Dagger / 工具原生结果生产者 | 回执消费者、Report 与 Gate | 原生产者给出结果或产物时 | 1:1 | 结果文件路径 + SHA-256 | 无来源时不能据此证明 Trace 与原生执行相同 |
| `raw_trace_ref` | 原生 Engine/SDK 输出与实际接收端 | 回执消费者 | 接收端确实捕获到内容时 | 1:1 | 捕获文件路径 + SHA-256 + 接收端标签 | 仅配置了 exporter 不等于已接收 |
| `observed_trace_span_ids` | 接收的原始 Trace/Span 数据 | 回执消费者 | 存在已绑定 Span 时 | 1:N、N:1、嵌套、并行均可 | 捕获中的原始 Span ID | 只记录实际收到的 ID，不按 Check 名或时间猜测 |
| `correlation_basis` | 调用端 / SDK / 接收端共同可核对的关联事实 | 回执消费者、B.1 的 `correlation_basis` | 有可核验关联键时 | 1:1 | 传播的父 Span 关系；引擎的 Check 键 | 同名或近似时间窗口不充分 |
| `cache_observation_scope` | 绑定 Span 上的原生缓存事实 | 回执消费者 | 绑定到带缓存事实的调用 Span 时 | 1:N（按观察到的节点列范围） | `dagger.io/cache.outcome` / `hit.route` | 无缓存事实时 `unknown`；无可绑定 Span 时 `not_applicable` |
| `binding_status` | 本回执 | 回执消费者 | 总是 | 1:1 | 本次判定 | `unbound` / `ambiguous` |

### 明确拒绝路径

| 触发 | 判定 | 保留 |
| --- | --- | --- |
| 没有捕获文件 | `unbound` / `no_capture`；`sent=unknown`、`receiver_observed=false` | 原生结果原样保留 |
| 捕获为空 | `unbound` / `capture_empty`；`sent=unknown` | 原生结果原样保留 |
| 声明的 Check 在捕获里没有出现 | `unbound` / `check_name_not_observed` | 捕获原文与原生结果保留 |
| Check 键出现但不在已知 Engine session 内 | `unbound` / `engine_session_not_observed` | 同上；Check 名相同但来源不同不构成同源引用 |
| Check 键出现在传播 Trace 之外 | `ambiguous` / `check_name_outside_propagated_trace` | 同上；不强行拼接 |
| 只有相同 Trace ID、没有传播父 Span | `ambiguous` / `trace_id_match_without_propagated_parent` | 同上；共享 Trace ID 单独不构成关联 |
| 没有可核验的关联键 | `unbound` / `no_correlation_key` | 同上 |
| 导出侧失败记录存在 | `sent=not_sent` | 失败记录与原生结果保留 |
| Required 明确要求可观察性属性但未绑定 | 覆盖保持 `partial`，`required_observability.satisfied=false` | 原生工具的成功或失败不被覆盖 |

任何一段缺少可证明对应关系，就保留各自原始事实并返回 `unbound`/`ambiguous`：不拼接、不伪造父 Span、不把旧 Span 改写成当前 Trace、不把每次变化的 Trace/Invocation ID 注入可缓存的 Function/Exec 输入。

### 原执行来源（复审 P1 的语义）

`new_tool_execution`（本次是否发生新的工具执行）与 `origin_execution.status`（本次结果能否引用既有 Origin Execution）分开记录：`executed`/`uncached` → 本次就是原执行；`hit` → 本次没有新执行且捕获里没有原执行的引用，来源 `unknown`；`joined` → 并发去重到另一次调用的执行，本捕获无法证明，`unknown`。同一 `cache.result.id` 且集合里存在已证明执行时，命中方指向那条**既有** Origin（`existing_execution`）——两种情况都不被统计成两次独立工具执行。

### 关联键的实测结论

`dagger.io/check.name` 在实测中一次未发出；Check 身份实际由**原生 Span 名**承载：`dag://?check=<name>`（CLI 选择器）、`<module>:<object>.<function>`（模块函数调用）、`<object>.<function>`（调用本体，携带缓存事实）。回执按实际观察到的键记录（`binding_keys`），不假设入口形态。
