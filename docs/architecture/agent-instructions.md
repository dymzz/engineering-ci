# Agent 指令的作用域与职责

AGENTS.md 按适用范围组织开发指令。技术契约仍由既有设计文档和代码定义，执行配置仍由
TOML / YAML 等配置定义，当前任务进度与证据保留在任务或 PR 交付记录中。

## 分层

| 位置 | 负责 | 内容边界 |
| --- | --- | --- |
| 用户全局 `~/.codex/AGENTS.md` | 跨项目开发约定 | 授权范围、Git/验证纪律、大阶段备份和上游观察；不写项目版本、临时状态或业务架构 |
| [仓库根 AGENTS.md](../../AGENTS.md) | 本项目目标、依赖边界和开发入口 | CI/CD 职责与外部边界、当前仓库操作授权、验证/发布边界和专项指令导航 |
| `.github/AGENTS.md`（如存在） | Actions 文件的专项约束 | 远端 DAG、权限、复用接口、证据关联与 Secret 传递；不复写全部 Job/Step 定义 |
| [.dagger/AGENTS.md](../../.dagger/AGENTS.md) | Dagger 文件的专项约束 | 容器执行、SDK/Engine、原始失败与单次 pytest、Secret 生命周期；不负责开发生命周期 |

## 加载与冲突

Codex CLI 官方说明按全局、仓库根到当前工作目录逐层发现并合并指令，后出现的目录更具体。
因此不能假定所有后代目录都存在或已加载专项文件；按当前根指令及实际文件确定适用范围，
最新用户指示具有明确优先级。来源：[OpenAI 官方指令加载说明](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide#using-agentsmd)。

子指令补充范围内的规则。层级解析并不代表可以无声扩大人的授权、降低质量门禁或更改项目位置；
发现实质冲突时报告具体条目，按当前用户指示处理，不用历史轨迹或生成配置替代授权。

全局共享规则维护一处；仓库根保留必要的项目边界和明确授权，专项文件引用现有技术契约。
上游观察只使用全局定义，本仓库日志位置为 `.agent/upstream-observations.md`，不再维护中英文两份规则。

### 规范与受检对象的版本分离

开始或接续任务、切换分支或 checkout 时，分别确定当前生效的工程工作规范及用户指令、实际受检候选的源码/配置/工具/环境，以及当前任务、远端状态和既有证据的适用版本。候选检出较旧或缺少新规范时，不能退回过期工作规范；按实际权威版本读取项目工作规范及适用专项指令，保留用户授权和作用域，不因读取规范而切换或修改受检源码。候选工程合同和实现按实际受检版本核对，不能以 main 的源码、配置或运行结果替代候选。

需要确认当前远端状态时独立查询实际 Git/GitHub 权威；fetch 失败后，本地跟踪引用只作为已有快照。优先用已有只读入口（如 gh API）确认实际远端 main、PR HEAD 和相关状态；仍无法确认则标明未知，不自行扩大权限、访问未获授权目录或创建替代工作区。

用户要求先报告矛盾时，发现实质冲突须先报告，再按用户指令处理相关修改，不得在实施、提交或推送后补作报告。

## Task continuity and evidence reuse

本节负责 Agent 的工程知识导航、关系维护、证据复用与任务接续；工具采用、结构化关系提取、可重建投影和查询能力由 [#162 C](https://github.com/dymzz/engineering-ci/issues/162) 分批交付。本节不定义运行时调度、信任根、检查合同或新的任务状态系统。
工作对象包括架构规划、实施、审阅与验收；当前用户授权优先。通过以下现有权威入口取得所需关系，
不要把历史快照、候选设计或自报完成状态当成当前事实。

审阅任务遵循 [ECI 审阅要求](eci-review-requirements.md) 及其引用的 ReviewResult JSON Schema；实际范围、关系发现、证据复用和结构化输出以该规范为准。仓库内由 AGENTS.md 导航；普通 Chat / GitHub Connector 直接读取该要求的 main 版本，不假定自动加载 AGENTS.md。规范加载与基线见 §1，实际结果校验见 §5；实施验收见[审阅试验记录](../verification/review-experiment/README.md)。

### 关系导航

| 需要回答的问题 | 原有入口 / 权威 | 必须保留的边界 |
| --- | --- | --- |
| 工程目标、责任与实际入口 | [仓库介绍](repository-overview.md#目标与职责)、[架构索引](README.md)、现有 Issue | 设计方向与已交付能力区分；Issue 负责需求与剩余范围 |
| 设计、执行与跨仓关系 | [Execution Graph](../../design/execution-graph/README.md)、实际 Plan/Workflow/Dagger 接口 | 设计草稿不等于实现；Dagger、Lefthook、Actions 负责各自实际执行 |
| Producer/Validator/Consumer 的依赖及证据 | [#125 授权依赖审阅](../verification/authorization-closure/README.md)，其中的 dependency-map、source-index 和 Coverage/Gap | 固定历史版本；关系边 ID、方向、来源与证据层次一起引用，不能把接口关系当作直接调用 |
| 已知缺陷、主责与修复入口 | [验证索引](../verification/README.md)、原缺陷台账及对应 Issue/PR | 原缺陷 ID、单一主责、实际修复和候选分别保留 |
| 当前任务、实施状态与净差分 | [Issues](https://github.com/dymzz/engineering-ci/issues)、[Pull requests](https://github.com/dymzz/engineering-ci/pulls)、当前源码与 Review | 实时读取；Git stack、文件重叠、替代与业务依赖不是一回事 |
| 已完成验证与原始结果 | [验证索引](../verification/README.md)、对应 Review、Run/Attempt/Artifact 及测试 | 保留原 Source、Rule、Tool、Environment、Scope 与证明层次；不由 CI success 反推全部授权 |
| 现行关系投影及工具采用 | [#162 C](https://github.com/dymzz/engineering-ci/issues/162)、[技术清单](technology.md)、实际已采用的配置和输出 | 查询已有权威或当前派生结果；候选工具不当作已启用能力 |

现有依赖图提供机器可读关系，文档、Issue、PR 和 Review 提供定义及实际任务关系。查询时沿对应原始引用
进入所需子图，不要求每次扫描全仓。跨资源索引若确有查询需求，应从原权威关系派生并保留其修订与范围，
不维护第二套手工缺陷、任务、证据或运行时依赖登记表。结构化关系表达、自动派生与查询按 #162 C 的实际采用范围推进，优先复用 Sphinx/MyST/Needs、CodeLinks、AutoAPI、Test Reports 等原生能力；
[Knowledge RDF 互操作](../../design/execution-graph/rdf-interop.md) 是候选跨仓契约，不是普通检查的运行依赖。

### 开始或接续任务

1. 识别目标、适用责任、当前任务及验收范围；查现有 Issue、PR、Review、缺陷台账和相关技术合同。
   有确切现存归属时在原处继续；确有独立需求时说明其不同责任与实际价值，不为减少任务数量硬并不同职责。
2. 确认实际 main、目标 HEAD、原记录基线与净差分。当前问题是否已经由其他实现解决，要看实际源码、
   接口、原生证据与未完成的独有差分，不仅凭标题、文件相交或 Git ancestry。
3. 沿所需 Producer/Consumer 关系确认实际前置条件。分别记录真实接口/数据/授权依赖、Git stack、
   文件重叠、替代与同根缺陷；只阻塞确实依赖未满足条件的工作。未知关系不是不存在的证明。
4. 实施前按预期变更、受影响关系、合同及验收要求确定验证义务；执行前按实际净差分更新义务，遵循[工程验证入口与选择](../../guides/common-tasks.md#工程验证入口与选择)完成环境绑定、入口选择与证据对账。复用仍适用的原生证据，按 Source、规则、工具/实现、环境、Scope 与证明层次比较；实际变化、矛盾、未知或明确要求触发定向验证。组件合同、交接合同、来源认证、生产接线是不同层次。新发现影响或缺口时更新义务，未取得所需证据不得缩减义务、替换证明层次或用无关成功结果宣称完成。已有证据能证明同一问题且无实际变化时，不重复读取全仓、执行历史矩阵或提交同义 Review。
5. 完成阶段后更新原任务和必要的权威文档/图关系，保留历史证据的原范围；新增生产证明回原验证入口，
   缺陷结算回原台账，不通过新增记录冒充实施完成。

### Graph maintenance on every submission

每次提交（Git commit、PR 创建/更新、Review、验收裁决）都必须维护受影响的工程知识图谱与任务交接，
而不是等到阶段结束才补记。提交前按实际净差分和当前证据确定影响范围；在同一提交或明确链接的配套更新中
完成相应关系及原权威记录的增量维护：

- **语义关系由 Agent 随实际工作维护。** 对新增、改变、失效、替代的目标/需求/设计/实现/Issue/PR/缺陷/证据关系，在原权威记录中维护类型、方向、引用、适用修订、事实依据和未闭合条件。分别处理真实工程依赖、Git stack、文件重叠、替代关系，不从名称推断关系。
- **机械事实由原生权威和已采用工具派生。** Git/GitHub 提供 HEAD、Merge SHA、Issue/PR 状态、Run/Attempt/Artifact 和提交关系；源码/API/模块关系及测试验证关系优先使用实际采用的原生分析和报告能力。按实际查询读取当前事实，不人工重复登记。#162 C 负责评估并采用 Sphinx-Needs 等结构化关系、CodeLinks/AutoAPI、Test Reports、needs.json 及适用 RDF 能力；不是本规则本身已实现这些功能。
- **现行投影随实际影响刷新。** 已有可用投影时按其原生入口增量刷新或重建；固定历史快照（包括 #125）保持原样，后续事实通过明确的新修订/增量关系引用原节点或边。查询时核对投影的来源、修订、范围及新鲜度；无法证明适用时回到原权威，不以缺边推断无依赖。
- **Handoff 与结算回原处。** 任务状态及审阅结论更新原 Issue/PR/Review；缺陷处置更新原台账，技术定义更新原设计，原生证据保留固定引用。已有权威关系直接引用或派生，不要求第二次手工记录。
- 提交记录可追溯到实际关系增量和 handoff（含下一动作与触发条件）。无实质关系或证据变化时，简要记录核对范围及依据即可；不为形式要求增加图边、重复审阅或重跑适用证据。
- 未采用统一投影工具时，直接使用上述权威资源并维护可追溯语义关系，已有投影照常维护。实际缺失的派生或查询能力由 #162 C 的原任务记录承接；不把反复声明“待同步”当作维护，也不声称未生成的机器可读投影已更新。工具采用按批独立交付，本节规则立即可用，不以 #162 C 完成为前置条件。

本规则约束每次提交的知识维护，不改变实际工程质量门禁、用户授权或既有检查/发布流程；
图谱核对及验证范围随实际影响确定，不要求每次遍历全仓。

### Agent 工作交接（handoff）

交接使用对应 Issue / PR / Review 或已有总体任务记录；若这些记录已足以接续，直接引用而不另写一份。
接续入口至少能定位：目标/任务与实际责任、当前基线、最后有效裁决、可复用的原始证据与其范围、
未闭合条件、下一接手动作及触发条件。存在不同基线或相互矛盾证据时，应保留区别和原链接。
跨任务阶段总结可在原总体任务留一个索引，不能把历史工作底稿整体复制成新的权威状态。

例如 #159 的 [历史 Review](https://github.com/dymzz/engineering-ci/pull/159#pullrequestreview-6061982053)、
[最终验收 Review](https://github.com/dymzz/engineering-ci/pull/159#pullrequestreview-6062446789)、
[联合 CI](https://github.com/dymzz/engineering-ci/actions/runs/45667215826) 和
[合并 Handoff](https://github.com/dymzz/engineering-ci/pull/159#issuecomment-7170199692) 已形成完整接续链。
从历史缺陷进入最终裁决，再读取实际合并提交及当前 main，区分 #160 已共同验收的 Source 合同、
#158 预上传采用与 #146 原生解析的后续整合。不重新抄录全部历史，也不把该固定案例当作未来任务状态；
有实际变更、新证据、矛盾或用户要求时继续审阅受影响关系。

Agent 工作交接不同于 [Local → Actions Handoff](local-actions-handoff.md)、Plan 生成物交付
（[Plan 依赖验证](../verification/plan-dependencies/README.md)）或 Receiver 生产切换
（[CI/CD 遥测](cicd-telemetry.md)）。它只引用工作与证据事实，不产生 Trusted/Required/Release 授权，
也不引入任务调度器或 Handoff Store。

## 操作与执行的区分

Agent 获得当前任务的 PR 自动合并授权后，可以通过 Git/gh 执行实际操作；
`release.py` 只请求、查询与等待 Release Workflow；Git/PR 使用原生命令。正式 Release 保持人工主动请求，
未覆盖的私有 Git 来源仍由安全门禁阻断，不因修改 AGENTS.md 自动获得豁免。
备份操作见[阶段分支备份](../../guides/common-tasks.md#大阶段分支备份)。

文档中的规划不能被记录为已实现能力。
