# GitHub Receiver 配置（Issue #75）

本配置使用官方发布的 OpenTelemetry Collector Contrib 镜像运行 `githubreceiver`（Webhook 模式），并将输出规范化到本项目固定的 OpenTelemetry SemConv `v1.45.0` Registry（供 `#74` Weaver 与 `#52` 消费者使用）。

## 固定组件与版本

| 组件 | 固定来源 | 说明 |
| --- | --- | --- |
| Collector Contrib | `ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-contrib@sha256:e40d61f10c9a617cca1ffd1be4d7e2b3a4b0f0001798ff07e58cabe82e618cea`（`0.165.0`） | 官方发布镜像，不修改上游二进制与代码 |
| Weaver CLI | `v0.28.0`（见 [weaver_tool.py](../../src/engineering_ci/weaver_tool.py)） | 执行 OTLP `live`/`terminal` 校验，独立于 Collector 与 Registry 版本 |
| SemConv Registry | `v1.45.0`（见 [semconv_registry.py](../../src/engineering_ci/semconv_registry.py)） | 固定消费 `tests/fixtures/weaver-cicd/` 中验证摘要 |
| Webhook Receiver | `receivers.github.webhook` + `include_span_events: true` | 上游 `metadata.yaml` 声明 `sem_conv_version: 1.43.0`，默认开启 `receiver.githubreceiver.UseCheckRunID` |
| 规范化管线 | `processors.transform/semconv_v1_45_0` + `filter/drop_raw_webhook_events` | 将 `1.43.0`/自定义属性转换为 `SemConv v1.45.0` 合法属性；保持 `traceId`/`spanId`/`parentSpanId`/`links` 与原生时间戳不变 |
| Weaver 匹配配置 | [config/github-receiver.weaver.toml](../../config/github-receiver.weaver.toml) | 无硬编码任务名、无 `finding_filters`；固定验证 [SemConv v1.45.0](../../tests/fixtures/weaver-cicd/registry-sha256.json) |
| Collector 配置 | [config/otel-collector-github-receiver.yaml](../../config/otel-collector-github-receiver.yaml) | Webhook → `transform` → `filter` → `otlp_grpc` |

## 必填环境变量与安全约束

- `GITHUB_WEBHOOK_SECRET`：GitHub App / Repository Webhook Secret，用于验证 `X-Hub-Signature-256` HMAC-SHA256，禁止硬编码。
- `GITHUB_SCRAPER_ORG`：上游 `githubreceiver` 的 `Config.Validate()` 要求至少配置一个 `scrapers` 条目，Webhook-only 运行也需声明。
- `ECI_OTLP_GRPC_ENDPOINT`：目标 OTLP/gRPC 接收端地址。生产配置默认启用 TLS；仅允许在隔离回环验证中按需显式配置不安全本地端口。
- 生产部署应将 Webhook 服务置于 TLS 终止代理/WAF 后，并视需要启用 `required_headers`（代理注入校验头）。

## 确定性 Trace/Span ID 与语义边界

- `UseCheckRunID`（默认开启）：`trace_id = sha256("{run_id}{run_attempt}t")[:32]`、`root_span_id = sha256("{run_id}{run_attempt}s")[16:32]`、`job_span_id = sha256("{check_run_id}-j")[16:32]`、`queue_span_id = sha256("{check_run_id}-q")[16:32]`、`step_span_id = sha256("{check_run_id}-{step_name}-s")[16:32]`。
- 任务中 `workflow_job.id` 缺失或等于 `0` 会被 `UseCheckRunID` 拒绝；同一 Job 内同名 Step 会产生相同 `step_span_id`。
- 原始 `githubreceiver`（`0.165.0` 未经规范化）在固定 SemConv `v1.45.0` Weaver 下产生 `27` 条 `violation`（`type_mismatch`、`missing_attribute`、`required_attribute_not_present`、`illegal_namespace`）；经 [otel-collector-github-receiver.yaml](../../config/otel-collector-github-receiver.yaml) 规范化后可达到 `0` violation、且仅对已证明映射的 GitHub Conclusion 设置 `cicd.pipeline.result` / `cicd.pipeline.task.run.result`（未映射值如 `neutral` 仍由 Weaver 拒绝）。
- 部分重跑（`attempt > 1`）时，GitHub API 列表中带 `run_attempt=N` 的复用 Job 不一定在本 Attempt 新执行。[github_receiver.py](../../src/engineering_ci/github_receiver.py) 中的 `partition_witness_jobs()`、`build_weaver_expectations()` 与 `reconcile_github_receiver_trace()` 始终完整保留 `result_inventory` / `result_completed_jobs`：
  - 仅在受约束且已验证完整的 `executed_job_witness`（含 `source`、`run_id`、`attempt`、`complete=True`）或独立 `run_started_at`/`job.started_at` 时间界定时，才允许将未执行 Job 归为 `reused_jobs`；
  - 裸列表 `executed_job_ids`（及 `complete=False` 的局部事件集合）仅对列出 ID 提供正向执行证据，**绝不用“不在列表”推断 reused**——缺独立开始时间时未列出 Job 留在 `unknown_execution_jobs` 并触发 `execution_attribution_unknown`，不缩小 Expected 制造假 PASS；
  - 零执行 Task（包含全部 Job 均来自历史复用的纯复用 Attempt 2）在无错时输出 `task_coverage="zero_task_confirmed"` 与 `expected_matchers=["gh.pipeline"]`，同时完整保留 `result_completed_jobs`。
- **职责与适用范围（`#75` vs `#52A`/`#52B`/`#63`/`#61`）**：
  - `build_weaver_expectations()` 与 `reconcile_github_receiver_trace()` 面向包含官方 Pipeline Root Span 的 **`#52B` Root-dependent 对账**（缺少 Root 返回 `root_span_missing`）；不用于驳回合法的无 Root **Task-only（`#52A`）** 原生 Live 校验。
  - 本仓 `#75` 仅提供官方 Receiver 集成与共享 ID/映射/分类合同；`#52` 负责消费者独立 Expected/Observed 验收，`#63` 负责 Trusted 准入，`#61` 负责 Required 门禁。共享辅助函数本身不授予 `trusted` / `required_satisfied`。
- 本地 Docker / Collector 端到端测试仅证明 Collector Contrib `0.165.0` → SemConv `1.45.0` → Weaver `0.28.0` 在受控环境可用，不自动证明目标仓库生产 Webhook 投递、OTLP 远端接收与五项切换就绪标志对应的真实运行证据。
