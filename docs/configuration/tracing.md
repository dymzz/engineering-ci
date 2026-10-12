# 原始轨迹归档配置

轨迹功能复用宿主 PostgreSQL，连接仅来自进程环境变量 `VESRYN_DATABASE`。
不在配置、JSON 结果或 Git 中保存连接值。重新打开终端后才能继承新设置的用户环境变量。

## 安装与运行环境

```powershell
uv sync --locked --extra trace --group dev
uv run --locked --extra trace engineering-ci trace status --json
```

`trace` 可选依赖当前锁定 CloudEvents 2.2.0、Psycopg Binary 3.3.6、OpenTelemetry SDK 1.45.1
（其生成语义约定包为 0.66b1）。开发组安装 CloudEvents、Psycopg，用于现有核心 pytest；
OTel SDK 单独进行显式专项验收。基础消费者依赖不包含轨迹库。
现有 release/download 不读取连接变量或自动初始化数据库。

本机验收使用普通 CPython 3.14.3；Psycopg Binary 3.3.6 未提供 Windows Python 3.14
free-threaded 的 cp314t wheel。Windows/Linux 应使用该版本提供 wheel 的普通 CPython，
或者自行准备 Psycopg 支持的运行环境；安装失败不切换数据库或工作区。

## TOML

`config/tracing.toml` 相对于 engineering-ci 自身定位，显式 `--config` 的相对路径也遵循此规则。
只接受已实现字段，不接受 DSN、任意 SQL 或 shell。

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| schema_version | 1 | 配置格式版本，与数据库迁移版本分开 |
| archive.schema | engineering_ci_trace | 独立命名空间；禁止 public、information_schema、pg_* |
| archive.connect_timeout | 10 | 连接超时，秒 |
| archive.statement_timeout | 15 | 每个事务内的语句超时，秒 |
| archive.lock_timeout | 5 | 每个事务内的锁超时，秒 |
| archive.batch_size | 100 | 每批最多记录数；同时使用 8 MiB 的批次软上限 |
| archive.max_record_bytes | 4194304 | 单行/尾片段字节上限；超限停止，不截断 |

整数必须为正整数，布尔值不作为整数接受。单个记录可能超过批次软上限，但不能超过记录上限。

## 数据库边界与内容

`trace status` 只执行只读预检，不执行迁移，也不声称归档已初始化。
`trace init` 显式建立专用 schema，记录组件身份、迁移版本和规范化 SQL 摘要。
已有但不属于本组件的同名 schema 被拒绝；不接管它，也不修改其他应用对象或服务器设置。
初始化撤销 PUBLIC 对专用 schema/表的访问；数据库管理员及已有角色权限仍由宿主管理。

原始 payload 作为 `bytea` 保留，CloudEvents 属性作为 `jsonb` 保存，不复制原始内容到属性。
仅导入显式选择的单个文件，不自动扫描账户所有会话。
原始内容可能包含私密输入输出，保持在宿主数据库；没有自动公开、遥测传输或定时清理。
首版由用户控制保留期和存储容量，手工清理之前需考虑快照引用；不承诺无限保留。
连接参数和驱动原始异常不进入归档诊断。原始来源本身可能含敏感内容，归档不会修改其字节。

数据库事务提交、进程崩溃恢复和服务器掉电/备份恢复是不同验收范围。
宿主预检读取 fsync/synchronous_commit，不自动更改它们。
归档失败不改变既有 pytest、Dagger、Workflow 或发布门禁。

来源与完整性定义见[实施架构](../architecture/workflow-optimization/design.md)，
命令见[CLI 参考](../../guides/cli-reference.md#原始轨迹)。

## 可选离线挖掘

```powershell
uv sync --locked --extra trace --extra mining --group dev
```

`mining` 锁定 PM4Py 2.7.23.9，当前环境解析到 pandas 3.0.6、NumPy 2.5.4。
它不进入基础消费者或 dev 依赖，不启动数据库、Collector、图形服务或模型。
首版调用 variants/DFG，无 Graphviz 可执行程序也可分析，不承诺 Windows free-threaded 兼容。

[PM4Py 上游许可证](https://github.com/process-intelligence-solutions/pm4py/blob/release/LICENSE)
为 AGPL-3.0-or-later。当前集成用于本地运行未修改库，未复制上游实现或发布包含库的镜像/服务。
库仍保留自己的许可，不将其标为 Apache-2.0。正式分发组合程序、重新分发库或提供修改后的网络服务，
应按实际分发方式审查通知、相应源码与许可义务；“不改库源码”不等于所有使用方式都无义务。
用户确定 PM4Py 只保留显式安装后的本地调用，不纳入基础 CI、Release 或默认安装。
本轮不改变项目 LICENSE；未来若改变为内置库、镜像或网络服务，重新审查实际分发方式。
