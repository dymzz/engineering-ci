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
