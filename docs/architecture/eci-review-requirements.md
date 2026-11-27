# ECI 审阅要求

## 1. 入口与基线

本文件是 ECI 设计、实现、PR、复审、验收及全仓审计的直接规范入口。仓库内 Agent 由 AGENTS.md 导航；普通 Chat / GitHub Connector 不假定自动加载 AGENTS.md，应直接读取 [本文件的 main 版本](https://github.com/dymzz/engineering-ci/blob/main/docs/architecture/eci-review-requirements.md) 及 [结果 Schema](eci-review-result.schema.json)。用户只指定目标、必要范围和操作意图，不提供输入 JSON 或对象清册。

开始审阅前必须实际查询仓库 main HEAD（Git ref/branches/main），再固定规范文件所在提交、受审对象及其状态、PR HEAD、PR base 元数据、实际 merge base、净差分及本轮修订差分；需要时查实际联合候选 Merge SHA。main、PR base、merge base 和规范版本不得混同；不能用旧 checkout、PR base 或上轮会话代替当前 main。规范与受审版本分别固定，具体证据使用对应版本。无法获取时说明缺口，不声称已完成加载。已合并/关闭对象只按实际状态追溯，不作为待合并候选。

默认分别执行四视点；仅用户明确限定时缩小视点。设计审阅核对目标、接口、可实施性与验收，不要求尚未实施的运行证据；实现审阅追踪实际变更及影响；验收针对当前候选、原缺陷与适用证据；全仓审计按授权范围遍历。审阅不自动授权写入、提交 Review、触发 Actions、合并或发布。

## 2. 共同方法

以工程目标、对象、关系、性质为中心，不限于 diff 或已有缺陷。各视点从各自起点发现关系，独立判断，共享事实和证据。对实际新增、修改、删除对象发现正反向依赖、实际入口、共享资源及配置、动态消费者、外部接口与预期缺失关系；必要时超出 diff，不无依据扩大到全仓。使用 Git/GitHub、源码/API、配置、已采用原生工具（Tach、Selection、pytest/JUnit/ReportLog、Dagger、Lefthook、Actions、OTel 等）和原任务事实，候选/仅安装工具不作已启用能力。

每条规则按具体对象及性质履行，而非打勾；记录实际搜索范围、方向、版本、方法、排除及局限。仅有充分有界搜索依据才能认定无关系；静态搜索不证明动态依赖完备。发现范围未知或未完成就标 partial/unknown；不适用须有证据。严重缺陷不终止其他视点，长度不足按视点或义务分批。

先查原 Issue、Review、缺陷及证据，再比较实际修订。保留原 Finding 身份；区分新问题、既有问题、修订核销、严重度调整，说明实际变更或新影响。不重复有效证据；仅因实际变化、矛盾、未知或明确请求补充定向验证。历史、文档一致性、安全/信任贯穿四视点。原语义关系和任务记录按[任务接续与图谱维护](agent-instructions.md#task-continuity-and-evidence-reuse)维护，机械事实从原权威派生，不复制账本。

## 3. 四视点规则

以下 32 个稳定规则 ID 保留现有合同。每行同时定义对象/关系、检查性质与完成条件；规则数量不等于覆盖完备。各规则须记录具体 checks 及实际发现范围。

### C1 Specification — 从目标、需求、裁决、设计、AC 与公共合同出发

| 规则 | 必审关系、判断及完成条件 |
|---|---|
| ECI-S01 目标与范围 | 双向追溯 Goal→Requirement→Issue→Design→Implementation→AC/Acceptance；查遗漏、过度收缩/扩张、无目标新增及未交付项，逐项目标对账。 |
| ECI-S02 权威合同 | 查上下游合同、历史裁决和冲突；用实际接口区分必要前置、并行任务、Git stack、文件交集及替代关系，不无据阻断。 |
| ECI-S03 职责与复用 | Component→Responsibility→Public Interface→Consumer；先定位既有公共合同、实现及成熟原生方案；新增/替代、平行解析器、Runner、Coordinator、FSM、缓存、Artifact Store、门禁或转发包装须有必要性或重复证据及可复用入口。 |
| ECI-S04 组件边界 | 查实际职责和依赖方向：ECI 工程验证/证据/交付，Dagger DAG/容器/缓存，Actions 编排/权限，Lefthook Hook 调度，Log/Tool Runtime 原边界；禁止直接/传递依赖 Vesryn/Workflow。 |
| ECI-S05 语义与不变量 | 定位适用 CheckDefinition、Source、Profile、Expected、Plan、NativeResult、Reconciliation、Trusted、Required、Release 定义；逐项受影响性质给出预期及来源，选择不得缩小独立 Required，诊断/原生计算不等于授权/受信复用。 |
| ECI-S06 工具与选型 | 核查原生能力、版本、限制、替代方案、实际收益；评价抽象、依赖、配置及兼容层必要性，不以防御为由延期合理设计或默认最小实现。 |
| ECI-S07 交付与验收 | 建立性质→AC→验收方法关系，区分实现、接线、验证、采用、授权及收益；列独有正反例、有效证据和未证明交付。 |
| ECI-S08 历史与安全 | 原问题→修订/替代→现行合同；核对版本、导航、权限、Secret、信任及规范强度；保留缺陷 ID，升级/降级说明旧依据、变化及新影响。 |

### C2 Structure — 从实际源码、配置、变更对象和上下游消费者出发

| 规则 | 必审关系、判断及完成条件 |
|---|---|
| ECI-T01 变更与引用 | 文件/模块/函数/类型/配置/Schema/API 新增修改删除→定义↔调用/读取/引用及外部消费者；记录正反向搜索，区分真实依赖和在途 PR 文件重叠。 |
| ECI-T02 模块与职责 | 实际 import/call、依赖方向、循环、重复、纯转发；核对结构与声明，定位公共实现/适配必要性，区分工具可见边、人工语义及未观察边。 |
| ECI-T03 构建依赖 | manifest→lock→环境→包→消费者；沿变更闭包核对根项目、.dagger、SDK、固定依赖、CI 安装及消费者环境，不漏 Dagger lock。 |
| ECI-T04 Source 与 Data | Event/Ref/Index/Tree/Worktree/Directory/Overlay/配置→校验→物化→读取；逐相关边核对身份、内容、ODB、per-ref、路径/链接/子目录、排除、数据前置；目标物化与历史基线证明分开。 |
| ECI-T05 选择与影响 | 变更→静态/动态/非代码消费者→Check/Test→Expected/Plan；查 Tach、Selection exact/prefix、Fixture、conftest、插件、Dagger helper 及遗漏边，记录方向、排除和未知。 |
| ECI-T06 契约交接 | Producer→接口/Schema/Artifact→Consumer；逐实际边核对字段、路径、命名、版本、缺失/失败、兼容及原始引用；原生 report/receipt/trace 与派生查询真实交接。 |
| ECI-T07 配置与集成 | Profile/Check/config→入口/Workflow/下游；查配置解析、默认值、固定版本、规则来源、Source/消费者版本和激活阶段，区分配置与启用。 |
| ECI-T08 文档与关系 | 设计、代码、配置、Guide/AGENTS、Issue/PR、缺陷及现行投影对照；定位冲突、路径/Secret 出口和信任边界，历史快照不改写。 |

### C3 Behavior — 从实际入口、原生工具、事件与执行实例出发

| 规则 | 必审关系、判断及完成条件 |
|---|---|
| ECI-B01 入口 | 查适用 Host CLI、显式 Verify、pre-commit/pre-push、Dagger check/API/Plan、Actions、Release、外部消费者；逐入口追到共享实现及反向调用，不以 Host 修复推断 Dagger。 |
| ECI-B02 选择与阶段 | Event→Profile→独立 Expected/Selection/Plan→Check；核对 Source、范围、数据前置、增量升级、跳过/不适用；按合同区分 Task-only 和需要 Pipeline Root 的终端阶段。 |
| ECI-B03 输入与原生结果 | 追实际 argv、环境、工具版本、目录/测试目标、物化/导出/递归收集与 NativeResult inputs、原生退出；区分未启动、失败、取消、缺报告，源码推断不冒充实测。 |
| ECI-B04 身份与时序 | Invocation/Run/Attempt/Job/Trace/Span/Session/Engine/Ref/Result；查真实关联键、父子、生产消费、重试、并发、取消、合法多发生；冲突限同身份角色和范围，不凭相同 SHA/Plan、最近文件/时间绑定。 |
| ECI-B05 数据结果闭环 | Check→同次 NativeResult→Report/Artifact/Enforce→Reconciliation/Consumer；核对缓存、失败/部分输出、导出/上传/恢复及数据前置；报告不重复计算，Dagger cache 非 Trusted，受信复用不切断下游产物。 |
| ECI-B06 权限失败 | Git/Runner/Provider/Workflow/Release 的实际凭据、权限、Secret、Source 及失败传播；观察、遥测、格式通过不授予 Required/发布，不误拒合法消费者和独立执行。 |
| ECI-B07 实际采用 | Tool/Rule→配置→真实调用→执行→证据；分别证明登记、安装、实现、配置、接线、启用及验收；默认 Hook、显式工具、affected/CI 区分，不以无关性能前置阻断。 |
| ECI-B08 资源成本 | 查 Process/uv/Git/Engine/传输/pytest/worker/外部工具/导出、缓存及重复工作；墙钟、累计、阶段、并行分开；收益须可比实测且不损义务，计时接线不冒充优化。 |

### C4 Verification — 从应证明性质、Check/AC、测试及原始证据出发

| 规则 | 必审关系、判断及完成条件 |
|---|---|
| ECI-V01 义务覆盖 | Requirement/Constraint↔Test/Check/AC↔实际验证双向对账；查独有正反例、失败/边界及未证明性质，测试数/全绿/格式不代替特定验证。 |
| ECI-V02 测试收集 | Test/Fixture/参数/共享配置/Mock/断言→被测合同；实际收集、选择、映射、未执行/deselected、setup/teardown、subtests、并行、隔离；识别漏测及重复低价值测试，保留独有验证。 |
| ECI-V03 原生真实性 | 实际入口、Source/配置/版本/环境/Scope→原始退出及结果；替身不冒充 Dagger/Collector/Provider/Hook 原生实测，按变化与风险定向验证。 |
| ECI-V04 证据适用 | 原始证据→命题；核对实际候选 checkout、Run/Attempt/Job/Artifact、身份/时间/范围；直接读取、原生实测、复用、推断、未知及反证分别记录。 |
| ECI-V05 接纳与 Required | 来源/资格、适用输入、当前规则、实际执行、覆盖及独立 Required 集合；区分 Selector、cache、遥测、Schema PASS、原生成功和受信接纳；按场景核对后置终端条件。 |
| ECI-V06 报告交付 | 原始 Report/Receipt/Trace/Artifact→实际消费/裁决；核对同次退出、完整/中断/缺失/封存失败、合法多执行及覆盖，不重复扫描或用诊断替代退出。 |
| ECI-V07 历史复验 | 原 Finding→修订→实际差分→原适用证据/必要新证据→结算；保留身份，核销不重开，重复探针或严重度修订说明变化/矛盾/独有价值。 |
| ECI-V08 验收风险 | 当前候选、适用 CI、AC、真实阻断、非阻断交接及未知边界；分别判断覆盖、性质、工程验收与有权动作；成本证据可比，CI 绿不掩盖逻辑错误。 |

## 4. 权威关系导航

按实际影响追溯以下关系，不另建线性流水线或图谱：目标→需求→实施→验收；Event/Ref→Source→Expected/Selection/Plan→实际输入→NativeResult；Producer/Prerequisite→Data/Artifact→Consumer；Execution→Result/Report/Trace→Reconciliation→适用 Admission/Required→Provider/Release；Tool/Rule/Version→配置→实际入口→证据；Finding→修订→影响→复验→结算；公共合同/版本→跨仓消费者→实际采用。各视点判断不同性质。

项目职责与原合同：[仓库介绍](repository-overview.md)、[架构索引](README.md)、[任务接续](agent-instructions.md)、[验证入口](../../guides/common-tasks.md)；[Execution Graph](../../design/execution-graph/README.md) 是设计草稿，不能当作已实施事实。相关权威任务 [#52](https://github.com/dymzz/engineering-ci/issues/52)、[#66](https://github.com/dymzz/engineering-ci/issues/66)、[#86](https://github.com/dymzz/engineering-ci/issues/86)、[#63](https://github.com/dymzz/engineering-ci/issues/63)、[#61](https://github.com/dymzz/engineering-ci/issues/61)；[#125](https://github.com/dymzz/engineering-ci/issues/125) 历史依赖快照、[#129](https://github.com/dymzz/engineering-ci/issues/129) 原缺陷、[#161](https://github.com/dymzz/engineering-ci/pull/161) 关系维护、[#162 C](https://github.com/dymzz/engineering-ci/issues/162) 现行工具采用。按具体关系读取当前适用版本，不遍历无关历史或推断已交付。

## 5. 输出与校验

### 5.1 实际输出

读取并使用 [eci-review-result.schema.json](eci-review-result.schema.json)（Draft 2020-12，2.0）。生成实际 review-result.json，再由同一记录给出人类可读 Review；不要仅在 Markdown 末尾附四中心概括。各中心按规则记录 coverage、discovery、scope、method、limitations、evidence_refs 和具体 checks（对象引用、性质、判断、证据/缺陷引用）。引用原事实，不复制图谱。Finding 含预期、实际、影响、原主责、可复用实现与修订、验收及历史依据；复用证据须说明适用性，违反须引用开放 Finding。

complete_request 须覆盖请求中心的全部规则 ID，包括未知/未审和有据不适用；它不代表实际完成或通过。partial_batch 允许多中心/同中心分批，不能整体 accept；汇聚按同基线、规范摘要、规则、对象、性质对账，共享事实，缺失/冲突补审。未发现范围不能凭自报证明完整。

### 5.2 实际校验

由执行者从实际请求和 Git/GitHub 获取 subject、head、base（受审比较基线，区别于当前 main）、task-kind、视点；从实际读取文件计算 requirements/schema SHA-256，不从结果反推校验参数。使用同一规范版本的 [validate.py](../verification/review-experiment/validate.py) 和原生 jsonschema：

```bash
uv run --script docs/verification/review-experiment/validate.py review-result.json \
  --subject "$SUBJECT" --head "$HEAD_SHA" --base "$BASE_SHA" \
  --task-kind implementation
```

非 PR 可省略 --base；明确专项才传 --viewpoints；任务类型按实际请求。Chat 无 checkout 时，可通过连接器读取固定提交的 MD、Schema、脚本到执行环境，保持上述相对路径，用可用 Python/jsonschema 实际执行同一校验，不要求另建工作树或用户上传。缺文件、工具或执行能力时如实说明，不伪造校验结果。

交付时报告规范提交、main、PR base/merge base/HEAD（适用时）、实际 JSON 文件及校验退出/结果、四视点覆盖和未知；校验未执行或失败，不称为合规审阅，保留有价值的部分诊断。退出 0 仅证明结构、请求/基线/摘要绑定、局部引用及声明覆盖一致性，不证明真实发现完备、证据真实性、工程性质或授权。

试验消费者不改变 ECI 依赖/锁、Hook、CI 或发布门禁。通用规范与验证由 Knowledge 持有，正式公共资产采用后迁移；宿主自动加载、原生结构化生成及独立事实覆盖仍需验收。实施验收和历史试验见[审阅试验记录](../verification/review-experiment/README.md)。
