# 上游库使用观察

## Psycopg Binary：Windows free-threaded Python 3.14 分发限制

- 状态：已确认的兼容性限制；不是已确认的上游 Bug，也不是数据库配置或镜像错误。
- 正常使用场景：v0.6.0 PostgreSQL 归档预检，引入 `psycopg[binary]==3.3.6` 后安装可选依赖。
- 实际环境：Windows AMD64；现有 `.venv` 为 CPython 3.14.8 free-threading build。
- 复现：在该环境执行 `uv sync --locked --extra trace --group dev`。
- 可观察结果：`psycopg-binary==3.3.6` 没有适合当前平台的 source distribution 或 wheel，安装失败。
- 核对依据：官方 PyPI 的该版本 Windows Python 3.14 分发为
  `psycopg_binary-3.3.6-cp314-cp314-win_amd64.whl`，未提供 Windows `cp314t` wheel。
- 当前处理：用户授权使用已安装的普通 CPython 3.14.3，原 checkout 的 .venv 更新后安装和真实 PostgreSQL 预检通过；未改数据库。
- 范围：仅影响采用 binary 实现的此运行时组合；不据此推断普通 CPython 3.14、纯 Python 实现或其他平台均不可用。
- 未开展额外上游调查，未搜索或提交上游 Issue/PR，未修改第三方库。

## OpenTelemetry Python OTLP/HTTP：导出返回值不含部分接收信息

- 状态：已确认的 API 信息限制；未认定为上游 Bug。不是 Jaeger、Collector 或数据库配置错误。
- 正常使用场景：v0.5.6 可选 OTLP/HTTP 出口，对官方 exporter 1.45.1 进行真实本地 HTTP 协议验收。
- 复现：`uv run --locked --no-sync pytest -q tests/integration/trace_telemetry.py::test_actual_partial_rejection_is_not_sdk_full_success`。
- 服务端实际返回 HTTP 200 + 官方 Protobuf ExportTraceServiceResponse，partial_success.rejected_spans=1。
- 可观察结果：SDK SpanExportResult 为 SUCCESS；本项目解析实际服务响应后记录 partial、acknowledged_spans=0、rejected_spans=1，不重试该部分接收请求。
- 同文件还验证 401/403、307 不跳转及畸形正文；SDK 状态不作为后端持久化或业务成功证据。
- 当前处理：继续复用官方编码、HTTP 和有限重试；用官方 Protobuf 消息读取接收确认，不复制上游实现或修改库。
- 未搜索或提交上游 Issue/PR，未开展额外上游调查。

## Propel / SQLx：Windows PostgreSQL 本地化错误无法解码

- 状态：用户实际桌面操作报告了协议错误，宿主配置已只读核实；未独立复现 SQLx 连接，未认定为新的上游 Bug。
- 场景：Propel Extraction Blueprint 连接已有宿主 PostgreSQL，尚未发现表。
- 用户错误：Postgres protocol error (reading ErrorResponse)，Postgres returned a non-UTF-8 string for its error message；其后改用同一 URI 报 pool timed out while waiting for an open connection。
- 宿主证据：server/client encoding=UTF8；lc_messages=Chinese (Simplified)_China.936；Psycopg 连接同一数据库成功；ssl=off，1 个客户端连接，max_connections=100，角色/数据库连接限额=-1。
- 分类：已观察到的错误诊断/本地化兼容性问题；真正的连接拒绝原因仍未知，不能从该错误认定密码错误、服务器连接耗尽或 Rust4PM 算法故障。
- 边界：当前应用角色不是管理员，pg_current_logfile() 被权限拒绝，未读取服务端认证日志；没有修改 PostgreSQL、凭据或第三方代码。
- 后续用户证据：Propel v0.3.1；pgAdmin4 使用 sslmode=prefer 可以登录，Propel 的 disable 尝试未解决问题。用户确认本次来源的 2026-10-13 15:56:39 CST 服务端日志仅为客户端强制断开；其他用户名/密码失败记录不作为该连接的归因依据。connection limit=-1 本身是正常的不限额设置。
- 源码观察：v0.3.1 的 Cargo.lock 锁定 dbcon 0.4.1 / SQLx PostgreSQL 0.9.0；官方 crate 仅在内存读取，SHA256=e8a30c7fecab2cccb75a38cf2fe1a75ab8e203f70277d086740881a92893b3ca 与锁文件一致。lib.rs 201–205、350–353 行在 on_a_runtime 中创建 PgPool，812–828 行的局部 Runtime 随临时线程退出，979–980 行发现表时另建 Runtime 使用原池。
- 待验证假设：原 Runtime 销毁后使用池内 I/O 的生命周期风险，与断开/池超时相符；Tokio 官方文档明确旧 Runtime 的未结束 I/O 无法继续使用。尚未执行相同版本最小复现，不把静态风险或 pgAdmin 登录成功当作已确认上游 Bug。
- 当前处理：[连接排障说明](../guides/propel-postgresql.md)提供 Source 别名语义与当前已验证的 prefer 配置，记录源码风险及直接 SQLx/dbcon 对照的下一步。不再推荐 disable 作为本例修复；是否调整消息语言由管理员决定。Propel 连接成功仍未验收。
- 未搜索或提交上游 Issue/PR，未扩大为第三方库修复。
- 用户已决定停止本次 Propel 排障，跳过该可选 UI；不继续开展复现、上游修复或 Issue 调查。上述源码风险仍保留未独立复现状态，不因用户判断而伪造验证完成。
