# Dagger 原生 Trace 捕获证据（#87 A.1）

Issue [#87](https://github.com/dymzz/engineering-ci/issues/87) A 段的真实捕获：原生
`dagger check` → Engine 原生导出 → 接收端实收 → 观察回执。**两次真实 Check**：一次通过，
一次用真实的 lint 失败。

**这是旧 CI Run 的候选观察事实归档**，不是 #63 受信接纳、#61 Required 聚合，也不是
CI/CD SemConv Pipeline/Task 映射。此处的旧 `bound` / `current_execution` 是**历史 Recorder 输出**，
不能当作新版本严格 Parent/Session 绑定或 Tool Origin 的独立验收。
当前 PR #89 的 A.1 验收以 [现行专用 CI 断言](../../../.github/scripts/assert_dagger_native_a1.py)
在**同一次新 Run 的原始 Capture/Native Result/Receipt** 上的结果为准，旧 Run 不重新记账。

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
| 旧 Recorder 原执行声明 | 旧回执写为 `current_execution`、`new_tool_execution=true`，**仅 Function 缓存事实实际观察到 executed，不能证明 Tool Origin** |
| 缓存观察范围 | 三次都是 `observed_execution`，范围是绑定到的调用 Span |
| Source 声明与 Dagger 摘要 | Check 入口两次声明相同 `HEAD^{tree}`，但失败那次注入额外受检文件，`observed_dag_digests` 不同；**Git Target Tree 与实际执行 Source 并未完成字节对账** |
| 覆盖 | Check 入口实收 2494 个 Span；Function 入口实收 201 个 Span、绑定 2 个 |

**失败那次的要点**：原生 lint 确实失败（`exit_code=1`），原始 Capture 确实包含相应 Selector/Function
Span。旧回执记录 `binding_status=bound`，但该判定**当时只检查名称，未验收真正 Parent/Session 唯一性**。
原生失败没有被观测回执改写；新算法必须对原始 Parent 链独立证明后才能给出新的 `bound`。

## 观察到的原生 Check 链路

`capture-check-spans.json` 与 `capture-check-spans-negative.json` 是两次捕获里 **Check 链路的 4 条
Span 原文**（未改写，仅按 Span 名筛出）：

| Scope | Span 名 | 关键属性 |
| --- | --- | --- |
| `dagger.io/util/parallel` | `dag://?check=lint` | CLI 的 Check 选择器 |
| `dagger.io/dagql` | `Check.sync` | Check 同步调用 |
| `dagger.io/dagql` | `engineering-ci:EngineeringCi.lint` | `dagger.io/dag.digest` |
| `dagger.io/core` | `EngineeringCi.lint` | `dagger.io/cache.contract=1`、`dagger.io/cache.outcome=executed`、`dagger.io/cache.result.id` |

**实际原生 Parent 链（按完整 Capture 的 `traceId/spanId/parentSpanId` 核对）：**
`dag://?check=lint` → `Artifact.value` → `EngineeringCi.lint` →
`engineering-ci:EngineeringCi.lint`。上表的 `Check.sync` 属于 Selector 下另一分支，
不是 Module/Core Function 的线性祖先。按名称筛选的四条原文不是完整父链，不可据该摘录
自行推断 Parent。

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

## 本次实施中发生过的失败

每条：现象 → 根因 → 修复 → 现在的状态。写在这里是为了不把失败藏在对话里。

| # | 现象 | 根因 | 修复 | 现在的状态 |
| --- | --- | --- | --- | --- |
| 1 | CI：Engine 起不来，CLI 干等 10 分钟（`start engine: new client: context deadline exceeded`） | 只给了 traces 端点；beta.16 的 workload export 要求**每个信号都有自己的端点**，缺 metrics 端点时 `cmd/engine/main.go` 直接返回错误、Engine 退出 | 补 `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`；加 Engine 就绪探针（20s 超时并打印 engine log），失败从 10 分钟降到 2 分钟 | 已修复；该 Job 自此持续绿 |
| 2 | CI：第二次捕获起不来（`OSError: [Errno 98] Address already in use`） | 两次捕获复用同一端口，却没有端口交接协议 | 接收端加 `POST /stop` 干净关闭与绑定重试（最多 60s）；CI 停干净并等端口释放再起下一个 | 已修复 |
| 3 | CI：4 个 Job 全在 2–3 秒内失败、无任何步骤输出 | **不是代码问题**：账号的 Actions 额度耗尽（annotation：`recent account payments have failed or your spending limit needs to be increased`） | 仓库侧无解；PR 正文如实标注该状态，不保留"全绿"的旧表述 | 额度恢复后已绿 |
| 4 | 本地 `test_manual_release.py::test_workflow_triggers_and_privileges` 失败 | 加的 `dagger call execute-plan` 违反仓库守卫：PR Workflow 里不得用 ad-hoc 函数调用 | 移除该步骤；Plan 入口改为**按仓库策略不在 PR CI 里跑**并写进文档；Function 入口改用 `dagger api call` | 已修复，本地绿 |
| 5 | 能力清单把 `dagger.io/check.name` / `check.passed` 写成 available | 只读了源码（`core/checks.go`），没等真实捕获 | 能力事实分三档：`source-present` / `observed-at-runtime` / `unknown`；实测 2494 个 Span 里一次未发出 → 降为 `partial` | 已修正，文档与 Issue 评论同步 |
| 6 | 独立 Function 入口的回执 `unbound`（`check_name_not_observed`） | 假设引擎用下划线命名函数调用（`repository_ruff_tools`）；**实测**引擎用驼峰（`engineering-ci:EngineeringCi.repositoryRuffTools`），CLI 用连字符 | 声明名与 Span 名改为**去分隔符 + 忽略大小写**比较；能力清单新增"函数调用名的形态"一行记录这个实测事实 | 已修复；用同一份真实捕获重跑回执 → `bound` / `engine_function_call` / `source_associated=true` |
| 7 | `git add -A` 把草稿文件扫进仓库 | 提交前没看暂存清单 | 提交前核对 `git status`，只 add 具体文件 | 已修正 |

## 复现

```text
uv run --locked --group dev pytest tests/test_dagger_native_telemetry.py -q
```

真实捕获只能由 CI 产生（本机没有可用的 Dagger Engine 会话）；步骤见
[.github/workflows/ci.yml](../../../.github/workflows/ci.yml) 的 `dagger-native-telemetry` Job。
