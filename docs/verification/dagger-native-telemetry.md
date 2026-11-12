# Dagger 原生遥测验证

Issue [#87](https://github.com/dymzz/engineering-ci/issues/87) A 段。上级入口：[技术文档](../README.md)；
契约见[Dagger 原生 OpenTelemetry 导出与接收](../architecture/dagger-native-telemetry.md)。

## 负例与边界

| 场景 | 输入 | 期望 |
| --- | --- | --- |
| 无捕获文件 | 只有原生导出配置 | `sent=unknown`、`receiver_observed=false`、`source_associated=false`、`status=incomplete`、退出 3 |
| 空捕获文件 | 捕获存在但没有 Span | `receiver_observed=false`、`sent=unknown`、`reason=capture_empty`，不按"已发送"记录 |
| 只有原生结果 | 无捕获、有原生 Check 结果 | `native_check_result=true` 且结果原样保留，`status=partial`、退出 0 |
| 相同 Trace ID、不同父 Span | 捕获 Span 与传播上下文同 Trace 但父 Span 不同 | `ambiguous`、`reason=trace_id_match_without_propagated_parent`，不绑定 |
| 捕获属于其他 Trace | 与传播上下文无关 | `unbound`、`reason=no_correlated_span`，保留原生结果 |
| 声明的 Check 未出现 | 捕获没有该 `dagger.io/check.name` | `unbound`、`reason=check_name_not_observed` |
| Check 出现但无 Engine session | 捕获有该 Check 名但没有 `dagger.io/session.id` | `unbound`、`reason=engine_session_not_observed`，Check 名相同不构成同源引用 |
| Check 出现在其他 Trace | 捕获有该 Check 名但不在传播 Trace 内 | `ambiguous`、`reason=check_name_outside_propagated_trace` |
| 导出侧失败记录 | 只有 exporter 失败输出 | `sent=not_sent`、`reason=exporter_reported_failure` |
| Required 要求可观察性 | 声明 `--required-observability` 且未绑定 | `status=partial`、`required_observability.satisfied=false`，不因原生结果假通过 |

## 正常路径

| 场景 | 输入 | 期望 |
| --- | --- | --- |
| 传播绑定 | 捕获 Span 携带传播的父 Span ID | `bound`、`basis=w3c_traceparent_propagation`、`status=produced`、退出 0 |
| Engine Check 属性绑定 | 捕获 Span 携带 `dagger.io/check.name` | `bound`、`basis=engine_check_attribute`（实测该属性未发出，见下） |
| Check 选择器绑定 | 捕获 Span 名为 `dag://?check=lint` | `bound`、`basis=engine_check_selector` |
| 模块函数调用绑定 | 捕获 Span 名为 `engineering-ci:EngineeringCi.lint` | `bound`、`basis=engine_check_function` |
| 本次执行了工具 | 捕获 Span 携带 `cache.outcome=executed` | `origin_execution.status=current_execution`、`new_tool_execution=true`、`binding.proves` 含 `origin_tool_execution` |
| 命中缓存 | 捕获 Span 携带 `cache.outcome=hit` | `origin_execution.status=unknown`、`new_tool_execution=false`、缓存观察保留 |
| 复用指向既有原执行 | 两条回执共享同一 `cache.result.id`，一条为 `executed` | 命中那条 `status=existing_execution` 且 `ref` 指向已证明的那次；两次调用只算一次工具执行 |
| 复用无既有原执行 | 只有 `hit` 一条回执 | `status=unknown`、`ref` 为空、`new_tool_execution=false` |
| 缓存观察范围 | 捕获 Span 携带 `dagger.io/cache.outcome` | `cache_observation_scope.status` 用 D-06 词表（`observed_hit` / `observed_execution` / `mixed` / `unknown` / `not_applicable`）并记录 `observed`、`scope`、`reason`；不上升为整个 Check 状态 |

## 定向验收（Issue #87 P0）

| 场景 | 期望 |
| --- | --- |
| 同名 Check 连续两次 Invocation | 两次回执各自绑定自己的 Span，互不合并 |
| 一个 Check 多个 Span | 该 Check 的多个 Span 全部绑定 |
| 并发不同 Check 的交错 Span | 绑定只取本次 Check 的 Span，不串到别的 Check |
| 缓存命中但没有新原生工具运行 | `new_tool_execution=false`、原执行来源 `unknown`，不虚构 Origin Execution |
| 单独 Host Check 无 Dagger Span | `unbound` / `no_capture`，原生结果原样保留 |
| 原生失败 Result 关联对应 Trace | 失败结果与它的 Trace 仍然绑定；失败不被 Trace 掩盖 |

## 本地运行

```text
uv run --locked --group dev pytest tests/test_dagger_native_telemetry.py -q
```

## CI 专属证据

真实的 `dagger check` → Engine 导出 → OTLP 接收端捕获只能在 CI 产生：本机没有可用的 Dagger
Engine/CLI 会话。该路径的运行与断言见 [.github/workflows/ci.yml](../../.github/workflows/ci.yml)
的 `dagger-native-telemetry` Job；它跑两次真实 Check——一次通过、一次用一个真实的 lint 失败——
并为两次各产出一份回执（`receipt.json` 与 `receipt-negative.json`）。**补充的失败捕获是非致命的**：
它的接收端没就绪或没有捕获时，结果记进工件而不让 Job 失败；主路径（第一次真实捕获）仍然严格。已提交的证据摘要见
[Dagger 原生 Trace 捕获证据](dagger-native-telemetry-evidence/README.md)。

未观察到的 Span、缓存结果或接收端身份一律记为 `unknown`，不由命令退出码推断。
