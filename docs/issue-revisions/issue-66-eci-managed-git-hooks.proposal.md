# ECI #66 — ECI-managed Git Hooks（增量设计提案与复审裁决）

> **权威来源：** [ECI Issue #66](https://github.com/dymzz/engineering-ci/issues/66)（2026-11-12 已纳入真实 Source/Execution/Hook 生产方契约），以及已合并 #67/#70/#71/#73/#79/#88 的实际公共实现。本 PR #72 仍是 **Draft 的增量设计/决策记录**，不作为第二份长期规范、独立实现要求或 Git Hooks 已交付声明。关联 [Knowledge #68](https://github.com/dymzz/engineering-knowledge/issues/68) / [PR #78](https://github.com/dymzz/engineering-knowledge/pull/78) 属消费者端迁移。

**本次裁决（针对 2026-11-12 Review）：** 保留通用 Git Hooks 生命周期、Source/事件/输入失效契约，以及广泛的跨语言原生工具适用性规划；撤销“未经事实比较即强制某一种 Hook 控制器”“一次实施所有候选工具”的未证实前提；**多工具条件触发、并行、生命周期和可诊断性是完整需求，不因避免重复调度而被收缩**。当前最高优先级是 #66 P0 **实际工具读取目标 Index/Commit/Push Source 字节**与 P1 CLI/Dagger/Actions 的一致生产事实，不能以 `git write-tree` 相等、`glob` 触发或 Plan 字段相等冒充真实执行。Hook 选择与本地工具扩展不阻断该生产方的独立验收。

**阅读原则：** 下列 CLI/YAML 名称为拟议接口/示意；以实际合并代码为准。审定后只把仍未纳入 #66 的增量条款回写权威 Issue，不在两个文件长期维护相同需求。

## 1. 工程目标与责任裁定

- **ECI 拥有 Git Hook 的通用实现和分发**：pre-commit/pre-push 原生入口、安装/升级/诊断/卸载、Git 事件与受检 Source 适配、原生检查/前置条件执行、结构化结果和失败传播。下游不得继续维护独立的通用 `hook_adapter.py`、Index/Push 快照引擎、独立 Check 调度/报告判定器。
- **下游拥有项目策略**：使用哪些 Git 事件、哪些实际适用 Checks、代码/非代码测试映射、全量升级条件、Identity Commit、wheel/CLI 命令及配置、特定工具版本。ECI 仅消费配置和执行公共机制，不嵌入 Knowledge 的 RDF/Manifest/业务数据语义。通用身份、真实执行输入、适用性与 Tool Result 由已有 `src/engineering_ci` 公共包生产，不能复制旧 `eci_core` 实现。
- **统一消费约定**：采用 ECI Hooks 的仓库由 ECI 安装器管理公共入口，不能要求每仓复制/维护 800 行适配代码。仓库可以保留自己的声明性配置和确有证据必要的项目专用 Hook；不得与 ECI 通用入口重复运行同一验证。
- **强制性分层**：在仓库的工程策略中可把安装并运行 ECI Hooks 定为开发工作流要求；但本地 `core.hooksPath`、`--no-verify` 和用户配置使 Hooks 不具备安全意义上的不可绕过性。合并/Release 真正强制性仍由原生 Actions、ECI #61 的 Required 完整性和经过核实的 GitHub 保护设置实现，不由 Hook 成功与否伪造 GitHub Required 状态。

## 2. ECI 交付接口（候选 CLI，尚未实施）

统一使用现有 `engineering-ci` CLI/包和 `src/engineering_ci/{selection,execution,hygiene,sources,plan_contract}.py`，在真实缺口处扩展，不创建第二套 Hook Runner、Check Registry 或重复执行算法。用户可见接口候选：

```text
engineering-ci hooks install --repo .
engineering-ci hooks status --repo .
engineering-ci hooks update --repo .
engineering-ci hooks uninstall --repo .
engineering-ci hooks run pre-commit --repo .
engineering-ci hooks run pre-push --repo .
```

### 2.1 生命周期生产与调度器选择

- **职责先于产品：** ECI 的 `hooks install/status/update/uninstall` 是面向消费仓库的公共管理接口，负责固定实际可执行来源、安装路径、冲突诊断、配置状态、升级与恢复；Git 原生薄 Hook 负责事件传递；Lefthook 可承担成熟的事件/工具 Job 选择、依赖或并行调度、诊断和生命周期管理。ECI 仍负责 Git Source/Selection/原生 Result 的权威语义，不能把 Lefthook 简化成只能传递事件。唯一实际管理者必须可核对，不同时让 ECI 与 Lefthook 独立覆写同一 Git Hook 文件。
- **两个候选，不双重安装：** (a) ECI 管理的零逻辑 Git 原生薄入口，直接传递 pre-commit 的 `GIT_INDEX_FILE` 与 pre-push 的完整 stdin；(b) ECI 管理配置/版本、由 Lefthook 负责 Hook 文件安装/生命周期，**每次事件仅一个** ECI Source/Selection 入口。**优先对 Lefthook 的成熟多工具 Hook 调度能力进行实际适配评估**；以原生薄入口作为对照，不预设其更优。比较多 Check 条件唤醒、可安全并行、失败收集、单事件输入复用、安装/升级/卸载、启动成本、配置复杂度和 Source/Result 语义。仅当现有调度器确实不能满足公共要求且净收益可证时再考虑自行实现缺失部分。prek 不与上述方案并存，可按同一准则比较。
- **必须完成的选择证据：** 固定版本与安装来源、Windows/Git for Windows/Unix、普通/链接 worktree、存在第三方 Hook 或 `core.hooksPath`、幂等安装与更新/卸载恢复、事件参数/stdin 保真、执行启动/依赖成本、可定位诊断。Lefthook 的 `core.hooksPath` 冲突和 `--force` / `--reset-hooks-path` 仅为显式迁移选择，不能自动覆盖用户原配置。
- Hook 配置更改不等于 ECI 版本升级。安装状态须识别当前真实生效的 Hook 路径/来源，已有 Hooks 共存通过明确的链式调用或迁移决议，不静默删除/覆盖。下游无需复制通用 Hook 适配器或每次事件执行 `uv sync`、SDK 生成、Engine 启动；确实需要的 Dagger Check 才使用现有原生调用入口。
- `--no-verify`、禁用 Hook 及用户 Git 配置会绕过本地 Hook；GitHub Required/Release 的不可绕过要求由 #61/服务器原生规则和受信原生证据承担，不由 Hooks 模拟。

### 2.2 Git 事件、受检对象与实际执行字节

- **pre-commit：** 读取 Git 实际 `GIT_INDEX_FILE` 与本次 Index Tree，原生工具须从该 Index 物化出的 Source 执行，或有可核验的读取字节等价证明。Index=B、默认 Index=A、Worktree=C 时，`git write-tree(B)` 正确不意味着普通根目录下的工具读了 B；这正是 #66 P0 未完成的现有缺口。
- **pre-push：** 一个原生 stdin 消费者读取全部 `local_ref local_oid remote_ref remote_oid`；保留每个 ref 的待推目标、真实 old→new 及来源。首次推送（旧 OID 全零）、强推、非 HEAD、多引用、缺远端对象均需正确扩大相关适用性或拒绝，不回退硬编码 `origin/main`。**删除远端 ref**（本地 OID 全零）单独裁决：没有待推的新 Commit，不能伪造 Source 执行或误当文件删除，按真实策略/删除影响检查并输出状态。
- **复用不是按 Tree 相等粗合并：** 两个 ref 的新 Tree 可以相同，但旧对象不同，old→new 变更/删除/兼容性义务不同；只有对具体 Check 的 Trigger、Analysis、Invalidation、配置/环境和执行对象均具有有效等价证明时才可复用结果。不引入 #66 自己的可信历史接纳（#63 负责）。
- Source/Config/Tool/Rule identity、实际执行内容、原生退出、`not_executed`、`blocked`、`not_applicable` 等结果复用既有 Plan/Report。某类业务测试为空不代表 Sources/Hygiene/前置或其他 Required Check 不适用。
- 单事件共享 Git 事件上下文与受检 Source 生产能力，但**不同 Push Ref 可各有 old→new、Source/Selection/Plan**，并按具体 Check 的实际输入和比较基线等价性复用原生 Result；项目级分析按其真实 import/build/关系闭包运行，同源且等价的 Tach 原生执行只有一个有效生产者。调用已安装的固定公共包；不是在 Lefthook 的每个文件命令里再次创建一个 ECI worktree。


### 2.3 调度分工与验收（避免局部优化造成架构退化）

- **必须保留多工具调度能力**：按事件、目录、项目/生态、配置/锁文件变化决定是否启动 Check；互不冲突的只读 Check 可并行；真实 PASS 前置关系决定依赖顺序；失败/未执行逐项报告；保留安装、升级、卸载、诊断和较低启动成本。Hook 不应该强制把所有工具变成一个不可并行的串行 Python 命令。
- **三种不同责任不能混为一谈**：Lefthook 等成熟 Hook 调度器可负责 Job 触发/并行/生命周期；ECI 的 Source/Selection/Plan/Execution 负责 Git 事件、实际受检字节、适用性与原生结果；Dagger 负责需要真实计算输入依赖和产物闭包的 DAG。应整合接口而非建第二套语义引擎，同时也不能为了避免第二套语义引擎而废除成熟的调度器。
- **单事件共享不是单 Plan**：一次 pre-push 可有多个独立 Git ref 和 Source/Selection 结果。允许共用事件解析、配置、Source 存储和输入等价的原生 Result，不能将多 ref 简化成只运行一次单 Source Check；结果汇总必须按每 ref/Check 显式保留覆盖关系。
- **验收衡量整体收益**：真实 Knowledge 与一个非 Knowledge 消费者上比较固定版本 Lefthook、必要时 prek 与原生薄入口；记录检查实际个数、适用集合、并行重叠、总延迟、事件完整性、错误诊断、第三方 Hook 共存和安装可恢复性。不得用跳过检查或牺牲多工具并发换取低延迟。

## 3. 现有职责与依赖（以最新 #66 为准）

| 责任 | 当前权威 / 已有资产 |
| --- | --- |
| Hook 公共生命周期与 Git Index/Push 实际对象、Source 物化、适用检查选择、生产方原生结果 | **ECI #66**；已有 `engineering_ci` CLI/Selection/Execution/Plan；#67/#70/#71/#73/#79/#88 已完成部分必须复用 |
| Native Check、工具资产、报告/Enforce 与 Actions 结果出口 | **ECI #52** 及既有 Tool Runtime/原生 Checks；新增具体扫描器不以 #66 Hook 交付为阻断前提 |
| 真正的 Data/PASS 前置 Dagger DAG、产物闭包、同次 Result 共享 | **ECI #86** 消费 #66 Source/Prerequisite 生产事实；#66 不再发明一套 DAG |
| 历史检查结果可信接纳、输入/范围有效覆盖和跨 Run 复用 | **ECI #63**；Hook 原生 PASS 不等于历史 Trusted |
| 最终 Required/Release、GitHub Ruleset/Branch Protection | **ECI #61**；本地 Hook 不能伪造服务端 Required |
| 项目领域规则/安装消费/真实 PR/main 工作流与旧适配器迁移 | **Knowledge #68**，复用 PR #73 的已有有效行为；PR #78 为同步草案 |

**交接不要求全 Issue 串行：** #66 P0 Source 与 P1 Host/Dagger 检查许可可并行；#86 独立接 Dagger DAG；#52/#63/#61 在各自实际事实成熟时消费。外部消费者可先配置项目规则，不因 #66 的 Hook 框架未选型而阻止已经可用的公共 Source/Check 接口。OTel/Weaver 已由 Knowledge PR #79 及 ECI #74/#75/#87 等专题管理，不回写本 Hook 草案。

## 4. 分层定向验收（保留已有效证据）

- **#66 P0 首先成立：** 临时 `GIT_INDEX_FILE=B`、默认 Index=A、Worktree=C（内容与结果相反），原生工具确实读取 B 或准确拒绝；无业务测试而有 Lychee/Sources 适用时同样成立。非 HEAD Commit/PR Merge 与本地 Worktree 不同则使用真正目标对象；Git ODB 不可用与 Commit 不存在分离。利用 #66 已有验证结果，不重跑所有工具。
- **Git 事件适配：** pre-commit 保留原事件；pre-push 首次、多 ref、非 HEAD、强推、缺旧对象、删除远端 ref；同新 Tree 但不同 old→new 的输入不得误复用受影响检查。真实 Source、Plan、原生执行与 Check 覆盖逐项对应。
- **Hook 生命周期：** 一份 ECI 公共安装/status/update/uninstall 契约，干净 Knowledge 与一个非 Knowledge 消费仓库；安装幂等、固定版本、第三方 Hook/`core.hooksPath` 共存、普通与 linked worktree、Windows/Unix 必要路径。记录真实选择依据；本地可绕过性与服务端 Required 分离。
- **Trigger/Analysis/Invalidation：** 改共享配置/工具规则/锁文件、改/删引用目标、Git 无变化但外部漏洞/链接状态失效，相关 Check 进入实际分析/失效处理，不相关语言工具或无依据的全部业务测试/wheel/fuzz 不启动。无法证实有效性时只对相关检查扩大/拒绝，不能凭未触发声称 PASS。
- **结果/成本：** 只对新增或改变的事件/工具行为补正反场景；在同一 Source/Check/版本下复用有效 Tach/扫描结果，记录 native exit、范围、选测数、一次代表性时间分段与去重次数。Hook 成功不得冒充 #61 Required、#63 可信历史复用或 #86 DAG 闭环。

## 5. 剩余设计决策与提案退出

1. **Source 正确性与工具调度能力并行推进：** #66 已纳入的 P0 真实消费字节、P1 共同 Tach 许可及生产回执先按原任务实施。无论使用原生薄入口还是 Lefthook 都必须调用该公共契约；禁止以 Hook 框架安装验证代替 Source 验证。
2. **Hook 机制选择**：Lefthook 是优先评估的成熟条件调度、并发与生命周期实现；原生薄入口是对照方案，prek 是可比较的替代工具。按实际执行矩阵、版本、平台、安装共存、失败/诊断、成本及完整 Git 事件保真决定最终方案，不得因薄入口代码少而默认降低多工具调度需求；只批准一个有效 Hook 安装所有者。子命令和配置字段在接线实施 PR 对照现有 CLI 核准，本文不能宣称接口存在。
3. **合入与归档**：#66 已吸收的通用生产方需求不再被此草案复制成新的长期规范。待 Hook 接线实施的具体方案确认后，此 Draft 可作为历史设计说明合并，或在结论已全部归入 #66 时关闭为 superseded；不直接转 Ready，不要求 #66 因本 Draft 仍在审阅而停止公共 Source/Check 实施。Knowledge PR #78 只同步其消费者差分。

## 6. 跨生态原生工具接入与 Hook 生命周期（可扩展候选与职责）

**实施裁定：一次设计公共接线，按实际仓库的适用工具矩阵成批实现，按执行类别集中验收；不采取一个工具一个 PR、一个全量 CI 验收，也不把全部候选默认配置成每次提交的 Required。** 已合并 ECI PR #67 的选择/Hygiene/CLI、ECI #52 的现有 Check/Actions/工具资产和原生 Dagger Checks 作为基线，先核对现有能力，只补实际缺口。

### 6.1 单事件生命周期实现的决策条件

- **原生零逻辑 Hook（功能/成本对照，不是默认首选实施）**：Git 本身已经提供 pre-commit/pre-push 事件、`GIT_INDEX_FILE` 与 stdin；薄入口调用一次 ECI Hooks CLI，能够在最少额外安装依赖下复用已存在的 ECI Source/Selection/Check。它不需要在 ECI 重建“工具生命周期调度框架”。
- **Lefthook（优先评估的成熟调度候选）**：适合真实需要独立只读工具并发、文件快速唤醒或成熟诊断的仓库；但 `glob`、`root`、`{staged_files}` 均非 Index/Push 真实字节证明，且多个 `use_stdin: true` 命令只有一个会收到完整 stdin。采用时，一个 ECI 入口独占 pre-push stdin，一次事件仅构建一次有来源的公共 Source/Plan，不由多个 ECI Profile 分别创建 worktree/重复 Tach。
- **prek（替代比较项）**：不能与 Lefthook 双安装；需对固定版本的多 ref stdin、目标快照与现有 Hook 兼容性做相同事实核验，不能由工具宣传或默认行为推断。
- **写入安全**：pre-commit 默认只读，任何工具通过路径读取的实际 staged 字节需由 #66 P0 闭合。Lefthook `stage_fixed` 或自动 `git add` 可能卷入未暂存工作树修改，不能作为默认自动修复模式；明确的 fix 命令另行执行并重新核验受检 Tree。
- **不重复安装/调度**：已存在的 ECI/Tool Runtime 原生工具与 Check 结果继续消费；Hook 管理不成为二次下载器、二次任务图、重复 Report 解释器或缓存账本。单一事件能并发无冲突的原生 Checks，但真正 Data/PASS 计算依赖、局部失败及同次产物共享由 #86 Dagger DAG/现有执行机制负责。

### 6.2 全阶段工具候选目录及按仓库启用策略

表中均为**候选能力**，应在一次批量盘点中标记 already-implemented / needs-binding / applicable-new / not-applicable，而不是将工具名称视为已交付或全部 Required。同类替代品应按项目语义择一或证明互补。

| 阶段 | 候选工具组（覆盖全部讨论范围） | 接入与执行裁决 |
| --- | --- | --- |
| pre-commit 格式/基础检查 | Ruff；Biome / Oxlint / dprint；shfmt；taplo；prek 内置基础检查 | Python、前端、Shell、TOML 按实际语言启用；格式修复与只读门禁分离，避免同一文件多工具改写。 |
| pre-commit Secrets/拼写 | Gitleaks 或 ripsecrets；typos | Gitleaks protect 已弃用；根据锁定版本核对 git --pre-commit --staged 等真实有效命令；匹配真实 Index，不把领域术语当拼写错误。 |
| pre-commit 规则/架构 | ast-grep、sd、ripgrep；Tach；Vulture；Import Linter（如有实际契约） | sd/ripgrep 主要用于替换/搜索，不是第三套 Gate；Tach 依据全图输入与原生校验；Vulture、Pyright 等需要项目/依赖上下文，不能只分析零散 staged 文件就声称全局通过。 |
| pre-commit 静态/文档 | ShellCheck、actionlint、zizmor；Pyright 或 BasedPyright；Lychee --offline；YAML/TOML/大文件/空白检查；Windows 适用 PSScriptAnalyzer | 复用 ECI 已有 actionlint/PowerShell 路径；glob 只决定启动与否，不证明 Source、完整导入图或 GitHub 原生失败正确。 |
| commit-msg | cocogitto 或 commitlint-rs | 只有仓库明确采用提交格式时才启用一项，读取 Git 给出的消息文件路径；不把规范无依据地施加给所有消费者。 |
| pre-push SAST/依赖 | Semgrep；osv-scanner；cargo-deny | Semgrep 的 baseline 使用实际受检 old→new；依赖检查按有效锁文件/声明变化或真实 Required 执行；Rust 规则仅对 Cargo 工程。 |
| pre-push API 兼容 | griffe；oasdiff；buf | 仅适用 Python 公共 API、OpenAPI、Proto 资产。griffe 对比可导入包与有效 Git ref；oasdiff 接受真实旧/新 schema 文件，而非将 HEAD 当 schema 路径；buf 依据实际 proto module/基线。 |
| pre-push 增量动态/形式 | ECI unittest/pytest 原生选测；候选 pytest-testmon；CrossHair；Hypothesis | ECI 是公共选择权威；testmon 只在实际收益与覆盖可证明时接入，不另跑重复的完整回归。CrossHair/Hypothesis 对应实际形式化/属性契约执行，不以预估 3–20 秒作保证。 |
| 条件专项/Actions/Release | Schemathesis；mutmut；cargo-mutants；Lychee 在线链接 | API fuzz 需服务和真实 schema（新版 Schemathesis 已移除 --app），mutation 受真实运行成本约束；cargo-mutants --in-diff 使用实际 diff 文件；在线链接可在适用风险/CI 环境运行，不能用波动网络代替可靠本地门禁。 |

### 6.3 原始 lefthook.yml 不能直接当作可执行配置

1. **50ms–3s 与 3s–20s 是未经测量的目标，不是承诺**。跨文件分析、符号执行、全量属性测试、变异测试和在线死链检查可能显著超过，必须按真实墙钟和原生命令记录。
2. **staged_files 不是暂存字节快照**：直接在开发工作区运行文件路径，可能读取未暂存修改；涉及原生验证结论时 ECI 必须使用 Index Tree 或已证明相同的隔离执行输入。stage_fixed=true 自动 git add 不默认使用。
3. **push_files / glob 不自动证明本次 Push 的所有 refs**：必须实际读取 Git 原生 stdin old/new、多个目标、强推/删除与缺失对象，并在对应目标快照执行。各项 baseline 不得硬编码 origin/main。
4. **原示例中多个命令需修正**：Gitleaks protect 已弃用；Schemathesis --app 已移除；oasdiff breaking 要求真实旧/新规范；cargo-mutants --in-diff 要求实际 diff 文件；griffe check 必须绑定真实包及 Git 对照。版本固定后据原生 help/doc 核实各项命令，不得通过错误命令的跳过冒充 PASS。
5. **parallel=true 不代表任意检查可安全并行**：只有不冲突的只读任务可并行；变更 Index/依赖环境/同一输出目录、或依赖同源原始 Check 的任务，必须依次消费有效结果并避免同样工具重复扫描。

### 6.4 批量工具接入的正确交付范围

1. **一次盘点已有事实、按适用能力组实施**：本节完整目录保留 Python、Rust、Java、SAST、API、形式化、fuzz/mutation、依赖和在线检查的候选能力，不把目录当作“本批全部必须安装/运行/Required”。以当前已合并原生 Check、版本、Source/Report/Enforce 为基线，明确 `already-delivered`、`needs-event-binding`、`needs-native-check`、`not-applicable`。
2. **#66 只交付公共 Hook/Source/触发/失效接口和已有检查的接线**；新扫描器的真实命令、原生 Exit/诊断、Report/Enforce 按 #52 及已有 Tool Runtime/Check 机制接入。可以在同一有意义的实施批次中并行处理对应功能，但不能以“一个 Hook PR 一次交付所有候选工具族”为 #66 的退出条件，也不强制每项工具单独开 PR。
3. **按真实消费者启用**：Knowledge 当前有 Python、RDF/Sources、TOML、Shell/Workflow、文档及依赖图输入，就在该消费端成批落地适用 Checks；无实际 Rust/Proto/OpenAPI/运行 API 的仓库不安装其运行时，也不新建空 Required。未来消费者出现真实输入时能直接使用相同公共 Source/Check 接口，不需要重新发明 Registry/任务调度。
4. **公共路径一次验证，新增风险定向验证**：Index/Worktree/Push、安装生命周期、Source/环境版本/缺工具/失败/skip 的公共证据已有有效则复用；仅对新适用命令和真正独特的风险补原生正反用例。测量有代表性的同范围成本、适用测试规模和重复扫描，不将跳过相关检查冒充性能收益。
5. **#86/#63/#61 边界不转移**：依赖产物闭包和 Dagger DAG 是 #86；历史有效覆盖接纳是 #63；Required/Release 仍由 #61。Hook 声称“选中/运行”不能替代任何一项来源/结果证明。

## 7. 工具触发、分析与失效的正交关系（当前可执行设计要求）

**必须从现有 ECI 配置、Source、Selection Plan 和原生 Check 结果中推导出** Trigger / Analysis / Invalidation 三类关系，不得另建三套配置表或规则引擎。它们分别描述唤醒、真实读取/覆盖、旧结果何时失效；`glob`、`root` 和 `{staged_files}` 仅提供 Lefthook 粗过滤，不能证明暂存 Tree 受检。现有 `engineering_ci.selection`、`execution`、`hygiene`、`sources`、`plan_contract` 及原生 Report 是实施位置。

### 7.0 每个 Check 的三类输入集合（规范性要求）

- **Trigger Set（触发集合）**：Git 事件或明确调度事件中，哪些路径/对象/条件的变化使该 Check **进入适用性判断**；Lefthook `glob` / `root` 只能作为保守的快速过滤器，不能代替 ECI 当前 `select`/Check 的真实影响决策。匹配到不代表检查已执行；未匹配到也不自动证明原有 PASS 可复用。
- **Analysis Set（实际分析集合）**：原生命令实际需要读取的 staged 文件、完整包/模块图、依赖、资源反向引用、真实 old→new 接口快照、运行环境等。F/P/R/D/E 描述此集合的形态，**不是**额外工具类型注册表。
- **Invalidation Set（结果失效集合）**：能够改变 Check 原始结论或覆盖范围的真实输入：被检 Source Tree/文件内容、配置/规则/忽略清单、工具及运行时版本、锁文件/构建定义、导入与反向引用闭包、生成资源、必要外部数据库/服务状态。变化后要么计算新的适用检查并重新执行，要么提供有效的输入/覆盖等价证明；没有证明时不能沿用旧 PASS。对外部漏洞公告、在线链接等非 Git 条件，必须有适用的独立定期/Release 触发或有效期规则，单靠文件 glob 不充分。
- **联系不变量**：对某 Check 的 Invalidation Set 任意成员发生有效变化时，必须由 Trigger Set 唤醒并由 Analysis Set 覆盖，**或**有 ECI #63 可采信的未受影响证明；不允许 `skipped` / Workflow path-filter 未触发被解释成 `passed`。Check 的实际原生执行属于 ECI #52/#66，历史证据可信接纳属于 #63，最终 Required 完整性仍由 #61 裁决；不新增另一套调度器或缓存账本。
- **分层保守性**：配置变化可能影响大量文件，应扩大相关 Check 的 Analysis Set（例如 Ruff 配置改动触发受影响 Python 文件检查、Tach 图配置改动触发完整适用依赖图），但**不因此无依据扩大成所有 unittest 和 wheel/CLI smoke**。只对无法判定的**相关** Check 扩大或阻断，明确原因和原始状态。

### 7.1 执行作用域分类（独立于 pre-commit/commit-msg/pre-push）

| 类别 | 典型工具与模式 | 被唤醒后实际需要的输入 | 对 Lefthook 的正确用法 |
| --- | --- | --- | --- |
| **F — 文件级（file-local）** | Ruff 单文件格式/局部规则、shfmt、taplo、dprint、google-java-format、typos；Biome/Oxlint 的纯文件规则；某些 ast-grep、ShellCheck/actionlint/zizmor 单文件规则 | 被选中的文件**实际暂存版本**，加有效的共享配置/忽略规则；部分工具虽接收文件名，仍可能按项目配置解释它们 | `glob`/ `root` 过滤，`{staged_files}` 传递路径；由 ECI 确保实际读取的是 Index 字节而非工作树中不同的未暂存版本 |
| **P — 包/模块/构建图级** | Pyright 或 BasedPyright、Tach、Import Linter、Cargo Clippy、Maven/Gradle、完整模块 lint；Vulture 全工程死代码分析 | 受影响模块/包及其 import/dependency/build 图、配置和必要跨文件引用；如没有可靠模块收敛则按适用项目范围执行 | 使用 `glob: "*.py"`、`glob: "*.rs"`、`glob: "*.java"` **作为唤醒条件**；执行真正的项目/包命令，不把 `{staged_files}` 冒充完整分析输入 |
| **R — 仓库/关系/资产级** | Gitleaks Git 历史/暂存扫描、Lychee 本地文档/锚点引用闭包、Semgrep 跨文件污点、全局 ast-grep、Vulture 全仓；Sources/RDF 对账 | 实际选定的 Git 对象/变更、引用目标及反向依赖闭包、全局规则/字典、工具版本；按实际扫描模式确定是否包含历史/外部输入 | `glob` 先过滤不相干变更，但删除链接目标/重命名或修改配置亦必须唤醒相关检查，不可只匹配文档后缀 |
| **D — 新旧契约/版本比较级** | Griffe、oasdiff、buf、ECI Git 变更影响选择、Semgrep baseline 模式 | Git 正确旧/新对象、公共 API/Schema/Protobuf 及兼容性规则；包含删除、重命名、强推 old→new | pre-commit 可按资源路径触发；pre-push **不能**仅凭 `{push_files}`/固定 `origin/main` 证明本次所有 ref，必须由 ECI 消费完整 stdin 并建立正确 baseline |
| **E — 动态执行/环境/服务级** | unittest/pytest、pytest-testmon、CrossHair/Hypothesis、Schemathesis、mutmut/cargo-mutants、在线 Lychee、wheel/installed CLI smoke、OSV/cargo-deny 依赖审计 | 真正的受影响测试/契约/目标服务、依赖环境、项目锁、可复用原始结果或许可证/漏洞数据；变异和模糊测试要有专项预算 | `glob` 只决定是否考虑执行；由现有 ECI Check/工具选择判断环境、依赖与阶段，绝不把“文件类型匹配”作为足够的完成证据 |

**边界案例**：同一工具可以有多种作用域。例如 Semgrep 单文件模式是 F、跨文件 taint 是 R；Lychee 针对已改文件的离线 URL 检查是 F/R，目标被删除时涉及 R 反向引用；Gitleaks staged 内容检查与 Git 历史扫描不是同一作用域。工具归类应按所用的**具体原生命令/检查策略**，不是永久绑定某个工具名称。

### 7.2 二维分发：阶段 × 执行作用域 × 实际事件

| 阶段 | 文件级 F | 包/模块级 P | 关系/资产级 R | 比较级 D | 动态/环境级 E |
| --- | --- | --- | --- | --- | --- |
| **pre-commit** | 匹配 staged 文件才启用，如 Ruff、taplo、shfmt；默认只读 | 匹配代码或包配置才运行适用 Tach/类型检查/模块 Lint；避免无故跑全部项目 | Secrets、来源对账、离线链接按变更闭包唤醒；不能漏掉被删除的引用目标 | 仅对暂存 Index 相对基线的真实变化执行适用契约检查 | 只运行有效小范围前置或定向测试；昂贵运行按实际风险移至其他阶段 |
| **commit-msg** | 消息文本本身的定向验证（cog 或 commitlint-rs） | 不适用 | 不适用 | 如提交策略需要时核对实际提交语义，不变成新测试选择器 | 不适用 |
| **pre-push** | 仅在必要时对真实 push 目标的文件快速复核 | 对受影响 Cargo/Java/Python 包执行包级 Checks | Semgrep 污点、依赖安全与在线链接按原生对象/有效输入运行 | Griffe/oasdiff/buf 等在真实 old→new 上比较 | 增量回归、适用形式验证；fuzz/mutation 优先专项/Actions，不能伪称常规 push 数秒完成 |
| **Actions / Release** | 复用原始 Check；缺失时定向补执行 | 必要全项目/构建型检查按策略运行 | 全依赖/安全和来源验证，检查 Required 证据 | 合并目标与发布候选兼容性 | 有实际服务/环境/预算的集成、fuzz、mutation 或发布验收 |

**重要的唤醒条件补足**：`Cargo.toml`/`Cargo.lock`、`pom.xml`/`build.gradle*`、`pyproject.toml`/`uv.lock`、Tach 和类型配置、工具版本与规则目录、CI Workflow、被链接资源的删除/重命名，都可能改变 P/R/D/E 的输出。只写 `glob: "*.rs"` 或 `glob: "*.py"` 会对这些非源码输入产生盲区；以有效工具依赖和消费者输入映射补全触发，而非一律升级全量。

### 7.3 事件入口与共享配置：以单次 Source/Plan 为准（非可执行示意）

- 若采用 Lefthook，`glob` 在 Git 仓根路径上匹配，`root` 还会过滤文件并改变执行目录；共享构建输入不能因仅处在根目录而被子项目 `root` 静默丢失。文件/路径过滤属于保守唤醒，不属于被验证 Source、完整 Analysis Set 或跨 ref 的可信适用性结论。
- 多语言包可在消费配置中声明 F/P/R/D/E 和全局配置/锁文件影响；从一次公共事件入口接收完整 Git 数据，按真实 ref 分别构造 Source/Selection/Plan，复用等价的输入/原生结果，再由成熟 Hook 调度器与 ECI 原生执行能力按职责分发适用 Checks。**不得**让不同 `--profile` 入口各自重复物化 Index、重新算 Plan 或重复执行 Tach；只有确实独立且不会冒充 ECI Source/Check 覆盖的只读工具，才考虑由 Lefthook 直接管理。
- 下列 YAML 是 **候选 Lefthook 模式示意**，不是可部署命令，`engineering-ci hooks` 目前不是已交付 CLI：

```yaml
pre-commit:
  commands:
    eci-source-bound-checks:
      run: engineering-ci hooks run pre-commit --repo .
pre-push:
  commands:
    eci-all-ref-checks:
      use_stdin: true
      run: engineering-ci hooks run pre-push --repo .
```

- 这个设计仍必须在 ECI 内正确解释 `GIT_INDEX_FILE` 和完整 pre-push stdin；不允许用本工作树文件路径或固定 `origin/main` 补足这些输入。Lefthook 可能在过滤后不调用某命令，因此承载全量事件/适用性判定的入口不能依赖会遗漏必要输入的 `glob`/子目录 `root`。
- 如果最终采用 Git 原生薄 Hook，需要另行证明它如何满足多个 Check 的条件唤醒、并发执行、失败汇总、成本和生命周期要求；若未满足，则不应为了代码少而采用。两种候选的安装权威必须唯一，但 ECI Source/Selection 与成熟 Hook 工具调度可以合理组合。

### 7.4 配置与判定数据（复用已有 ECI 公共契约）

每项 Check 的相关信息应**从现有 ECI 对象推导或定点扩展**。下列名称是逻辑视图，**不要求新增同名 `trigger_set`、`analysis_set`、`invalidation_set` 的 TOML/Plan/Report 字段**，更不允许建立第二份工具 Registry、失效算法或执行结果账本：

- `hook_phase`、F/P/R/D/E `analysis_scope`、Trigger Set 的输入路径/事件/共享配置和 Invalidation Set 的完整依赖规则及外部有效期；必要时区分根目录与子模块执行上下文。
- Check ID/适用规则、原生运行输入、实际 Source Tree、受影响模块或旧新契约、工具/规则/锁版本、必要环境与前置条件及真实执行状态。
- 复用的原始 Check 及其有效覆盖/身份，工具进程是否启动、单项耗时、并行或写入冲突。`skipped`、`not_applicable`、`not_executed`、`blocked` 和 `passed` 不得互相代替。

### 7.5 定向验收：验证唤醒、分析范围、失效与结果来源

- [ ] Index=B、默认 Index=A、Worktree=C，实际工具（包含零业务测试时适用的 Lychee/Sources）读取 B 字节或明确拒绝，不仅核验 `git write-tree` 或只比较 Plan 元数据。
- [ ] 同一事件保留一个共享 Event Context，但不同 ref 的 Source/Impact/Selection/Plan 彼此独立；只对实际等价的 Check 输入复用同次原生结果，Tach 和适用 Native Check 不因 Hook Profile 重复执行；工具出错、不可用、not_executed/advisory 区别保留。
- [ ] 只修改 Python/其工具配置：受影响 F/P Checks 唤醒，相关配置触发相应完整包/模块 Analysis Set；Rust/Java/未适用生态不启动。只改 `Cargo.lock`、共享 Gradle/POM 或 Pyright/Tach 配置也能唤醒相关 Check。
- [ ] 删除/重命名被引用文档目标或 RDF/Sources 资源时检查反向影响；仅改变工具、规则、环境版本或 CI Workflow 时，旧 Check 覆盖按真实 Invalidation 失效或有有效受信等价证据。
- [ ] 首次推送、多 ref、强推、非 HEAD、远端删除 ref 和缺旧对象各按真实 Git 事件裁决；**同 Tree 不同 before→after** 不得误用相同的比较 Check/选择范围。
- [ ] Git 无变化但网络/漏洞/在线链接数据实际失效时，有真实独立的适用检查时点/有效期规则或明确未验证状态；Hook file glob 不保证外部状态新鲜。
- [ ] 只对公共 Source、Git 事件、Hook 生命周期的新增路径做代表性验收，额外工具只验证其原生命令特有风险，不重跑 PR #67–#88 已有效的全部工程检查。

### 7.6 现有 ECI 对象与待补的具体实现缺口（按当前 main）

| 现有对象 | 已交付的事实 | 本 Hook/Source 提案尚须消费或补齐 |
| --- | --- | --- |
| `engineering_ci.selection.ChangeSet` / `changeset_multi` | Index/Worktree/PR/Push 的对象、引用与变化路径，#73 已加快照校验 | Hook 真实事件进入同一 Selector；对所有 Push ref 保留各自 old→new；读取字节需走 #66 P0 物化 |
| `engineering_ci.selection.Config` / `load_config` / `select()` | 现有模块测试、非代码消费者、Check Scope、工具及必要前置的选测规则 | 按 Trigger/Analysis/Invalidation 准确表达共享锁、规则、版本、反向引用，不增加 Hook-only TOML |
| `engineering_ci.selection.plan()` + `plan_contract` | PR #88 已交付 schema 2、依赖声明/拒绝与 `source_binding_verified=unknown` | 记录/消费真实 Source/Config/Check/Prerequisite 同源事实，不能把 unknown 升格为已验证 |
| `engineering_ci.execution._verify_execution_binding()` / `execute()` + Hygiene/Sources | #70/#71 已修无业务测试而适用 Check 的执行、Tach external 与类型化前置；#73 有 Index Tree 核对 | 工具真实读取 Index/Commit/Push 目标字节；共同 Tach 子集许可，实际 Native Result/失败归因；Host/Dagger 一致 |
| Dagger `execute_plan()`、Source handoff | #88 给出待消费的 Plan 依赖与原 Source 身份引用 | #66 保证真实生产输入；#86 建原生 DAG/Artifact Closure/Result Sharing；不由 Hook 调度器重复实现 |
| ECI #52/#63/#61 | 原生 Report/Enforce、历史可信接纳、最终 Required 分有权威职责 | Hook 只提供真实 Producer/Source/Check 事实，不制造平行 Gate/历史受信结果 |

若现有 Schema 足够，只改使用方式；实际欠缺字段按其已存在的 Source/Plan/Report 扩展，不维护第二份同义 Registry。无有效证据时不复用旧 PASS，但也不能因为一个相关 Check 失效便无依据重跑所有业务测试和 Wheel。

### 7.7 OTel 专题独立，不再形成当前设计依赖

Knowledge [PR #79](https://github.com/dymzz/engineering-knowledge/pull/79) **已经合并**，其 `design/engineering-verification/opentelemetry-weaver-delivery.md` 是 OTel/Weaver 专题设计来源；具体实施按 ECI #74/#75/#87 和消费者独立开展，不在此复制 Span/Exporter/Root 或 Trace Context 的完整规范。

PR #72 保持 Draft 的原因是 **成熟多工具调度器的适配验证、Git 事件/Source 的接口交接和消费迁移仍有待确认**，并非 OTel 尚未完成阻断 Hook。#66 的 Source/Check 生产方实施、#86 DAG 和 OTel 工作均可按真实接口独立推进；不因暂停此 Draft 而等待。

**收口规则：** 若 #66 正文已吸收同一有效需求，本 PR 仅保留差分决策、证据和对应实施入口。Hook 实施确认后择一保留短期设计历史或关闭本草案，不作为长期并行权威；不得创建新缓存层、任务调度框架或 Required 判定器。
