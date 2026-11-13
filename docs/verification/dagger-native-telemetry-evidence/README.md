# Dagger 原生 Trace 捕获证据（#87 A.1）

Issue [#87](https://github.com/dymzz/engineering-ci/issues/87) A 段的真实捕获：原生
`dagger check` → Engine 原生导出 → 接收端实收 → 观察回执。**两次真实 Check**：一次通过，
一次用真实的 lint 失败。

**这是 CI 产生的候选证据**，不是 #63 的受信接纳，不是 #61 的 Required 聚合，也不是 CI/CD SemConv 的
Pipeline/Task 映射。

## 本次执行是什么

| 事实 | 值 |
| --- | --- |
| 来源 | [CI run 43386983906](https://github.com/dymzz/engineering-ci/actions/runs/43386983906) 的 `dagger-native-telemetry` Job |
| CLI / Engine | `v1.0.0-beta.16`（commit `2d95ef98`），`linux/amd64`，`runner-host: container://dagger-engine` |
| 原生导出 | Engine 配置 `/etc/dagger/engine.json` 的 `telemetry.workloadExport=true`；`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 与 `..._METRICS_ENDPOINT` 指向 runner 的 Docker bridge 地址；协议 `http/protobuf` |
| 接收端 | runner 本机临时接收端（`.github/scripts/otlp_http_receiver.py`），绑定 runner 网桥地址，非通配地址 |
| 原生检查（通过） | `dagger check --engineering-ci --check=lint`，退出码 `0` |
| 原生检查（失败） | 同一命令，源码中注入一个真实的 lint 违规，退出码 `1` |
| 单独 Report/Artifact Function | `dagger api call repository-ruff-tools export`；原生结果是产物，不是 Check |
| 回执 | 三份都是 `status=produced` |
| 五个事实（Check 入口两次一致） | `exporter_configured=true`、`sent=sent`、`receiver_observed=true`、`source_associated=true`、`native_check_result=true` |
| 关联键（Check 入口两次） | `engine_check_selector` + `function_call` |
| 关联键（Function 入口） | `engine_function_call`（键 `function_call`）：引擎把函数调用命名为**驼峰**（`engineering-ci:EngineeringCi.repositoryRuffTools`），CLI 用连字符（`repository-ruff-tools`） |
| 原执行来源 | 三次都是 `current_execution`、`new_tool_execution=true`、`cache.outcome=executed` |
| 缓存观察范围 | 三次都是 `observed_execution`，范围是绑定到的调用 Span |
| Source 绑定 | Check 入口两次的 `source_binding.source_binding_ref` 相同（同一 checkout 的树），但 `observed_dag_digests` 不同——失败那次多了一个注入文件，内容身份随之改变 |
| 覆盖 | Check 入口实收 2494 个 Span；Function 入口实收 201 个 Span、绑定 2 个 |

**失败那次的要点**：原生 lint 真的失败了（`exit_code=1`），Trace 仍然把这次调用绑定到它的 Check 上，
失败**没有**被 Trace 掩盖，Trace 也**没有**掩盖失败——`native_check_result` 保留 `exit_code=1`，
`binding_status` 仍是 `bound`。

## 观察到的原生 Check 链路

`capture-check-spans.json` 与 `capture-check-spans-negative.json` 是两次捕获里 **Check 链路的 4 条
Span 原文**（未改写，仅按 Span 名筛出）：

| Scope | Span 名 | 关键属性 |
| --- | --- | --- |
| `dagger.io/util/parallel` | `dag://?check=lint` | CLI 的 Check 选择器 |
| `dagger.io/dagql` | `Check.sync` | Check 同步调用 |
| `dagger.io/dagql` | `engineering-ci:EngineeringCi.lint` | `dagger.io/dag.digest` |
| `dagger.io/core` | `EngineeringCi.lint` | `dagger.io/cache.contract=1`、`dagger.io/cache.outcome=executed`、`dagger.io/cache.result.id` |

父链：`dag://?check=lint` → `Check.sync` → `engineering-ci:EngineeringCi.lint` → `EngineeringCi.lint`。

## 本次**没有**证明的

- `dagger.io/check.name` / `dagger.io/check.passed` 在捕获的 Span 里一次都没有出现；Check 身份实际由
  Span 名承载。
- Engine 是否把会话 Span 挂在外部传入的 `traceparent` 之下：本次没有传播上下文，未验证。
- Engine 侧"所有 Span 到达接收端"的保证：固定源码明确说接收端接受不等于持久保证。
- 缓存命中场景的原执行引用：两次都是首次执行（`executed`）；命中路径只有单元测试覆盖。

## 完整捕获

完整捕获未入库（仓库现有最大文件 636 KB），保留在 CI 工件 `dagger-native-telemetry` 里（保留 7 天）：

| 文件 | 字节 | SHA-256 |
| --- | --- | --- |
| `capture.json`（Check 入口，通过） | 3000237 | `3b8c03551ff22c898d6f2a9706dfcbaab7cd0fceb93ee16f2757e3ec71d8acc0` |
| `capture-negative.json`（Check 入口，失败） | 1695100 | `094658810e9fae57c5851fc8c40b44420d43bbe54727609e1f8ee2a898868439` |
| `capture-entrypoints.json`（Function 入口） | 293783 | `09160528f42b201b6d08f66d43df798660a6bc5e595ee76e0457ee3920c2276b` |

入库的是按 Span 名筛出的原文（`capture-check-spans*.json`），未改写。

## 文件

| 文件 | 作用 |
| --- | --- |
| `receipt.json` / `receipt-negative.json` / `receipt-entrypoints.json` | 三份观察回执：五个事实、关联判定、原执行来源、缓存观察范围、Source 绑定、覆盖 |
| `capture-check-spans.json` / `capture-check-spans-negative.json` | Check 入口两次捕获里 Check 链路的 4 条 Span 原文 |
| `capture-check-spans-entrypoints.json` | Function 入口捕获里的函数调用 Span 原文 |
| `native-result.json` / `native-result-negative.json` | 两次原生检查结果（独立于 Trace 保留） |
| `dagger-version.txt` | 实际 CLI / Engine 版本 |
| `check.exit` / `negative-check.exit` | 两次原生检查的退出码（`0` / `1`） |
| `receipt.exit` / `receipt-negative.exit` / `receipt-entrypoints.exit` | 三份回执生成的退出码 |
| `SHA256SUMS` | 上述文件的摘要 |

## 复现

```text
uv run --locked --group dev pytest tests/test_dagger_native_telemetry.py -q
```

真实捕获只能由 CI 产生（本机没有可用的 Dagger Engine 会话）；步骤见
[.github/workflows/ci.yml](../../../.github/workflows/ci.yml) 的 `dagger-native-telemetry` Job。
