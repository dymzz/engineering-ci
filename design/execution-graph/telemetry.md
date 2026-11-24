# Native Dagger OTel、Trace/Root 与 CI/CD SemConv

**状态：目标集成契约；Dagger 原生 Tracing 已有，ECI 跨入口关联未统一交付。** [总览](README.md)

## 1. 两条独立原生链路
1. **Dagger Native Execution**：Engine/SDK 产生自身 Function、Container 与内部计算的 Traces/Spans；ECI 不重新生成或改写这些 Span。原生导出实际配置、协议、接收端和固定 `v1.0.0-beta.16` SDK 行为必须核实。
2. **CI/CD Telemetry**：#75 的目标主链路为 `GitHub Webhook → OpenTelemetry Collector Contrib githubreceiver → OTLP → SemConv v1.45.0 Weaver → #52`。既有 `engineering_ci.cicd_telemetry` 通过 GitHub Attempt/Job API 与 [PR #150](https://github.com/dymzz/engineering-ci/pull/150) `terminal_event` 提供过渡 Task/LogRecord 与独立 Witness，待官方 Receiver 与 #52 消费者迁移实测覆盖后退出重复 GitHub Trace Producer。[#152](https://github.com/dymzz/engineering-ci/issues/152) 单独记录一次显式 ECI 验证的宿主起止和原生结果引用。现有 GitHub Actions Job 与制品路径保持现状。Producer、OTLP、Receiver、Weaver、#63 Admission 分别证明其实际范围。

两条链路可以依据真实 Run、Check 和 Trace Context 关联，不能凭命令/时间猜父 Span。

## 2. 三个不同 Root / 生命周期
| 作用域 | 真实控制方 | 禁止的推断 |
|---|---|---|
| Dagger Invocation/Session | Dagger Engine | 不将其结束时间宣称 GitHub Workflow End |
| ECI Verification Session | ECI 真正控制的验证范围 | 不将局部 Check 的 Parent 伪称完整 Provider Root |
| CI Provider Pipeline Run/Attempt | 上游官方 `githubreceiver`（消费 `workflow_run` Webhook）或实际拥有完整边界的 Provider；GitHub Attempt API 只证明 Attempt/Job 事实 | 不将 ECI 自采样、REST 轮询 `updated_at`、最后 Job End、观察上界或虚构误差预算升级为可信 `time_source_verified=true` End |

三层语义必须严格区分：
1. **OTel CI/CD SemConv v1.45.0**：定义推荐 `SERVER` Pipeline Run Span 与 `INTERNAL` Task Span，未硬编码 GitHub 具体时间戳字段。
2. **OpenTelemetry 官方 `githubreceiver`**：在 `createRootSpan()` 中以 `workflow_run` Webhook 的 `run_started_at` 至 `updated_at` 生成 Pipeline Root（经 `correctActionTimestamps` 保序），以 `workflow_job` + `UseCheckRunID` 生成确定性 Job/Step Span，并链接上一 Attempt Trace。上游组件当前声明 `sem_conv_version: 1.43.0`，必须以真实 Webhook Capture 对本仓固定的 `1.45.0` Weaver Registry 实测核对，不得预设通过或改写官方 SemConv YAML。
3. **ECI 严格本地/API 时间边界与 #150 过渡基线**：采用官方 `githubreceiver` 时，不再继续扩建自研 ECI GitHub Root/Task Producer 来替代上游。在非 Webhook 的 ECI Attempt API 路径下，#150 `terminal_event` 保留真实 Attempt/Job/Task、观察 LogRecord 与独立 Witness（`root_emission=not_emitted`、`time_precision=unproven`）；`last_job_end`、任意后置观察时刻、普通 REST `updated_at` 或无来源 `--max-end-uncertainty-ns` 近似 Root 仍不得作为认证精确结束时刻，也不作为 #52B 长期合格来源。Dagger 原生 Span 仅代表自身 Engine/Function/Check 的实际执行范围。ECI 在同一执行主机于调用 `lefthook run verify` 前与原生命令返回时读时钟，时间只属于已声明的本地验证范围。具体实现与验收见 [ECI 验证计时](../../docs/architecture/cicd-telemetry.md)。

宿主计时写入独立的本地回执，`time_source_verified=false`。它包含 Lefthook 调度与命令开销。#87 原生 Span 和缓存属性照原范围解释。只有同一次原生调用已经存在可核验的 #87 绑定时才关联两份记录。当前 `verify` 无该绑定时保留独立记录。

## 3. Trace 交接（拟议）
- **Producer**：Dagger SDK/Engine 产出原生 Trace；官方 `githubreceiver` 产出 GitHub Actions Workflow/Job/Step Trace（基于 `run_id/run_attempt` 与 `workflow_job.id`/`check_run_id` 生成确定性 Trace/Span ID）；ECI Caller 产出 Invocation/Check/Source/Result 引用与必要独立 Witness。
- **Binding**：记录原生 `trace_id/span_id`（能观察到才有）、Check Invocation/Native Result 及实际捕获来源/内容身份。允许 1:N、N:1、缓存全命中无新扫描、纯历史复用无新 Dagger Span。
- **Propagation**：真实 W3C Trace Context 可传时形成父子关系；否则使用确有依据的 Span Link/Report Reference。不可为了追踪而将随机 ID/traceparent 普通注入可缓存的计算环境，造成缓存失效。
- **Cache Attribution**：Dagger OTel 中的 `dagger.io/dag.cached` 等字段必须对齐实际 Span/节点，不能用 Preparation Hit 宣称整个 Check 未执行。未知来源保持 unknown。
- **Transport Evidence**：区分 exporter configured、OTLP sent、receiver observed、signal accepted、semantic check passed、trusted admitted；不以任一布尔覆盖其他环节。适用信号 Traces/Logs/Metrics 按声明单独核验。
- **Failure Semantics**：Trace 不可用时显式记录缺口；不能伪造 Span，也不能无依据撤回已经由独立原生产物证明的 Check 事实。需要 Trace 的 Required 属性则不能假通过。

## 4. CI/CD SemConv 与执行身份
Dagger Function Span 并非自动符合 `cicd.pipeline.run.server` 等 CI/CD Semantic Conventions。只有实际生命周期和被检查语义符合官方固定 Registry，才能映射 Pipeline/Task。官方 SemConv 来源不能为匹配 ECI 本地 Checker 或上游 `githubreceiver` 差异而改写。

#52 Expected / Terminal / Reconciliation 与 #52A/#52B 消费者须向官方 `githubreceiver` 确定性 Trace/Span ID、Attempt 边界、全量 Job/Retry 及零 Job 行为对齐，而不是要求上游适配 ECI Schema 9 或 `_stable_ids`。完整 Task 集合须从独立 Run/Attempt/Job 来源判定；不能从被检 Capture 倒推 Expected。后置 B 不能阻断产生自身 Terminal 事实的运行生命周期，也不能反向成为自身完成前的 Required 条件。

## 5. 定向验收
- 真实（或签名本地回放）`workflow_run`/`workflow_job` Webhook 经官方 `githubreceiver` → OTLP → 固定 SemConv v1.45.0 Weaver，按实际输出暴露并核对 1.43.0 vs 1.45.0 语义差异，不预设 PASS。
- #52 消费者完成官方 `githubreceiver` Trace/Span ID、成功/失败/取消/零 Job/全量与部分 Rerun 实测覆盖后再退出重复 ECI GitHub Trace Producer，保留 #150 `terminal_event` 独立 Witness 能力或显式收敛。
- 实际 Dagger Check 在固定 OTel 接收端产生 Trace，可与 Check/Result 关联；失败 Check 同样保留原生结果。
- Preparation Cache Hit + Tool 实际运行，报告 mixed 而不是完整 Check Hit；Cache 全命中不产生独立原生运行事实。
- 相同 Invocation/Result 关联真实 Dagger Trace；不以未证明的时间/命令猜 ID。
- GitHub Attempt Task-only 有独立 Expected；PR #150 `terminal_event` 无自建 Root 时不伪造近似 Root；后置流程允许独立验收且不循环等待。
- Traces 已接收但 Logs 失败时分别记录，不能宣称全部 Signal 成功。

[现有 #75 Producer](../../docs/architecture/cicd-telemetry.md) · [#52](https://github.com/dymzz/engineering-ci/issues/52) · [#74](https://github.com/dymzz/engineering-ci/issues/74) · [#75](https://github.com/dymzz/engineering-ci/issues/75)