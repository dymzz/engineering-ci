# ECI 结构化工程审阅设计要求

状态：ECI 审阅要求及过渡版输出合同，供实际审阅直接使用。公共《可执行工程规范知识与结构化工程审阅 — 总体设计》尚待 Engineering Knowledge 正式发布；本仓不复制其草稿。本文附可独立校验的过渡版 ReviewResult Schema（见 §5）；不另立长期通用 Schema 权威、审阅 Runtime、关系权威或授权机制。2026-11-27 核对 ECI main `6c5efb9691e71b36a62a185dcb918cdca4ba3e5b`；实际审阅必须重新固定所审对象的有效基线，而非长期使用这个 SHA。

## 1. 目的、范围与权威

ECI 审阅必须以工程目标、实际对象、类型化关系、适用约束和验证义务为中心，发现需求遗漏、职责错误、重复建设、影响遗漏、Source/Execution/Result 绑定错误、实际接线缺失、验证证据不足及不必要成本。不能仅以 PR diff、文件列表、既有缺陷或 CI 状态作为审阅范围。

本文件定义 ECI 的实际工程约束及适用审阅义务，不重新定义 Knowledge 的通用 ReviewProfile / ReviewResult、身份、证据和覆盖语义。四视点为 Specification、Structure、Behavior、Verification。历史与演进、文档一致性、安全与信任边界贯穿各视点。各视点独立起点、共享事实与证据；同一缺陷保持一个身份。

适用对象包括架构设计、Issue/实施计划、PR、修订、验收及明确授权的全仓审计。区分任务类型：设计审阅检查可实施合同、成熟方案、职责、接口和验收，不要求未实施的功能已经运行；实现审阅检查实际实现及受影响关系；验收审阅核对当前候选、原缺陷和实际适用证据；全仓审计以声明的仓库及工程关系范围为输入，而不局限于某 PR。对其他仓库按具体公共接口、实际消费者及授权读范围追溯，不擅自扩大审计对象、执行或写入权限。

ECI 现行权威入口（固定基线）：

- [AGENTS.md](https://github.com/dymzz/engineering-ci/blob/6c5efb9691e71b36a62a185dcb918cdca4ba3e5b/AGENTS.md)、[Repository overview](https://github.com/dymzz/engineering-ci/blob/6c5efb9691e71b36a62a185dcb918cdca4ba3e5b/docs/architecture/repository-overview.md)、[Agent instructions](https://github.com/dymzz/engineering-ci/blob/6c5efb9691e71b36a62a185dcb918cdca4ba3e5b/docs/architecture/agent-instructions.md) 确定职责、任务接续、关系维护及证据复用。
- [Common tasks](https://github.com/dymzz/engineering-ci/blob/6c5efb9691e71b36a62a185dcb918cdca4ba3e5b/guides/common-tasks.md) 与实际 CLI/配置确定标准验证入口；不由审阅另建入口。
- [Execution Graph](https://github.com/dymzz/engineering-ci/blob/6c5efb9691e71b36a62a185dcb918cdca4ba3e5b/design/execution-graph/README.md) 是整合设计草稿；实际合同以原架构文档、Issue 裁决、现行代码与原生证据分层核对，不将候选设计视为已交付。
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

下列 `ECI-Sxx/Txx/Bxx/Vxx` 是拟议规则族标识，非缺陷编号或正式 Knowledge 词汇。根据实际发现的工程对象、关系与适用规则实例化为对象/关系/性质级 ReviewObligation；不能每族仅打一个勾。每族均需适用性判断和来源；不适用与未知不得删去。不同视点对同一对象的不同性质分别记录。

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

## 5. 结构化输出、覆盖和验收要求

### 5.1 唯一必需的机器可读输出合同

使用 [**`eci-review-result.schema.json`**](eci-review-result.schema.json)，JSON Schema Draft 2020-12，ECI 过渡版 **1.1**，具有完整本地 `$defs` 引用闭包。它是模型结构化输出目标及实际审阅结果校验合同，不是用户输入格式。原 1.0 的独立输入 Schema 已退出本设计的使用路径；无需准备、提交、校验或引用 `review-input.json`。本次是破坏性合同修订，旧结果作为历史保留，不能直接当作 1.1 合规结果。Knowledge 正式公共资产发布后通过兼容性核对迁移公共部分，ECI 保留项目规则。

结果由审阅实现产生，直接记录实际基线（`baseline`）、审阅类型（`task_kind`）、请求视点、实际范围（`scopes`）、32 类规则的适用性（`rule_family_applicability`）、本轮识别的具体义务（`required_obligations`、`discovery_obligations`）、对象/关系/约束、实际审阅发生及各中心判断、发现、证据、缺陷、未完成项与处置建议。已有工程事实允许直接引用原权威，不复制源码或建立第二套图谱。内部自动收集的事实/义务集合可保存或引用，但不要求生成单独输入文件，不要求审阅开始前已经穷尽所有关系。

默认完整关联审阅覆盖四视点，完整请求响应 `result_kind=complete_request` 中每个请求中心必须存在、至少有判断/发现/未完成记录，并登记对应全部八类规则（四中心共32类）；适用/未知规则引用具体义务或发现义务，不适用须有证据。`complete_request` 表示报告面向完整请求范围，**不代表审阅已完成或工程已通过**。明确限定视点时按实际请求；不可自行缩小全量审阅。`partial_batch` 支持一次一个或多个中心、同中心按部分义务分批：记录 `batch_ref`、`assigned_viewpoints`、实际审阅发生与涉及义务；只须登记该批涉及规则，不得给出整体 accept。汇聚完整结果必须覆盖全部请求规则族。单 Agent 四视点不得冒充四个独立 Agent。

`disposition` 记录工程处置建议、阻断、非阻断交接和剩余义务，不授予权限。未审/部分审阅不得声称满足；违反须引用 Finding；原生实测须引用实际执行；复用须引用原证据并说明适用性。未知及受阻要据实记录，不为满足 Schema 填造事实。核心字段禁止未定义属性，扩展不能替代核心约束。

### 5.2 实际使用与验证

1. 正常提出审阅请求，例如“审阅 #165”或“对 ECI 做职责与复用审计”。现有审阅入口按任务自动加载本要求和固定结果 Schema，并按 §2–4 查询 Git/GitHub、源码、配置、原生工具和历史证据。没有手工输入准备步骤或额外提示词模板。
2. 实际审阅实现根据四视点主动发现关系、生成并履行义务；共享已验证事实，持续追加实际发现的对象、约束与义务。支持模型原生结构化输出时使用兼容投影；不支持时生成 JSON 文件，由实际消费者运行完整结构校验。
3. 对实际结果执行：

```bash
python -m jsonschema -i review-result.json eci-review-result.schema.json
```

4. 实际消费者使用固定采用规则、真实审阅目标、原生事实/已取得的独立事实清册和结果进行语义核对：核验当前基线、请求视点、32类规则适用性、实际已知义务及发现义务、引用、证据、冲突、原缺陷与新增关系；计算缺失义务，定向补审。不得以结果自报义务作为唯一分母，不得因缺少内部清册阻止正常发起审阅；无法独立确认的发现范围或覆盖必须标未知。内部事实清册可由工具自动产生及持续更新，既有原生事实直接复用，不重复收集或强制存档。

本次仅交付 Schema 结构校验能力。跨记录语义 Validator、自动事实/义务发现和实际消费入口的强制接线尚未实现；不能以 Schema PASS 证明义务覆盖、来源真实性或工程验收。当前审阅仍须据实进行上述核对。

### 5.3 义务、证据与覆盖语义

以对象、关系、性质、约束、视点和基线为义务身份；例如 `ECI-T05 × selection-app fixture→test_reconciliation × consumer_coverage`。同一义务多路径去重，不同性质及范围不混同。审阅实现可以在执行中不断发现新义务；已知必审集合由适用规则和原生事实约束，而不是用户输入或审阅者自报决定。发现记录区分实际已搜索范围、方法、来源、方向、排除和未观察关系。

实际覆盖由可独立核对的已知义务与审阅记录差分计算，区分发现范围、已知义务、部分/未审、已审未知、满足、违反、不适用及冲突。已知义务100%不证明未知关系穷尽；空分母有证据才为已知空，不能自动通过。无法证实独立分母则明确“覆盖未确定”，不得伪造完整率。无完整发现证据时仍可交付有价值的缺陷与部分审阅诊断，不冒充完整。各中心不能因其他中心发现 P0/P1 停止。

分批按同一实际审阅基线、规范版本、实际发生及义务身份汇聚，引用共享事实，不重复整份数据；缺失与矛盾定向补审，不多数票或 last-wins。Markdown/GitHub Review 与覆盖矩阵从实际记录派生；原缺陷、任务、证据及技术定义维护原权威。按真实风险复用有效证据，不为报告重复执行。

## 6. 使用入口与默认行为

用户仅提出目标、必要范围或实际操作意图。默认“全量关联式审阅”由审阅实现自动采用本文件四视点及32类规则、获取事实、按结果 Schema 输出；用户可明确选择设计审阅、实现审阅、验收、全仓审计或特定中心。审阅、提交 Review、修改代码、触发 Actions、合并及发布各有实际授权边界。无需每次粘贴长提示词、手工制作 JSON 或预先分析对象关系。规范应通过现有入口自动加载并由实际工具/消费者强制验证；文档不是替代接线的提示词。

## 7. 采用位置、可执行接口与强制边界

现有入口为根 [AGENTS.md](../../AGENTS.md) 的 Engineering Review 导航及 [Agent instructions](agent-instructions.md) 的任务接续导航，均指向本文；本文 §5 引用同目录结果 Schema。不在 AGENTS 重写公共规范或工程图谱。#162 C 承接现行关系派生及实际消费；#161 的关系维护、#129 的缺陷身份、各原任务的接口和验收继续有效。当前交付是规范资产与导航，实际自动校验消费仍按 §5.2、§8 验收。

Knowledge 实际发布固定 Schema、规则、可执行 Validator 后，ECI 通过公开接口消费，无需依赖 Knowledge 内部 Python 包、常驻 RDF4J、Vesryn 或 Workflow。适用原生验证器可以独立执行固定资产；如需跨仓执行，沿现有公开 Dagger/工具接口。ECI 的工程事实、适用规则和授权由 ECI 持有，不复制通用校验。

实际消费约束：审阅入口解析普通请求、自动加载规范、获取工程事实并发现义务；Agent 生成单次/分批 ReviewResult；`validate` 对结果执行 Schema 与实际规则/事实校验；必要时 `combine`；`coverage` 派生缺失与冲突；`impact` 复用 #18 定向复审。内部类型化参数按实际接口需要定义，不设强制输入 Schema 或固定准备流程。逻辑名称引用通用设计，不是新增六个 ECI CLI。正式采用后，在实际审阅结果消费/验收入口拒绝不合规或覆盖不足的完整交付，允许明确标注的部分诊断/Review；现有 ECI 工程验证、Required 和 PR/Release 权限不被替代。不要把审阅过程强行加入每次 pre-commit 或普通 pytest。

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

限制：本文件从已读更新总设计及 ECI 固定权威入口派生规则族；已形成 §5 的本地过渡版 JSON Schema 并实测其结构约束；尚未逐项映射到正式 Knowledge 词汇、已采纳 Profile、公共 Schema 或跨文件语义 Validator，也未对 ECI 全仓进行新审计。四视点与规则族是拟议采用要求，不是数学完备性证明，不保证发现所有未知关系。本规范资产的采用不代表自动发现、语义校验或消费入口已验收；实际提交及验证事实由对应 Git 提交和原任务记录维护。
