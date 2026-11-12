# Dagger 原生 Trace 捕获证据（#87 A.1）

Issue [#87](https://github.com/dymzz/engineering-ci/issues/87) A 段的真实捕获：一次原生
`dagger check` → Engine 原生导出 → 接收端实收 → 观察回执。

**这是 CI 产生的候选证据**，不是 #63 的受信接纳，不是 #61 的 Required 聚合，也不是 CI/CD SemConv 的
Pipeline/Task 映射。

## 本次执行是什么

| 事实 | 值 |
| --- | --- |
| 来源 | [CI run 43245046315](https://github.com/dymzz/engineering-ci/actions/runs/43245046315) 的 `dagger-native-telemetry` Job |
| CLI / Engine | `v1.0.0-beta.16`（commit `2d95ef98`），`linux/amd64`，`runner-host: container://dagger-engine` |
| 原生导出 | Engine 配置 `/etc/dagger/engine.json` 的 `telemetry.workloadExport=true`；`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 与 `..._METRICS_ENDPOINT` 指向 runner 的 Docker bridge 地址；协议 `http/protobuf` |
| 接收端 | runner 本机临时接收端（`.github/scripts/otlp_http_receiver.py`），绑定 runner 网桥地址，非通配地址 |
| 原生检查 | `dagger check --engineering-ci --check=lint`，退出码 `0` |
| 回执 | `status=produced`，`receipt.exit=0`，`check.exit=0` |
| 五个事实 | `exporter_configured=true`、`sent=sent`、`receiver_observed=true`、`source_associated=true`、`native_check_result=true` |
| 关联键 | `engine_check_selector` + `engine_check_function`（同一次捕获里两个键都命中） |
| 原执行来源 | `status=current_execution`、`new_tool_execution=true`、`cache.outcome=executed`、`cache.result.id=7921`、`ref=2721950eab62e1f2` |
| 覆盖 | 实收 2494 个 Span、2 个 Trace、2 个 Engine session；绑定 3 个；`cache.outcome` 观测到 `executed`/`hit`/`uncached` |

## 观察到的原生 Check 链路

`capture-check-spans.json` 是本次捕获里 **Check 链路的 4 条 Span 原文**（未改写，仅按 Span 名筛出），
同一 Engine session `9tu1d4colgjai9f0uvpjuwcnw`：

| Scope | Span 名 | 关键属性 |
| --- | --- | --- |
| `dagger.io/util/parallel` | `dag://?check=lint` | CLI 的 Check 选择器 |
| `dagger.io/dagql` | `Check.sync` | Check 同步调用 |
| `dagger.io/dagql` | `engineering-ci:EngineeringCi.lint` | `dagger.io/dag.digest` |
| `dagger.io/core` | `EngineeringCi.lint` | `dagger.io/cache.contract=1`、`dagger.io/cache.outcome=executed`、`dagger.io/cache.result.id=7921` |

父链：`dag://?check=lint` → `Check.sync` → `engineering-ci:EngineeringCi.lint` → `EngineeringCi.lint`。

## 本次**没有**证明的

- `dagger.io/check.name` / `dagger.io/check.passed` 在 2494 个 Span 里一次都没有出现；Check 身份实际由
  Span 名承载。
- Engine 是否把会话 Span 挂在外部传入的 `traceparent` 之下：本次没有传播上下文，未验证。
- Engine 侧"所有 Span 到达接收端"的保证：固定源码明确说接收端接受不等于持久保证。
- 缓存命中场景的原执行引用：本次是首次执行（`executed`）；命中路径只有单元测试覆盖。

## 完整捕获

完整 `capture.json` 为 2997051 字节，未入库（仓库现有最大文件 636 KB），保留在 CI 工件
`dagger-native-telemetry` 里（保留 7 天）：

```text
sha256  85377d4a00a5c967fa0e7b99b4fc7eb901cb249098e7b3ea5ee19129df4120bb
```

## 文件

| 文件 | 作用 |
| --- | --- |
| `receipt.json` | 观察回执：五个事实、关联判定、原执行来源、覆盖范围 |
| `capture-check-spans.json` | 本次捕获里 Check 链路的 4 条 Span 原文 |
| `native-result.json` | 原生检查结果（独立于 Trace 保留） |
| `dagger-version.txt` | 实际 CLI / Engine 版本 |
| `check.exit` / `receipt.exit` | 原生检查退出码 / 回执生成退出码 |
| `SHA256SUMS` | 上述文件的摘要 |

## 复现

```text
uv run --locked --group dev pytest tests/test_dagger_native_telemetry.py -q
```

真实捕获只能由 CI 产生（本机没有可用的 Dagger Engine 会话）；步骤见
[.github/workflows/ci.yml](../../../.github/workflows/ci.yml) 的 `dagger-native-telemetry` Job。
