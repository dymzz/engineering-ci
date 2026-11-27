# ECI 结构化工程审阅设计要求

状态：试验版 2.0。ECI 持有项目审阅规则，公共合同尚待 Knowledge 正式发布。采用现有工程事实，不建设新图谱、审阅 Runtime 或授权机制。规范修订与受审对象修订分别固定；当前试验分支未进入 main，必须显式选择它作为规范来源。

## 1. 目的、范围与权威

ECI 审阅必须以工程目标、实际对象、类型化关系、适用约束和验证义务为中心，发现需求遗漏、职责错误、重复建设、影响遗漏、Source/Execution/Result 绑定错误、实际接线缺失、验证证据不足及不必要成本。不能仅以 PR diff、文件列表、既有缺陷或 CI 状态作为审阅范围。

本文件定义 ECI 的实际工程约束及适用审阅义务，不重新定义 Knowledge 的通用 ReviewProfile / ReviewResult、身份、证据和覆盖语义。四视点为 Specification、Structure、Behavior、Verification。历史与演进、文档一致性、安全与信任边界贯穿各视点。各视点独立起点、共享事实与证据；同一缺陷保持一个身份。

适用对象包括架构设计、Issue/实施计划、PR、修订、验收及明确授权的全仓审计。区分任务类型：设计审阅检查可实施合同、成熟方案、职责、接口和验收，不要求未实施的功能已经运行；实现审阅检查实际实现及受影响关系；验收审阅核对当前候选、原缺陷和实际适用证据；全仓审计以声明的仓库及工程关系范围为输入，而不局限于某 PR。对其他仓库按具体公共接口、实际消费者及授权读范围追溯，不擅自扩大审计对象、执行或写入权限。

ECI 权威入口（从受审对象的实际适用版本读取，记录证据版本；不是永久固定历史 SHA）：

- [AGENTS.md](../../AGENTS.md)、[Repository overview](repository-overview.md)、[Agent instructions](agent-instructions.md) 确定职责、任务接续、关系维护及证据复用。
- [Common tasks](../../guides/common-tasks.md) 与实际 CLI/配置确定标准验证入口；不由审阅另建入口。
- [Execution Graph](../../design/execution-graph/README.md) 是整合设计草稿；实际合同以原架构文档、Issue 裁决、现行代码与原生证据分层核对，不将候选设计视为已交付。
- [#52](https://github.com/dymzz/engineering-ci/issues/52)、[#66](https://github.com/dymzz/engineering-ci/issues/66)、[#86](https://github.com/dymzz/engineering-ci/issues/86)、[#63](https://github.com/dymzz/engineering-ci/issues/63)、[#61](https://github.com/dymzz/engineering-ci/issues/61) 分别涉及 Check/原生结果、选择与 Source、原生执行及产物、受信接纳、Required 裁决等相关权威。具体分工以相应任务及实际接口为准；不得从 Issue 编号推断某功能已完成。
- [#125](https://github.com/dymzz/engineering-ci/issues/125) 固定历史依赖证据、[#129](https://github.com/dymzz/engineering-ci/issues/129) 缺陷审计、[#161](https://github.com/dymzz/engineering-ci/pull/161) 工程关系维护、[#162](https://github.com/dymzz/engineering-ci/issues/162) 工具采用和关系投影。原记录维护实际事实，不建立第二套手工账本。

## 2. 审阅实现的基线与关系发现要求

用户只需指定审阅目标（例如“审阅 PR #165”）。审阅实现自行识别任务、加载适用规范、获取事实；不要求调用者准备 JSON、对象清册、关系或义务。开始时由审阅实现固定目标、实际 Scope、所审对象类型和状态（Issue 与 PR 不混用）、main/目标 HEAD、必要时实际联合候选 Merge SHA、完整净差分、最近修订差分、适用要求和历史裁决。已合并/关闭的对象可读以追溯，但不当作待合并候选提交 Review。PR 更新先核对原 Review、原缺陷身份、实际修订和证据变化，不机械重复之前审阅。

事实与关系清册至少从以下适用来源产生：

| 来源 | 实际关系与必须识别的局限 |
|---|---|
| Git/GitHub | 文件变更、版本、Issue/PR/Review、实际 commit、Run/Attempt/Job/Artifact；Git stack、文件重叠、真实业务依赖、替代关系分别记录 |
| 源码、配置、公共 API | 定义/调用/引用、配置读取、包依赖、运行入口、输入输出、数据/文件消费；静态搜索不等于动态依赖完备 |
| 原生工具及配置 | 已实际使用的 Tach/Selection、原生 pytest/JUnit/ReportLog、Dagger、Lefthook、Actions、OTel 等；候选或仅安装的工具不作现行事实来源 |
| 原任务与技术合同 | 目标、AC、责任、Check/Expected/Plan/NativeResult/Trusted/Required/Release 等合同与实际适用阶段；分别核对设计、候选、合并和采用 |
| 历史审阅与证据 | 原缺陷、修订、执行及来源、有效范围和待证项；固定历史不可覆盖，不能用旧 HEAD PASS 覆盖受影响的新 HEAD |

必须同时生成已知对象/关系的审阅义务和发现义务：对新增、修改、删除对象的正反向依赖、所有实际入口、共享配置、资源/Fixture 消费者、原生结果消费、外部接口以及适用的预期但缺失关系，声明查找类型、方向、版本、搜索范围、来源、方法、排除及未知。关系发现以实际相关范围为界；不能仅沿已有图边，也不能因没有图边就一律扫描全仓。全仓审计另按明确范围进行。零结果只在有界完备性依据充分时证明该范围内不存在相应关系。

## 3. 四视点的 ECI 必审义务

下列 `ECI-Sxx/Txx/Bxx/Vxx` 是拟议规则族标识，非缺陷编号或正式 Knowledge 词汇。根据实际发现的工程对象、关系与适用规则实例化为对象/关系/性质级 ReviewObligation；不能每族仅打一个勾。每族均需适用性判断和来源；不适用与未知不得删去。不同视点对同一对象的不同性质分别记录。以下规则族保持既有身份和工程内容；八类的数量仅是当前组织方式，不是完整性的证明。

### C1 Specification — 目标、合同、架构职责

独立起点：工程目标、用户裁决、Issue、设计合同、AC、原公共接口及权威标准，而不是 diff 或其他视点的发现。

| 规则族 | 必审关系及性质 |
|---|---|
| ECI-S01 目标与范围 | Goal→Requirement→Issue→Design→Implementation→Acceptance 双向追溯；需求是否遗漏、过度收缩或扩张；新增实现是否有目标依据，未完成项是否被错误结算 |
| ECI-S02 权威合同 | 核对适用上游与下游合同、历史裁决和冲突；独立区分真实必要前置、可并行任务、文件重叠和 Git stack；不因其他任务未结束而无依据阻断 |
| ECI-S03 职责与复用 | Component→Responsibility→Public Interface→Consumer；先定位既有公共合同、成熟原生方案和实现，再评价新增/替代；发现平行解析器、Runner、Coordinator、FSM、缓存、Artifact Store、门禁或转发包装须给出实际重复证据和可复用入口 |
| ECI-S04 组件边界 | ECI 持有 CI/CD 工程验证、证据与交付；Dagger 持有计算 DAG、容器和缓存；Actions 持有远端编排与权限；Lefthook 持有原生 Hook 调度；Tool Runtime/Log 按已有边界；禁止对 Vesryn/Workflow 的直接或传递依赖，不以通用审阅功能引入该依赖 |
| ECI-S05 语义与不变量 | CheckDefinition、Source、Profile、Expected、Plan、NativeResult、Reconciliation、Trusted、Required、Release 的适用定义和不变量；选择不能缩小独立 Required 义务；诊断不授予权限；原生计算与受信复用不混同 |
| ECI-S06 工具与方法选型 | 已有原生工具/标准的真实能力、限制、版本及实际工程目标，优先复用成熟方案；判断新增抽象、依赖、配置、兼容层是否有收益；不默认最小实现或因防御性设计延期合理架构 |
| ECI-S07 交付与验收合同 | AC 是否能区分实现、实际接线、验证、采用、授权及收益；定义必要反例、真实验收入口、可复用证据与剩余义务；没有证据不能宣称交付 |
| ECI-S08 历史、知识及安全约束 | 追溯原问题/修订/替代关系、适用版本、架构导航、权限/Secret/信任要求；建议、采用规则、用户授权、标准规范强度不混同 |

### C2 Structure — 实现、数据、来源与影响关系

独立起点：实际变更对象、完整候选、源码、配置、数据及其上下游消费者，尤其是不在 diff 内的受影响对象。

| 规则族 | 必审关系及性质 |
|---|---|
| ECI-T01 变更对象与引用 | 文件/模块/函数/类型/配置/Schema/API 的新增、修改、删除；定义↔调用、读取、公共 API、外部消费者；核对与主线/在途 PR 的真实关系，不把文件交集等同功能依赖 |
| ECI-T02 模块与职责实现 | 实际 import/call、依赖方向、循环、重复代码和纯转发；检查真正实现与声明图一致；公共实现与适配层的必要性；区分工具发现能力、人工语义关系和未观察边 |
| ECI-T03 构建与依赖闭包 | manifest→lock→环境→包→实际消费者；检查根项目、`.dagger` 模块、SDK/固定外部依赖、CI 安装和消费者环境；例如根 dev 依赖变化影响 `.dagger/uv.lock` |
| ECI-T04 Source 与 Data | Event/Ref/Index/Tree/Worktree/Directory/Overlay/配置快照→校验→物化→实际读取；身份、实际内容、Git ODB、逐 Ref、路径/链接、子目录、排除规则及数据前置；区分目标物化状态与历史基线证明 |
| ECI-T05 选择与影响 | 变更对象→静态/动态/非代码依赖→Check/Test→Expected/Plan；Tach 图、Selection mapping、精确与前缀规则、Fixture/共享 conftest/插件、Dagger helper；查漏边、未知和错误范围，而非只统计映射数量 |
| ECI-T06 契约生产消费 | Producer→接口/Schema/Artifact→Consumer；字段、路径、命名、版本、失败/缺失语义、向后兼容、原始引用；原生 report/receipt/trace 与派生查询必须有真实交接 |
| ECI-T07 实际配置与集成 | Profile/Check/config→入口/工作流/下游项目；固定工具版本、规则来源、配置解析、默认值、阶段激活、源与消费者版本；区分已配置和已启用 |
| ECI-T08 关系与文档一致性 | 原设计、源码、配置、Guide/AGENTS、Issue/PR、原始缺陷和现行投影；沿真实关系维护原处，候选工具不作已交付，固定历史不改写；路径、Secret 出口、信任边界按实际对象追溯 |

### C3 Behavior — 实际执行、身份、采用与成本

独立起点：实际工程入口、原生工具、事件、执行实例和控制流。不能仅依据接口签名或测试替身推定运行行为。

| 规则族 | 必审关系及性质 |
|---|---|
| ECI-B01 实际入口覆盖 | 逐一识别适用 Host CLI、显式 Verify、pre-commit/pre-push、Dagger check/API/Plan、Actions、Release、消费者；从实际入口遍历并反查共享实现的调用者；不假定 Host 修复自动覆盖 Dagger |
| ECI-B02 选择与阶段执行 | 触发 Event→Profile→独立 Expected/Selection/Plan→实际 Check；正确 Source、作用域、数据前置、增量升级、跳过与不适用；区分 Task-only 和需要 Pipeline Root 的终端阶段，按实际合同不提前要求 Root |
| ECI-B03 输入绑定与原生结果 | 实际 argv、环境、原生工具版本、目录/测试目标、物化/导出及 NativeResult inputs；检查实际递归收集而非仅 CLI 字符串；原生退出、未启动、失败、取消、缺报告分别保持 |
| ECI-B04 执行身份与时序 | Invocation/Run/Attempt/Job/Trace/Span/Session/Engine/Ref/Result；同一提交、配置或 Plan 不等于同次执行；真实父子、生产消费、批次、合法多发生、重试、并发、取消；冲突限同一身份角色/作用域，不靠最近文件、时间或目录推测绑定 |
| ECI-B05 数据/结果闭环 | 原生 Check→同次 Result→Report/Artifact/Enforce→Reconciliation/Consumer；缓存、失败/部分输出、导出/上传、恢复；不因生成报告重新计算；Dagger 缓存不自动成为 Trusted，受信复用不切断仍需的产物前置 |
| ECI-B06 权限与失败边界 | Git/Runner/Provider/Workflow/Release 实际凭据与权限、Secret、Source 验证及错误传播；本地观察/遥测/格式通过不授予 Required 或发布；不误把正常合法消费者和独立执行拒绝 |
| ECI-B07 实际采用 | Tool/Rule→配置→真实调用者→执行→证据；分别记录登记、安装、实现、配置、接线、启用、验收；默认 Hook 与显式工具/affected/CI 不同，不能以全局性能前置阻塞独立采用 |
| ECI-B08 资源与性能 | 原生 Process/uv/Git/Engine/传输/pytest/worker/缓存/外部工具及报告导出；实际墙钟、阶段、并行及缓存来源，区分累计耗时与墙钟；减少重复执行与读取，验证性能收益未损失义务；不用计时接线冒充优化 |

### C4 Verification — 验证义务、测试、证据与裁决

独立起点：目标性质、Check/AC、测试及原始证据。必须双向验证“要求→实际验证”和“实际验证→所证明要求”。

| 规则族 | 必审关系及性质 |
|---|---|
| ECI-V01 验证义务覆盖 | Requirement/Constraint→Test/Check/AC→实际验证；检查缺少正反例、范围、失败和边界义务；不能以测试数、全量成功或格式检查代替特定性质验证 |
| ECI-V02 测试结构与真实收集 | Test/Fixture/参数/共享配置/Mock/断言→实际被测合同；实际收集/选择与测试映射对照，检查未执行、deselected、setup/teardown、subtests、并行、测试隔离；识别重复低价值测试，保留独有验证 |
| ECI-V03 原生验证真实性 | 实际入口、实际 Source/配置/工具/环境/Scope→原始 exit/结果；替身测试不冒充真实 Dagger/Collector/Provider/Hook；按风险做必要定向验收，而非要求所有外部链重复运行 |
| ECI-V04 证据身份与适用 | 版本、实际候选 checkout、Run/Attempt/Job/Artifact、原生结果、时间来源、关联和证明范围；分别记录直接读取、实际执行、复用、推断、未知及反证；证据对命题的支持/反驳关系明确 |
| ECI-V05 证据接纳与 Required | 独立核对来源与资格、适用输入、当前规则、实际运行、覆盖和 Required 集合；Selector、缓存、遥测、JSON/Schema PASS、原生成功及受信接纳是不同事实；终端后置条件按具体场景核验 |
| ECI-V06 报告及交付验证 | 原始 Report/Receipt/Trace/Artifact→实际消费及裁决；逐项核对完整/中断/缺失/封存失败、合法多执行及覆盖；报告生产与消费一致，不重复扫描，不以诊断成功替代原始退出 |
| ECI-V07 历史复验及修订 | 原 Finding→修订→实际差分→适用原证据/必要新证据；已核销不重复打开，独有变化、矛盾、新发现和明确要求才复审；保留历史编号；对既有结论修正说明具体依据 |
| ECI-V08 工程验收与剩余风险 | 最新候选、适用 CI、当前 AC、实际阻断、非阻断交接及未知边界；区分审阅覆盖、性质满足、工程验收和有权动作；评估性能/成本证据可比性，不因 CI 绿掩盖逻辑缺陷 |


### 3.5 规则执行与完成条件

对每族按下表发现对象、核对具体性质；仅在适用关系的有界范围已追溯、各相关性质均有判断及依据时标记 reviewed。发现方法必须说明实际读取/查询/搜索的范围、方向、版本与排除；不适用须给实际依据，无法确定则 partial/unknown，不以空结果证明无边。共享原始事实，各视点独立判断，不重新抄写或重复运行。表中是完成条件，不要求固定顺序或全仓扫描。

| 规则 | 实际发现/核对来源（按适用范围） | 完成条件 |
|---|---|---|
| ECI-S01 | 原任务、裁决、AC 与实际变更 | 逐项追溯本轮目标和交付，说明无归属新增项及未交付要求 |
| ECI-S02 | 上下游权威合同、依赖 Issue 与实际接口 | 区分每条必要前置、可并行、stack、文件重叠和替代关系，列出冲突 |
| ECI-S03 | 新增职责与既有公共入口、实际消费者、成熟工具 | 逐项判断复用或独立实现的必要性，引用现有实现而非名称 |
| ECI-S04 | 组件责任、实际依赖及调用边 | 核对变更导致的责任/依赖方向，确认禁止方向或明确未发现范围 |
| ECI-S05 | 适用不变量与定义、生产消费合同 | 每项受影响性质给出规范来源、对象和预期，不代替运行检查 |
| ECI-S06 | 工具原生能力、固定版本及替代方案 | 对实际选型给出能力差异、成本与必要性依据 |
| ECI-S07 | 要求、AC 和交付阶段 | 列出性质到验收方法的对应关系及尚未证明的交付 |
| ECI-S08 | 原缺陷、旧裁决、变更与信任规范 | 保留原身份；变更严重度必须比较旧依据、实际变更及新影响 |
| ECI-T01 | 净差分、符号引用及消费者 | 记录真实新增/修改/删除对象和正反向引用搜索范围 |
| ECI-T02 | 源码、import/call、公共定义 | 核对真实结构与规范责任；列出重复或转发的具体实现 |
| ECI-T03 | manifest、lock、环境及构建消费者 | 沿变更依赖闭包核对各相关环境，不漏 Dagger 锁或安装入口 |
| ECI-T04 | Source、物化、转换、路径及消费者 | 记录每个相关边的输入输出与成员/身份保持，未搜索范围明示 |
| ECI-T05 | Tach、Selection、测试/Fixture、配置、源码引用 | 查找正反向静态和动态消费者，记录排除及未观察范围 |
| ECI-T06 | 生产者、Schema/文件/接口与消费者 | 逐项核对字段、路径、版本、失败及兼容，定位实际交接边 |
| ECI-T07 | 配置读取、锁定版本、启用条件 | 从配置查实际入口，确认适用值和版本，不以安装代替采用 |
| ECI-T08 | 原设计、实现、配置、导航及原记录 | 列出真实冲突或核对范围，区分现行与固定历史 |
| ECI-B01 | 实际 Host/Dagger/Hook/Actions/消费者入口 | 逐一追踪适用入口及共用实现调用，不从一个入口推断全部 |
| ECI-B02 | 真实触发、选择、阶段与独立 Expected | 核对实际执行范围、跳过条件、前置和阶段，不缩小义务 |
| ECI-B03 | 实际 argv、工作目录、Source、收集与原生退出 | 追到原生工具实际输入及输出，区分静态推断与实测 |
| ECI-B04 | 原生 Invocation/Run/Attempt/Job/Trace/Ref 等身份 | 核对实际关联键、时序、重试、多发生、并发和冲突边界 |
| ECI-B05 | 实际 Result/Report/Artifact/Enforce 与缓存消费 | 追踪同次结果、数据前置、失败/恢复，核对是否重复执行 |
| ECI-B06 | 实际凭据、权限、Secret 与失败传播 | 检查适用入口的允许/拒绝边界和非授权诊断，不扩大授权 |
| ECI-B07 | 工具配置、调用者、执行记录 | 区分登记、安装、接线、启用与实际采用，记录证明层次 |
| ECI-B08 | 实际资源路径、重复工作、缓存及已有计时 | 定位重复执行或成本来源，量化需有可比实测，不重复低价值测试 |
| ECI-V01 | 性质、Check/Test/AC 与实际结果 | 双向核对验证映射，列出缺少的独有正反例和未覆盖性质 |
| ECI-V02 | 测试源、Fixture、收集/选择、断言及替身 | 核对实际被测合同和收集范围，识别漏测与重复测试 |
| ECI-V03 | 真实原生入口、执行输入、环境、版本、结果 | 证据层次与实际证明对象对应；必要定向探针，不默认重跑 |
| ECI-V04 | 原始证据、实际候选、身份、命题和范围 | 逐项判断证据是否适用、支持或反驳，区分实测和推断 |
| ECI-V05 | 来源/资格、当前规则、独立 Required 与接纳结果 | 检查授权链缺口，原生成功、缓存和诊断不替代接纳 |
| ECI-V06 | 原始报告、结果、消费及缺失/失败情况 | 核对同次报告与原生退出，判断实际产物及消费证明 |
| ECI-V07 | 旧 Review/Finding、证据、修订差分 | 先核销旧项再发现新项；重跑、改编号或升级需有具体变化/矛盾依据 |
| ECI-V08 | 实际 AC、当前候选、CI、风险及处置 | 分别判断覆盖、性质、验收建议和权限，列出未闭合义务 |

## 4. 必须单独追踪的 ECI 关系链

四视点分别对链中适用性质建立义务，而不是第五个中心。可按实际任务选用链，不要求每个 PR 都跑所有阶段。

| 关系链 | 关键核对对象 |
|---|---|
| 目标与交付 | Goal→Issue/Constraint→Design→Implementation→Test/AC→Review/Acceptance |
| Source 到验证 | Event/Ref→Source→Profile/Expected↔Selection/Plan→实际输入→NativeResult |
| 数据与依赖 | Producer/Prerequisite→Data/Artifact→Consumer→影响及闭包 |
| 原生执行到裁决 | Execution→NativeResult→Report/Artifact/Trace→Reconciliation→适用 Evidence Admission→Coverage/Required→Provider/Release；阶段按具体场景，不能强制线性化全部原生流程 |
| 工具与实际采用 | Tool/Rule/Version→配置→真实入口→执行→原始证据→实际阶段适用 |
| 演进 | Finding→原责任任务→修订→受影响对象/关系→定向验证→结算；历史不可覆盖 |
| 跨仓 | 公共合同/版本→ECI 或外部消费者→实际接线/效果；原事实权威与实际授权各归其主 |

## 5. 结构化结果与试验校验

### 5.1 输出合同

[eci-review-result.schema.json](eci-review-result.schema.json)（Draft 2020-12，2.0）是唯一必需的审阅结果合同。旧 1.1 保留在 Git 历史，不作为新结果。用户只发普通审阅请求，无需输入 JSON、预制清册或额外提示词。

结果包含 baseline、policy、result_kind、requested_viewpoints、centers、evidence、findings、unresolved、disposition。每中心按稳定规则 ID 记录 coverage、discovery、scope、method、limitations、evidence_refs 与具体 checks；check 直接引用实际工程对象、性质、判断和证据，不复制图谱。相同事实与 Finding 共用引用。历史 Finding 保持原 ID；existing/regraded/resolved 引用原记录并说明 change_basis；新缺陷先核对原记录，不能将旧反例改名当作新发现。复用证据注明原来源和适用依据；违反须引用开放 Finding。

complete_request 表示覆盖报告面向全部实际请求，非审阅完成或验收。每个请求中心须包含全部适用规则族（包括不适用和未审记录）；partial_batch 可记录一个或多个中心的部分规则/性质，不可整体 accept。默认全四中心，明确专项范围例外。汇聚时按同基线、规范摘要、规则、对象和性质对账；实际缺失和冲突补审，不重复采集。当前不提供自动汇聚器。

### 5.2 试验入口及实际校验

规范未进入 main 时，试验宿主先明确选择本规范分支的固定提交，从该提交读取 AGENTS、本文、Schema 与校验脚本；受审 PR 的 HEAD/base 独立从 GitHub 获取，不能用 PR 中缺失或改变的规范代替试验规范。正式采用后加载 main 的适用规范，再固定实际版本。缺失规范或不能执行校验时明确报告未接入，不声称通过试验。

审阅执行者读取 §2–4 及本合同，按真实工程关系发现并履行义务，生成 JSON 制品。宿主从实际请求和 Git/GitHub 获得目标、head、base、task-kind、视点，并计算实际采用的规范文件 SHA-256；这些是宿主内部参数，不由用户填写，也不能取自模型自报结果。将摘要传入结果并用实际文件重新核对。然后从同一规范 checkout 执行现成 jsonschema 及轻量试验核对入口 [validate.py](../verification/review-experiment/validate.py)：

```bash
uv run --script docs/verification/review-experiment/validate.py review-result.json \
  --subject "$SUBJECT" --head "$HEAD_SHA" --base "$BASE_SHA" \
  --task-kind implementation
```

上例变量由宿主取得；非 PR 基线可省略 --base；显式专项才传 --viewpoints。脚本用 PEP 723 隔离依赖，不改变 ECI 项目锁文件、默认 Hook、CI 或授权路径。宿主保留实际 JSON、校验退出及输出；失败不得称为合规审阅，合法部分诊断可保留。Markdown/GitHub Review 从同一结果提取，不以 Markdown 替代机器产物，不为校验重跑工程检查。没有可执行环境时可读取规范并做审阅，但必须标记试验未通过接线验证。

校验器实际检查结构、目标/基线/任务类型、规范摘要、真实请求视点与中心、规则族、局部证据及缺陷引用、重复 ID、明显不完整/未知/阻断与 accept 冲突。退出 0 仅证明这些条件；不证明证据真实性、规则发现完备性、真实对象/性质覆盖、历史 Finding 去重或权限。后续复用 Knowledge 的独立原生事实、类型化影响及有界关系覆盖机制，不能以审阅者自报清册作为分母。对无法核实的范围记录 unknown/partial，不声称完整。当前脚本是显式试验消费者，不是自动 Agent 拦截器。

## 6. 使用入口

根 [AGENTS.md](../../AGENTS.md) 与 [Agent instructions](agent-instructions.md) 导航本文。普通“审阅 #165”默认四视点；明确限定范围才采用专项。审阅、Review 提交、工程修改、Actions、合并和发布分别遵循现有授权。不得要求用户准备输入合同；规范自动加载与结果校验由实际宿主负责，不能用更多自然语言指令代替接线。

## 7. 采用边界

Knowledge 持有未来公共规范与验证合同；ECI 持有本项目规则、事实及适用范围。#162 C 负责相关工程关系工具采用；#161 的现行关系维护、#129 缺陷身份与原任务继续有效。试验校验脚本仅用于验证消费设计，不建设新 Runtime、CLI 子系统、调度、任务存储或门禁，不引入 Knowledge 内部运行依赖。正式公共能力可用后迁移并删除重复试验实现。实际宿主自动加载、原生结构化生成、独立事实义务覆盖与真实审阅效果仍需验收，不能以合成例代替。

## 8. 实施与验收要求

| 编号 | ECI 实际验收场景 |
|---|---|
| ECI-AC01 规范与范围 | 实际 PR/Issue 绑定当前基线、历史裁决及适用工程合同，仅以普通审阅请求启动，自动获取基线及事实，输出四中心各自义务，不依赖用户预制输入或 Agent 自报分母 |
| ECI-AC02 关系发现 | 用 #165 历史案例检出 Fixture→测试消费者、Dagger helper→测试映射，以及 Source 物化/递归收集关系；区分原生探针、源码推断、真实生产验收 |
| ECI-AC03 跨入口与依赖 | 用 #168 历史根 manifest→Dagger lock、xdist→报告、#169 生产消费/多 Ref 与身份案例检查未修改对象及实际入口；不把相同 SHA 当同次 Invocation |
| ECI-AC04 公共合同复用 | 在实际新变更中定位已有 Check/Source/NativeResult/证据/报告等公共接口，发现有证据的重复实现并说明修复位置；成熟原生能力不重造 |
| ECI-AC05 输出真实强制 | 实际消费 Knowledge 固定 Schema/Validator；故意遗漏必审义务、错版本、缺引用、非法状态的产物不能作为完整审阅交付；合法部分可作为诊断 |
| ECI-AC06 增量与汇聚 | 多视点及合法分批保留原缺陷身份和独立证据；无关变更不重跑适用证据；真实新增依赖触发对应新义务 |
| ECI-AC07 工程授权边界 | 审阅符合性、实际原生验证、Trusted/Required/授权分别核对；未完成不得冒充已验收；不引入新调度、状态、门禁或图谱系统 |
| ECI-AC08 实际收益 | 在新的实际变更上比较审阅遗漏、错误建议、重复核查、证据复用及成本；历史已知缺陷回放只验证表达与特定检查能力，不证明未知缺陷发现率 |

历史案例来源：[PR #165 Review 6084403085](https://github.com/dymzz/engineering-ci/pull/165#pullrequestreview-6084403085)、[PR #168 Review 6075021820](https://github.com/dymzz/engineering-ci/pull/168#pullrequestreview-6075021820)、[PR #169](https://github.com/dymzz/engineering-ci/pull/169)。上述历史案例是验收设计输入，不是当前 PR 状态判断或本轮实测。

限制：本次修订保留原32类规则与全部工程关系；结构及试验消费合同为2.0。规则族不是数学完备性证明，未知关系发现率和实际审阅收益需通过真实试验评估。
