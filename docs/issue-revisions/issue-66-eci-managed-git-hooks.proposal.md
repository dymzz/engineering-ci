# ECI #66 — ECI-managed Git Hooks（需求补充提案，待审定）

> 目标 Issue：[ECI #66](https://github.com/dymzz/engineering-ci/issues/66)。这是**增量需求草案**，不是另一个 Issue 全文版本，也不是实施代码。审定后将有效条款并入 #66 §3「同源本地、Dagger、Actions 入口」和验收清单。已有 ECI PR #67 已合并的 `eci_core` 选择/Hygiene/CLI 为基础，不能重新实现一套。

## 1. 工程目标与责任裁定

- **ECI 拥有 Git Hook 的通用实现和分发**：pre-commit/pre-push 原生入口、安装/升级/诊断/卸载、Git 事件与受检 Source 适配、原生检查/前置条件执行、结构化结果和失败传播。下游不得继续维护独立的通用 `hook_adapter.py`、Index/Push 快照引擎、独立 Check 调度/报告判定器。
- **下游拥有项目策略**：使用哪些 Git 事件、哪些实际适用 Checks、代码/非代码测试映射、全量升级条件、Identity Commit、wheel/CLI 命令及配置、特定工具版本。ECI 仅消费配置和执行公共机制，不嵌入 Knowledge 的 RDF/Manifest/业务数据语义。
- **统一消费约定**：采用 ECI Hooks 的仓库由 ECI 安装器管理公共入口，不能要求每仓复制/维护 800 行适配代码。仓库可以保留自己的声明性配置和确有证据必要的项目专用 Hook；不得与 ECI 通用入口重复运行同一验证。
- **强制性分层**：在仓库的工程策略中可把安装并运行 ECI Hooks 定为开发工作流要求；但本地 `core.hooksPath`、`--no-verify` 和用户配置使 Hooks 不具备安全意义上的不可绕过性。合并/Release 真正强制性仍由原生 Actions、ECI #61 的 Required 完整性和经过核实的 GitHub 保护设置实现，不由 Hook 成功与否伪造 GitHub Required 状态。

## 2. ECI 交付接口（CLI 命名待实现期核对；不视为已存在）

推荐复用现有 `engineering-ci = engineering_ci:main` 包入口，提供如下统一子命令，而不是建立新框架：

```text
engineering-ci hooks install --repo .
engineering-ci hooks status --repo .
engineering-ci hooks uninstall --repo .
engineering-ci hooks run pre-commit --repo .
engineering-ci hooks run pre-push --repo .
```

### 2.1 安装与生命周期

1. 安装器核对 ECI 可执行文件及**能力合格**的固定包/Commit 版本、项目规则版本，创建可复用的薄 Hook 启动入口，通过仓库本地 `core.hooksPath` 或等效 Git 原生机制接线。可选 ECI 自管薄入口文件须明确所有权，不得让下游维护逻辑副本。
2. 不静默覆盖、删除已有第三方 Hook 或 `core.hooksPath`；检测冲突并给出显式的保留、链式调用或经确认的迁移决议。安装幂等；`status` 证明当前仓库真实生效的 Git Hook 路径、入口和 ECI 来源；`uninstall` 仅撤销自身变更，恢复已记录的旧设置，不修改不属于 ECI 的文件。
3. 同一入口在 Git for Windows / Unix 工作，尊重仓库实际 Git 顶层、worktree 的 Git config 作用域、路径/空格及原生退出码。调用已安装的固定 ECI，不在每次 Git 事件中运行 `uv sync`、重新安装 CLI、拉取 Dagger Engine 或重新生成 SDK。
4. 不能因为安装过就声称用户所有 Git 操作必经 Hook；检查、报告、Actions 和真实服务端 Required 状态分别记账。

### 2.2 Git 事件到真实执行对象

- **pre-commit** 保留 Git 传入的 `GIT_INDEX_FILE` 等原始事件上下文，读取本次 Index Tree；选择与原生执行必须在可验证的**同一 staged Tree** 上发生（例如以临时 Commit/隔离 worktree 执行），不得在未暂存的不同工作树上冒充已验证暂存内容。构造与清理隔离 worktree 是 ECI 的公共责任。
- **pre-push** 读取 Git 实际提供的全部 `local_ref local_sha remote_ref remote_sha`，逐个验证待推目标。保留有效提交内容身份；既有目标使用真实 old→new（包括强制推送的移除项）；新分支、缺失基线/对象和多引用应给出可核查的扩大检查或阻断，不能 `origin/main == HEAD` 空差异通过。变更中断或缺前置事实不得假 PASS。
- `select`、`execute`、Hygiene、Sources、Tach/工具及前置条件必须消费同一 Source/配置/工具身份；测试目标为空不代表其他工程 Checks 不适用。未执行、失败、未知、不适用与复用结果保持分离。默认只使用必要本地进程；确实需要 Dagger 时才进入对应原生执行路径。

### 2.3 成本与重复消除（避免 Hook 变成全量 CI）

- 分别为 pre-commit / pre-push 声明适用检查及真正的升级规则；Hook 本身变更应有**真实 Hook/事件契约测试映射**，不因未映射而无依据每次运行所有 Knowledge unittest + wheel；未知影响仍需可靠升级，不允许简单忽略 Hook 文件。
- `select --validate-tach` 产生的原始 Tach 事实，输入/工具/配置相同且范围有效时应被后续 Hygiene 直接消费，不得仅为第二份报告再跑 `tach check`；`tach check-external` 是不同检查，按适用范围保留。
- Identity Commit、wheel 构建和仓外 CLI Smoke 是类型化前置条件：只在实际影响/策略要求时运行或**有真实有效证据**时复用；覆盖缺失或无法确定必须阻断适用最终结论，不以降级为「无测试」规避。
- 为独立 worktree、select、execute、Hygiene/Sources、前置条件、清理提供轻量**阶段耗时**及原生诊断；即使子进程输出需保持机器可读，也要有可见阶段进度与最终耗时分解。不得再建第二个缓存/结果数据库或全量性能平台。

## 3. 与现有 Issues 的接口

| 责任 | Owner / 现有资产 |
| --- | --- |
| 公共 Hooks CLI、Git Index/Push 事件适配、Source-bound 执行及定向结果/时长 | **ECI #66**，扩展已合并 PR #67 的 `eci_core` / CLI |
| 原生 Check Report/Enforce、Actions 执行结果出口 | **ECI #52**，不重建 Check Runner |
| 历史结果 T1-b PR→main 与 T2 本地→Actions 受信接纳 | **ECI #63**，本地 Hook 成功不等于已可受信复用 |
| 最终 Required/Release、真实 GitHub Ruleset/Branch Protection | **ECI #61**，不建平行 Gate |
| `ci-selection.toml` 等领域规则、安装消费、PR #73 旧适配器迁移/删除及验收 | **Knowledge #68** |

上述公共 Hooks 生产能力可与 #66 的 Source/Check 修复、#52 Actions、#63 历史接纳并行推进；只有某条路径的集成验收才需要对应已完成的公共接口，**不得设置全 Issue 串行等待**。

## 4. 可执行验收与反例

- [ ] 干净的 **Knowledge** 和至少一个**非 Knowledge** 消费仓库，安装同一固定 ECI Hooks 包、配置并实际触发 pre-commit/pre-push；验证 `status`、重复安装、更新、卸载/恢复、已有第三方 Hook 以及 Windows/Unix 必要路径。
- [ ] 临时 `GIT_INDEX_FILE=B`、默认 Index=A、未暂存工作区=C 时，只验证 B 的原生事实；准确保留 Git 事件原始身份及隔离执行 Tree，非零原生失败阻断提交。
- [ ] 非 HEAD 推送、多引用、旧→新强推包含删除、首次推送、缺失远端对象：正确执行目标内容及有效输入覆盖；不可解析时扩大范围或阻断，不出现空差异成功。
- [ ] 测试目标为空但 docs/source/Tach 适用时真实执行对应原生 Checks；不因 `no_selected_tests` 跳过。
- [ ] Hook 脚本/配置变更选中相关公共/消费者测试；证明**不相关**业务全集和 wheel 不会被误触发，同时相关影响不漏测。
- [ ] 同源 `tach check` 单次执行并复用其有效原生事实；不同的 `check-external`、Sources 和适用 Identity/Wheel 仍如实执行。
- [ ] 记录一次有代表性的真实 pre-commit 耗时分段、适用测试数和重复检查数；无效/缺工具不标 PASS。
- [ ] 安装/运行 Hooks 不能替代 GitHub 端 Required；`--no-verify` 的本地绕过路径在实际服务端验证策略中按其风险独立处理，不宣称无法绕过。

## 5. 待审决策与合入步骤

1. 公共安装是采用 ECI 自管稳定 Hook 目录，还是 ECI 安装时创建**零逻辑薄转发入口**：以 Git for Windows/多 worktree 的可复现性、已有 Hook 兼容和版本升级成本比较，**不提前制造两套实现**。
2. 确认 CLI 子命令名称及配置字段后再进行代码实施，避免在本需求 PR 中宣称现成接口。
3. 审定后将**仅新增/冲突的条款**并入 ECI #66；保留其已交付 PR #67 事实、现有来源/Hygiene/选择器接口及 #52/#61/#63 职责，不复制本草案成另一个常驻规范源。


## 6. 批量工具接入与生命周期策略（第二轮审定）

**实施裁定：一次设计公共接线，按实际仓库的适用工具矩阵成批实现，按执行类别集中验收；不采取一个工具一个 PR、一个全量 CI 验收，也不把全部候选默认配置成每次提交的 Required。** 已合并 ECI PR #67 的选择/Hygiene/CLI、ECI #52 的现有 Check/Actions/工具资产和原生 Dagger Checks 作为基线，先核对现有能力，只补实际缺口。

### 6.1 采用一个成熟 Hook 调度器，而不是两套

- **Lefthook 为默认候选**：使用其 Git 生命周期入口、文件 glob、适用并发和 commit-msg 转发。ECI 保有事件 Source、Index/Push Git 对象、影响选择、原生 Check、身份和结果判定；glob / staged_files / push_files 是调度输入，不能代替 ECI 的真实 Tree 证明。
- **prek 是备选、不是同时启用的第二套**：其 Rust/uv 环境和内置快检有价值，但截至本次核查，公开文档明确指出 pre-push 默认只检查第一个 ref，且可能在当前工作树上执行。除非由 ECI 独立接管所有 Git 原始 ref 及目标提交快照，否则不满足 #66 的多引用和非 HEAD 保真要求。
- **Lefthook 的 use_stdin 有一个关键约束**：即使有多个命令开启 use_stdin，只有一个接收 Git pre-push 的完整 stdin。将 Git 原生 ref 元组交给一个 ECI 入口，使用现有 ECI 选择/执行器对适用检查分发；其余独立工具不能按 HEAD 或 origin/main 结果冒充特定 old→new 推送验证。避免在 ECI 里再建立一个与 Lefthook 并行的生命周期调度器。
- **只读检查可以并发，写 Index 的检查不能无条件并发**。Lefthook stage_fixed 会调用 git add；Ruff/shfmt/taplo 自动修复若与部分暂存或不同 GIT_INDEX_FILE 叠加，可把未暂存字节纳入提交，或使先前验证的 Tree 失效。pre-commit 门禁默认 check-only；自动修复由显式命令或能核验真正 Index 更新的独立流程完成。
- **工具包及缓存不双建**：继续复用现有 ECI/Runtime 的可安装工具、原生结果、版本和制品来源；Hook 管理仅负责触发与接线，不为每项扫描器复制安装器、下载器、结果解释器和状态数据库。

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

### 6.4 一批接线，按公共路径和新增风险集中验收

1. **一次资产与已有证据盘点**：查看 ECI/Runtime tool_id、入口、版本/OS、原生 Checks、可用失败/跳过状态和现存验证；不用再逐工具设计 Schema、重新安装或跑全套正反例。
2. **一个公共适配 PR 批量接入所有对目标仓库确实适用的工具**：先确定唯一 Hook 调度器、统一 Source/Config/Result、工具执行组及前置关系；静态基础、架构/文档、依赖/API、动态/形式检查可分组并行配置。其他语言/未使用的 API 协议保留在 ECI 能力目录，真实消费者出现时再启用，不降低长期需求范围。
3. **按公共执行类别共享验收**：Index≠Worktree、临时 GIT_INDEX_FILE、真实多引用/强推、工具未安装/真实非零失败/跳过/不适用、Source/配置不匹配及原始结果复用，各用代表场景验证**公共路径一次**；对新命令、独特语义或尚无有效证据的 adapter 补其必要原生正反例。已有同输入/同版本证据直接复用，不要求每项都重复完整 CI。
4. **Required 与性能分别验收**：Required 使用现有 ECI #52/#61/原始事实，Hook 汇总 PASS 不取代服务端完备；选择一组文档、Python、Hook/配置、依赖、未映射、push old→new 变更记录墙钟、重复扫描、选测规模和覆盖。只比较相同有效输入/检查范围的成本，不靠漏跑项目提高速度。
5. **独立消费者收口**：ECI #66 证明一个 Knowledge 与至少一个非 Knowledge 消费者、公共 Hook 与原生结果可用；Knowledge #68 在有效固定版本接入并删除重复的 hook_adapter.py / 安装器。#63 历史可信接纳与 #61 Required/Release 继续按各自实际契约验收，不阻断新检查执行。

**审定重点**：选择 Lefthook 作单一生命周期调度层，但不要另起 ECI 并行事件调度；一次批量接入不是让每次 Git 事件都运行所有候选；本需求 PR 不宣称工具均已成功运行或已修改 Ruleset。审定后仅同步到 Issue #66 的有效新增条款，不把此文件留下作为第二份长期规范。


## 7. 工具触发与执行作用域的正交分类（第三轮审定）

**必须从现有 ECI 配置、Source、Selection Plan 和原生 Check 结果中推导出** Trigger / Analysis / Invalidation 三类关系，不得另建三套配置表或规则引擎。它们分别描述唤醒、真实读取/覆盖、旧结果何时失效；`glob`、`root` 和 `{staged_files}` 仅提供 Lefthook 粗过滤，不能证明暂存 Tree 受检。原有 `eci_core.selection`、`execution`、Hygiene/Sources、Plan/Report 是实施位置。

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

### 7.3 Lefthook 配置语义、共享配置及根目录过滤（示意）

- `glob` 决定是否唤醒相应命令；`{staged_files}` 和 `{push_files}` 只提供路径而非保证读取了暂存 Tree/正确推送 refs。`root` 设置子目录并可能进一步限制候选文件；`glob` 仍按**仓库根**路径解释。没有匹配路径时省去工具进程启动，但不等于没有调度器/Git 固定成本。
- **P/R/D/E 的项目级入口必须同时考虑全局配置/锁文件和依赖输入**。例如 Java 子项目 `root: "services/java/"` 即使其 `glob` 列了仓根 `settings.gradle`，仍可能因 `root` 范围筛选而失去唤醒。对消费仓根共享配置的模块检查，优先让触发入口运行在仓根，由 ECI 按 `--project` 或配置指向实际目标；仅在证明完全独立时使用 `root` 做窄化过滤。共享配置影响哪些子项目，需要受信依赖映射或适当扩大，不是盲目全部运行。
- `src/**/*.js` 等递归 glob 在不同匹配模式下可能漏根层直接子文件；匹配模式须以安装的 Lefthook 版本及实际路径测试固定。删除/重命名、带空格/特殊字符的路径仍由 ECI 的原生 Git 事件与 NUL 安全解析覆盖。
- **Read-only pre-commit 默认**：文件级 Check 也要基于本次 `GIT_INDEX_FILE` 对应的 Index Tree，不能直接用未暂存工作区内容伪造 PASS；`stage_fixed: true` 的 git add 可能改变部分暂存内容，不能默认作为验证模式。
- **Pre-push 单一 stdin 消费者**：从 Git 提供的完整 ref old/new 元组生成 Source/Plan；ECI 复用现有选择/执行，不允许以硬编码 `origin/main` 或单个 HEAD 的结果覆盖所有引用。

以下 YAML **仅是触发与执行范围关系的设计示意，非可部署的已实现 CLI**；命令 `engineering-ci hooks run ... --profile` 仍待审定。需要精确覆盖的条件、缺失 glob、真实版本参数必须在实施 PR 中定向验证。

```yaml
pre-commit:
  commands:
    python-file:
      glob:
        - "*.py"
        - "*.pyi"
        - "pyproject.toml"
        - "ruff.toml"
        - ".ruff.toml"
      run: engineering-ci hooks run pre-commit --profile python-file
    python-project:
      glob:
        - "*.py"
        - "*.pyi"
        - "pyproject.toml"
        - "uv.lock"
        - "tach.toml"
        - "pyrightconfig.json"
        - "ci-*.toml"
      run: engineering-ci hooks run pre-commit --profile python-project
    rust-package:
      glob:
        - "*.rs"
        - "Cargo.toml"
        - "Cargo.lock"
        - "build.rs"
        - ".cargo/**"
        - "rust-toolchain.toml"
      run: engineering-ci hooks run pre-commit --profile rust-package
    java-package:
      # No root here: root-scoping may hide shared repository build inputs.
      glob:
        - "services/java/*.java"
        - "services/java/**/*.java"
        - "services/java/pom.xml"
        - "services/java/build.gradle"
        - "services/java/build.gradle.kts"
        - "pom.xml"
        - "settings.gradle"
        - "settings.gradle.kts"
        - "gradle.properties"
        - "gradle/libs.versions.toml"
      run: engineering-ci hooks run pre-commit --profile java-package --project services/java
pre-push:
  commands:
    eci-all-ref-checks:
      use_stdin: true
      run: engineering-ci hooks run pre-push
```

**示例的 `glob` 不是规范性完整 Trigger Set**：消费其他脚本、父 POM、共享插件、Git attributes、构建源码或动态配置时应扩展相应触发规则。ECI 可把候选事件统一交给一个 Source/Impact Plan，从而让多个 Check 复用同一 staged Tree 及 Tach 原生结果，避免按每个 Lefthook Job 再建一个无关临时 worktree。不存在相关项目输入的语言工具不应启动。

### 7.4 配置与判定数据（复用已有 ECI 公共契约）

每项 Check 的相关信息应**从现有 ECI 对象推导或定点扩展**。下列名称是逻辑视图，**不要求新增同名 `trigger_set`、`analysis_set`、`invalidation_set` 的 TOML/Plan/Report 字段**，更不允许建立第二份工具 Registry、失效算法或执行结果账本：

- `hook_phase`、F/P/R/D/E `analysis_scope`、Trigger Set 的输入路径/事件/共享配置和 Invalidation Set 的完整依赖规则及外部有效期；必要时区分根目录与子模块执行上下文。
- Check ID/适用规则、原生运行输入、实际 Source Tree、受影响模块或旧新契约、工具/规则/锁版本、必要环境与前置条件及真实执行状态。
- 复用的原始 Check 及其有效覆盖/身份，工具进程是否启动、单项耗时、并行或写入冲突。`skipped`、`not_applicable`、`not_executed`、`blocked` 和 `passed` 不得互相代替。

### 7.5 定向验收：同时检验唤醒、分析覆盖与失效

- [ ] 仅变更 Python 文件：适用 Python F/P Checks 运行；无对应 Rust/Java 输入时其原生命令**不启动**。
- [ ] 只修改 `ruff.toml` / `.ruff.toml` / `pyproject.toml`：唤醒对应文件/项目检查，并扩大到受配置影响的分析集合，不能仅检查本次改动的 TOML 文件；无依据不升级所有业务测试/所有 wheel。
- [ ] 只修改 `pyrightconfig.json` 或类型存根/依赖版本：类型检查覆盖其受影响模块；只改 `Cargo.lock`、Cargo workspace 配置或共享 Gradle/POM：唤醒相关包，包括 `root` 限制外的共享配置来源。
- [ ] 只删除/重命名某文档引用的目标或 Sources/RDF 资源：Lychee/Sources/消费者反向依赖按适用关系验证；不能仅因没有 Markdown 改动就跳过。
- [ ] 单独更新工具规则/版本、CI Workflow、构建定义：受影响 Check 旧 PASS 失效或由可信等价证据保留，不以 Hook 空唤醒或文件扩展名匹配代替原生覆盖。
- [ ] 在线链接或漏洞数据库在 Git 无变更时发生有效变化：适用的计划/Release 检查重新判定，或明确既有结果有效期和未验证状态；不能宣称文件过滤已经充分保障。
- [ ] 原始 Git Index 与 Worktree 不同、临时 `GIT_INDEX_FILE`、多引用/强推/非 HEAD 目标及共享根配置均按真实被检对象和依赖范围判定。
- [ ] 公共 Source/失效/范围规则只做一次组合正反验收；新增工具仅补真实命令与特有风险，已有同输入/同版本 Check 成果不重跑全量 CI。

### 7.6 对应现有 ECI Source/Selection/Check（实施映射）

| 现有对象 | 已实现的事实 | 应扩展的缺口 |
| --- | --- | --- |
| `selection.ChangeSet` / `changeset_multi` | `snapshot`、`index_tree`、`execution_tree`、`references`、`changed_paths` 和 Git 完整性 | 让 Hook Index 和每个 Push ref 复用现有 Source；glob 不得变成第二份最终影响判定 |
| `selection.Config` / `load_config` | `escalation_files/prefixes`、`ignored_files/prefixes`、`module_tests`、`non_code_rules`、`prerequisites`、`check_scope` | 将配置、锁文件、工具规则、非代码依赖的触发与失效关系写入已有消费者规则；不能用新的 Hook-only TOML 重复定义 |
| `select()` / `_reverse_closure()` / `_check_scope()` | Python 反向依赖、非代码映射、逐 Check `selected`/`unknown`/`not_applicable` | 按 Check 扩大实际受影响范围，取代把所有配置变更一律升级为全部单测和所有前置条件 |
| 既有 `plan()` 的 `generator/git/selection/check_scope/inputs` | Git Tree、ECI 版本、选择原因、Tach 和部分规则摘要 | 在 `inputs` / 相关 Check 节点补充真实消费的 Hygiene、Sources、工具规则、依赖和环境身份；不重复一个新 Plan |
| `execution._verify_execution_binding()` / `execute()` 与 Hygiene/Sources | Git 输入绑定、原生 Check 和前置条件结果、失败/未执行状态 | 执行前核实相应规则/环境身份；消除 Hygiene 用独立 `code_changed` 路径再次决定 Selection 已裁定的同一 Check 适用性 |
| ECI #63 和 #61 | 分别负责旧证据受信、最终 Required/Release 完备 | 扩展既有接纳/覆盖证据消费，不在 #66 新增受信引擎、最终 Gate 或缓存账本 |

**规则**：对真实输入/Check 的新增需求直接补现有 Selection、Plan、Execution 或原生 Report；若已具备充分数据就只修改使用逻辑。没有证据的旧 PASS 不可复用，但也不能为一个相关 Check 的失效无依据重跑所有业务测试与 wheel。

### 7.7 OpenTelemetry (OTel) 执行观测需求：复用 Dagger 原生 Tracing

**上游规范权威已确立，不在 ECI 重新裁定：** Knowledge 已合并的 [Engineering Verification 总设计 §3.6](https://github.com/dymzz/engineering-knowledge/blob/main/design/engineering-verification/overall-design.md#36-opentelemetry-cicd-semantic-conventions--canonical-vocabulary-and-native-correlation) 是本专题采用 OpenTelemetry CI/CD/VCS Semantic Conventions 的**现行上游工程采纳契约**；[分阶段实施计划 §8](https://github.com/dymzz/engineering-knowledge/blob/main/design/engineering-verification/implementation-plan.md#8-cross-stage-opentelemetry-cicd-integration-track) 已确定 O1–O4 跨阶段交接。OpenTelemetry 官方原生规范本身保有字段、资源和语义的源权威，Knowledge 规定采纳范围及具体原生身份映射；ECI 负责其本仓实现和原生运行事实。不得在 ECI #66/#72 另起一套同义 Pipeline/Task、Resource、Metric、Log、VCS 命名或标准采纳 Profile，也不应创建第二个 OTel 全仓架构 Issue。

**按上游规范直接消费**：实际 GitHub Pipeline/Job/Attempt 才使用标准 Pipeline Run / Task Run Spans、CI/CD Resources、LogRecord、Metrics 与 VCS 属性；只存在本地 Hook/CLI Check 的场景使用适用通用 OTel Span/Log，不伪造 Pipeline、Job URL、Run/Attempt ID。实际交付时固定 SemConv 版本与字段稳定性（包括 Release Candidate / Mixed），按 §3.6 已列出字段/映射实施，不能仅写“参考 OTel”或把旧的 `vcs.repository.ref.*` 当有效字段。对 OTel 尚无法表达的 ECI 原生 Source/Selection/Check/Required 语义，继续保留 Plan/Report/#63/#61 权威，仅作有来源的关联或必要的有名扩展，**不反向修改标准定义**。本节及下文验收只规定 #66 的公共执行端交付，不重复维护这些标准字段的可编辑定义。

**现状分层**：ECI 当前 GitHub Actions 已实际执行 `dagger check`，CLI 输出 `TRACE PASSED`、原生 Check 结果和时长；Dagger 本身已有基于 OpenTelemetry 的 Tracing 能力，**应直接复用**。这份日志只证明 Dagger 原生 Trace/计时运行，**没有证明 OTLP Span 导出或与宿主 ECI 的父子 Trace 已连通**。当前 ECI `main` 未见 Hook→Selection→Check→Report 的宿主埋点及其专项验收，本节属于待实施要求，不能将现有 Dagger TRACE 视为新链路通过。历史 ECI #39 已移除没有真实 CI 消费者的通用 Tracing/Mining/日志依赖，不能因此恢复另一套框架。

- **现有能力优先、按上游归属**：Dagger Engine/Functions/Checks 内部的操作级 Span、Check 结果与耗时由 **Dagger 原生 Tracing** 负责，ECI 不为每个 Dagger Function 再包同义 Python Span，不重复生成每项工具的第二份结果。只在实际缺失的**宿主边界**（Git Hook/ECI CLI、Git Source 绑定、Selection、非 Dagger 原生 Check、Prerequisite 与 Report）添加 ECI 侧必要 Span，并关联既有 Dagger Trace；有独立意义的外层边界 Span 不等于重复内部操作埋点。
- **具体的 Context 交接**：明确由**调用方注入**、由**接收方提取**的传播契约，记录真实使用的 Trace Context carrier（如经 SDK/目标命令确认支持的环境变量或显式参数）。普通 `subprocess` 环境继承、Dagger 进程启动或 GitHub Actions Job 边界**不自动证明**父子 Span 关系；验证确实传播时才建立 parent，其他场景按实际可验证的 Span Link 或独立 Trace 表达。CI/CD 语义约定版本及使用的属性按固定 SDK/规范版本核实，不预设尚不稳定的字段名已标准化。
- **原生证据权威**：`ChangeSet`/Source Tree、Selection/Plan、Check ID、原始 Report/JUnit、ECI #63 受信接纳和 #61 Required 仍负责验证事实。Trace/Span ID 仅用于观测关联；一个 Span 成功或导出成功不能把原生 `failed`、`blocked`、`not_executed`、`skipped`、`not_applicable` 提升为 PASS。Lefthook 在进入 ECI 前跳过的检查必须由现有调度/选择事实解释；**没有 Span 不等于检查被执行或正确跳过**。
- **轻量、可选且安全**：本地阶段耗时、原生 Check 结果、原因和重复执行次数不依赖 SDK/Collector；OpenTelemetry API/SDK/Exporter 仅按实际启用路径使用，OTLP 不作为每位下游开发者的安装或 Git 提交前置。未配置、禁用、Exporter 超时/网络错误不能改变原生结果及 Git 退出码，不能造成无边界的隐性 Hook 网络等待。默认不得记录令牌、源码/文件内容、敏感参数和 stdout/stderr；观测本身的时延/资源开销要测量。

### 7.8 OTel 可机器核对的验收产物（公共 ECI 验证一次）

**产物不是“在日志中看到 TRACE PASSED”**。针对一条真实 ECI Hook 或 CI Run，以临时测试 Exporter（例如 Python SDK `InMemorySpanExporter`，或能读取的等效测试接收端）收集**实际完成的 Span 数据**，同时保留同次真实的 Selection Plan、原生 Check Report/退出码和阶段计时。验收应可自动化核对，且不必启动所有候选工具。

| 机器核对对象 | 必须验证的事实 |
| --- | --- |
| Run / Trace / Span 身份 | Trace ID、Span ID 合法；能与同次 Source/Selection/Check/Report 关联；重复进程没有冒名 Trace |
| Parent / Link | 对每个声称的父子边或 Span Link，核对双方 Span Context/目标身份；未传播时显示独立边界，不能凭时间相近推断 |
| 原生 Check / Source 关联 | 对应同一原始 Plan 的 Source Tree/Run、Check ID、规则及工具版本；不以 OTel 属性取代来源/证据身份 |
| 时序 / 性能 | 各实际启动的阶段具有真实 start/end/duration；与原生阶段计时误差可解释，OTel 不造成二次 Tach/测试/构建执行 |
| Status / Skip | Check 的原生 `passed/failed/blocked/not_executed/not_applicable` 与 Report 一致；Span 状态表达该操作的观测信息，不能据此替代 ECI 原生结论；Lefthook 提前跳过则核对调度/Selection 事实 |
| Privacy / Export | 测试导出的 Span 中不包含敏感源码、token、原始命令输出；禁用与故障下仍有原生结果和计时 |

- [ ] **成功场景**：ECI 真实执行一次适用的宿主 Check/Prerequisite（若场景实际适用），导出 Span 并逐项对照 Plan/Report、工具启动次数及耗时；Dagger 已有原生 Trace/计时可复用原始日志，不强制再跑 Dagger 同义检查。
- [ ] **失败与跳过**：各选一个代表性原生失败和不适用/调度跳过，校验 Check 原生状态、Git 退出码及缺失/存在 Span 的正确解释；一个失败的 Exporter 不能掩盖原生失败。
- [ ] **跨进程 Context**：对一处确实要求传播的 Hook/ECI→子进程或 Dagger 边界，检验注入和提取双方的 Trace Context（或已验证 Link），不能只检查“子进程继承了环境变量”。
- [ ] **可选性与故障**：SDK 未启用、Exporter 缺失/不可达/超时，原生 Check 状态与退出码保持一致，阶段计时仍可读取；测量采集额外耗时并确认无敏感输出泄漏。
- [ ] **重复建设检查**：没有恢复 ECI #39 删除的通用日志/Mining/Tracing Provider，没有为 Dagger 内部 Checks 重复埋同义 Span，没有新增 OTEL 专用 Gate/结果库或为了验收重跑完整工具矩阵。

**所有公共 OTel 状态映射、失败、导出异常、传播与隐私验证在 ECI 集中验收一次。Knowledge #68 等下游只验证真实固定版本的消费者 Source/Check 关联与基本耗时/原生状态，复用以上公共证据，不重复执行这些公共正反测试。**

上述条款审定后仅同步 ECI #66 的实际新增部分，避免把提案长期保留为第二套规范；无需新建缓存层、框架或 Required 判定器。
