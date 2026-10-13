# 分层检索与上下文边界

PostgreSQL 长期保存原始历史及固定派生结果。查询只返回当前问题需要的统计和引用，
不把归档规模等同于 Agent 的上下文规模。本轮服务当前 Codex 的 CLI 查询，
没有 LLM 调用、上下文管理服务、自动摘要或独立数据库。

## 职责与数据来源

`cli/trace_query.py` 校验选择及输出 JSON；`tracing/retrieval.py` 组织只读视图、
过滤、书签和返回预算；`TraceCatalog` 负责既有 PostgreSQL 索引与选中成员读取。
查询不调用 Executor、不改变 FSM、不执行历史工具，也不触发 Rust4PM。

Rust4PM 仍由现有 `mine` 产生变体、DFG 和统计。
检索复用其实际分析与原投影，通过既有 `checked_cases` 校验成员、范围和计数，
复用候选来源及 ID 算法；没有新增发现引擎或第二套候选身份。
文件模式验证实际输入文件字节；登记模式验证文档摘要及父关联。

| 层级 | 返回内容 | 必需选择 |
| --- | --- | --- |
| Summary | 登记分析的统计目录；选中分析后提供完整统计和候选概览 | 可选 analysis；目录可按 snapshot 过滤 |
| Pattern | 候选序列、次数、case 支持率、观察结果及候选引用 | analysis |
| Candidate | 指定候选、参数可用性、依赖可用性、工具映射和审查状态 | analysis + candidate |
| Evidence | 指定候选的投影见证、Turn/event 引用和原操作结果 | analysis + candidate |
| Raw Trace | 指定快照中整条原始记录的 Base64 字节及摘要 | snapshot + 显式 allow-raw |

Summary 目录只读登记元数据，不读取分析全文，也不声称校验过文档内容。
其统计属于各个分析，不聚合成未经查询的“全部历史统计”。
选中分析后的统计与候选概览来自经过关联校验的固定数据集。

## 统计与状态

支持率为 `support.cases / support.selected_cases`，同时保留整数分子、分母。
分母是这个分析准入的 Turn，排除 Turn 和采集完整性单独保留；不代表完整历史覆盖率。
`occurrence_member_outcomes` 统计观察实例内的操作；重叠的相邻对可能重复计数。
Summary 的 `selected_outcomes` 则统计实际准入操作，二者不可互换。

粗粒度活动没有真实命令参数、输入身份、因果依赖或工具覆盖证明。
Candidate 明确返回 unavailable/unmapped，不从后继关系推断依赖。
`analysis_tools` 是分析库版本，不是历史执行工具版本。
查询不会扫描本地生成目录以猜测已固化或已验证状态；那些事实仍由独立工作流的
[执行契约](generated-workflows.md)提供，未来需要显式关联后才能查询。

`status=returned` / exit 0 表示查询完成。原操作 failed/unknown、
`review=not_reviewed`、`quality_gate=not_evaluated`、`optimization_verified=false`
均独立保留，不作为发布或检查放行证据。登记 `origin=file` 不证明算法实际运行。
空匹配集合可以成功返回；缺失或未验证的分析输入返回 unavailable / exit 3。
非法参数、缺依赖/配置、损坏或关联冲突返回具体状态 / exit 2。

## 分页、过滤与预算

- 默认 20 条，最多 100；返回 `page.next_after`，没有后续页时为 null。
- 书签绑定层级、实际来源摘要、candidate 和过滤条件；跨输入或过滤条件拒绝。
  页大小与预算可在后续页调整。书签不提供访问授权，也没有保存在服务端的会话。
- Pattern/选中分析的 Summary 支持 kind、activity、outcome、min-cases。
  Evidence 的 outcome 筛选包含该结果的见证。未适用的参数明确拒绝，不静默忽略。
- 输出预算默认 16384 字节，范围 1024–16777216。
  计量是紧凑 UTF-8 JSON 连同最后一个换行，包含信封、引用和书签。
  不以条数、字符数或 Token 数代替字节。
- 只返回完整条目，预算不足时保留下一页书签与 `budget.limited=true`。
  第一条或必需信封无法容纳时返回 budget_exceeded / exit 3，不截断 JSON，
  不通过空页面假装完成；可增加预算。原始记录始终整条返回。
- `remaining_items` 是当前固定过滤集合剩余条数。目录未读取全量，返回 null，
  不为显示总数扫描所有文档。

预算限制返回内容，不限制现有派生文档的读取/关联校验成本，也不是模型上下文预算。
选中一个分析后仍需读取其分析和投影全文，才能使用现有摘要及成员校验。
大型历史可先使用元数据 Summary 定位一个分析；将来若确有读取性能问题，再增加索引。

## Raw 与完整性

Raw 查询默认拒绝；只能显式选择 snapshot 并提供 allow-raw。
该标志是调用者确认读取原文的接口，不替代 PostgreSQL 凭据、角色权限或用户授权。
Agent 未获授权时不得自行添加它。前四层不返回 Prompt、Diff、工具参数、日志全文或原 payload。

Raw 先查选中快照的成员索引及字节大小，在预算容纳后才读 payload。
校验选中记录的摘要、引用、长度及连续成员位置。Base64 保持非 UTF-8、CRLF、尾片段等原字节。
局部读取只标记 `selected_member_digests; full_snapshot_not_verified`；
没有读完整快照就不声称捕获摘要整体有效。完整回放仍使用原 `trace replay` 校验路径。
生产归档不会为检索自动迁移、重写或创建新结果类型。

命令见[分层查询指南](../../../guides/trace-queries.md)，实测见
[轨迹验收](../../verification/trace-validation.md#v062-分层检索)。
