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

## 已归档能力反馈的只读检索（P3-1）

v0.6.5 的 `workflow feedback-query` 读取 schema 3 的 workflow_feedback；原
`workflow feedback` 仍负责保存/显式归档，原 trace query 层级不改。执行与批准以
[能力契约](generated-workflows.md#能力批准版本选择与执行反馈)为准。

按 feedback_id、approval_id、execution_id、能力 name/version、原结果 status、观测
completeness 过滤。字段类型/枚举复用 FEEDBACK Schema，非法输入在连接前拒绝。使用
既有 VESRYN_DATABASE/tracing.toml，不依赖本地能力目录或执行文件，不构建 Runtime、
执行工具、迁移或登记反馈。

返回前核验实际字节 SHA-256、规范 JSON、原 Schema、feedback_id 内容身份和批准 ID
列关联；损坏不会被过滤成空结果，不回显原文。仅返回能力/执行标识、原结果/退出码、
累计观察指标、归档时间与摘要引用，不复制日志、Prompt、Diff、环境变量或连接串。
数据库/用户身份只保留摘要。

同时接受反馈文档 v1/v2；v2 额外核验索引与累计计数的一致性，只返回索引协议、摘要与
数量，不展开整个 Attempt 列表。文档升级以[反馈契约](generated-workflows.md#反馈文档-v2-与-attempt-索引)为准。

query.status=returned/exit_code=0 仅表示查询成功；item.recorded_result 的失败/非零、
incomplete/unknown 保持原事实。current_input_validity、approval_state_now、
execution_evidence_validation 为 not_checked，摘要核验不代替原执行文件或当前准入。

### 有界源页与输出预算

schema 3 没有执行 ID/状态等独立查询列。本轮不新增 migration 或任意 SQL 配置；按 ID/
批准列参数化筛选，再核验并过滤其他字段。单页读取最多 101 个候选，核验最多 100 个；
达到请求条数即停止核验。source_page 计数仅描述本页，不是整库统计。

items=[] 且 next_after 非空表示本候选页无匹配，可继续；游标可指向最后已核验但不匹配
的候选。预算不能容纳后面的匹配项时，只推进到最后实际返回项，不漏掉未返回数据。
首项/完整信封放不下时 budget_exceeded/退出 3；增加预算重查，不输出半条 JSON。

limit 默认 20、范围 1–100；max-bytes 默认 16384、范围 1024–16777216，包含紧凑 UTF-8
完整 JSON、新行、元数据与游标。保存 JSON 使用原 2 MiB 原子文档限制，失败非零。
输出预算不等于数据库读取字节预算。

分页按 feedback_id 升序进行，游标绑定查询协议、数据库/用户摘要、schema 及全部过滤，
不绑定 limit/预算；源/过滤变化或书签记录删除会拒绝旧游标。它不是权限凭据，也不是
实际执行顺序或跨页固定快照。并发插入且 ID 位于已读位置之前的记录须从头新查；每页
自身使用原只读 REPEATABLE READ 事务。

普通列表无记录为成功空页；指定 feedback-id 无匹配为 feedback_unavailable/非零。
权限、缺连接、旧 schema、内容损坏各自非零，不能成为正常空列表。不自动补授权、
迁移或恢复原工作流。--json 只能覆盖同类查询回执，不能覆盖原证据。

### 阶段边界

查询对象是反馈快照，不是唯一调用或完整任务。恢复前后累计 5/10 条观察的两份快照都
保留，不能相加为 15 次调用；collection_coverage=unknown，不推断整体成功率。
P3-1 不聚合/去重、比较效果、选择/停用能力或宣称速度/费用收益。P3-2 覆盖关系核验、
汇总和 P3-3 再审查依据由下文单独入口执行。操作见[反馈查询](../../../guides/generated-workflows.md#查询已归档反馈)。

## 已归档反馈的覆盖与汇总（P3-2）

`workflow feedback-summary` 只读显式选择的 1–32 个反馈 ID。重复 ID 去重；不从一页
查询结果冒充全库，也不自动遍历所有历史。一个只读 REPEATABLE READ 事务读取整组选中
记录；所有记录先走原字节/身份/Schema 与 v2 语义校验。缺少任何明确选择的成员或损坏
均非零，不用剩余部分产生总数。schema 3 不变，不执行工具、登记反馈或自动迁移。

按 execution_id 分组。同一执行必须保持批准、能力版本、generation/definition、冻结
catalog_revision 和 required_step_ids 一致，才能汇总。跨执行同名 Step/Attempt 仍是
不同观察。组内以 Step/Attempt 标识去重：相同身份的事实及来源行摘要必须一致；变化
标记 conflicting，整个组 metrics=null，保留冲突键和每份原结果，不挑对结果有利的快照。
部分边界后来补全也可能改变摘要，本轮保守列为冲突，不自动推断演进链。

索引一致的子集可由另一份选中索引覆盖，covered_by 给出实际反馈 ID。相等集合使用
固定 ID 顺序选代表，不形成循环；独立/部分重叠索引取集合并集，只去除明确重复引用。
因此累计 5/10 的两个一致索引返回 10 个 unique_observed_attempts，去除 5 个重复引用，
不会返回 15。required_checks 不是执行次数；adapter batch 按 session/batch 身份去重，
不代表进程数或 Dagger 缓存未命中。

原 v1 没有索引，legacy_unverifiable；不比较累计值来猜覆盖。与 v2 混合时只计算已核验
索引子集，标记 partial 并列出未覆盖的 legacy_feedback_ids。incomplete/unknown 保持
缺口。known_check_submissions/known_reused_checks 只计已知真标记，unknown_*_measurements
列出未知项，不把未知视为实际零；Attempt 原结果分布与快照原失败/非零分别保留。
covered_by 不把旧失败改成通过，不通过归档时间或较大计数选“最新结果”。

返回 status=summarized/exit_code=0 表示汇总操作成功。snapshot_coverage、has_coverage_gaps
说明可聚合程度，conflicting/legacy 组也可作为诊断返回，不能作为质量放行。全局
collection_coverage=unknown，统计范围仅 explicit_feedback_snapshot_selection；当前输入、
能力启用及原执行文件核验均 not_checked。不计算全库成功率、整体优化效果、时间或成本。

核心结果与 summary_id 由协议、来源身份及排序后的明确反馈集合决定；输入顺序和重复
参数不影响身份。--max-bytes 沿用完整 UTF-8 JSON 预算，默认 16384。无法容纳整份结果
则 budget_exceeded/非零并给出摘要引用；不删掉执行组后输出一个不完整总数。增加预算
只重查同一证据，不重跑检查。--json 只能覆盖同类汇总回执，原反馈和查询回执不覆盖。
操作见[反馈汇总指南](../../../guides/generated-workflows.md#核验覆盖并汇总反馈)。

## 已归档反馈的再审查依据（P3-3）

`workflow feedback-review` 消费已成功生成的 P3-2 汇总 JSON，以及明确指定的现有能力
目录和最多 32 个 call 执行目录，不扫描目录猜测关联。沿用同一 tracing 配置，从数据库
重读原 1–32 个明确反馈并重算汇总；完整规范 JSON 摘要必须与输入文件一致。编辑过的
计数、未知字段、缺记录、错误来源或旧协议均不能成为审查依据。读取过程中汇总文件
改变也非零。数据库仍只读，不迁移、归档或用文件内统计替代真实来源。

复用 CapabilityStore 的身份/定义校验和原 admission 的比较核验：批准中的 baseline/
candidate Scope、比较内容摘要与 ID、原质量门禁和 check-submission-reuse 范围必须匹配。
不再实现一套比较器，也不运行两个源执行。能力 enabled_at_read 是目录读取时状态；
已停用仍可审查历史，但不会因报告而启用。

目标 Pipeline 策略变化单独报告 trial.status=policy_changed、旧/新策略摘要和
follow_up=review_new_definition_and_controlled_trial，建议 investigate。这不是文件损坏，
也不能把原比较冒充当前准入；需由人确认新定义和受控试验。enabled_at_read=true 不代表
旧定义仍可准入，实际 select/call 继续执行原策略检查，不放宽 admission。

每份反馈关联显式 call 目录的冻结绑定（能力/批准、generation/definition、catalog_revision
及 store）。完全相同 Scope 还要匹配原结果、metrics 与 v2 索引；v1 也可按此核验完整
同一时点。较旧 v2 仅在当前保留的每个原 Attempt 行一致时标记 verified_retained_attempt_rows，
historical_state_validation=not_available，不伪造旧 state 文件。较旧 v1 无完整匹配 Scope
则保持不可核验。报告保留 execution_scope_at_read，使读取事实可追溯。

核验到的实际 call 原检查还必须通过既有质量 gate，并与原 candidate 的完整身份、
输入/工具/参数/环境及可选 executor 身份匹配。双方原生执行共同没有 executor 不算
变化；必需身份缺失不能成为已知可比事实。Scope/索引核验不替代原工具质量结果。

返回三种确定性的人工审查依据：

- retain_evidence：原比较和选中 call 证据成立，所记录上下文匹配；只表示保留历史依据。
- new_controlled_trial：证据成立，但 call 的已记录输入、工具、参数或环境不同；需要人工决定新试验。
- investigate：原失败、缺证据/身份、内容/绑定冲突、不可核验快照或原质量门禁不成立；保留原因和原结果。

status=reviewed/exit_code=0 只表示报告生成；decision_required=true，当前工作区输入仍
not_checked，整体历史覆盖 unknown。不得据此自动 approve/activate/deactivate/call、
放行 CI、调优或发布。原失败/非零不被后续成功或 retain_evidence 改写；报告不宣称新的
优化收益、速度、成本或整体 Agent 效果。llm_used=false，规则版本为 workflow-feedback-review/1。

review_id 绑定源汇总内容摘要、读取时能力目录摘要和明确校验结果；不包含时钟或日志。
--max-bytes 预算覆盖完整 UTF-8 JSON，超限整份非零，不丢组。--json 只覆盖同类审查回执，
不得覆盖输入汇总或进入能力目录、call 目录、原 baseline/candidate，即使失败回执也一样。
锁与校验限定读取时点，事后输入改变不产生当前放行；能力选择和原检查继续独立准入。
命令见[再审查指南](../../../guides/generated-workflows.md#关联原比较与执行证据进行再审查)。
