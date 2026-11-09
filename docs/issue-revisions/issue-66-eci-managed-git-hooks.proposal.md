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
