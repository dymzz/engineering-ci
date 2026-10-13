# Propel 连接宿主 PostgreSQL

本次连接排障已按用户决定停止，以下保留操作和观察的历史范围。
Propel 不属于当前 Agent Harness 方案的必需依赖或验收门槛；后续工作使用既有 Rust4PM 接口。

## 使用离线 cases.xes

当前推荐从文件导入。已有派生 analysis/projection 文件时，不需要连接数据库或再次运行 Mining：

```powershell
uv run --locked engineering-ci mine export-xes .test-tools/harness-p0/analysis.json --projection .test-tools/harness-p0/projection.json --out .test-tools/harness-p0/cases-new.xes --json
```

本轮已生成 `.test-tools/harness-p0/cases.xes`。将该文件拖入 Propel 窗口，使用 EventLog 文件导入；
[Propel 上游说明](https://github.com/aarkue/propel#features)明确支持拖入 XES。
这个路径不使用 Extraction Blueprint、数据库 Connection 或 Source id。
本项目已验证导出及 Rust4PM 读回，当前桌面安装的实际 UI 导入仍由用户验收。

XES 仅包含准入的 Turn/活动、原结果、真实完成时间与来源身份，
根属性保留 completeness 和排除数量；不含 Prompt、命令、工具输出或认证信息。
本轮实录仅有粗粒度活动，不能将 command_execution 当作具体 pytest。
文件已存在时拒绝覆盖；改用新的输出文件名。命令完整说明见
[CLI 参考](cli-reference.md#标准化与离线分析)。下文保留此前连接排障记录。

Propel 是 Rust4PM 的图形程序，不是 engineering-ci 的查询后端。
本项目没有注册 Propel 数据源或实现自动数据库提取 Blueprint。
以下依据 [Propel 上游连接界面](https://github.com/aarkue/propel/blob/main/packages/components/src/extraction-blueprint/edit/ConnectionsDialog.tsx)
及[连接路由](https://github.com/aarkue/propel/blob/main/apps/studio/src/extraction-sources.ts)。
界面随其版本变化；当前 desktop 构建可启用 PostgreSQL，网页 WASM 不直接连接数据库。

## 连接与 Source id

1. 在桌面版打开 Extraction Blueprint，选择 **Connect a database**。
2. Source id 填 Blueprint 内的连接别名，例如 `engineering-ci-local`。
   它与 Source 节点的 `source_id` 对应，不是 `engineering_ci_trace.sources.source` 的长 URN，
   也不需要先往数据库插入一条新 source。
3. 选择 PostgreSQL，填写宿主实际 Host、Port、User、Password、Database。
   或选择 Custom，粘贴当前 `VESRYN_DATABASE` URI。
4. 连接成功后发现表，Source 节点使用同一个别名。
   本项目专用 schema 默认 `engineering_ci_trace`，不是 public。

本机本轮只读核实的定位为 `127.0.0.1:5432`、database/user `vesryn`。
这些是本机实测值，其他机器应使用自己的配置。密码和连接 URI 不写入文档、Git 或聊天。

```powershell
# 在已继承用户环境变量的终端，复制到本机剪贴板供界面粘贴，不打印凭据。
Set-Clipboard -Value $env:VESRYN_DATABASE
```

`No discovered sources` 可能只是连接失败后的空状态。
应先处理其后的连接错误，不能用补数据库 source 行解决认证或网络问题。
连上表不等于已经形成可挖掘事件日志：归档 `events.payload` 是原始字节，
派生 JSON 是本项目的分析契约。Propel 的事件/对象映射仍需显式定义；没有自动转成 OCEL/XES。

## non-UTF-8 ErrorResponse

本轮确认：server/client encoding 是 UTF8，而 `lc_messages` 为
`Chinese (Simplified)_China.936`。用户的 SQLx 错误与该事实吻合：
服务端错误文字解码失败，真正的认证/连接拒绝原因被遮住。
不能单凭此错误断定密码错误，也不能把改语言当成修复认证。

需要 PostgreSQL 管理员查看失败时刻的服务端日志。
当前应用用户非管理员，读取 `pg_current_logfile()` 失败，Windows 服务读取也被拒绝；
本轮未切换账号、改密码、改认证规则或服务器配置。

管理员如需让后续错误可读，可在有权限的 SQL 会话执行：

```sql
ALTER SYSTEM SET lc_messages = 'C';
SELECT pg_reload_conf();
```

这会改变服务端消息语言；应由管理员决定并核对实际配置生效，
再用真正显示出来的错误处理连接问题。参考
[PostgreSQL 参数](https://www.postgresql.org/docs/17/runtime-config-client.html)。

## 连接池超时

`pool timed out while waiting for an open connection` 表示没有取得可用连接，
不证明 TCP、TLS 或认证已经通过。先核对是否同一 Host/Port/User/Database 和凭据。
本轮同一宿主的只读检查发现当前 1 个客户端连接、max_connections=100，
用户/数据库限额均为 -1；未观察到服务器连接数耗尽。

用户进一步确认：Propel 为 v0.3.1，pgAdmin4 使用 `sslmode=prefer` 可以登录，
Propel 使用 `disable` 未解决连接问题。当前保持 `prefer`，不继续要求关闭 SSL。
`prefer` 优先尝试 TLS、允许非 TLS 回退；pgAdmin 成功不证明实际会话使用了 TLS。
可在该 pgAdmin 会话只读核对实际目标，不更改认证或服务器配置：

```sql
SELECT current_user, current_database(),
       inet_server_addr() AS server_address,
       inet_server_port() AS server_port,
       (SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()) AS session_ssl;
```

`connection limit=-1` 表示角色没有单独的连接数上限，不表示客户端连接池无限可用。
用户确认来自本次 Propel 的 2026-10-13 15:56:39 CST 日志只有客户端强制断开，
不能由此判定密码错误，也不能把其他时刻的 anonymous/密码失败尝试归给这一条连接。

### v0.3.1 连接池生命周期风险

[v0.3.1 锁文件](https://github.com/aarkue/propel/blob/v0.3.1/engine/Cargo.lock)
锁定 dbcon 0.4.1，其 PostgreSQL 使用 SQLx 0.9.0。
只在内存检查了[官方 dbcon 0.4.1 源码包](https://static.crates.io/crates/dbcon/dbcon-0.4.1.crate)，
摘要与锁文件的 `e8a30c7fecab2cccb75a38cf2fe1a75ab8e203f70277d086740881a92893b3ca` 一致。

可观察实现：`src/lib.rs` 的 inner_from_connection_string/new_postgres
通过 on_a_runtime 创建 PgPool（约 201–205、350–353 行）；辅助函数在临时线程中
创建局部 current-thread Runtime，完成 job 后退出（812–828 行）。
发现表时又通过另一次 on_a_runtime 使用该池（约 979–980 行）。
[Tokio Runtime 契约](https://docs.rs/tokio/latest/tokio/runtime/struct.Runtime.html#shutdown)
说明销毁 Runtime 后，绑定其上的未结束 I/O 无法继续使用。

这构成客户端 Runtime/连接池生命周期的具体风险，与断开和池超时症状相符；
尚未在同版本独立执行复现，不能标为本次故障的已确认根因或已修复 Bug。
后续应以相同连接参数对照直接 SQLx 连接与 dbcon new_any 的最小调用，
再判断是否需要上游修复。本轮没有安装该 crate、创建源码副本、修改上游或提交 Issue。
pgAdmin/Psycopg 成功、Propel 连接失败和这个源码风险分别记录。
