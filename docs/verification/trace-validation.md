# 轨迹归档与分析阶段验收

本记录汇总 pre-0.6.0、v0.5.5–v0.5.7 的阶段验收及 v0.6.0 版本准备，不代表正式发布或自动优化闭环已经完成。
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

本节为 P0 首轮结束时的历史范围。后续 Rust4PM、Linux SDK/算法专项及 PR CI 已在下文各阶段验收；
PM4Py 执行依赖已移除，其历史结果保留。正式发布事实仍须由对应 Release/Manifest 确认。

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

## v0.5.6 遥测与比较验收

2026-10-12 至 2026-10-13，基线为 PR #21 合并提交 541e1264bb4f1e101123b9abeaff0fbf2e6f9861，
同一主 checkout 的分支 codex/trace-observability-v0.5.6。四处元数据提前统一为 0.5.6。
用户选择本地 Jaeger 与 OTLP/结果比较联合验收，未发起正式发布。

官方 exporter 1.45.1 + 真实本地 HTTP 协议 fixture 覆盖 Protobuf/时间/失败/ID、
HTTP 200 partial 拒绝、401/403/422、307 不跟随、损坏正文、响应丢失及 TLS 错误。
服务器是测试替身；真实 HTTP/SDK 成功不作为真实 Jaeger 交付结果。
该使用过程发现 SDK 返回 SUCCESS 不包含部分接收信息，观察记录在 `.agent/upstream-observations.md`。
项目用官方 Protobuf 读取确认，未修改库或搜索/提交上游问题。

另行实际启动 Jaeger 2.22.0 与 Collector 0.162.0，Docker Server 29.4.0。
Jaeger 镜像 digest 为 sha256:836b967b5abcf1f7758066aa6b348619267a2f1459f72d472840570f9bd88a17；
Collector 为 sha256:310a800ad69ee430e7c541796852a242c9c7db97aaad4daa5ccf843c525fbdb2。
显式 `tests/integration/trace_jaeger.py` 3 passed：直连、Collector 转发、Jaeger 停止后恢复。
读回确认原退出码 1、failed、实际开始/持续时间和 snapshot/Span ID；Collector 确认与后端读回分别断言。
仅使用本次带专属标签的容器、network 及 loopback 随机端口，最终只清理这些资源。

对 P0 已登记的固定投影进行一次真实 Collector → Jaeger 发送和完整读回：3017 Span、62 Trace。
projection 3a85a34406134b30c3bcf7d10a1421eec386662daad6fce4a54dc82934a4c3aa，
snapshot 4add04f0a96013dd0aacfe2ddb5e72aa40b07e223af274c72e3b71fe7909b237。
PostgreSQL 中同一投影的摘要未变化；没有重读原始 rollout、重新投影或执行历史工具。
实际 receipt 与后端读回证据分别保存为 `.test-tools/trace-p1/real-send.json`、`real-backend-readback.json`。

已登记历史 PM4Py 分析与 Rust4PM 分析，通过同一投影父关联进行比较，结果 equivalent。
没有重跑任一分析后端。比较为固定输入的算法结果，quality_gate=not_evaluated、optimization_verified=false。
没有伪造工作流总耗时、分析运行时间、费用或跨运行质量验证，完整性仍 unknown。
比较结果在 `.test-tools/trace-p1/real-comparison.json`，相关测试 JUnit 同样保存在忽略目录。

最终宿主联合专项 191 passed，包含真实 PostgreSQL、SDK/Rust4PM/HTTP 协议测试与单元替身。
Dagger/Linux 可选专项 15 passed，实际安装 trace/mining/telemetry extras；未传宿主数据库凭据。
完整 PR Profile：1036 tests、105 subtests；Ruff、四处版本、Workflow 绑定和锁文件通过。
该报告的五项检查 input_stable 均为 true；对应 JUnit 与 JSON 保存在 `.test-tools/trace-p1`。
v0.5.6 wheel 构建及内容检查通过，含新 CLI/模块和既有迁移 001/002；
未打包原始实录、凭据、上游 mining 实现或本地验收产物。文档文件链接检查通过。
专项命令：

```powershell
uv sync --locked --extra trace --extra mining --extra telemetry
uv run --locked --no-sync pytest -q tests/test_trace_telemetry.py tests/test_trace_comparison.py tests/test_trace_preflight.py tests/test_trace_results.py tests/test_trace_mining.py tests/integration/trace_telemetry.py tests/integration/trace_catalog.py tests/integration/trace_store.py tests/integration/trace_projection.py tests/integration/trace_mining.py
uv run --locked --no-sync pytest -q tests/integration/trace_jaeger.py
uv run --locked --no-sync engineering-ci checks pr --reuse --json .test-tools/trace-p1/pr-checks.json
```

真实 GitHub PR CI 的链接、候选 SHA 和最终状态在交付 PR 中另行记录。
本轮不验证 Collector 持久化队列、Jaeger 长期保存/服务器掉电、远端认证/mTLS或跨运行优化效果。
基础 CI 不依赖个人数据库、Docker 后端或可选 telemetry 服务；发布与消费者契约保持原职责。

## v0.5.7 候选与审查依据验收

2026-10-13，以 PR #22 合并提交 fe44fa3d95a859c6b0bc710f1e2e0afdadd93fb7 为基线，
在原 checkout 的 codex/trace-candidates-v0.5.7 分支交付；四处版本提前统一为 0.5.7。
用户选择先做可追溯候选与审查依据，LLM 第二输出和固化执行未进入本轮验收。

- 宿主联合专项 84 passed：候选/比较/身份/case 准入单元测试，以及真实 SDK、Rust4PM、PostgreSQL 集成。
- Dagger 1.0.0-beta.16 / Linux 可选端到端专项 1 passed：真实原生协议 fixture → SDK 投影 → Rust4PM → 候选。
  没有向容器传 PostgreSQL 凭据，数据库专项仍在宿主运行。
- 完整共享 PR Profile：1067 tests、105 subtests；Ruff、版本/Workflow 绑定、锁文件通过。
  五项检查 input_stable 均为 true；基础 CI 未增加个人数据库、模型或后端服务依赖。
- wheel 构建与内容检查通过，含新模块及既有 SQL 迁移，没有打包本地数据、凭据或上游实现。
  文档文件链接检查通过。

真实库的协议 fixture 与此前用户实录分开验收。已登记历史分析仅只读加载一次当前派生输入，
未重读原始 rollout、重新投影或执行历史工具/发现算法。Rust4PM 和历史 PM4Py 两份分析的候选观察相同，
原分析与投影文档摘要未变，身份及 origin 仍分开。
固定投影/分析 ID 延续上一节；实际 report_id 为
0efa268cb8969c1f44cd5c9f5545446171cc4742801268f3f1971b100152e543。
真实 ID 模式 CLI 与直接候选 API 生成的报告一致。

当前实录有 3017 个投影操作，其中仅 6 个 Turn、40 个操作满足顺序准入。
56 个 Turn、2977 个操作被排除：54 个 Turn 重叠/并列、2 个缺结束边界。
得到 6 个候选（3 类活动的相邻重复与观察后继各 3 个），没有重复完整变体候选。
全量统计仍为 255 failed、2759 passed、3 unknown；准入操作为 1 failed、39 passed。
这不是整体工作流的优化效果验收，也不把排除操作当作已通过。
报告均为 not_reviewed、quality_gate=not_evaluated、optimization_verified=false、llm_used=false。

覆盖历史分析兼容、真实父关联、输入字节变动、模型/统计不符、空结果、失败/取消/未知、
排除覆盖率、输出上限、长见证截断和每条观察后继引用；写入错误返回非零并保留可继续的来源信息。
已有输出拒绝覆盖，不为该错误连接数据库；新输出可从同一派生证据恢复，不重跑算法。
宿主 JUnit、Linux JUnit、PR JSON、real-candidates/real-legacy-candidates/real-cli-candidates 在忽略目录 `.test-tools/trace-p2`。

专项命令：

```powershell
uv sync --locked --extra trace --extra mining
uv run --locked --no-sync pytest -q tests/test_trace_candidates.py tests/test_trace_comparison.py tests/test_trace_results.py tests/test_trace_mining.py tests/integration/trace_candidates.py --junitxml=.test-tools/trace-p2/junit-host.xml
uv run --locked --no-sync engineering-ci checks pr --reuse --json .test-tools/trace-p2/pr-checks.json
```

默认核心测试的替身成功不替代真实库/数据库证据；GitHub PR CI 的实际候选 SHA、Run 与结果另记于交付 PR。
本轮不声称获得完整任务/检查身份、条件分支或因果 DAG；具体 Handler、质量及跨运行比较证据仍待选定目标后补充。

## v0.6.0 版本准备

2026-10-13，PR #23 已合并，基线为 a91fc9303e3b16529efc8b86b3b95500961593c6。
用户确定按已完成能力准备 v0.6.0，不增加 LLM 或固化执行。使用同一 checkout 的
codex/prepare-release-v0.6.0 分支；源代码、依赖及配置契约保持不变，四处元数据一致为 0.6.0。

阶段实测证据继续按上文原样保留。本轮没有重新读取原始会话、导入/投影、挖掘、
发送相同 OTLP 数据或连接数据库进行迁移，也没有将历史通过记录直接冒充当前通过。

```powershell
uv run --locked --no-sync engineering-ci version set v0.6.0
uv run --locked --no-sync engineering-ci checks pr --reuse --json .test-tools/trace-v060/pr-checks.json
uv build --wheel --out-dir .test-tools/trace-v060/dist
```

独立结果文件从 P2 检查证据起步，原 P2 记录未覆盖。入口实际核验输入、工具、环境及原证据，
复用 Ruff Lint、Format、pytest（原实际 1067 tests、105 subtests），仅在 Dagger/Linux 执行 version/lock。
四处 v0.6.0、Pipeline/Workflow 绑定与锁文件检查通过，五项 input_stable 均为 true。
此处的 pytest 数量是原执行证据，不能解释成本轮新增执行了同样测试。

v0.6.0 wheel 构建及内容检查通过：版本、trace/mining/telemetry extras、新模块与迁移 001/002 正确，
没有打包本地原始/派生数据、凭据或上游实现。受影响文档文件及标题链接检查通过。
新 PR 的实际 CI 会使用新提交运行并另记于 PR；上述复用不替代 GitHub 门禁。
本轮未启动 release.yml，未创建 Tag、Draft Release 或正式 Release。
正式发布的 Run/Attempt/SHA、完整检查及 Manifest 仍须人工合并后从既有 publish 流程取得，
不能以此版本准备记录认定 v0.6.0 已发布。

### v0.6.0 正式发布后的只读确认

用户完成正式发布后，本轮只读核对 [v0.6.0 Release](https://github.com/dymzz/engineering-ci/releases/tag/v0.6.0)：
PR #24 的合并提交为 `e4fc1cdd98140e380e5dbec401290bc87de79f15`，Release 非 Draft，
发布时间为 2026-10-13 01:53:48 UTC。实际 Manifest 关联
[Run 38333998282](https://github.com/dymzz/engineering-ci/actions/runs/38333998282)、Attempt 1、
workflow_dispatch、release.yml 和同一 SHA，result=passed。

下载 [release-manifest.json](https://github.com/dymzz/engineering-ci/releases/download/v0.6.0/release-manifest.json)
并核对实际 6044 字节与 GitHub Asset digest 一致：
`2f42f9c3cfb9d7a561bc1fc8c5583665976a7217a5feb8c5659d08891137e9d5`。
这是已有正式发布的身份/Asset 只读确认，没有再次启动发布流程，
也没有将下载成功当成本轮重新执行了全部发布检查。

## v0.6.1：首个 PR 检查 Handler 绑定

用户确定首个目标为基础 PR 检查的同输入复用，以已发布 v0.6.0 main 为基线，
在原 checkout 的 codex/pr-check-handler-v0.6.1 分支交付四处 0.6.1 元数据、代码与文档。

实际选定候选 `5de6ca846d52575adae5313249a8ad45f57e03fd34b630563b901de91c098cb4`：
连续 command_execution 相邻对出现 9 次、涉及 5 个 case。
只读使用上文登记的 Rust4PM 分析及父投影；完整性仍为 unknown。
没有重新读原始 Codex 实录、运行 mining 或 SDK、写数据库或发送 OTLP。

```powershell
uv run --locked --no-sync engineering-ci mine bind --analysis-result 1a730b2a683b402ef06ce4eb1073505886ec4a4183413f94b81d5b217b6a66d0 --candidate 5de6ca846d52575adae5313249a8ad45f57e03fd34b630563b901de91c098cb4 --profile pr --out .test-tools/handler-v061/pr-check-reuse.json --json
uv run --locked --no-sync engineering-ci checks --binding .test-tools/handler-v061/pr-check-reuse.json --reuse --json .test-tools/handler-v061/pr-checks.json
```

生成绑定 `a54203a4b8d97687b08baa3c1d68f90b2658c84d926fd9b62731b6b125b23ad0`，
固定完整五项 Profile，Pipeline SHA-256 为
`ac38cbd6b63a749490686e63855a304aefbb3013064acfd6a97aadbff9b4145a`。
产物和实际检查 JSON 保留在 `.test-tools/handler-v061/`，不提交派生来源数据。
mapping_basis=explicit_target_selection、historical_check_identity=unavailable、optimization_verified=false；
绑定成功不表示历史检查冗余已证明。

Host 局部故障回归：160 passed（原检查、Dagger 协议替身、候选及新增 26 项绑定测试）。
使用真实 pytest 和原执行器，以外部进程替身覆盖拒绝无关候选/缺关联、模型篡改、部分 Profile、
任意 Handler/命令、配置变更、内容损坏、失败复用、缺工具、运行中输入改变、输出冲突及关联写入失败。
这些替身结果与下面实际 Dagger/Linux 执行分别记录。

首次实际绑定调用：Ruff Lint/Format、pytest、version、lock 全部通过。
Dagger CLI/Engine 1.0.0-beta.16、Linux、锁定 uv 0.12.23、Ruff 0.16.10 与 Python 3.14；
核心 pytest 为 1093 passed、105 subtests passed，必需测试没有跳过，四处元数据和 Workflow 绑定一致。
JSON/JUnit 计数为 1198，包含这些 subtests，不能当成额外执行了 1198 个普通测试。

同一命令再次调用：五项都 reused=true、executed_this_invocation=false、当前输入通过；
核验原始 Dagger 文件与环境，未向 Dagger 提交任何检查执行。
这证明受控同输入复用可用，不是历史成本节省或跨运行优化结论。

真实负向验证在原 checkout 临时新增 `tests/test_handler_negative_probe.py`，只含明确失败的 assert。
使用同一绑定、独立 negative-checks.json 和 `.test-tools/handler-v061/negative-junit.xml`：
实际 pytest 为 1 failed、1093 passed、105 subtests passed，工具及宿主入口都返回 1。
虽然 Dagger 文件导出的外层 Trace 为 PASSED，报告仍为 failed，不以导出完成代替检查通过。
相同负向输入再次调用：五项均复用，其中 pytest 仍 failed/exit 1，当前通过为 false，
没有重新提交检查。失败条目的统计沿用原契约，由保留的 JUnit 提供，不补造 JSON counts。

finally 仅移除本轮创建的探针；恢复原正向输入后，原 pr-checks.json 五项重新匹配并全部复用通过。
负向报告、JUnit 和原始文件摘要独立保存，没有覆盖正向报告或修改测试预期。
上述验证没有触发 GitHub Workflow、Merge、Tag 或 Release；PR 远端结果另行记录。

### 范围外已复现的输入核验缺口

在选择本轮目标前，使用内存中的受控数据确认两个已有集成问题，未修改来源或上游库：

- `mining.analysis.analyze` 的直接文件输入路径可以接受缺少快照/投影等完整身份字段的投影，
  真实 Rust4PM 发现返回 passed，但 snapshot_id/projection_id 为 null。后续应在发现前校验原投影契约。
- `mining.comparison.compare` 可以在双方声明相同但实际投影不存在的成员时返回 results_equivalent=true。
  后续应将声明成员与实际投影逐项核对，继续区分结果差异和不可比较。

本轮绑定使用候选的完整元数据及 actual projection/model 核验，不使用上述宽松结果作为执行准入，
不调用 comparison，也不将它的等价结论作为检查通过。上述问题保留为后续输入契约加固事项，
不记成 Rust4PM/OTel 的上游问题或已完成修复。
