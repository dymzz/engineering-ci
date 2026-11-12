# Native Dagger OTel、Trace/Root 与 CI/CD SemConv

**状态：目标集成契约；Dagger 原生 Tracing 已有，ECI 跨入口关联未统一交付。** [总览](README.md)

## 1. 两条独立原生链路
1. **Dagger Native Execution**：Engine/SDK 产生自身 Function、Container 与内部计算的 Traces/Spans；ECI 不重新生成或改写这些 Span。原生导出实际配置、协议、接收端和固定 `v1.0.0-beta.16` SDK 行为必须核实。
2. **GitHub Provider CI/CD Telemetry**：#75 的 Run/Attempt/Job 原生 API 数据可形成候选 Pipeline/Task 记录；由 #74 的 Weaver 消费。此 Producer **不 instrument Dagger Functions**；GitHub Candidate、OTLP Transport、Receiver Observed、Weaver PASS、#63 Trusted 是独立事实。

两条链路可以依据真实 Run、Check 和 Trace Context 关联，不能凭命令/时间猜父 Span。

## 2. 三个不同 Root / 生命周期
| 作用域 | 真实控制方 | 禁止的推断 |
|---|---|---|
| Dagger Invocation/Session | Dagger Engine | 不将其结束时间宣称 GitHub Workflow End |
| ECI Verification Session | ECI 真正控制的验证范围 | 不将局部 Check 的 Parent 伪称完整 Provider Root |
| CI Provider Workflow Attempt | GitHub/相应 Provider | 不用最后 Job End 或观察上界伪称精确结束时刻 |

GitHub `workflow_run.completed` 后置观察取得的 `last_job_end <= run_end <= observation` 只是有条件的时间区间，不是硬编码 17～19 秒，不是确证的精确 Root。没有可信 Root 时 Task-only 语义仍可独立验收，Terminal Root 覆盖不能伪造成功。

## 3. Trace 交接（拟议）
- **Producer**：Dagger SDK/Engine 产出原生 Trace；ECI Caller 产出 Invocation/Check/Source/Result 引用；Provider 产出真实 Run/Attempt/Job 身份。
- **Binding**：记录原生 `trace_id/span_id`（能观察到才有）、Check Invocation/Native Result 及实际捕获来源/内容身份。允许 1:N、N:1、缓存全命中无新扫描、纯历史复用无新 Dagger Span。
- **Propagation**：真实 W3C Trace Context 可传时形成父子关系；否则使用确有依据的 Span Link/Report Reference。不可为了追踪而将随机 ID/traceparent 普通注入可缓存的计算环境，造成缓存失效。
- **Cache Attribution**：Dagger OTel 中的 `dagger.io/dag.cached` 等字段必须对齐实际 Span/节点，不能用 Preparation Hit 宣称整个 Check 未执行。未知来源保持 unknown。
- **Transport Evidence**：区分 exporter configured、OTLP sent、receiver observed、signal accepted、semantic check passed、trusted admitted；不以任一布尔覆盖其他环节。适用信号 Traces/Logs/Metrics 按声明单独核验。
- **Failure Semantics**：Trace 不可用时显式记录缺口；不能伪造 Span，也不能无依据撤回已经由独立原生产物证明的 Check 事实。需要 Trace 的 Required 属性则不能假通过。

## 4. CI/CD SemConv 与执行身份
Dagger Function Span 并非自动符合 `cicd.pipeline.run.server` 等 CI/CD Semantic Conventions。只有实际生命周期和被检查语义符合官方固定 Registry，才能映射 Pipeline/Task。官方 SemConv 来源不能为匹配 ECI 本地 Checker 改写。

#52 Task-only A 与后置 Root/Terminal B 分别保留覆盖、Matcher、Source Closure、时间来源和原生 Gate。完整 Task 集合须从独立 Run/Attempt/Job 来源判定；不能从被检 Capture 倒推 Expected。后置 B 不能阻断产生自身 Terminal 事实的原 Workflow。

## 5. 定向验收
- 实际 Dagger Check 在固定 OTel 接收端产生 Trace，可与 Check/Result 关联；失败 Check 同样保留原生结果。
- Preparation Cache Hit + Tool 实际运行，报告 mixed 而不是完整 Check Hit；Cache 全命中不产生独立原生运行事实。
- 相同 Invocation/Result 关联真实 Dagger Trace；不以未证明的时间/命令猜 ID。
- GitHub Attempt Task-only 有独立 Expected；无 Root 时不伪造 Terminal；本次后置流程允许独立验收且不循环等待。
- Traces 已接收但 Logs 失败时分别记录，不能宣称全部 Signal 成功。

[现有 #75 Producer](../../docs/architecture/cicd-telemetry.md) · [#52](https://github.com/dymzz/engineering-ci/issues/52) · [#74](https://github.com/dymzz/engineering-ci/issues/74) · [#75](https://github.com/dymzz/engineering-ci/issues/75)