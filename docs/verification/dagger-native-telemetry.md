# Dagger 原生 OTel Telemetry 验证记录（Issue #87-A / A.1）

本文档为 [Issue #87](https://github.com/dymzz/engineering-ci/issues/87) 阶段 **A / A.1** 的长期验证索引，记录固定 Dagger Engine 版本的实测原生 OTLP 捕获证据、A.1 独立门禁摘要、原始产物 SHA-256 与保留/访问限制。

## 固定版本与原生能力边界

- **Engine / CLI 固定版本**：`v1.0.0-beta.16`（来自 `dagger-module.toml` `engineVersion`）
- **Workload Export 配置**：`/etc/dagger/engine.json` 写入 `{"telemetry":{"workloadExport":true}}`，且必须同时提供 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 与 `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`（缺任一会导致 `NewWorkloadExport` 启动失败）。
- **实测 Parent 图方向（beta.16）**：`dagger.io/util/parallel` (`dag://?check=<name>`) -> `dagger.io/dagql` (`Check.sync` 等) -> `dagger.io/core` (`EngineeringCi.<camelFn>`) -> `dagger.io/dagql` (`engineering-ci:EngineeringCi.<camelFn>`)；独立 Artifact Function 无 `dag://?check=` 祖先，且 `File.export` 位于同一 `POST /query` 下，其子 Span `resume file` 通过 OTel Span `links`（`dagger.io/link.purpose="cause"`）指向目标 Function 的 `Core` Span / 子树（`export_attribution_status="attributed"`、`export_attribution_basis="span_link"`）。仅同 Trace/Session/Engine 下未关联到所选 Function 的 `File.export` 会保留 `export_span_observed=true`，但归因状态保持 `unknown`。
- **`traceparent` 传播分层能力**：`BETA16_CAPABILITIES.traceparent_propagation` 将能力拆分为三层：`correlator_parsing_and_matching="supported"`（关联器解析与 lineage 匹配）、`sdk_client_injection_transport="supported_upstream_not_exercised_in_a1_ci"`（上游 SDK 传输层能力）、`engine_end_to_end_parent_propagation="unverified"`（A.1 原生 CI 在隔离 Engine 且未注入 `TRACEPARENT` 下运行，故固定版本端到端真实父 Span 传播尚无实盘归档证据）。
- **已知部分支持项**：`dagger.io/check.name`、`dagger.io/check.passed` 为 `partial`（实测通常不发出），因此不允许用缺失属性代替父图关联；`dagger.io/cache.outcome` 仅在 Function 级发出时，`node_level="function_only"`，`new_tool_execution` 保持 `null`，`origin_execution.status` 保持 `unknown`。
- **CI 调度策略**：`dagger-native-telemetry / a1-gate` 在 `src/engineering_ci/{dagger_telemetry,cicd_telemetry,cli}.py`、`tests/test_dagger_native_telemetry.py`、`dagger.json`、`dagger-module.toml`、`pyproject.toml`、`uv.lock`、`.dagger/`、`.github/workflows/ci.yml` 变更或显式触发（`[reverify-a1]` / `reverify_a1_capture` / 版本文档不匹配 / 缺少基准提交）时执行三次隔离 Engine 实跑捕获；无关变更仅运行离线受控 Capture 合同回归测试（`pytest tests/test_dagger_native_telemetry.py`），不重复启动隔离 Engine，也不对过期或版本变更后的旧 Capture 假称有效。

## 证据层级说明（解码后 OTLP JSON vs 原始 HTTP 请求字节）

- 下表中 `*-capture.json` 的文件级 SHA-256 均对应 **临时 HTTP Receiver 将收到的 OTLP 请求解码并规范化为小写 hex ID 后保存的 JSON 快照（`capture_format="decoded_normalized_otlp_json"`、`payload_representation="decoded_normalized_otlp_json"`）**，**不等同于**当时回环网络请求中未经解析的原始二进制 HTTP `ExportTraceServiceRequest` Protobuf Payload。
- Receiver 会在每个已保存 `trace_batches[i]` 条目上同时记录入站解压后 HTTP 请求体的 `raw_payload_sha256`、`raw_payload_bytes` 与 `content_encoding`，在无需自建永久 Telemetry Store 的前提下准确区分“原始 HTTP OTLP 请求字节摘要”与“解码/规范化后 JSON 文件摘要”。

## 已归档实跑证据索引（PR #97 / Run `43544495531` 与 Run `43547766091`）

- **初版规范化基线**：Workflow Run [#43544495531](https://github.com/dymzz/engineering-ci/actions/runs/43544495531)（Attempt `1` / Job ID `132292488841` / HEAD `5e57d3d5d9c44bbfe4fc7bb6113d9df3dbae6a49` / Artifact ID `14093208337`）
- **最新实跑复验**：Workflow Run [#43547766091](https://github.com/dymzz/engineering-ci/actions/runs/43547766091)（Job `dagger-native-telemetry / a1-gate` / Job ID `132302956261` / HEAD `7ccd0ddeeb72f4e0bcd6451a7f96ecd94d7e7151`）
- **保留与访问限制**：GitHub Actions Artifact 设置 `retention-days: 7`，仅保留 7 天，且需具备仓库读取权限的 GitHub 认证方可下载；仓库内不长期提交完整 OTLP 原始抓包 JSON（遵循 `AGENTS.md` 不另建持久化 Telemetry Store）。

### 规范化捕获快照与原生产物 SHA-256 摘要（Run `43544495531` / Artifact `14093208337`）

| 文件 | 大小 (Bytes) | SHA-256 | 证据层级与说明 |
| --- | --- | --- | --- |
| `pass-capture.json` | 3737075 | `e45be1a0557674fdabfdadc7628e19ec06ccd94077eec8b94675bceae57d339f` | Receiver 解码/规范化后 OTLP JSON 快照（隔离 PASS `lint`，非原始 HTTP Protobuf 字节） |
| `pass-receipt.json` | 2223 | `70875cc29004b59d08a456e339db4cc31448cb03c2ff61a82b577e44947fd1e0` | PASS `lint` 关联回执 |
| `pass.exit` | 2 | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | 原生 `dagger check --check=lint` 退出码 `0` |
| `fail-capture.json` | 2964602 | `deb095403acafe5ef090954ab09f3eda55bc41f8d2b9173c77c6fc70a810b611` | Receiver 解码/规范化后 OTLP JSON 快照（隔离 FAIL Ruff `F401`，非原始 HTTP Protobuf 字节） |
| `fail-receipt.json` | 2223 | `495fb1750aa5374d7597550091326440cdee2b8c515e7717cd46e8d6085d5b9d` | FAIL `lint` 关联回执（保留 `failed` 结果） |
| `fail.exit` | 2 | `4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865` | 原生 `dagger check --check=lint` 非零退出码 |
| `artifact-capture.json` | 365911 | `eadc6e94aabe365e3303ca5d09fa2a0a3982a51f357d151870987a0d096d60c9` | Receiver 解码/规范化后 OTLP JSON 快照（隔离 `repository-ruff-tools` 导出调用，非原始 HTTP Protobuf 字节） |
| `artifact-receipt.json` | 2250 | `6c5f32bda2c88ded72084fd3480ab4486764ff268b966210e906e7423bb4de2f` | Artifact Function 关联回执 |
| `artifact.exit` | 2 | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | 原生 `dagger api call repository-ruff-tools export` 退出码 `0` |
| `a1-verification.json` | 648 | `cf5e7df345e534e89af5aadbe6513e9bc7c4d4adffcb17ae7997831f88853147` | Run `43544495531` A.1 门禁报告 |

## 已验证范围与未完成边界

- **本阶段已验证（#87-A / A.1）**：
  - 每 Span 独立 Resource Session/Engine 归属、真实父图与精确 `dagger.io/dagql`/`dagger.io/core` Scope 验证；仅凭 `expected_session_id` 在非隔离捕获中不得升格为 Invocation 级 `bound`。
  - `verify-a1` 针对真实 Capture 重新运行统一 `correlate_capture()` 并对账全部身份字段、原生 exit/F401 诊断、导出产物 `SHA256SUMS` 与按 Span Link / 子树归因的 `File.export` Span（同 Trace 其他函数的未归因 Export 不得冒充归因）。
  - 全部定向正反例与并发 OTLP/HTTP（`protobuf`/`json`/`gzip`）原子落盘（含入站 HTTP 请求体 `raw_payload_sha256` 与 `decoded_normalized_otlp_json` 分层标注）。
- **显式未声称 / 保持 Open（Issue #87）**：
  - `source_binding_status` 保持 `unknown`（Git 源码绑定留待 `#66` 物化回执）。
  - `origin_execution.status` 在 A.1 保持 `unknown`（跨 Run Tool Origin 等价与 `#52/#86` 回溯留待 `#87-B / B.2`）。
  - `#87-B`（`execute-plan` 捕获）、`#87-B.1`、`#87-B.2` 与 `#87-C` 均尚未实施，Issue `#87` 继续保持开启。
