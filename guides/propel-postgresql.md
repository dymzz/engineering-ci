# Propel 连接宿主 PostgreSQL

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

当前宿主 `ssl=off`。仅对此本机配置，可在 Custom URI 明确指定 `sslmode=disable`：

```powershell
$propelUri = [UriBuilder]$env:VESRYN_DATABASE
$propelQuery = [System.Web.HttpUtility]::ParseQueryString($propelUri.Query)
$propelQuery['sslmode'] = 'disable'
$propelUri.Query = $propelQuery.ToString()
Set-Clipboard -Value $propelUri.Uri.AbsoluteUri
```

该参数由 [SQLx 连接接口](https://docs.rs/sqlx/latest/sqlx/postgres/struct.PgConnectOptions.html)
支持。未启用 SSL 的本机诊断不推广到远端部署；需要 TLS 的服务器使用其实际策略。
如果仍失败，由管理员读取对应日志，区分认证拒绝、网络故障与驱动行为。
本项目 Psycopg 连接成功只是宿主连接证据，不冒充 Propel/SQLx 已成功连接。
