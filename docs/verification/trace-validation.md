# 原始轨迹阶段验收

本记录对应 v0.6.0 功能分支的阶段验收，不代表正式发布或自动优化闭环已经完成。
基线为 v0.5.4，提交 f1d7d31014417468fa7299be27a86f0a57b47521。

## 实际来源与数据库

2026-10-12 在主 checkout D:\workspace\engineering-ci 验证。
用户授权当前 Codex 会话的原生 rollout JSONL；生产者版本为 0.162.0-alpha.2，
来源为桌面运行时，不能冒充 `codex exec --json` 的公共事件格式。
适配器实际检查了 106072215 字节的固定前缀，格式错误 0，最大记录 1379804 字节，
未显示 prompt、工具参数、输出或推理内容。来源正在增长，后续捕获的成员集可以不同。
没有协议提供的全局序列或预期事件总数，完整性保持 unknown。

宿主 PostgreSQL 为 17.9，数据库 vesryn；只读预检通过，fsync=on、synchronous_commit=on，
初始 engineering_ci_trace schema 不存在，当前用户具备创建权限。
运行环境为用户授权的普通 CPython 3.14.3。未修改数据库服务、账号或其他应用 schema。
脱敏运行结果保存在忽略目录 .test-tools/trace-implementation，不提交原始记录或凭据。

实际初始化专用 schema 后，已归档同一会话的固定前缀：106285994 字节、20604 条记录、207 批。
快照 `4add04f0a96013dd0aacfe2ddb5e72aa40b07e223af274c72e3b71fe7909b237`，
原始 SHA256 `bb53c649b45f0583c4f81d0d7cd229ba167388d71841faa5a8663d38f21fe5dc`，
完整性 unknown。归档确认不表示该会话中的业务操作都成功。
归档读回与源文件固定前缀的字节数、记录数和 SHA256 完全一致。
依据后续用户要求，当前 inspect 只采样开头/末尾各 32 KiB；以上全量统计是已执行的历史结果，
不把边界采样结果冒充全量核查，其余 Codex 原生输出默认可信。

## 自动化验证

```powershell
uv run --locked --group dev pytest -q tests/test_trace_preflight.py tests/test_trace_source.py tests/test_trace_store.py tests/test_unified_cli.py
```

167 passed。包含预检错误分类、来源格式区分、未知记录和原始字节保真、追加/截断/改写、
稳定事件身份、尾片段恢复、SDK CloudEvents 二进制往返、输出安全及回放失败不发布半文件。
Ruff 使用既有固定 0.16.10、isolated、py311 参数，涉及文件检查通过。
驱动错误测试使用替身；SDK 序列化测试实际调用 CloudEvents。

```powershell
uv run --locked --extra trace --group dev pytest -q tests/integration/trace_store.py
```

初次 11 passed；补充批次边界及外部组件身份拒绝后，最终 13 passed，实际连接宿主 PostgreSQL。
每项测试使用唯一 engineering_ci_trace_test_* schema，
清理前核对组件归属，只删除该测试 namespace。
覆盖真实 DDL 重复调用、事务回滚、字节回放、幂等导入、身份冲突拒绝、
截断尾行后完成、固定快照、数据库内容损坏、游标竞争、双进程导入，以及提交前后强杀进程。
“提交响应丢失”是实际提交后注入客户端错误，并验证下一次从已提交游标恢复；
不是实际断网或 PostgreSQL 代理故障验收。

该集成文件不由普通 pytest 自动收集，显式运行时缺失数据库会失败，不静默 skip。
GitHub 核心 CI 无个人宿主连接，单元测试通过不能替代数据库集成结果。

## 标准化与真实非 LLM 分析

OTel SDK 1.45.1（语义约定 0.66b1）从同一固定快照生成 3017 个 Span，
全部使用原生 item_completed 的开始/结束时间。退出结果：failed 255、passed 2759、unknown 3。
未根据终端文本推测状态；外层 response_item 工具包装未再次计数，17454 条非目标记录保持归档，
不声称已经获得所有模型调用、token、美元成本或业务检查身份。

显式 OTel/PM4Py 算法及准入测试当次为 28 passed（包含 5 项实际 SDK/算法验收，其他为单元测试）。
PM4Py 2.7.23.9 在普通 Windows CPython 3.14.3 安装并实际运行，
样本分析得到 6 个入选 Turn、6 个变体、8 条 DFG 边；56 个 Turn 未入选，
其中 54 个因重叠或时间并列，2 个因未关闭/缺少边界。结果 llm_used=false、completeness=unknown。
输入与输出位于忽略目录，不调用付费模型，不执行候选，不发送在线遥测。

## 整体本地回归

统一 `engineering-ci checks pr --reuse --json ...` 在 Dagger Linux 完整通过：
971 tests、105 subtests，未将可选 PostgreSQL/OTel/PM4Py 集成冒充核心测试。
Ruff Lint、Ruff Format、四处 v0.6.0 元数据/Workflow 绑定、锁文件均通过。
宿主显式套件最终为 18 passed：13 项真实 PostgreSQL、2 项 OTel SDK、3 项 PM4Py 算法。
本地 wheel 构建通过，含 SQL 迁移，不打包原始轨迹、PM4Py 或其他上游库源码。
文档相对文件链接核对通过。

## 未完成范围

Linux 核心容器回归已通过；Linux 可选 OTel/PM4Py 专项、真实 PR CI 和正式 v0.6.0 Release 尚未验收。
服务器掉电、备份恢复、真实提交响应丢失、公开稳定 rollout 协议兼容性未验证。
非 LLM 分析与原始归档的整体完成状态继续以[实施步骤](../architecture/workflow-optimization/design.md#实施顺序与阶段验收)为准。

## v0.5.5 查询、关联与 Rust4PM 联合验收

2026-10-12 在同一主 checkout、分支 codex/trace-evidence-query-v0.5.5 继续开发。
基线为 PR #20 合并后的 main（48353f534a19104fa1ea76ad3082f555fe8945c4）；
此前 0.6.0 是未发布的设计/预备阶段。本轮按用户选择将四处元数据统一为 0.5.5。
历史 PM4Py 证据保留，不把新后端结果改写到旧分析身份中。

- 宿主联合专项 183 passed，包括真实 PostgreSQL 归档恢复、查询和显式迁移、OTel SDK、Rust4PM，以及相应单元测试。
- Dagger/Linux 核心 PR Profile：996 tests、105 subtests；Ruff、版本/Workflow 绑定、锁文件通过。
- Dagger 1.0.0-beta.16、Linux CPython 3.14.8 可选专项：6 passed（4 项 Rust4PM、2 项 OTel SDK），实际安装锁定 r4pm 0.6.2。
- Windows 普通 CPython 3.14.3 实际运行 r4pm 0.6.2，验证多个变体、边界频次、XML 转义、确定性身份和 Rust 注册对象清理。
- wheel 构建通过，包含迁移 001/002，没有打包上游 mining 源码、原始实录或凭据；受影响文档文件链接通过。

专项命令：

```powershell
uv run --locked --no-sync pytest -q tests/test_trace_preflight.py tests/test_trace_source.py tests/test_trace_store.py tests/test_trace_projection.py tests/test_trace_mining.py tests/test_trace_results.py tests/integration/trace_catalog.py tests/integration/trace_mining.py tests/integration/trace_projection.py tests/integration/trace_store.py
uv run --locked --no-sync engineering-ci checks pr --reuse --json .test-tools/trace-evidence-query/pr-checks.json
```

`--no-sync` 使用已经通过 `uv sync --locked --extra trace --extra mining --group dev` 安装的可选环境。
基础 CI 不运行可选 mining/数据库专项，不把单元替身成功冒充真实 SDK/服务器结果。
Linux 可选专项通过固定 Dagger SDK/Engine 执行既有 integration 文件，未传宿主 PostgreSQL DSN。
操作 JSON、JUnit、完整 PR 检查报告及构建产物位于忽略目录 `.test-tools/trace-evidence-query`。

专用宿主 schema 显式从 1 升级至 2；来源和原快照的 metadata 保持一致，没有重读或重写原始实录。
已有投影 3a85a34406134b30c3bcf7d10a1421eec386662daad6fce4a54dc82934a4c3aa
和历史 PM4Py 分析 ec3b43164b3c497db6509893ff5d141dd4edcd05ce780be8efcd13c7fd622293
直接登记为 origin=file，没有重跑 SDK 或 PM4Py。
Rust4PM 同一固定投影的分析为 1a730b2a683b402ef06ce4eb1073505886ec4a4183413f94b81d5b217b6a66d0，origin=computed。
6 个入选 Turn、6 个变体、8 条 DFG 边；56 个排除 Turn 的原因和所有比较统计字段与历史 PM4Py 相同。
lineage 关联 source → 同一 snapshot → projection → analysis；费用仍 unknown，llm_used=false。

覆盖输出路径已存在后的结果恢复、原文件登记不重算、旧 schema 查询不自动升级、
缺父结果、摘要损坏、同身份内容冲突、分页过滤和 metadata 查询不读取原始 payload。
提交确认丢失使用实际 PostgreSQL 提交后的客户端错误注入，不是实际网络切断。
不声称已经完成 OTLP 在线传输、优化效果验证、服务器掉电/备份恢复或正式发布。
新 PR 的真实 CI 状态在 PR 中另行记录，模拟和本地容器结果不替代远端门禁。
