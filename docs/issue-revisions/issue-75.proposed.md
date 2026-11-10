## 目标

落实 [Knowledge PR #79 §3、§5.1–§5.7、§7 O2/O3](https://github.com/dymzz/engineering-knowledge/pull/79)（已合并；权威正文见 [`opentelemetry-weaver-delivery.md`](https://github.com/dymzz/engineering-knowledge/blob/main/design/engineering-verification/opentelemetry-weaver-delivery.md)），让 Engineering CI 在**真实 GitHub Workflow Run / Attempt / Job** 上生产可追溯的 OTel CI/CD Pipeline/Task Spans、必要 Resource/Log/Metric 与终态结果，并通过实际受控出口交接。Dagger 原生 Trace 继续由 Dagger 负责，不重复 Dagger Function 埋点。

该任务与 [ECI #74 — 独立 Weaver Checker](https://github.com/dymzz/engineering-ci/issues/74) **并行**：先以标准 OTLP + 真实可接收的中立 Collector/Receiver 或可验证原生 Artifact 验证生产链，不把 Weaver v0.28 的监听/Matcher 实现当成生产者开发前置。跨组件联调由 [#52](https://github.com/dymzz/engineering-ci/issues/52) 在双方可验证输入就绪后承接，不作为本 Issue 启动条件。

## 当前实现基线（2026-11-10）

- **尚无 ECI CI/CD Pipeline/Task 生产者：** `eci_core` / 公共 CLI **没有** GitHub Run/Attempt/Job → OTel Pipeline/Task Span 的生产路径。不得把「文档已写」或「Dagger SDK 已有 OTel」当成生产者已交付。
- **不得扩展为 ECI 生产者：** `.dagger/sdk` 内的 OpenTelemetry 配置属于 **Dagger Engine/SDK 自有 Trace**。本 Issue 禁止为 ECI 业务在 Dagger Function 上重复埋点，也禁止替换或关闭既有 Dagger 观测出口。
- **与 #66 无关：** Hygiene/Tach/Selection/Git Hooks（含 Draft PR #72）不在本 Issue 范围；不阻塞、不重开 #66 已交付切片。
- **与 #74 的边界：** #74 用固定样本证明 Weaver Checker；#75 提供真实 Run/Attempt/Job 样本。合成 Span 只可用于检查器自测，不能代替本 Issue 的真实生产者验收。

## 生产者设计与实现

1. **生产者选择（须写入实施 PR）：** 默认采用 **GitHub Actions 运行时事实 +（必要时）GitHub Run/Job API** 的 ECI 事件适配器，从 `GITHUB_*` / API 取得 Run ID、**Attempt**、Job ID/URL、Conclusion、Commit/Tree（可得时）与可确认时间戳，映射为 Pipeline Run / Task Run Span。若改用 Workflow 内嵌 instrumentation 或其他原生 SDK，须在同一 PR 写明谁生产何种 Span/Resource，并证明不伪造缺失字段。
2. 对自由命名的 Span 提供**与官方固定 SemConv 版本相符的原生属性和 Group 依据**（版本候选与 Knowledge #80/#81 对齐；未固定前可用已核实官方属性名，但须标明「Registry 身份待 #80 固定」）。按选定 Signal 范围生产 Pipeline/Task/必要 Log/Metrics。未提供的阶段/URL/Metric **不伪造**；不能把 ECI Selection/Required 的业务状态机械编码为 OTel `cicd.pipeline.result`。
3. 选择现实网络拓扑及 Exporter（优先 **OTLP/gRPC**，因 Weaver live-check 默认真接收端；若只能 HTTP 则记录 Collector 转换）。**保留**原有 Dagger/观测出口。可先交付可验证的 **OTLP JSON / 证据 Artifact**，再接线真实 Endpoint；不可假设原始 OTLP JSON 直接是 Weaver sample JSON。
4. 处理生命周期：Job/Task 已完成时可 Flush；**Pipeline Root Span 终态**须由真正终态确认后 End+Flush。同一尚未结束的 Workflow **不得**自我验收最终 Root；终态后置消费者与 #63/#61 交接时只引用已完成 Run 的证据。
5. 确定高基数 Run Resource、Trace Context 传播、无法传播时的原生 Run/Artifact 关联、采样、敏感信息与诊断出口边界。Token、源码、私密参数不得作为 Span 属性默认导出。

## 独立验收

- [ ] **一个真实 GitHub Workflow/Attempt/Job** 的受信 Run/Job 字段 → 实际 Span/LogRecord → 已完成 Flush → OTLP Receiver/Artifact 的证据，不以合成 Span 替代真实生产者。
- [ ] 至少一次受控传输/Flush 故障或**缺失终态/Attempt/Job 身份**的真实/定向负例，不能被报成已经生成完整 Pipeline Span；Dagger 自身 Trace 不因接线丢失。
- [ ] 明确选定官方语义版本与真实输入身份；在 Checker 尚未交付时只报告「OTLP 已生产/可达」，**不宣称 Weaver 符合性 PASS**。
- [ ] 向 #74 提供真实样本/Source/Run/Attempt 交接契约；向 #63/#61 提供可验证的证据引用，但**生产者完成不依赖最终 Required 已启用**，也不依赖 #74 先合并。

## 不在本 Issue

不开发 Weaver Check/Matcher/Registry（#74）；不负责 Knowledge F0/#24/#80/#81 内容；不改通用 Hygiene/Tach、Git Hooks PR #72/#78；不自建 OTel 常驻平台；不涉及 Vesryn。最终两端联调按实际新 Check/Run 接入 ECI 现有 Report（#52），不能重跑无关全 CI。

## 并行实施与总控索引（2026-11-10）

[Knowledge PR #79](https://github.com/dymzz/engineering-knowledge/pull/79) 已合并、为统一 OTel/Weaver 设计权威；[Knowledge PR #82](https://github.com/dymzz/engineering-knowledge/pull/82) 提供四条既有专题任务的并行分发。本 Issue 可独立在相同已核实的上游 SemConv 固定来源身份上开发与交付；不等待其他专题 Issue 整体关闭。只有声称跨仓端到端通过时才核对其实际源字节、接口、生产者、检查结果。本 Issue 的验收只依赖自身真实原生输入/负例及对应输出，不用总控 PR 文档合并或其他任务状态代替。
