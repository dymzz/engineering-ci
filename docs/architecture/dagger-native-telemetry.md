# Dagger 原生 OpenTelemetry 导出与接收

Issue [#87](https://github.com/dymzz/engineering-ci/issues/87) A 段。设计权威见
[Execution Graph v0.4 Telemetry](../../design/execution-graph/telemetry.md)；本文件只记录**固定版本**
的原生能力与已交付的观察契约，不声称跨 Run 受信接纳或 Required 聚合。

## 生产者决定

| 对象 | 生产者 | 原生事实 |
| --- | --- | --- |
| Engine Function/Exec/Check Span | Dagger Engine `v1.0.0-beta.16` | `dagger.io/check.name`、`dagger.io/check.passed`、`dagger.io/cache.*`；ECI 不重新埋点 |
| Module Client Span | 仓库内固定生成的 Dagger Python SDK | `dagger.io/sdk.python`，仅 traces，OTLP/HTTP + protobuf |
| 接收端捕获 | 实际运行的 OTLP Receiver | 收到的 Span 原文、接收端身份与内容摘要 |
| 关联回执 | `engineering_ci.dagger_telemetry` | 五个独立事实的只读记录；不产生 Span，不新建 Telemetry Store |

ECI 只**读取**原生 Span 与原生结果，不生成、不改写、不补造 Trace/Span ID。

## 固定版本原生能力

版本固定：`dagger-module.toml` 的 `engineVersion = "v1.0.0-beta.16"`；CI 从该值派生 CLI 版本
（`DAGGER_VERSION` 环境变量 → `dl.dagger.io/dagger/install.sh`）并以同一版本启动
`registry.dagger.io/engine:v1.0.0-beta.16`。仓库内生成的 SDK **没有版本标记**，其版本即生成它的 Engine。

| 能力 | 状态 | 事实 | 来源 |
| --- | --- | --- | --- |
| Engine workload 导出 | 可用 | Engine 配置文件 `/etc/dagger/engine.json` 的 `telemetry.workloadExport` 打开导出 | `engine/config/config.go::TelemetryConfig`；`engine/telemetry/workload_export.go::NewWorkloadExport` |
| 导出协议 | 可用 | `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` 或 `OTEL_EXPORTER_OTLP_PROTOCOL`：`grpc` / `http/protobuf`（默认 http/protobuf） | `engine/telemetry/workload_export.go::workloadExportProtocol` |
| 导出端点 | 可用 | `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 或 `OTEL_EXPORTER_OTLP_ENDPOINT`；两者都为空时导出**不启动** | `engine/telemetry/workload_export.go` |
| 导出信号范围 | 可用 | Spans + workload readings；**不导出 logs** | `engine/telemetry/workload_export.go` |
| 导出持久性 | 部分 | 接收端接受不等于 Engine 侧持久保证；有界队列 + BatchSpanProcessor | `engine/telemetry/workload_export.go::WorkloadExport` |
| Check 身份属性 | 可用 | Span 携带 `dagger.io/check.name` 与 `dagger.io/check.passed` | `core/checks.go`（`CheckNameAttr`/`CheckPassedAttr`） |
| 缓存观察属性 | 可用 | `dagger.io/cache.contract`、`dagger.io/cache.outcome`（`hit`/`executed`/`joined`/`uncached`）、`dagger.io/cache.hit.route` | `engine/telemetryattrs/attrs.go` |
| Module Client 遥测 | 可用 | 固定 SDK 依 `OTEL_*` 环境变量初始化 OTel，仅导出 traces | `.dagger/sdk/src/dagger/telemetry.py` |
| Module Client 传输 | 可用 | SDK 依赖 `opentelemetry-exporter-otlp-proto-http`，即 OTLP/HTTP + protobuf；`OTEL_EXPORTER_OTLP_PROTOCOL` 对固定 SDK 无效 | `.dagger/sdk/pyproject.toml` |
| Module 上下文传播 | 可用 | SDK 读取 `TRACEPARENT` 并把 `traceparent` 注入 GraphQL 请求 | `.dagger/sdk/src/dagger/telemetry.py::_DaggerPropagationConfigurator`；`.dagger/sdk/src/dagger/client/_session.py::TelemetryTransport` |
| Module 实时导出 | 可用 | `OTEL_EXPORTER_OTLP_TRACES_LIVE` 使 Span 在开始时导出而非结束时 | `.dagger/sdk/src/dagger/telemetry.py::LiveSpanProcessor` |
| SDK 版本标记 | 不可用 | 生成的 SDK 代码没有版本常量，不推测 | `.dagger/sdk/src/dagger/client/gen.py` |

**未知项（固定版本未被仓库内来源证明）：** Engine 是否把会话 Span 挂在外部传入的 `traceparent` 之下；
Engine 是否有"所有 Span 到达接收端"的保证；Engine 之外的 Function 级缓存观察字段。以上在
[观察回执](#观察状态)中按 `unknown` 记录，不由较新 Dagger 文档推断。

## 导出与接收路径

1. **Engine 侧**：Engine 容器读取 `/etc/dagger/engine.json`；`telemetry.workloadExport` 为真且环境提供
   OTLP 端点时，Engine 导出自身 Spans。CI 当前启动 Engine 的命令没有这两项，因此**现状是未配置导出**。
2. **Module 侧**：固定 SDK 在 Module 运行时读取 `OTEL_*`；导出的是 Module Client Span，不是 Engine Span。
3. **接收端**：任何实际运行的 OTLP Receiver。仓库内已固定的原生 Receiver 是 Weaver 自身的 OTLP/gRPC
   接收端（`weaver registry live-check --input-source otlp --output http --otlp-grpc-address …`），
   其就绪/停止/报告协议见 `weaver_runtime.py`；它是 **replay 语义**，不是常驻推送目标。
4. **关联回执**：`engineering-ci dagger-telemetry` 读取上述配置与捕获文件，写出只读回执。

同一进程内两条链路互不覆盖：Engine 导出与 SDK 导出是不同事实，SDK 的 `sent` 不证明 Engine 的 `sent`。

## 观察状态

回执把五个事实分开记录，任何一个都不覆盖其他：

| 事实 | 含义 | 提升条件 |
| --- | --- | --- |
| `exporter_configured` | 原生导出被配置（SDK 环境变量或 Engine 配置 + 端点） | 读到真实配置；接收端已启动不算 |
| `sent` | `sent` / `not_sent` / `unknown` | `sent` 需接收端实际收到；`not_sent` 只由导出侧失败记录证明；否则 `unknown` |
| `receiver_observed` | 接收端确实捕获到 Span | 捕获文件存在且非空，并记录路径与 SHA-256 |
| `source_associated` | 捕获内容可归因到本次调用 | 满足[关联键](#关联键)规则 |
| `native_check_result` | 独立成立的原生 Check 结果 | 原生退出与产物本身，不依赖 Trace |

命令 `exit=0`、Collector 已启动、时间窗口接近，都**不是**任何一层的证据。缺少 Trace 时不按时间戳或命令
猜 Trace/Span ID；原生 Check 结果若能独立证明则原样保留。

`sent=unknown` 与 `receiver_observed=false` 只降低 Trace/缓存归因，不抹除原生工具的成功或失败。

## 关联键

可核验的原生键：

| 键 | 来源 | 归因强度 |
| --- | --- | --- |
| `w3c_traceparent_propagation` | SDK 注入的 `traceparent` 与捕获 Span 的父 Span 关系 | 强：捕获 Span 直接携带被传播的父 Span ID |
| `engine_check_attribute` | 捕获 Span 的 `dagger.io/check.name` 等于本次调用的 Check 名 | 中：Engine 自身的 Check 身份 |
| `engine_check_attribute_in_propagated_trace` | 两者同时成立 | 最强 |

**不足以归因：** 相同 Trace ID、相同 Check 名、近似时间窗口、相同 Scope 名。共享 Trace ID 必须再核对实际
Span 的 Function/Check 与调用上下文；无法唯一判定时返回 `unbound` 或 `ambiguous` 并保留各自原始事实，
不拼接、不伪造父 Span。

## 缓存观察范围

Engine 在**每个调用**的 Span 上给出缓存事实：`dagger.io/cache.outcome` 是 `hit`、`executed`、`joined`
还是 `uncached`，`dagger.io/cache.hit.route` 说明命中路径。回执只记录**实际观察到的范围**
（`coverage.cache_outcomes`），不把单个节点的命中上升为整个 Check 的状态；准备节点命中与工具实际执行
必须分别记录。

## 导出产物

`engineering-ci dagger-telemetry` 写出一份只读回执（`kind = engineering-ci-dagger-telemetry`，
`evidence_class = candidate_record`），包含固定版本清单、两个导出 Scope 的真实配置、五个事实、
关联判定与观察范围。文件以二进制写入并固定 LF，保证记录的 SHA-256 与文件字节一致。

| 退出码 | 含义 |
| --- | --- |
| 0 | 回执已写出（`status` 为 `produced` 或 `partial`）；消费方必须读 `status`，退出 0 不代表 PASS |
| 2 | 参数、输入文件或本地环境错误 |
| 3 | 没有可用的观察产物（无捕获、无原生结果、无导出失败记录） |

## 未交付

- 跨 Run 的受信 `native_result_ref` / OriginAccepted 与 Required 接纳由
  [#63](https://github.com/dymzz/engineering-ci/issues/63) 决定。
- Dagger Span 不因存在就符合 `cicd.pipeline.*` SemConv；官方 Registry 映射不由本仓改写。
- 本仓不实现 Collector、不实现 exporter、不保存 Trace 原文；接收端与保留策略属接收方权限。

## 相关

- 权威需求：[Issue #87](https://github.com/dymzz/engineering-ci/issues/87)
- [验证记录](../verification/dagger-native-telemetry.md)
- [Dagger 集成记录](integrations/dagger.md) · [CI/CD 遥测](cicd-telemetry.md)
