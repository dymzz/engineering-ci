# Dagger 原生遥测验证

Issue [#87](https://github.com/dymzz/engineering-ci/issues/87) A 段。上级入口：[技术文档](../README.md)；
契约见[Dagger 原生 OpenTelemetry 导出与接收](../architecture/dagger-native-telemetry.md)。

## 回执逐条对照（验收基线）

本页是 **Issue #87 两份审阅回执的逐条对照**：每条要求 → 证据位置 → 状态。验收以这张表为准，
不以"完成了哪些工作"为准。

| # | 回执条目 | 证据位置 | 状态 |
| --- | --- | --- | --- |
| 1 | 列出固定版本实际提供的原生可关联数据 | 架构文档「固定版本原生能力」17 行；[Issue 评论](https://github.com/dymzz/engineering-ci/issues/87#issuecomment-6829679688) | 已交付 |
| 2 | 列出实际调用入口（`dagger check` / `dagger call execute-plan` / 单独 Report/Artifact Function） | 架构文档「原生调用入口」；CI 的实际捕获 | 已记录入口：Check PASS/FAIL、单独 Artifact Function 已实测；`execute-plan` **未实测** |
| 3 | 拟议最小交接九字段 + 逐项生产者/消费入口/存在条件/唯一性/原始出处/拒绝原因 | 架构文档「拟议最小交接」；[issue-revisions/issue-87.proposed.md](../issue-revisions/issue-87.proposed.md)；[Issue 评论](https://github.com/dymzz/engineering-ci/issues/87#issuecomment-6841856627) | 已交付 |
| 4 | 只有可核验的 Parent/Session + Caller 共同上下文才 `bound`，否则 `unbound`/`ambiguous`，保留原生结果与 Trace | `_capture_spans/_bind_capture`；`.github/scripts/assert_dagger_native_a1.py` | 修订中：新算法与 A.1 CI 机器断言已接线，需核对新 HEAD 的 CI 验收 |
| 5 | 不得把每次变化的随机 ID 注入可缓存的 Function/Exec 输入 | 架构文档「边界与已知限制」；实现只在回执里关联 | 已交付 |
| 6 | 定向验收：同 Capture 同名两次／多 Span／并发交错／Host 无 Dagger Span／失败 Result 关联 Trace | `tests/test_dagger_native_telemetry.py`、独立 A.1 原始 Capture 验收脚本 | 已修订错误测试期望，待 CI 确认；Tool Origin/历史 Cache 属 B，不冒充 A.1 完成 |
| 7 | 核查 CLI/SDK/Engine 实际版本 | 能力清单「版本事实」；证据 `dagger-version.txt` | 已交付 |
| 8 | OTLP 导出路径与协议 | 能力清单「Engine workload export」；`/etc/dagger/engine.json` + 标准 OTLP 环境变量 | 已交付 |
| 9 | CLI/Client/Engine 分别产生哪些 Span | 架构文档「Span 的生产者」（实测 scope 分布） | 已交付 |
| 10 | 生命周期结束与 flush 行为 | 能力清单 `engine_export_flush` / `sdk_export_flush`；[Issue 评论](https://github.com/dymzz/engineering-ci/issues/87#issuecomment-6842908582) | 已交付 |
| 11 | 能否拿到 Check Span 属性与 Cache 观察字段 | 能力清单：Check 属性 **partial（实测未发出）**、Cache 字段 **available（已观察）** | 已交付 |
| 12 | 把版本特定事实/不支持项/原生命令写入该 Issue | [Issue 评论](https://github.com/dymzz/engineering-ci/issues/87#issuecomment-6829679688) | 已交付 |
| 13 | 采集端区分 exporter-configured / receiver-observed / 有效 Check Span / 结果成功 | 回执 `claims` 的五个事实 | 已交付 |
| 14 | 没收到的 Span 不得猜造 | 拒绝路径 `no_capture` / `capture_empty` → `sent=unknown` | 已交付 |
| 15 | 只做有依据的 Cache Attribution，不另建命中检测器 | 实现只读取引擎原生 `dagger.io/cache.*`；架构文档与 #86 的边界行 | 已交付 |
| 16 | Trace 收到或 Check 通过 ≠ #63 受信 / #61 Required | 回执不含 trusted/required 断言；架构文档边界行 | 已交付 |
| 17 | OTel 失败只降低观察，不覆盖可独立证明的原生失败或成功 | CI 的真实失败 Check：`exit_code=1` 保留且仍 `bound` | 已交付 |
| 18 | 没有可证明的完整 Check/Exec 作用域时报告 `unknown`/`mixed` 与具体范围 | 回执 `cache_observation_scope`（D-06 词表 + `observed`/`scope`/`reason`） | 已交付 |
| 19 | 不为 Dagger Span 再埋 OTel；不改 SemConv YAML；不强行同一 Trace Root | 架构文档「边界与已知限制」 | 已交付 |
| 20 | `actual_invocation_ref` 与 `origin_execution_ref` 语义分离 | 回执 `invocation.actual_invocation_ref` 与 `origin_execution.*` | 已交付 |
| 21 | 仅从 Function 缓存状态不能产生新 Tool Origin；未证实时保持 unknown | `_origin_execution`、`resolve_origin_execution` | A.1 候选回执校准为 unknown；正式 Origin 仍属 B |
| 22 | Trace 绑定须说明证明的是本次调用还是原工具执行 | 回执 `binding.binding_proves` | 已交付 |
| 23 | 仅在实际取得原执行 Trace 时引用其原始来源 | `origin_execution.ref` 只在 `current_execution`/`existing_execution` 时有值 | 已交付 |
| 24 | 真运行后再次命中缓存的历史 Origin | 原生 beta.16 可见 Cache 属性与 B 的真实 Origin 交接 | A.1 未验收工具原执行复用；B 后续定向验证 |

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

## 当前 A.1 验收入口

新 HEAD 的专项 CI 不再只信 Recorder 的 `exit 0`：`.github/scripts/assert_dagger_native_a1.py`
独立读取实际 Receiver 的完整 PASS/FAIL/Artifact Capture、原生退出与回执，检查 Parent/Session、完整
Span 内容身份、回执证明范围及故意 F401 保留。原始失败工具退出 1 是预期负例；关联断言失败是专项 CI
失败。单次隔离 Capture 由 workflow 的独立 Receiver 生命周期声明（`--capture-isolated`）。
尚未核实新 HEAD CI 的运行结果前，不把旧版回执归因状态当作当前代码已验收。

## 正常路径

| 场景 | 输入 | 期望 |
| --- | --- | --- |
| 传播绑定 | 捕获 Span 携带传播的父 Span ID | `bound`、`basis=w3c_traceparent_propagation`、`status=produced`、退出 0 |
| Engine Check 属性绑定 | 捕获 Span 携带 `dagger.io/check.name` | `bound`、`basis=engine_check_attribute`（实测该属性未发出，见下） |
| Check 选择器绑定 | 同一隔离调用的 Selector→Core→Module 实际 Parent 链与 Session 一致 | `bound`、`basis=engine_check_selector`；只有同名 Selector 应 `unbound` |
| 独立 Artifact Function 绑定 | Caller 独立 Capture 中的 Core→Module 原生 Parent 链唯一 | `bound`、`basis=engine_function_call`；只有同名 Module Span 不成立 |
| Function 节点计算 | 捕获 Function Span 带 `cache.outcome=executed` | 仅 Function cache 观察；Tool Origin 未核验时 `unknown`、`new_tool_execution=null` |
| Function 命中缓存 | 捕获 Function Span 携带 `cache.outcome=hit` | Origin 未证实时 `unknown`、`new_tool_execution=null`；不新增工具执行事实 |
| 复用指向既有原执行 | 真实 Tool Exec/Cache Result 作用域与原执行来源可核验（B） | A.1 不以数字 `cache.result.id` 直接关联；仅有 Function 命中时 Origin 仍 unknown |
| 复用无既有原执行 | 只有 `hit` 一条回执 | `status=unknown`、`ref` 为空、`new_tool_execution=null`（未证明 Tool 层） |
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
