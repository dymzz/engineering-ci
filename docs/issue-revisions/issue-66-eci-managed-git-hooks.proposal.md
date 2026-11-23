# ECI #66 — Git Hooks 架构裁决、实施要求与历史记录

## 当前有效指令（2026-11-21；替换旧 L1/L2 候选和 Coordinator 方案）

**规范权威为 [Issue #66 当前置顶裁决](https://github.com/dymzz/engineering-ci/issues/66)。旧 PR #72 中 L2 优先、ECI 管理 Git Hook 文件、外层 Event/Hook Coordinator、比较后另选调度器等条款已被撤销，仅作为历史审查证据；任何旧段落的“当前/必须/下一步”均不能据此指导代码或创建任务。**

- **已安装/验证的 Lefthook 2.2.2 为现行基线。** `lefthook install` 负责 Hook 文件；声明式配置负责 Job 过滤、必要顺序及安全并行。完整 pre-push 在 2.2.2 中既存在含空格参数的 `script` 失真，也存在默认 push 文件比对（`HEAD` 与 `@{push}`）为空时跳过 Job 的已知行为，必须采用实测保真且可靠唤醒推送事件的原生交接方式或已核实修复版本，实际版本和 YAML 同步固定；不能因此删减多 Ref 功能，也不恢复 ECI 自建 Hook Coordinator。正式环境核对合并后的 `lefthook dump`（local/extends/remotes）及获批准配置，而非仅相信主 YAML。
- **保留完整检查能力。** ECI 的 #102 Git Event/Invocation、#66 Source/Selection/Plan、#52 原生 Execution/Report、#104 Reconciliation/Receipt 和 Hook Exit 各自复用既有实现；#86 处理真正的 Dagger Data/PASS DAG，#63/#61 处理受信/Required。本裁决不减少多 Ref、共享 Source、输入闭包、文件/项目级条件检查、失败归因和有效的并行能力。
- **Hook 运行链路：** Lefthook 唤醒本次适用 Job；一个指定 ECI 入口读取完整 pre-push argv/stdin 或 pre-commit Index，为每 Ref 准备有效 Source/Expected；各 Check 消费同次准备事实并保留原生 Report/Attempt。由 Lefthook 管理的接入方式调用已有最终对账 API，处理可控 Job 失败、过滤、缺失及真实 Exit；不再新建外围调度器。
- **已合并 PR #115 的退出策略：** `hook_coordinator.py` 已有的真实来源绑定、Plan、Report/Receipt 检验按职责回收至现有模块；停止扩展 Coordinator 专用状态与 CLI。默认 `scheduler_ref=lefthook` 不是 Lefthook 实际执行的证据。有效测试和证据按范围继承；只有新接线的真实风险需要定向验证。
- **验收：** 使用实际安装的 Lefthook、真实适用工具、pre-commit Index、pre-push 多 Ref/完整 stdin、并行与过滤/失败后的 Exit 完成端到端验收。既有 Source/Result 正反例不重做；也不启动新一轮候选选型。
- **示例安装（仅描述已锁定的机制）：** `uv tool install "lefthook==2.2.2"`（已有可执行程序则复用）；`lefthook version`；`lefthook install`。已有 Hook 与 `core.hooksPath` 冲突通过 Lefthook 实际机制显式处理，不能静默覆盖。

### 历史来源说明

以下保留 2026-11-12～11-17 的方案比较、风险矩阵、局部实验、错误决策和 Review 轨迹，以便溯源。**这些内容中 L1/L2 候选、ECI `hooks install`、Event Coordinator 以及旧“当前设计基线”均为已撤销历史陈述；只有与本裁决不冲突且仍有效的 Source/Check/Result 事实可继承。**

---

## 1. 工程目标与责任裁定（当前有效）

- Lefthook 2.2.2 是 Git Hook 文件管理和 Job 调度的唯一所有者；`lefthook install` 是实际安装入口。ECI 保持真实 Git Event、Source、Selection、Plan、原生 Check/Result 和 Reconciliation 的公共工程能力。
- 采用已安装的 uv/PyPI Lefthook；各仓库声明适用 `lefthook.yml` Job，不复制自建通用 Hook 安装器/任务协调器，也不把独立 Checks 收缩成串行单命令。下游仓库只配置业务/项目适用规则。
- Git Hook 本地可被绕过，服务端 Required/Release 仍按 #61、真实权限和原生 GitHub 保护执行。

## 2. Hook 调度与 ECI 接口（当前有效）

```sh
uv tool install "lefthook==2.2.2"  # 已安装则直接复用
lefthook version
lefthook install
```

ECI 提供现存的 `hooks capture`、Selection/Source/Execution/Report、Reconciliation 接口，由 Lefthook 的实际 Job 调用；不开发 `engineering-ci hooks install/status/update/uninstall` 替代 Lefthook 生命周期，也不开发新的 Hook Coordinator/Dispatcher/Runner。

### 2.1 唯一 Hook 所有者与输入交接

- Lefthook 控制 Hook 文件、条件过滤、Jobs/Group、并行和安装诊断；ECI 控制原始 Git Input、每 Ref Source/Expected、真实 Native Report 和消费后本地 Exit。
- 一个可靠的 pre-push 入口收齐 Git 传入的完整 argv/stdin 并建立本次独立 Invocation；其余 Job 消费相同已验证事实，不按 HEAD 猜测。本地失败后对账由现有结果消费接口实现，不是授予自建调度器的理由。
- `GIT_INDEX_FILE`、Push Ref、Source 实际读取字节及配置/锁文件/关系影响闭包按 #66 的有效来源合同验证；Lefthook `glob` 只是保守唤醒。
- 已有第三方 Hook/`core.hooksPath` 使用成熟工具的真实安装与显式迁移机制处理，不无条件覆盖。

### 2.2 Git 事件、受检对象与实际执行字节

- **pre-commit：** 读取 Git 实际 `GIT_INDEX_FILE` 与本次 Index Tree，原生工具须从该 Index 物化出的 Source 执行，或有可核验的读取字节等价证明。Index=B、默认 Index=A、Worktree=C 时，`git write-tree(B)` 正确不意味着普通根目录下的工具读了 B；这正是 #66 P0 未完成的现有缺口。
- **pre-push：** 一个原生 stdin 消费者读取全部 `local_ref local_oid remote_ref remote_oid`；保留每个 ref 的待推目标、真实 old→new 及来源。首次推送（旧 OID 全零）、强推、非 HEAD、多引用、缺远端对象均需正确扩大相关适用性或拒绝，不回退硬编码 `origin/main`。**删除远端 ref**（本地 OID 全零）单独裁决：没有待推的新 Commit，不能伪造 Source 执行或误当文件删除，按真实策略/删除影响检查并输出状态。
- **复用不是按 Tree 相等粗合并：** 两个 ref 的新 Tree 可以相同，但旧对象不同，old→new 变更/删除/兼容性义务不同；只有对具体 Check 的 Trigger、Analysis、Invalidation、配置/环境和执行对象均具有有效等价证明时才可复用结果。不引入 #66 自己的可信历史接纳（#63 负责）。
- Source/Config/Tool/Rule identity、实际执行内容、原生退出、`not_executed`、`blocked`、`not_applicable` 等结果复用既有 Plan/Report。某类业务测试为空不代表 Sources/Hygiene/前置或其他 Required Check 不适用。
- 单事件共享 Git 事件上下文与受检 Source 生产能力，但**不同 Push Ref 可各有 old→new、Source/Selection/Plan**，并按具体 Check 的实际输入和比较基线等价性复用原生 Result；项目级分析按其真实 import/build/关系闭包运行，同源且等价的 Tach 原生执行只有一个有效生产者。调用已安装的固定公共包；不是在 Lefthook 的每个文件命令里再次创建一个 ECI worktree。


### 2.3 调度分工与验收（保留多工具能力）

- **必须保留多工具调度能力**：按事件、目录、项目/生态、配置/锁文件变化决定是否启动 Check；互不冲突的只读 Check 可并行；真实 PASS 前置关系决定依赖顺序；失败/未执行逐项报告；保留安装、升级、卸载、诊断和较低启动成本。Hook 不应该强制把所有工具变成一个不可并行的串行 Python 命令。
- **三种不同责任不能混为一谈**：Lefthook 等成熟 Hook 调度器可负责 Job 触发/并行/生命周期；ECI 的 Source/Selection/Plan/Execution 负责 Git 事件、实际受检字节、适用性与原生结果；Dagger 负责需要真实计算输入依赖和产物闭包的 DAG。应整合接口而非建第二套语义引擎，同时也不能为了避免第二套语义引擎而废除成熟的调度器。
- **单事件共享不是单 Plan**：一次 pre-push 可有多个独立 Git ref 和 Source/Selection 结果。允许共用事件解析、配置、Source 存储和输入等价的原生 Result，不能将多 ref 简化成只运行一次单 Source Check；结果汇总必须按每 ref/Check 显式保留覆盖关系。
- **固定方案的整体收益验收**：在 Knowledge 与一个真实非 Knowledge 消费者中核验所选 Lefthook 方案的实际 Check 数、适用性、安全并行、总延迟、真实 Git 参数与 Index 字节、诊断、第三方 Hook 共存和生命周期恢复。这是已选工具的交付验收，**不是**重开 Lefthook/prek/薄 Hook 的方案比较。

### 2.4 最终调度器选型结论（2026-11-21）

**采用 Lefthook 2.2.2，不再进行 L1/L2/prek/自建薄 Hook 等重复选型。** 历史比较已经记录在本文件第 8 节，可用于解释过去决策但不能形成新增任务。检查适用性、真实受检字节、Native Result、证据复用和失败对账仍分别归原有 ECI 模块；成熟调度功能由 Lefthook 声明式配置提供。

保留原功能验收目标：多 Check 条件唤醒、合法并行与必要前置、单次 Event 的多 Ref、失败/过滤/不适用、真实安装及诊断、Source/Report/Receipt/Exit 交接；无证据不得丢失检查或伪造 PASS。

## 3. 现有职责与依赖（以最新 #66 为准）

| 责任 | 当前权威 / 已有资产 |
| --- | --- |
| Git Hook 文件的安装、状态诊断、卸载与 Job 调度 | **Lefthook**；通过原生安装和声明式 Job 配置管理 Hook 文件、过滤、分组、顺序与并行 |
| 仓库 Hook 接线、Git Index/Push 实际对象、Source 物化、适用检查选择与原生结果交接 | **ECI #66**；复用已有 `engineering_ci` CLI/Selection/Execution/Plan 与 #67/#70/#71/#73/#79/#88 已交付部分 |
| Native Check、工具资产、报告/Enforce；当前 Actions、目标 #152 受控 Provider 结果交接 | **ECI #52** 及既有 Tool Runtime/原生 Checks；新增具体扫描器不以 #66 Hook 交付为阻断前提 |
| 真正的 Data/PASS 前置 Dagger DAG、产物闭包、同次 Result 共享 | **ECI #86** 消费 #66 Source/Prerequisite 生产事实；#66 不再发明一套 DAG |
| 历史检查结果可信接纳、输入/范围有效覆盖和跨 Run 复用 | **ECI #63**；Hook 原生 PASS 不等于历史 Trusted |
| 最终 Required/Release、GitHub Ruleset/Branch Protection | **ECI #61**；本地 Hook 不能伪造服务端 Required |
| 项目领域规则/安装消费/真实 PR/main 工作流与旧适配器迁移 | **Knowledge #68**，复用 PR #73 的已有有效行为；PR #78 为同步草案 |

**交接不要求全 Issue 串行：** #66 P0 Source 与 P1 Host/Dagger 检查许可可并行；#86 独立接 Dagger DAG；#52/#63/#61 在各自实际事实成熟时消费。外部消费者可先配置项目规则，不因 #66 的 Hook 框架未选型而阻止已经可用的公共 Source/Check 接口。OTel/Weaver 已由 Knowledge PR #79 及 ECI #74/#75/#87 等专题管理，不回写本 Hook 草案。

## 4. 分层定向验收（保留已有效证据）

- **#66 P0 实际交付**：对有效的部分暂存 Index=B、默认 Index=A、Worktree=C，原生 Host/Dagger 工具必须实际读取 B 的目标字节与所需配置/依赖闭包；现有 `selection.index_source_refusal()` 拒绝不一致仅为未接通 Source 时的保护，不等于已经支持合法部分暂存。真正无法建立/核实受检 Source 时明确拒绝；无业务测试而有 Lychee/Sources 适用时仍按原 Check 合同运行。
- **Git 事件适配：** pre-commit 保留原事件；pre-push 首次、多 ref、非 HEAD、强推、缺旧对象、删除远端 ref；同新 Tree 但不同 old→new 的输入不得误复用受影响检查。真实 Source、Plan、原生执行与 Check 覆盖逐项对应。
- **Hook 生命周期与交接：** 在干净 Knowledge 与一个非 Knowledge 消费仓库验证 Lefthook `install`、`check-install`、升级到获准版本及 `uninstall` 的实际行为。覆盖重复安装、固定版本、第三方 Hook/`core.hooksPath` 共存、普通与 linked worktree、Windows/Unix 必要路径。ECI 侧核对 Lefthook Job 消费的 Event、Source、原生检查结果和 Receipt。记录真实选择依据；本地可绕过性与服务端 Required 分离。
- **Trigger/Analysis/Invalidation：** 改共享配置/工具规则/锁文件、改/删引用目标、Git 无变化但外部漏洞/链接状态失效，相关 Check 进入实际分析/失效处理，不相关语言工具或无依据的全部业务测试/wheel/fuzz 不启动。无法证实有效性时只对相关检查扩大/拒绝，不能凭未触发声称 PASS。
- **结果/成本：** 只对新增或改变的事件/工具行为补正反场景；在同一 Source/Check/版本下复用有效 Tach/扫描结果，记录 native exit、范围、选测数、一次代表性时间分段与去重次数。Hook 成功不得冒充 #61 Required、#63 可信历史复用或 #86 DAG 闭环。

## 5. 剩余设计决策与提案退出

1. **Source 与既定 Lefthook 接线并行推进：** #66-P0 实际 Index/Commit/Push Source 字节、P1 共同 Tach 许可和真实 Check 回执均在既有 ECI 公共模块落地；真实 Git Hook 由 Lefthook 安装/唤醒，不能以 Hook 安装成功替代 Source 真正被工具读取的证明。
2. **Hook 机制实施**：采用已固定 Lefthook 2.2.2；实施真实 `lefthook.yml` 条件 Job、并行/过滤、完整 Git Event 和 ECI 原生 Check/Receipt 接线。停止 L1/L2 及额外候选对比，禁止 ECI 自建 Hook 文件安装、调度控制层。
3. **现存设计与证据**：保留历史 PR #72 的来源记录，当前施工只依 Issue #66 的现行裁决。已交付 Source/Check/Result API 继续有效；仅对 Lefthook 接线及受影响行为做定向验证。

## 6. 跨生态原生工具接入与 Hook 生命周期（可扩展候选与职责）

**实施裁定：一次设计公共接线，按实际仓库的适用工具矩阵成批实现，按执行类别集中验收；不采取一个工具一个 PR、一个全量 CI 验收，也不把全部候选默认配置成每次提交的 Required。** 已合并 ECI PR #67 的选择/Hygiene/CLI、ECI #52 的现有 Check/Actions/工具资产和原生 Dagger Checks 作为基线，先核对现有能力，只补实际缺口。

### 6.1 已确定的 Hook 生命周期（不再选型）

**当前唯一方案：Lefthook 2.2.2 + 已有 ECI Source/Check/Result API。** 固定版 Lefthook 负责安装与静态/条件/并行 Job 调度；ECI 通过现有 `hooks capture` 等接口接收真实 Git Event/Ref、Selection/Plan、Native Result 和 Receipt。后续任务是实际声明 `lefthook.yml`、真实调用与失败交接，不是比较 L1/L2/prek/薄 Hook 或建设新的 Hook Coordinator/安装器。安装冲突采用 Lefthook 本身支持的机制与显式用户选择处理。

完整跨生态工具候选范围在 §6.2 继续保留，不以“无须自行调度”收缩任何实际适用的 Check。

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
| 条件专项/受控 CI/Release（现行 Actions，目标 #152 替换） | Schemathesis；mutmut；cargo-mutants；Lychee 在线链接 | API fuzz 需服务和真实 schema（新版 Schemathesis 已移除 --app），mutation 受真实运行成本约束；cargo-mutants --in-diff 使用实际 diff 文件；在线链接可在适用风险/CI 环境运行，不能用波动网络代替可靠本地门禁。 |

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
| **pre-push** | 仅在必要时对真实 push 目标的文件快速复核 | 对受影响 Cargo/Java/Python 包执行包级 Checks | Semgrep 污点、依赖安全与在线链接按原生对象/有效输入运行 | Griffe/oasdiff/buf 等在真实 old→new 上比较 | 增量回归、适用形式验证；fuzz/mutation 优先专项/受控 CI，不能伪称常规 push 数秒完成 |
| **受控 CI / Release（当前 Actions；目标 #152）** | 复用原始 Check；缺失时定向补执行 | 必要全项目/构建型检查按策略运行 | 全依赖/安全和来源验证，检查 Required 证据 | 合并目标与发布候选兼容性 | 有实际服务/环境/预算的集成、fuzz、mutation 或发布验收 |

**重要的唤醒条件补足**：`Cargo.toml`/`Cargo.lock`、`pom.xml`/`build.gradle*`、`pyproject.toml`/`uv.lock`、Tach 和类型配置、工具版本与规则目录、CI Workflow、被链接资源的删除/重命名，都可能改变 P/R/D/E 的输出。只写 `glob: "*.rs"` 或 `glob: "*.py"` 会对这些非源码输入产生盲区；以有效工具依赖和消费者输入映射补全触发，而非一律升级全量。

### 7.3 Git Event 与 Lefthook 可并行 Job 的适配（当前有效）

本阶段已固定 Lefthook 2.2.2，原 L1/L2/prek/原生薄 Hook 的比较属于第 8 节历史决策，不继续开发。

1. **单次原生事件采集：** `pre-commit` 使用实际 Index；`pre-push` 取得真实 argv、全部有序 ref 元组。调用现有 `hooks capture`，每次 Invocation 独立；一个事件可含多个 Ref/Source/Expected。
2. **真实 Job 调度：** `lefthook.yml` 使用工具原生 `jobs`/`group`/条件/并行选项，实际适用关系由已存在的 ECI Selection/Plan 确定；共享 Source/Config/等价 Native Result，但不能让多个 Job 重新各自运行同一 Check。
3. **独立结果生产与对账：** 每 Job 使用已有 `execution.execute()` 与 Check/Report，`reconciliation.reconcile_expected_results()` 按 Scope 消费有来源的 Routing、Result、Receipt；最终对账在可控 Job 失败/过滤后仍执行；不得将 Lefthook Job exit=0 或自报调度器身份提升为 Source-bound PASS。
4. **单一 Hook Exit：** 聚合每个 Ref/适用 Check 的失败、缺失、unknown 和覆盖。由实际 Lefthook 调度结果和已有 ECI 本地对账决定；无须自建 Event/Hook Coordinator 或独立调度生命周期。
5. **验证依据：** 使用已存在的真实 Git Event、Source 和 Check 证据，只补真正改变的 Lefthook 接口、并行/过滤以及失败后对账的定向正反例。

### 7.4 配置与判定数据（复用已有 ECI 公共契约）

每项 Check 的相关信息应**从现有 ECI 对象推导或定点扩展**。下列名称是逻辑视图，**不要求新增同名 `trigger_set`、`analysis_set`、`invalidation_set` 的 TOML/Plan/Report 字段**，更不允许建立第二份工具 Registry、失效算法或执行结果账本：

- `hook_phase`、F/P/R/D/E `analysis_scope`、Trigger Set 的输入路径/事件/共享配置和 Invalidation Set 的完整依赖规则及外部有效期；必要时区分根目录与子模块执行上下文。
- Check ID/适用规则、原生运行输入、实际 Source Tree、受影响模块或旧新契约、工具/规则/锁版本、必要环境与前置条件及真实执行状态。
- 复用的原始 Check 及其有效覆盖/身份，工具进程是否启动、单项耗时、并行或写入冲突。`skipped`、`not_applicable`、`not_executed`、`blocked` 和 `passed` 不得互相代替。

### 7.5 定向验收：验证唤醒、分析范围、失效与结果来源

- [ ] Index=B、默认 Index=A、Worktree=C：实际适用工具在合法部分暂存下**读取并检查 B**（含 Lychee/Sources 等独立适用检查），核对真实 Source/配置/Tool/原生 Exit；只有源无法安全物化或输入同一性确实无法证明时，才保留明确拒绝，不能将全部 Worktree 分歧作为功能验收的替身。
- [ ] 同一事件保留一个共享 Event Context，但不同 ref 的 Source/Impact/Selection/Plan 彼此独立；只对实际等价的 Check 输入复用同次原生结果，Tach 和适用 Native Check 不因 Hook Profile 重复执行；工具出错、不可用、not_executed/advisory 区别保留。
- [ ] 只修改 Python/其工具配置：受影响 F/P Checks 唤醒，相关配置触发相应完整包/模块 Analysis Set；Rust/Java/未适用生态不启动。只改 `Cargo.lock`、共享 Gradle/POM 或 Pyright/Tach 配置也能唤醒相关 Check。
- [ ] 删除/重命名被引用文档目标或 RDF/Sources 资源时检查反向影响；仅改变工具、规则、环境版本或 CI Workflow 时，旧 Check 覆盖按真实 Invalidation 失效或有有效受信等价证据。
- [ ] 首次推送、多 ref、强推、非 HEAD、远端删除 ref 和缺旧对象各按真实 Git 事件裁决；**同 Tree 不同 before→after** 不得误用相同的比较 Check/选择范围。
- [ ] Git 无变化但网络/漏洞/在线链接数据实际失效时，有真实独立的适用检查时点/有效期规则或明确未验证状态；Hook file glob 不保证外部状态新鲜。
- [ ] 只对公共 Source、Git 事件、Hook 生命周期的新增路径做代表性验收，额外工具只验证其原生命令特有风险，不重跑 PR #67–#88 已有效的全部工程检查。

### 7.6 现有 ECI 对象与待补的具体实现缺口（按当前 main @ `a99efd5b59dd`，2026-11-21）

| 现有对象 | 已交付的事实（截至 `main@a99efd5b59dd`） | 本 Hook/Source/Reconciliation 提案尚须消费或补齐 |
| --- | --- | --- |
| `engineering_ci.hooks` + `engineering_ci.selection.ChangeSet` / `changeset_multi` | PR #100（`6c3123c`）已交付逐 Ref new/delete/force Baseline、部分 Linked Worktree Source 拒绝及实际 Worktree/Tree 绑定；PR #102（`53d5eb7`）已交付 `hooks.py` 原生 Git Event 采集、Index/Remote 去敏与独立 `invocation_ref` 持久化；PR #111（`a82d11e`）已将 `git check-attr -a` 有效空白属性绑入规则身份 | 首次提交（`unborn HEAD`）空 Tree 基线（现 `main` 仍返回 `head_unresolved`）；实际 Lefthook Hook 入口与单次 Event/Invocation 到 Selector 的运行时接线；`pre-push` 原生完整 argv/stdin 保真交接 |
| `engineering_ci.selection.Config` / `load_config` / `select()` | PR #100（`6c3123c`）已交付有效 Hygiene/Sources 配置与哈希、`tach_external` 及逐 Ref 配置来源绑定；PR #103（`3a6afad`）与 PR #135（`edd4094`，`NEW-055`）已交付 Workflow 配置快照与受检 Source 准入绑定 | 逐 Ref 目标树内的 `[push]` 策略与多动作 `effective_profile` 解析；按 Trigger/Analysis/Invalidation 准确表达共享锁、规则、版本、反向引用，不增加 Hook-only TOML |
| `engineering_ci.selection.plan()` + `plan_contract` / `expected_checks` | PR #100（`6c3123c`）与 PR #108（`bf8bede`）已结算 EffectiveProfile→Expected Schema v2 枚举、逐 Ref Expected 与 Producer/Expected 合同对齐；PR #112（`58d40ee`）与 **PR #143（`a99efd5`）**已交付 `#66-B2` Phase 绑定、Phase Projection 及非 Sample Obligations Universe Issuer 验证 | 首次提交空 Tree 基线（`baseline_status="unborn"`）的 Expected/Plan 合同；Hook Event→Expected（含目标 Index/每 Ref Tree 策略读取）的运行时接线；不能把 `unknown` 升格为已验证 |
| `engineering_ci.reconciliation.reconcile_expected_results()` | PR #104（`c430ef7`）与 PR #110（`eb23582`）已交付独立 Expected→Routing/Job/Attempt→Native Result 对账消费合同、Report digest/成员关系、Job Receipt 上游归属、Attempt eligibility 与 `native_pass` 满足性校验（不承认自报 PASS）。PR #115（`6a6d0c2`）新增的 `hook_coordinator.py` 外层控制层按 2026-11-21 裁决撤销，仅回收其可复用 Report/Receipt/Exit 校验 | 由 Lefthook 的最终 Job 调用既有 `reconcile_expected_results()`，在可控失败或过滤后消费同次 Invocation 的实际产物、逐 Ref 对账并保留失败退出；退出 `#115` 专用 Coordinator CLI、控制状态与硬编码 `scheduler_ref`。若真实接线暴露公共接口不足，应修订该公共接口而非另建对账器 |
| `engineering_ci.execution._verify_execution_binding()` / `execute()` / `evaluate_source_resolution()` + Hygiene/Sources | PR #100（`6c3123c`）已交付有效 Hygiene/Sources 配置与 Worktree/Tree 绑定；PR #111（`a82d11e`）已交付 `#66-P0` `build_pre_execution_source_proof()` 独立执行前宿主输入证明；PR #132（`b10dce6`，`NEW-053`）与 PR #139（`e6e2098`）已交付 Hygiene forwarding 基线 Git Source/ODB 证明与 AST 迁移语义；PR #135（`edd4094`，`NEW-055`）与 PR #140（`bb52c6d`，`NEW-049`）已交付 Workflow/Shell 受检 Source 绑定与 Actionlint 校验；**PR #141（`c1f3019`）已交付 `#86/#66` Generated Producer Artifact Handoff 与独立 `evaluate_source_resolution()` → Expected/Native 验收**；**PR #143（`a99efd5`）**已补齐 Directory Overlay 成员通过 Artifact Closure Digest 的独立核验 | **独立 Source Resolution 与 Generated Producer Artifact Handoff 已由 PR #141 交付，不再列为待实现**；剩余工作为 Hook 触发下的 Index/Push 目标树物化（使各适用 Host/Dagger 工具在正常部分暂存下实际读取目标 Index/Ref 字节而非拒绝全部脏 Worktree）、正式 Profile 接入与 Native Report/Receipt 交接 |
| Dagger `execute_plan()`、Plan result sharing | PR #90（`bca1f12`）已交付原生 Plan DAG、Artifact Closure 与同次 Result Sharing；PR #117（`dd18b6a`）已交付 `#52` D2/D3 原生 Producer 与 Source Handoff；PR #119（`f3f5a7c`）已交付动态输入缓存；**PR #141（`c1f3019`）已将 Dagger verified-tree workflow handoff 与独立 Source Resolution 对齐并完成原生 Engine 验收** | Hook Event 的 Index/Push 目标字节、对比基线及 Git ODB 向 Dagger `plan-execution` 的真实交接，以及 Dagger 结果到正式 Hook Receipt 的消费；已交付 DAG/Closure/Source Resolution 直接复用 |
| ECI #52/#63/#61 | PR #113（`80c86a1`，T1-a）、PR #114（`a149edc`，R1）、PR #116（`910515c`，T2）、PR #118（`9af9001`，R2）、PR #120（`b8bb899`，T1-b）、PR #121/#136（R4 合同）、PR #122（`512d3ab`，G2/G3）、PR #123/#134（R5 合同）、PR #124（`0007a38`，`#52-D4`）、PR #138（`d0566bc`，D-F02 T1/T2→R1 Gate）、**PR #142（`f18e520`，Required Assessment 发布与 Source App 绑定，禁止 `skipped`/`neutral` 作为允许性结论）**及 **PR #143（`a99efd5`，`#66-B2/#63/#61` D-G01/D-G02、`NEW-014/059`、前置 Result 受信依赖、严格 Producer Grant 与 R1 受控联合消费）**已在 `main` 合入并完成合同验收 | 本地 Hook 只提供真实 Producer/Source/Check 事实与本地结构性 Receipt（`trusted=false`、`required=false`），不制造平行 Gate/历史受信结果；非 Workflow 受控 CI 执行来源、GitHub App 安装、Ruleset 服务端门禁与 R4/R5 生产闭环按 [#152](https://github.com/dymzz/engineering-ci/issues/152) 阶段②–⑤推进 |

**现行证据与修复规则（取代任何“已核销/不得重开”的绝对措辞）：** 已有符合当前合同的公共能力应复用，避免平行实现；但“已实现/已合并/测试曾通过/文档已冻结”与“当前契约完整”是不同事实。出现新证据、契约冲突、真实回归或未覆盖风险时，**必须修复正确归属的既有实现**（例如 #104 的对账接口、#102 的 Event Invocation/暂存实现），并按实际变化与风险验证；旧证据只在输入、范围、代码与规则仍等价时复用。不能机械重跑无关全量测试，也不能以“避免重复”为由拒绝必要修复。

若现有 Schema 足够，只改使用方式；实际欠缺字段按其已存在的 Source/Plan/Report 扩展，不维护第二份同义 Registry。无有效证据时不复用旧 PASS，但也不能因为一个相关 Check 失效便无依据重跑所有业务测试和 Wheel。

### 7.7 OTel 专题独立，不再形成当前设计依赖

Knowledge [PR #79](https://github.com/dymzz/engineering-knowledge/pull/79) **已经合并**，其 `design/engineering-verification/opentelemetry-weaver-delivery.md` 是 OTel/Weaver 专题设计来源；具体实施按 ECI #74/#75/#87 和消费者独立开展，不在此复制 Span/Exporter/Root 或 Trace Context 的完整规范。

**状态纠正：PR #72 已于 2026-11-16 合并**，历史上的 Draft/P1 设计验收及“后续决定 Ready/合并”不再是当前待办。其已合并内容中的有效 Source/Check/触发合同仍按实际证据保留；旧 L1/L2/Coordinator 方案仅在 §8 作为历史审计资料。#66 的 Lefthook→ECI 实际接线与 [#152](https://github.com/dymzz/engineering-ci/issues/152) 的完整 GitHub Workflow 退出按各自真实生产/消费职责推进，不被 OTel 专题错误地阻断。

## 8. 历史归档：S1-E v1 候选调度器核验（2026-11-15；当前已撤销）

> **历史性内容，不是实施指令。** 下述所有 L2 优先、ECI 独占 Hook 文件、Coordinator 及 `engineering-ci hooks install` 指令已由本文件首节及 [Issue #66](https://github.com/dymzz/engineering-ci/issues/66) 现行裁决撤销。旧负例“不显式转发 argv/stdin 的 Lefthook 普通 Job 收不到”不能推导出 Lefthook 原生 Hook 不可用。保留文字仅供追踪错误设计的形成、修复及已观察局部事实，现行验收按 §1–§2、§7.3。

**冻结对象：** Issue #66 的 S1-E 基础矩阵键 `matrix_version=eci-66-s1-hooks-v1`，以及“ECI 事件/Source/Check 权威 + 成熟调度器静态 Job 分派”的机制边界。矩阵的规范性内容仍只登记在 Issue #66；本节记录本 PR 的核验证据和差分，不建立第二份可独立变更的矩阵。

### 8.1 冻结的机制边界

1. **唯一 Hook 所有者（executable 与 Hook 文件分离）。** 同一仓库只允许一个实际安装/恢复 Git Hook 文件的组件；Lefthook、prek、原生薄入口不得各自覆写同一 `core.hooksPath`。L2 基线由 ECI 安装器独占 Hook 文件，Lefthook 只提供 Job 调度且不执行 `lefthook install`；只有 L1 胜出时 Lefthook 才作为唯一 Hook 文件所有者。迁移必须显式处理已有 Hook，不能静默覆盖。
2. **Invocation 身份独立于 Event 内容身份。** 每次 Hook Invocation 必须有独立 `invocation_ref`；内容摘要 `event_payload_ref` 只表示输入等价，不能充当 Job/Run/Attempt/Result 归属。外层 Coordinator 维护 `invocation_ref → run_ref → (job_ref, attempt_ref)` 映射并绑定 Report/Receipt/Hook Exit；#104 输入继续使用其现行 Run/Attempt/Receipt 字段，不直接注入未定义的 `invocation_ref` 字段。每次 Invocation 的 Run/Attempt 不得混同。
3. **ECI 事件与 Source 权威。** pre-commit 的 `GIT_INDEX_FILE` 与 pre-push 的完整 stdin 由唯一 ECI 入口读取并持久化为本次 Event Context；每个 Ref 的 Source/ChangeSet/Selection/Plan 由 ECI 生成。调度器只消费已准备事实，不能从 `HEAD`、`origin/main` 或文件 glob 反推。
4. **静态 Job，不假设动态注入。** 固定版调度器可按声明式 Job 条件、分组和并行执行；不假设其支持运行时按 ECI Plan 动态增删 Job。表达不了动态关系时，使用保守唤醒 + ECI 精确适用性判断，保留独立 Job 并发。
5. **Check 执行与结果归 ECI/#52。** 调度器进程退出、Job 成功或原生 stdout 不等于 Source-bound Check PASS；必须经现有 Check/Result 适配保留 Check ID、Source/输入、原生 exit/status、覆盖和未执行原因。
6. **单事件不等于单 Plan。** 一个 pre-push 事件可按 Ref 产生多个 Source/Plan，只对实际输入、规则、基线和环境等价的 Check 复用原生结果；跨 Invocation 复用另需 #63 受信接纳。

### 8.2 固定版本候选实测（Windows，Git 2.56.0）

| 候选 | 固定版本/来源 | 已观察事实 | 结论 |
| --- | --- | --- | --- |
| Lefthook | `2.2.2`；默认可执行安装基线 `uv tool install "lefthook==2.2.2"`（PyPI 官方 Wheel）。此前 npm 夹具仅作历史渠道观察 | `uv tool install` 安装成功；`lefthook version` = 2.2.2，可执行路径为 uv tool bin；`install` 同步 `pre-commit`/`pre-push`（仅作为 L1 对照观察，L2 不调用）；`dump` 可输出实际配置；`uninstall` 移除 Hook（仅 L1 对照）；两个各 sleep 0.3s 的 `parallel` Job 总耗时约 0.63s；`GIT_INDEX_FILE=<临时 index>` 原样传入 Job；两个 `use_stdin: true` Job 均收到完整 pre-push 元组；Windows 需 Git 自带 `sh` 在 PATH，否则 Job 以 `exec: sh not found` 失败 | 作为**参考调度器**继续同条件核验；其 stdin 行为只作观察，不写成多 Job 保证；Hook 文件安装仅 L1 候选 |
| prek | `0.5.10`；此前通过 npm 夹具安装，默认安装路径需按 PyPI/uv 另行核对 | `install` 将已有 Hook 迁移为 `.legacy` 并继续运行 legacy；两个 local Hook 均 Passed；安装/执行可工作 | 保留为替代比较项；不因一次 pre-commit 正例选定，也不与 Lefthook 双安装 |
| 原生薄 Hook | 无新增运行时实现 | 仅作为机制对照；并行、诊断、生命周期和失败汇总须由现有能力或有依据的新增公共机制证明 | 不宣称已交付，不默认胜出 |

**默认可执行安装基线（2026-11-15 更正，2026-11-16 收口）：** ECI/Knowledge 已有 `uv` 工具链，Lefthook 官方提供 PyPI Wheel，因此默认使用 `uv tool install "lefthook==2.2.2"` 取得 executable；需要随项目锁定时使用 `uv add --dev "lefthook==2.2.2"`，并以受控入口运行 `uv run lefthook version`。**这不表示执行 `lefthook install`：L2 的 Git Hook 文件只由 ECI 安装器创建/更新/卸载。** 只有 L1 最终胜出时才允许 Lefthook 管理 Hook 文件。不把 npm 作为默认或必须路径，也不维护 npm/uv 两套等价安装链。`uv python install` 只安装 Python 解释器，不得写成 Lefthook 安装入口。

**证据级别：** 本文 §8.2、§8.4、§8.5.5–8.5.6 中的实验为候选夹具记录的局部观察；本 PR 当前仅提交设计文档，**未附可独立复现的完整实验脚本、配置快照、命令和原始 stdout/stderr/exit 日志**。因此保留已报告事实及其具体限制，但它们不是经本轮审阅独立复核的运行时证据，更不是 S1-E 验收通过。后续仅归档已有有效原始材料，确实缺失且影响具体选型/实施的场景再定向执行；不得为修订证据等级重新跑全部无关检查。

**实测限制必须保留：**

- 以上仅验证调度器候选的 Job 分发/生命周期行为；Tach、Lychee、Sources 的真实原生 Job 尚未在该夹具中执行。
- Lefthook 2.2.2 在本夹具中两个 `use_stdin` Job 都收到 stdin，但设计仍要求“唯一 ECI 入口先消费/持久化原始事件”，不得依赖该行为作为多 Job 输入协议。
- `lefthook run pre-commit` 在无 staged files 时会跳过无 glob 的 Job；手工夹具需 `--all-files`。这不是 Git Hook 正常事件语义的替代。
- Windows 下 Lefthook 依赖 `sh`；安装器/诊断必须显式检查并给出可定位错误。

### 8.3 S1-E v1 覆盖状态（冻结内容，不是通过声明）

| 矩阵块 | 冻结要求 | 当前证据状态 |
| --- | --- | --- |
| F/P/R 三基础 Job | Tach `tach check`/`check-external`、Lychee、Sources `reconcile` 的条件唤醒、并行/必要顺序、失败/缺工具/未执行/不适用、Source 绑定 | **未完成**：候选夹具只证明调度器可并行和传递 Index/stdin；三工具真实 Job 未运行 |
| D 类 | 首次/强推/多 Ref/非 HEAD/远端删除/缺旧基线的 old→new 差异与不可复用反例 | **未完成**：等待真实 Push Source 夹具 |
| E 类 | 原生测试/前置条件/环境版本、未执行与失败传播；非 Git 事件不塞入 Hook | **未完成**：复用现有 execution 证据后再补 Hook 绑定 |
| 生命周期 | install/status/update/uninstall 幂等、第三方 Hook/`core.hooksPath` 共存/恢复 | **部分**：Lefthook install/uninstall、prek legacy 迁移已观察；共存/恢复和 ECI 安装器未实现 |
| 平台 | Windows/Git for Windows/Unix、普通/链接 worktree | **部分**：仅 Windows 普通仓库；Unix 与 linked worktree 未测 |

**冻结裁决：** 机制边界按 8.1 冻结；Lefthook `2.2.2` 作为参考调度器进入 S1-E 同条件验收，最终采用仍取决于 F/P/R 真实工具、D/E 反例和生命周期/平台矩阵。矩阵未通过的部分保持 `unknown/not_executed`，不得用调度器安装成功或两个合成 Job 的并行结果宣布 S1-E 通过。

**本 PR 的收口条件：** 本文档只登记冻结边界与已观察证据；真实 Tach/Lychee/Sources、Push 多 Ref、Host/Dagger Source 和消费者验收进入相应实施 PR。#66 S1-E 矩阵版本变更必须先更新 Issue #66，再更新本节证据引用。

### 8.4 S1-E 实施入口、共享身份与验收出口（2026-11-15 补充）

本节把 Review 提出的 Git 原生 argv/stdin、合并配置单一 Prepare、逐 Ref Exit 聚合、Glob 误过滤和安装副作用收敛为实施合同；不新增调度器、Registry 或 Required Policy。

#### 8.4.1 pre-push 原生参数与事件/Invocation 身份

唯一 ECI Event Prepare 必须同时接收并保留：

- Git 传入的两个 Hook 参数：`remote_name`、`remote_location`；
- 完整、有序的 stdin 行：`local_ref local_oid remote_ref remote_oid`；
- 对象格式长度（不得硬编码 40 字符零 OID；SHA-1/SHA-256 均按 Git 实际格式）。

**已实测（Windows，Git 2.56.0，原生 Hook）：**

```text
argv = origin|<bare remote path>
stdin = refs/heads/master <new_oid> refs/heads/master <old_oid>
```

**已实测（Lefthook 2.2.2 普通 Job）：** 未声明 `use_stdin` 的 Job 收不到 argv，stdin 为空。因此：

- **L1（Lefthook 启动 ECI Prepare）不能仅凭主 YAML 的 Job 命令声称获得 Hook argv/stdin。**
- **L2（ECI 独占安装的原生入口先读取 argv/stdin，再委托 Lefthook Job）是当前可证明的入口。**
- 若选择 L1，必须提供固定版 Lefthook 实际支持的外层 wrapper/模板证据；不得虚构 Job 参数 API。

**Git 原始 argv 整体未受信，`remote_name` 也可能含凭据（2026-11-16 补）：** Git 官方 pre-push 的两个 argv 都是 remote 标识；在未使用命名 remote 直接 push URL 时，`remote_name` 与 `remote_location` 都可能是带 `user:token`、认证 query 或其他敏感片段的 URL（例如 `git push https://user:token@example.com/org/repo.git HEAD:main`）。因此 **`remote_name` 不能先验当作安全字段**：两者都只允许在受控 Prepare 内部短暂使用；持久 Event、CLI `--json` stdout、日志、Job/Report/Artifact 一律写入去敏且可关联的 remote 身份（如受控 host/path 规范化摘要或稳定 label），不得原样输出用户名、密码、Token、认证 query 或其他确证含密片段。处理必须覆盖非 https、scp-like（`user@host:path`）及其它非标准表示，不能只依赖 `urlsplit()` 的 https happy path。

PR #102 的实际 `capture_pre_push()`/`persist_event()`/`--json` 产出须统一清洗 `remote_name` 与 `remote_location`；#59/#60 的 Secret 扫描不能替代 Hook argv 的安全序列化，也不新建通用脱敏服务或第二套 Event Store。定向回归：`remote_name == remote_location == "https://user:token@example.com/org/repo.git?access_token=…"` 时，持久 Event 文件与 `hooks capture --json` stdout 均不得出现 user/token/query secret，同时仍保留可关联的稳定 remote 身份；另以必要的非命名/非标准 remote 输入验证该身份可用。

`remote_location`（以及经清洗后的 `remote_name`）不得出现在不受控持久路径；首次 Push、远端 Ref 删除、强推、多 Ref、非 HEAD、同 Tree 不同 old→new 均按各自 tuple 保留，不得用单一 `HEAD`/`origin/main` 替代。

**Event 内容身份不得替代 Invocation/Run/Attempt 身份（2026-11-16 修订）：**

- `event_payload_ref`：对完整 argv/stdin tuple、对象格式、去敏 remote 身份等 canonical 内容计算摘要；它只表示**内容等价**，可用于内容缓存/去重键，不得直接充当一次执行的身份。
- `invocation_ref`：每次 Git Hook 实际调用独立生成（仓库 + hook 类型 + 进程/时间/随机 nonce 或等价 UUID/ULID，至少避免仅由 payload 摘要派生）；同内容连续两次 Hook 仍是两个 Invocation。
- `run_ref` / `attempt_ref`：由本次 Invocation 对应的 Scheduler/执行入口分配，并作为 #104 已定义 Routing/Receipt 对象的 Run/Attempt 区分身份。**`invocation_ref` 保留在 ECI 外层 Coordinator 的关联记录中，不直接写入 #104 的严格字段集合**（`_job_instance()`/`_job_receipt()` 目前不接受额外字段）；Coordinator 维护并验证 `invocation_ref → run_ref → (job_ref, attempt_ref)` 映射，Report/Receipt/#104 输入仍使用其现行 `job_ref/run_ref/attempt_ref/runner_ref/report_ref/issuer_ref/verification_status` 字段。若真实执行证明既有 Run/Attempt 不足，应在 #104 的公共接口按版本化修复，而不是在文档中假设新增字段已被支持。
- **跨 Invocation 复用**只在 #52 既有 Source/Rule/Input/Baseline 等价合同与 #63 受信接纳允许时成立；同 Event 哈希、同 Ref/OID 均不能单独证明旧 Result 可复用。
- **已有 PR #102 交接：** `src/engineering_ci/hooks.py` 的 `event_path()/persist_event()` 以 Event JSON 摘要生成持久路径，这只能作为内容身份；后续运行时须新增独立 Invocation 身份与路径（或 Invocation 目录），不得把内容路径直接作为 Job/Attempt/Result 归属。PR #102 的暂存写入还必须满足 §8.4.1a 的生命周期要求。
- **定向负例（S1-E）：** 连续两次内容完全相同的 Push 必须产生不同 Invocation/Attempt；第二次不得因相同 Event 摘要接受第一次 PASS，旧 Result 只能按 #63 的显式等价/受信复用规则处理。

#### 8.4.1a Event 暂存、原子发布与生命周期（PR #102 运行时验收要求）

这不是本设计 PR 的实现，也不新增 Event Store；它是 PR #102 `persist_event()` 的运行时交接验收条件：

- 每次 Invocation 使用唯一临时文件名/目录（含 invocation nonce），禁止固定同目录 `.tmp` 名并发互写；写完、fsync/关闭后以原子 rename/publish 发布到受控位置。
- 发布失败或进程异常时清理本次未发布临时内容；不得让半写文件成为可消费 Event。
- 原始事件目录限制仓库/运行者访问、明确存续时间与清理责任；普通日志/持久 Artifact 只写去敏身份（§8.4.1）。
- 相同 `event_payload_ref` 的并发/连续 Invocation 必须各有独立 Invocation 身份与暂存命名，不能以内容摘要覆盖正在被引用的 Event；不得为此新建通用 Secret 服务或第二套持久化框架.
- 运行时验收至少覆盖：并发相同 Payload 的独立发布、失败清理、权限/存续边界、Invocation 与 Event 路径的关联；未观察到竞态也可由代码可见风险要求修复，不在本文声称已观察到线上竞态。

#### 8.4.2 合并配置的单一 Prepare

一个原生 Hook Event 只能有一个可核验的权威 Prepare 结果。主 `lefthook.yml`、`extends`、`remotes`、`lefthook-local` 可能共同产生多个 `setup`；不得因为主 YAML 只写一个 `setup` 就宣称运行时只 Prepare 一次。

- 同一 Event 的重复 Prepare 必须共享同一 Event Context/Source 身份，或明确拒绝冲突；
- 各 Job 只能消费该结果，不得各自回退到 `HEAD`、`origin/main` 或独立 worktree；
- `lefthook dump` 是配置快照，不证明运行中实际 Job 激活/条件过滤；路由记录必须绑定本次有效合成配置与 Event 版本；
- L2 委托必须保证 Lefthook 子命令不会重新进入已安装 Git Hook，也不会二次读取原始 stdin。

**已实测（Lefthook 2.2.2，`extends: [extra.yml]`）：** 主配置与 extended 配置各声明一个 `pre-push.setup`，一次 `lefthook run pre-push` 同时执行了两者（两个 setup 标记文件均生成）。因此“主 YAML 一个 setup”不能证明运行时只 Prepare 一次；必须以同次 Event 的单一权威 Prepare 结果收敛。

**证据边界：** 已在本次夹具中观察到 `extends` 的两条 `setup` 均被执行；**具体合并顺序、冲突处理以及 `remotes`、`lefthook-local` 的对应行为仍未独立核验**。不能把已有 `extends` 正例写成未观察，也不能将局部观察推广成所有配置来源的保证。

#### 8.4.3 逐 Ref/Check → Git Hook 单一 Exit

- 当前有效本地策略要求执行的 Check，只要失败、未执行、Source/输入/路由身份不可验证，或未取得执行许可，Hook 必须以非零退出；
- 一个 Ref 成功不能覆盖另一个 Ref 失败；
- 远端 Ref 删除按真实删除策略处理，不伪造新 Commit 的 Native PASS；
- 合法不适用与 Advisory 失败按有权本地策略决定是否阻断；
- 逐 Ref/Check 的原生 exit/status/原因全部保留；Git Hook Exit 只表示本地 Hook 聚合，不等于 #52 Native Exit、#63 Trusted 或 #61 Required；
- pre-push 非零退出会中止整个 Push，必须明确提示该聚合语义。

#### 8.4.4 Glob/root 误过滤负例（必须纳入 S1-E）

固定 Lefthook 版本下必须验证：

- 根目录、嵌套目录和零层文件；
- 配置/锁/Manifest 变化；
- 被引用资源删除/重命名；
- 无匹配 Push 文件但仍有 Ref 比较/删除策略；
- staged 文件列表为空但仍有独立适用义务。

静态 `glob/root` 只能保守唤醒相关 Job，不能删除 Expected、不能单凭无匹配让 Hook PASS。无法静态表达的依赖由 ECI 精确适用性判断补齐，但不得默认唤醒所有生态工具。

#### 8.4.5 安装副作用与唯一所有者

- **默认可执行安装：** 使用 `uv tool install "lefthook==2.2.2"` 的官方 PyPI Wheel；若随项目锁定，使用 `uv add --dev` 并以受控入口运行 `uv run lefthook version`。安装器必须核验实际解析到的可执行路径与版本，而不是只核对声明版本。
- **Hook 文件安装（L2）：** 只由 ECI `hooks install/status/update/uninstall` 创建/更新/移除 Git Hook 文件；不得在 L2 默认路径执行 `lefthook install`。安装器必须记录实际 `.git/hooks/pre-commit`/`pre-push` 或 `core.hooksPath` 目标路径、内容/哈希、已有 Hook 处理与退出恢复；Lefthook 仅以 `no_auto_install`/`--no-auto-install` 运行 Job。
- **L1 条件模型：** 只有 L1 最终胜出时，才由 Lefthook 独占 Hook 文件安装/生命周期，ECI 不得同时安装；在文档/验收记录中明确唯一所有者。
- **历史非默认渠道观察：** 早前 npm 夹具中 `lefthook@2.2.2` 的 `postinstall` 被 npm 警告但未执行，新仓库未生成 Hook。该事实只属于当时非默认渠道，不构成本项目默认生命周期的验收要求，也不推广到 uv/PyPI。
- 真实 Hook 安装/升级/卸载/恢复、第三方 Hook 与 `core.hooksPath` 共存只能由一个生命周期所有者协调；禁止默认 `--force` / `--reset-hooks-path`，不得静默覆盖已有 Hook。
- 现有 `lefthook install`/dump/uninstall 观察仅证明 L1 对照或局部可执行体行为，不证明 ECI 安装器、L2 单所有者、Unix/linked worktree、共存恢复已完成。

#### 8.4.6 本轮证据状态

| 项目 | 状态 |
| --- | --- |
| 原生 Hook argv+stdin | 已实测（Git 2.56.0/Windows） |
| Lefthook 普通 Job 的 argv/stdin | 本次夹具的普通 Job **没有显式传递 Git argv，且没有启用 `use_stdin`**，因此未获得 argv/stdin；这不证明 Lefthook 不支持 `{0}`/`{1}` 等参数模板或其他输入传递方式，亦不能据此排除 L1 |
| L2 原生入口委托 | 已观察原型；ECI 实现未交付 |
| L2 Hook 文件单所有者 | 设计基线：ECI 安装/恢复；原型仅手工放置 `.git/hooks/pre-push`，ECI 安装器、`core.hooksPath`/共存/恢复未实现 |
| Invocation / Event 内容身份分离 | 设计合同；PR #102 现有 `event_path()/persist_event()` 仍是内容摘要路径，独立 `invocation_ref`、唯一暂存与原子发布未实现 |
| Lefthook `no_auto_install` | 已定向观察：配置项与 CLI 参数均不改写自定义 Hook，真实 Push 成功 |
| 合并配置单一 Prepare | 未独立核验 |
| 逐 Ref Hook 非零退出 / Final Reconciliation | 未实现/未实测 |
| Job Receipt → #104 对账接线 | #104 消费合同已合并；真实上游 Receipt 生产、Attempt qualification 与 Hook 外层对账未实现 |
| Glob 边界负例 | 未实测 |
| npm postinstall 副作用 | 仅历史非默认渠道观察；不构成 uv 实施要求 |
| uv/PyPI executable 安装基线 | 已实测：uv 0.13.6 安装 lefthook 2.2.2，`lefthook version` 正常；不包含 Hook 文件安装 |
| ECI Hook 文件安装/status/update/uninstall | 未实现；`lefthook install` 仅为 L1 对照观察，L2 不采用 |
| S1-E F/P/R、D/E、生命周期、Unix/linked worktree | 仍未完成 |

**8.4 裁决（2026-11-15 更新）：** 先前原生 Hook argv/stdin 正例和普通 Lefthook Job 无显式传参的负例只证明各自实际观察范围，**不足以断言 L2 已经完成委托闭环，也不足以否定整个 L1**。固定版的 Git 参数模板、`setup`、stdin 交接以及 Job 结束后 Final Reconciliation 均须在真实配置下核验。L2 优先验证其 Event Coordinator 闭环，L1 只有证明同等闭环后才进入完整比较；prek 与薄 Hook 仍保留完整候选地位。矩阵状态仍以 Issue #66 `eci-66-s1-hooks-v1` 为准，本节不新增第二份规范性矩阵。


### 8.5 从接口提案到完整事件闭环的实施判据（2026-11-15 系统复审）

本节**只记录 PR #72 的候选机制、执行衔接及验证证据差分**；规范性责任和阶段范围以 [Issue #66 的 S1-B/D/E 执行闭环统一裁决](https://github.com/dymzz/engineering-ci/issues/66) 为准。此前 §8.4 的实测记录与未验证项保留为历史原始观察，不能将其扩大声明为完整 Hook 实施。

#### 8.5.1 首先证明外层收敛，而不是只证明 Job 能启动

- **ECI Event Coordinator** 是逻辑上的一次 Hook 事件控制路径，可以复用现行 ECI CLI/Execution/Plan/Result 能力；不批准第二个 Runner、工作流 FSM、结果数据库或 Check Gate。输入包含真实 pre-commit Index 或 pre-push argv+所有 Ref 元组；生成独立 `invocation_ref` 与逐 Ref 的 `run_ref`/`attempt_ref`；输出为同次、按 Ref 受控的 Source/Selection/执行前 Expected 及可消费的 Source Execution View。
- **Dispatcher** 使用成熟工具已经支持的静态 Job/Group 条件并行、必要依赖及诊断；每个 Job 消费同次 Event/Ref/Source，产出已有 #52/Runtime Native Check/Result。调度器的 `glob/root` 只作保守唤醒，不承担 Expected 枚举权威或受信 PASS 判定。
- **Final Reconciliation 是一个外层阶段，不是一次 #104 调用**：Dispatcher 返回后，即使部分 Job 失败、缺失或被过滤，外层 Coordinator 仍须执行该阶段。对多 Ref 的每个可执行 Ref Scope 分别传 `ref_scope_id` 调用 `reconcile_expected_results(expected, ref_scope_id=…, …)`；单 Scope Expected 不传 `ref_scope_id` 或传 `None`（非空会被拒绝为 `expected_ref_scope_unexpected`）；多 Ref 聚合容器不是可执行 Source，不能整体传给 #104。每个 Scope 产生独立 Receipt，再由外层统一汇总未归属 Observations、缺失/失败/unknown 和当前本地策略，形成一个 Git Hook Exit。跨 Ref 的原生 Result 复用仍须 Check/Rule/Source/Baseline/Input/Attempt 等价证明，不能因逐 Ref 调用机械重复执行；一个 Ref PASS 不能掩盖另一个 Ref 的 missing/FAIL。Job Receipt 只在核对实际 Execution Report 与 Job/Run/Attempt 后生成，不能把 Job exit=0 填入 `upstream_verified`。若进程被强制终止，无法保证回执存在，不能伪称完成；可控异常或结果缺失时应禁止成功退出。
- **Hook Exit** 只聚合当前本地策略下所有 Ref/Check 的执行许可，失败 Ref 不被成功 Ref 覆盖；Native Exit/Check 归因原样保留，不替代 #63/#61 受信和最终 Required 判定。Expected 域未完整认证时必须保留 `partial/unknown`，不能把已运行 Job 的成功当成完整 ExpectedSet。

#### 8.5.2 候选拓扑与优先筛选

| 候选 | 同次 Event / Job / 最终对账的责任 | 首道可行性证据 |
| --- | --- | --- |
| **L2：优先验证** | ECI 唯一管理 Hook 文件安装/恢复与 Receive/Prepare/Invocation；委托 Lefthook 固定版 Job 调度（不执行 `lefthook install`）；**ECI 在调度返回（含失败）后 Final Reconciliation** | Git argv+stdin 原样捕获；真实支持的 `lefthook run`/配置来源/版本与进程退出；ECI Hook 文件路径/哈希 + `no_auto_install: true` 或 `--no-auto-install` 保持文件不变；无二次调用 Hook 或重复 Prepare；Job 过滤及失败后仍完成对账；同名内容不同 Invocation 不混同 |
| **L1：保留条件候选** | Lefthook 安装 Hook（单一所有者），使用真实支持的 Git 参数模板/Setup/wrapper 交接完整事件，并有**明确的外层机制**保证末端独立对账；ECI 不同时安装 Hook 文件 | 合成有效配置与多 Setup 的唯一权威 Prepare；原生参数和 stdin 完整转交；失败/过滤后 Final Reconciliation 仍然执行；Hook 文件实际路径/哈希与恢复证据 |
| **prek + ECI** | 同样需要可靠的 Event Prepare、可并行的适用 Job 和外层 Reconciliation | 固定版真实 CLI/安装/过滤/退出语义，功能覆盖与 L2/L1 同等 |
| **原生薄 Hook + ECI** | Git 原生入口传递 Event；利用现有 ECI/Host/Dagger 功能完成必要调度和最后对账 | 并发、条件唤醒、诊断、失败汇总与生命周期的维护成本有真实证据，不退化为单串行执行 |

**选择顺序**：先作入口/退出/收敛可行性裁决；无法履行完整事件闭环的候选**停止其完整跨生态成本矩阵投入**，但保留已有效的局部观察。只有可行候选才比较同条件 Check 覆盖、并行关键路径、生命周期和跨平台净收益。不能凭某次 synthetic sleep 时间或安装成功直接选定方案。

#### 8.5.3 Source Execution View 与受控写入

Source Execution View **不是新增 Schema/存储层**：从 #66 P0 现行 Source/Plan 物化/挂载目标字节，File Job 使用真实文件内容；P/R Job 使用必要完整模块、配置与关系闭包；Git-aware Job 保留可用 ODB、Index 和正确 Git 工作目录；Host/Dagger 消费相应目录/产物及真实执行 Origin。目标 Index=B、默认 Index=A、Worktree=C 时，原生检查必须读 B 或拒绝，`{staged_files}` 路径或 `git write-tree` 匹配不能单独证明真实读取。

只读 Checks 可以在相同不可变 View 并行；合法自动格式化/修复是**不同写入阶段**，不得与读者暗中竞争。若写入导致最终待提交 Source 从 A 变成 B，所有实际依赖变化字节/配置/关系的旧 Result 按现有 Invalidation 规则失效，对 B 重新建立必要的 Source/Selection/Expected/Check 证据；不无依据重跑无关 Check，也不禁止所有可控自动修复。

#### 8.5.4 逐层验收，不按每个反例新增一套实现

**首道可行性验收**：用真正的 pre-commit Index 或 pre-push 全参数 Event、实际受控 Source/Expected，运行 Tach、Lychee、Sources 三项适用原生 Job（必要时使用其中不适用分支反例），至少覆盖全成功、某 Job 失败、Job 未启动/被过滤、Prepare 失败、Scheduler 返回失败，以及**最终对账是否仍产生正确 Hook Exit**。检查同次 Source/Tach 是否重复生产。该验收可复用已完成的真实 Check 原始证据，补缺的只有 Hook 联动事实。

**必须包含的身份/所有权反例：**

- 连续两次内容完全相同的 pre-push（相同 `event_payload_ref`）产生不同 `invocation_ref`/Attempt；第二次不能沿用第一次 PASS，旧 Result 只能按 #63 显式受信等价规则复用；#104 输入使用独立 Run/Attempt/Receipt，不注入未定义 `invocation_ref` 字段；
- 多 Ref：对每个可执行 Ref Scope 分别传 `ref_scope_id` 调用 #104，产生多个 Ref Receipt；外层汇总未归属 Observations/缺失/失败/unknown 后形成一个 Hook Exit，不能把聚合容器整体传入；
- 单 Scope Expected：调用时不传 `ref_scope_id`/传 `None`，非空值必须被 #104 拒绝为 `expected_ref_scope_unexpected`；合法空 Expected 不因空集自动成为受信完整 PASS；
- L2 单所有者：ECI 安装/委托前后实际 Hook 文件（或 `core.hooksPath` 目标）路径与哈希不变，未执行 `lefthook install`；第三方 Hook 与已有 `core.hooksPath` 的安装/退出恢复可核对；
- 错误 Job/Run/Attempt/runner/report 或缺失 Job Receipt 时 #104 返回 unknown/missing，不得由 Job exit=0、自报 receipt 或同内容 Event 路径提升为 matched；
- 提前拒绝/blocked/not_executed：生产方提供合法 `checks=[]` Report 时，Collect/Verify 保留真实 status/exit/reason 并交由对账；不产生合法 Report 时不得伪造 Result/Receipt，外层将缺失证据作为 unknown/missing/blocked 并保持非零 Hook Exit；
- 终态 Attempt FAIL 时旧 PASS 不能覆盖；Final Reconciliation 在失败/过滤后仍执行并映射到非零 Hook Exit。

**候选可行后**：再验完整 F/P/R/D/E 中真实新增的 old→new/多 Ref、动态输入、写入 A→B、配置/规则版本与 Glob 过滤，Windows/Unix/Linked Worktree、install/status/update/uninstall、既有 `core.hooksPath` 与**实际采用的 uv/PyPI 安装、升级、卸载、Hook 文件修改及恢复行为**、Knowledge 与非 Knowledge 消费者的同条件成本。**不要求默认方案验收未采用的 npm `postinstall` 或 npm 安装链**；仅有确实使用该渠道的消费者才需针对其特有副作用定向验证。不同风险分层验收，不把完整生态候选清单误作一次 Event 的 Expected 域。

证据应可关联事件/源/Ref/配置、Check/Job 的激活和原生结果、调度返回状态、最终对账与 Hook Exit；缺少原始运行记录时明确 `unverified`。**PR #72 仍是 Draft 机制提案，不宣称本节接口已实现或全部矩阵通过**。

#### 8.5.5 安装基线：executable 与 Git Hook 文件分离（uv/PyPI）

**L2 默认路径（当前设计基线）：**

```sh
# 1) 只安装并核验 Lefthook executable，不接触 Git Hook 文件
uv tool install "lefthook==2.2.2"
lefthook version
# 记录 Get-Command / which 解析到的实际 executable 与版本

# 2) Git Hook 文件只由 ECI 生命周期入口安装/更新/卸载
engineering-ci hooks install --repo .
engineering-ci hooks status --repo .
# 核验实际 .git/hooks/pre-commit、pre-push 或 core.hooksPath 目标
# 的路径、内容/哈希及恢复状态；此处不执行 lefthook install

# 3) 委托 Job 时禁止 Lefthook 改写 Hook 文件
lefthook run --no-auto-install pre-push
# 或配置 no_auto_install: true（两者择一，不同时部署）
```

需要随 Python 项目锁定时：`uv add --dev "lefthook==2.2.2"` 与 `uv run lefthook version` 只负责 executable；Git Hook 文件仍由 ECI 安装器管理。

**只有 L1 最终胜出时**，`lefthook install` 才可作为唯一 Hook 文件安装者；此时 ECI 不得同时安装/恢复同一文件。文档/验收记录必须明确实际所有者，不能把 `lefthook install` 留在 L2 默认路径中。`no_auto_install`/`--no-auto-install` 只抑制运行时自动同步，**不等于**允许显式 `lefthook install`。

**已实测（Windows x64）：**

- `uv 0.13.6` 执行 `uv tool install "lefthook==2.2.2"` 成功，安装 1 个 executable；
- `lefthook version` 输出 `2.2.2`；
- 可执行路径为 uv tool bin（`%USERPROFILE%\.local\bin\lefthook.exe`），由 `Get-Command` 解析；
- 该可执行体执行 `lefthook install` 同步 `pre-commit`/`pre-push`（**仅 L1 参考观察，不构成 L2 默认路径**）；`lefthook run pre-commit --all-files` 正常；
- `no_auto_install: true` 下 `lefthook run` 前后自定义 Hook 文件哈希不变（见 §8.5.6）；
- 此前 npm 夹具的调度行为（parallel、`GIT_INDEX_FILE`、argv/stdin、dump）与当前 uv 安装体同一上游版本，仅分发渠道不同；不重跑全部矩阵来证明换包管理器。

**L2 仍需验证：** ECI 安装器对 executable identity/路径的绑定；Hook 文件实际路径（含已有 `core.hooksPath` 与第三方 Hook）在 install/status/update/uninstall 前后的字节/哈希；`core.hooksPath` 退出恢复；L2 Prepare→Job→Final Reconciliation；Unix/linked worktree。npm 仅作为历史非默认渠道记录，不进入默认生命周期验收。

#### 8.5.6 L2 原生入口 → Lefthook Job → Hook Exit 的原始原型（非产品实现）

在临时 Git 仓库中用固定 `lefthook 2.2.2` 做了一次 L2 形状验证，**不是 ECI 运行时实现**：

- `.git/hooks/pre-push` 是原生 `sh` 入口，读取两个 argv 和完整 stdin，写入临时事件目录，再执行 `lefthook run pre-push`，等待返回后写 `rc.txt` 并以该返回值退出；
- Lefthook Job 从 `ECI_EVENT_FILE` 读取已准备事件并写 `job-receipt.txt`；
- 成功 Push 时 Job receipt 与事件 argv/stdin 一致，Hook exit 0；
- 将 Job 改为 `exit 7` 后，真实 `git push` 被中止，`lefthook` 返回非零，wrapper 记录 `rc.txt=1` 并以 1 退出；
- 原生入口先消费 stdin，Job 未再次读取 Hook stdin；该形状避免了“多个 Job 争抢原始 stdin”。

**`no_auto_install` 定向核验（2026-11-15）：**

- 配置项 `no_auto_install: true` 在固定 `lefthook 2.2.2` 中生效：自定义 `.git/hooks/pre-push` 文件哈希在 `lefthook run pre-push` 前后不变，没有 `can't rename pre-push to pre-push.old` 警告；
- CLI `lefthook run --no-auto-install pre-push` 同样不改写 Hook 文件；
- 在 `no_auto_install: true` 下完成一次真实 L2 Push：Job receipt 与 argv/stdin 一致，Push 成功，Hook 文件哈希不变，`rc.txt=0`；
- 因此 L2 首选使用配置项（固定版支持，作用域覆盖所有 Job 调用）；CLI 参数作为等价备选，**不同时部署两套控制**。

**敏感信息边界：**

- 上述原型使用无凭据的本地 bare remote，所以临时 `argv.txt/stdin.txt` 未含 credentials；这只是夹具条件，**不证明生产直接 URL Push 安全**。Git 的 `remote_name` 与 `remote_location` 都是未受信原始参数，二者都可能含 `user:token`、认证 query 或 scp-like 凭据。
- 生产 Event Prepare 必须限制原始事件暂存位置、访问、存续与清理；原始两个 remote argv 只在受控 Prepare 内使用，持久 Event、CLI `--json`、日志、Job/Report/Artifact 只输出 §8.4.1 的统一去敏、可关联 remote 身份（含对 `remote_name` 的处理）。
- 回归必须断言持久 Event 文件与 `hooks capture --json` stdout 均不含 user/token/query secret，并保留非命名 remote 的稳定关联身份；不能只用无凭据 bare remote 或仅处理 `remote_location` 的 https 路径证明安全。
- 不得把原型 `.git/eci-event/*.txt` 的持久化方式直接作为正式接口，也不为此新建通用 Secret 管理服务；#59/#60 的扫描报告不能替代 Hook argv 的安全序列化。

**同时观察到的限制：**

- 该原型没有 Expected/Final Reconciliation、没有 Tach/Lychee/Sources、没有多 Ref、没有 Unix/linked worktree，也没有将 Job 原生结果映射为 #52 Result；因此只证明 L2 的入口/Exit 形状可行，不是 S1-E 通过。
- **L2 上线前的明确退出条件：** ECI 管理的 Hook 文件/`core.hooksPath` 在委托前后保持不变；固定版 `no_auto_install` 或 `--no-auto-install` 必须有真实文件状态及安装/委托退出证据；若固定版仍存在特定配置副作用，再记录实际阻碍，不开发第二个 Hook 安装器或兼容框架。

#### 8.5.7 Expected→Result 对账的上游 Producer 与 #104 接线（2026-11-16 收口）

#104 已交付 `engineering_ci.reconciliation.reconcile_expected_results()`，并要求 Routing/Resolution/Observation 由真实执行事实生产。**PR #72 本设计只规定生产责任与交接，不在此实现运行时，也不允许第二个 Reconciliation/Runner/Gate。**

**真实生产责任：**

1. **ECI 外层 Event Coordinator（Invocation 所有者）**：读取一次 Hook 的完整 argv/stdin，生成独立 `invocation_ref`；按 Ref 产生真实 Source/Selection/Expected；向 Scheduler/执行入口分配本次 `run_ref`/`attempt_ref`，并记录实际激活、过滤、失败、重试终态。Coordinator 内部维护并验证 `invocation_ref → run_ref → (job_ref, attempt_ref)` 映射；#104 对象只接收其现行 Run/Attempt/Receipt 字段，不注入未定义的 `invocation_ref`。它产出 #104 的 `routing.job_instances[]`（Job/Run/Attempt/runner/report 身份与 `eligible/superseded/terminal/unknown` + `qualification_ref`），不得从 Job 名称或 exit=0 反推。
2. **Job 执行包装**：消费同次 Event/Ref/Source，调用既有 `execution.execute()` 或等价原生入口，产出 Execution Report；Job 自身成功/失败只作为执行事实，不是 Check PASS 或 #63 受信。**提前拒绝/blocked/not_executed 等路径必须由生产方提供 #104 可验证的 Report 结构**（至少 `checks=[]`，并保留真实 `status/exit_code/reason`），或明确声明不产生有效 Report；Collect/Verify 不得伪造 `checks`、Native Result 或 `upstream_verified`。此接口修改属于对应 Runtime/Execution 实施 PR，本文只规定交接要求。
3. **ECI Collect/Verify 层（Job Receipt 生产者）**：在调度器返回后读取实际 Execution Report，计算 `report_ref` digest，核对 Report→Result 成员关系；只有同时核对实际激活 Job/Run/Attempt/runner 后，才生成 `kind=engineering-ci-job-attempt-receipt`、`report_ref`、同一 Job/Run/Attempt/runner、ECI Producer `issuer_ref` 与 `verification_status=upstream_verified` 的 Receipt。**不得用 Job 成功字符串、Hook exit=0 或自报配置直接填写 `upstream_verified`。** Report 缺失、`checks` 非法或结果成员关系不可证明时，不签发 receipt，把该 Job 作为 `missing/unknown/blocked` 执行事实传给外层对账，并保留非零 Hook Exit 的可能。
4. **Resolution 生产者**：从受控规则/配置、#66-P0 Source materialization/baseline、真实执行事实产生 #104 的 `resolutions`；Native Result 的 Rule/Source 不能被反写成 Expected，缺失证据保持 unknown/insufficient。
5. **Final Reconciliation（逐 Scope 调用）**：Dispatcher 正常/失败/过滤返回后，外层 Coordinator 执行一次 Reconciliation 阶段，并按 Expected 形态调用 #104：
   - Schema v2 多 Ref `per_ref` 容器：对每个可执行 Ref Scope 分别传对应 `ref_scope_id`、该 Ref 的 Routing/Resolutions/Observations；
   - 单 Scope Expected（不含 `per_ref`）：调用时不传 `ref_scope_id` 或传 `None`；传非空值会触发 `expected_ref_scope_unexpected`，不得为统一循环伪造 `ref_scope_id`。

   每个调用产生独立 Receipt；外层汇总所有 Receipt、未归属 Observations、真实失败/缺失/unknown、跨 Ref 结果与当前本地策略，形成一个 Git Hook Exit。不能把多 Ref 聚合 Expected 容器整体传给 #104；一个 Ref PASS 不能覆盖另一个 Ref 的 missing/FAIL；跨 Ref 原生 Result 复用仍需 Check/Rule/Source/Baseline/Input/Attempt 等价证明，不因逐 Ref 调用重复执行。合法空 Expected 仍须按完整义务与不可信/未知来源处理，不能仅因空集自动成为受信完整 PASS。
6. **接口不足的处理**：真实接线若证明 #104 公共接口不足，应修订 `engineering_ci.reconciliation` 的公共接口并按其实际变化验收；**不得**另建第二套对账、证据数据库、Scheduler 或 Required Gate。

**定向验收（运行时 PR 执行）：** 合法 Report→Result/Job Attempt 正例；同 Payload 不同 Invocation 负例（旧 PASS 不满足新事件）；错误 Runner/Report/Attempt、旧 Run 重挂、过滤/未启动、终态 FAIL 后仍执行 Final Reconciliation；#104 receipt 的 unknown/missing 正确映射到非零 Hook Exit；单所有者安装文件哈希不变。文档 PR 不重跑真实 Dagger/Tach/Lychee/Sources 全矩阵。

**更新状态（2026-11-16）：** L2 的原生 argv/stdin 接收、委托 Job、非零 Exit 传播及 `no_auto_install` 防同步冲突已有**候选夹具报告的局部观察**；本 PR 未附可独立执行的完整原始脚本、配置快照和 stdout/stderr/exit 记录，**尚未完成独立证据复核**。本节新增的 Invocation 身份、单所有者 Hook 文件安装、Job Receipt 生产与 Final Reconciliation 接线仍是设计合同，运行时实现与验收未完成。不可因此宣布 L2 可部署或 S1-E PASS；但本文档设计可在这些合同收口后独立复审。

**收口规则：** 若 #66 正文已吸收同一有效需求，本 PR 仅保留差分决策、证据和对应实施入口。Hook 实施确认后择一保留短期设计历史或关闭本草案，不作为长期并行权威；不得创建新缓存层、任务调度框架或 Required 判定器。
