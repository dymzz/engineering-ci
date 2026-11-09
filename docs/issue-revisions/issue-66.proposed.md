## 目标

将 [engineering-knowledge](https://github.com/dymzz/engineering-knowledge) 已合并的工程 Hygiene、Tach 与增量测试选择通用能力落到 `engineering-ci` 公共实现，供多个 Python 仓库本地、Dagger 和 GitHub Actions 使用。Knowledge 成为公共能力的真实消费者，仅维护自身规则与配置。

**以 ECI [PR #67](https://github.com/dymzz/engineering-ci/pull/67) 已合并代码为当前实现基线**：对照 Knowledge [PR #61](https://github.com/dymzz/engineering-knowledge/pull/61)、[#66](https://github.com/dymzz/engineering-knowledge/pull/66)、[#69](https://github.com/dymzz/engineering-knowledge/pull/69)、[#70](https://github.com/dymzz/engineering-knowledge/pull/70) 的**实际合并行为与有效测试**核实覆盖；**只补生产方缺口并扩展既有 ECI API/CLI/Dagger/Workflow**，禁止把已交付的 `eci_core` 选择/Hygiene/CLI 再迁一遍。不以「接口尚未存在」为理由在 Knowledge 永久保留第二套通用实现。验收依据是执行行为、真实测试覆盖与收益，不是代码行数最少或兼容旧包装。

## 当前实现基线（2026-11-09；保留此前已核实的迁移依据）

- **已交付，不再重新迁移：** ECI [PR #67](https://github.com/dymzz/engineering-ci/pull/67) 已合并，公共 `eci_core` Git/Source 选择、Hygiene、Sources、`engineering-ci select` / `execute`、Plan/Report 和部分 Dagger/Actions 接口进入 `main`。已交付代码不能作为新任务再次迁移。

- **剩余生产方缺口：** 修复 Index/Worktree/PR/Push Source 选择与实际执行内容的身份一致性；`eci_core/execution.py` 目前在无选中业务测试时提前返回，适用原生 Checks/前置条件仍可能未运行；Dagger `execute_plan` 的 `tree_verified: false` / `trusted: false` 不证明执行输入受信。完成原生 Check/前置条件、Agent 可定位诊断和实际 Dagger/Actions 调用。不得用成功的选择计划、Check 名称或业务测试 PASS 代替覆盖完成。

- **后续职责：** #66 继续交付原生执行生产方；#52 工具 Report/Enforce 与 Actions 传递；#63 受信历史执行接纳与复用；#61 负责最终 Required 完备/聚合及 Release/服务端连接——**复用既有 Release/Manifest/Run/Artifact 机制，不新建平行 Gate、聚合器、Runner 或证据账本**。

- **历史段落读法：** 下文「已核实的 ECI 现状」与 §1/§2 中的 Knowledge 脚本路径是**迁移前行为基线与契约清单**，用来对照验收；**不得解释为还要从这些脚本重新实现一遍**。2026-11-09 `main`（含 PR #67）已交付的能力只核实缺口并补齐，不重迁。

## 已核实的 ECI 现状与复用边界

- 现有 `.dagger/src/engineering_ci_module/main.py` 有 `python_vulture`、`python_test`（pytest）、`python_test_artifacts`、核心 `@check`，`python_static.py` 提供 Vulture 的原生退出码/诊断分类。**复用**这些能力，不重写执行器或将 Vulture findings 误判为工具故障。
- `.github/workflows/reusable-python-ci.yml` 已承载公共 Dagger Checks、报告与 Artifact；新增入口应与其实际调用和现有报告契约对接。
- **PR #67 之后（以当前 `main` 为准）：** 公共 `src/eci_core/hygiene.py` 已包含 Lychee/Vulture/Tach 配置和 `tach check` 执行路径，`selection.py` / `execution.py` / `sources.py` 与 `engineering-ci select`/`execute` 已在仓内。**具体未完成部分：** `hygiene.py::_tach()` 目前只执行 `tach check`；虽然配置解析了 `tach.external`，此路径尚未实际调用 `tach check-external` 并传播其独立结果；选中原生 Check 的完整 Dagger/Actions 执行交接同样尚未完成。本 Issue **不再**把已有通用 Lychee/Tach/Hygiene/选择算法当作「缺失、从零接入」的任务；只补这些确认缺口并核对相对 Knowledge 基线的行为差异。[ECI #47](https://github.com/dymzz/engineering-ci/issues/47) 的历史表述仅作背景，不能覆盖上述已交付事实。不为已交付能力另开零散 Issue。
- [ECI #52](https://github.com/dymzz/engineering-ci/issues/52) 管已有工具资产/选择/门禁/Actions 连接（不是本 Issue 的 Git→测试影响选择算法）；[ECI #63](https://github.com/dymzz/engineering-ci/issues/63) 管 Required Checks、可信证据、Release 与门禁传播。本 Issue 交付可用的检查/选择**生产方接口与结果**，并与两者衔接，不重建其治理/证据机制。

## 1. Hygiene 公共能力：对照基线核实，只补缺口

以 Knowledge `scripts/check_repo_hygiene.py` 与 `tests/test_repo_hygiene.py` 的**已验证行为**为对照基线，核对 PR #67 已合并的 `eci_core` Hygiene/Sources/公共入口是否已覆盖下列契约；**仅补真实缺口与回归**，禁止从 Knowledge 脚本重新提取/再实现一套通用 Hygiene：

- Git 基线、变更/工作区文件、重命名与删除、`git diff --check`；Python 变更导致的纯转发模块检测与原生诊断。
- 可配置的有效文档文件集合、相对路径保真与 Lychee 原生离线检查；原生命令缺失、无适用文件、实际链接失效及执行故障不得被混为 PASS。
- 复用已实现的 Tach `check`；补齐并验收 `check-external` 的实际执行路径、独立原生结果/退出码与适用 Dagger/Actions 的失败传播。配置中有 `tach.external` 不等于执行过外部依赖检查，不再重实现 Tach/选择算法。
- 对已有 `python_vulture` 衔接消费者的扫描路径、置信度、advisory/required 策略及原生结果；不能为摘要/门禁重复扫描。
- 来源文件的路径合法性、文件存在、摘要/大小、Git attributes 等**可通用校验机制**。Manifest 的字段投影、来源身份、豁免语义、适用目录/文件集、来源契约判定仍由消费者配置/扩展定义；不要把 Knowledge 的 RDF/Catalog 特定规则硬编码进 ECI。

调用方能指定根目录、Git 基线、实际检查集合、项目配置和规则；通用代码不依赖 Knowledge 作为运行时模块。允许调整已有 ECI API 和 Dagger Functions，不通过新增宿主 Runner/包装注册表解决组合问题。

### 公共实现、领域规则与 Hygiene 输入的交接

- **唯一实现位置：** 通用 Git/Hygiene、Tach 解析、增量选择、结果结构及本地执行以 PR #67 已进入 `main` 的可安装公共实现为准（`src/eci_core` 等）。ECI 可安装包与独立 Dagger 模块 `.dagger/src/engineering_ci/` 不能假定彼此可直接 import；须通过实际可安装的公共包/依赖让本地 CLI 与 Dagger 消费**同一版本算法**，禁止再复制源码或维持第二套选择器。可调整两侧模块与构建配置；公共 CLI 必须能在 ECI checkout 之外独立使用。
- **Hygiene 真实输入集合：** Dagger 的 `repository.py::SOURCE_EXCLUDES` 为 Python 检查排除了 `guides`、`docs/**/*.md`、`scripts/*.md` 等输入，不能直接当作 Lychee/来源字节检查的通用 Source；按具体检查所需文件构造输入并对齐宿主相对路径。文档存在于上述排除目录时也必须真实检查，不能因目录不在容器中报告不适用。
- **来源校验分工与适用范围：** 已合并的 `eci_core/sources.py` 具备来源校验机制，但 `load_config()` 当前强制非空 `acquired_roots`，且 `require_under_acquired_roots` 默认 `true`，因此**已实现的是 acquired-source 约束下的来源校验，不自动等于通用任意来源清单对账**。实施时先核定公共 Sources 的实际消费范围：若仅服务 acquired-source 配置，明确其可复用边界并保留相应限制；若需要一般来源文件集合对账，才调整这些非必要强制前提、相应配置/验证，同时保留 acquired-source Profile 真实约束。通用机制处理可配置文件存在、路径、真实大小（`size_bytes`）、SHA-256、Git attributes 与原生诊断；Knowledge 的 Manifest 字段投影、空路径、环境目录豁免、未登记 acquired 文件和来源身份语义留在消费者领域规则中。不得把 Knowledge 的 JSON 字段解释硬编码为所有消费者的行为，也不得因泛化而弱化既有 acquired-source 验收。
- **对照而非重迁：** 将 Knowledge 精确路径/前缀、资源类型、测试消费者、升级/忽略规则继续作为可加载消费者配置或必要领域扩展；明确精确匹配优先级、组合/去重、未知输入处理。对 `tests/test_repo_hygiene.py` 的通用案例：以 ECI 已有测试/夹具核对覆盖，**只补缺失回归**；Knowledge 领域契约正反测试仍留在 Knowledge。不要为了删旧脚本丢失异常来源、路径或字节约束，也**不要**把已迁入 ECI 的通用用例再实现一遍。

## 2. 增量选择与原生测试执行：对照基线核实，只补缺口

以 Knowledge `scripts/select_tests.py`、`tests/test_test_selection.py` 的**已验证行为**为对照基线，核对 PR #67 已合并的 `eci_core` selection/execution 与公共 CLI；**仅补真实缺口**，禁止从 Knowledge 脚本重新迁移一套选择器：

- Git diff、merge-base、当前工作区、push/PR 对象识别；正确处理新文件、删除和重命名，不用可变分支名推断历史 merge 对象。
- 解析消费者现有 `tach.toml`、Python 模块匹配、反向依赖闭包；独立表示测试文件的真实消费者关系与非 Python 数据/脚本/资源输入映射。
- 消费者可声明「模块→测试」「文件/前缀→测试」「安全忽略」「升级触发」「需要的前置检查」「测试框架及执行参数」。选择器输出可解释的文件、基线、影响闭包、被选测试、扩大原因与实际前置条件；**无证据覆盖时不以 0 tests 宣称通过**。
- 保留 Python 标准库 `unittest` 的实际选择性运行、原生退出码和 Windows UTF-8 行为；与 ECI 现有 pytest/Dagger 报告能力并存，各自使用其原生执行，不强制消费者迁移测试框架。
- 区分 *selected / executed / passed / advisory / not-applicable / not-executed / reused* 的可验证事实；候选历史证据能否复用由 #63 的有效性与门禁链决定，选择器本身不得把「无需执行」伪装为 PASS。

**Knowledge 负责的配置：** `tach.toml`、模块与测试实际对应关系、RDF/Content/Catalog/Application 数据输入和消费者映射、Identity 固定提交与 Wheel/CLI 前置条件等。ECI 不硬编码 `data/catalog/**`、`content/issue-24/**` 等 Knowledge 专用路径。

### Tach 图可信性与测试前置步骤

- **增量图先决条件：** 当前图解析只读 `tach.toml` 声明，不证明它与实际 Python imports 一致。使用反向闭包作测试覆盖依据时，必须关联同一输入快照/规则版本下有效的 `tach check` 结果；图配置缺失、校验失败或无对应有效证据时，不能直接把静态闭包当完整覆盖。可以按适用策略扩大相关回归或阻断选择性通过，但不能静默选零项。第三方依赖边界由 `tach check-external` 另行检查，不与图正确性混为一个结论。
- **可执行前置条件：** Knowledge 现有 `requires_identity_commit`、`requires_wheel_smoke` 只是选择器输出信号，不会抓取提交或构建 wheel。公共结果应表达类型化的必要前置检查及所需消费者参数（例如固定身份提交、wheel/仓外 CLI），执行端真实执行/复用并记录各项结果；缺少参数、未运行或失败不得因测试集合自身 PASS 而标为覆盖完整。不得把 Knowledge 的固定 SHA 或 CLI 指令写死在 ECI。
- **原生结果清晰分层：** 选择结果只描述应执行的集合及原因，执行结果另外报告真正运行的 unittest/pytest、Tach/Hygiene 与前置检查、原生退出码、失败及不适用项；历史证据接纳仍由 #63 决定。

## 3. 同源本地、Dagger、Actions 入口

- ECI 提供消费者目录可直接调用的本地 Python API/CLI，轻量 Git/Hygiene/选择性 unittest/pytest 检查**不以启动 Dagger Engine 为默认前提**。
- 复用原生 Dagger Checks/Functions、输入依赖图与缓存，适用的隔离扫描仍在 Dagger 执行；Actions 经由 ECI 公共接口调用同一规则和结果表达。不得维护第二份选择器。
- Git Hooks 仅在消费者仓库按 Git 事件触发 ECI CLI（项目可配置 pre-commit / pre-push 等适用事件）；Hook 不复制影响算法，Agent/AGENTS.md 不承担固定执行编排。
- PR/main 以正确对象与实际 Git Tree 为准。按 #63 的可信原始运行/覆盖/配置/环境证据决定执行、复用或补验；失败、跳过、未执行、证据不足不能被伪装成 Required PASS。
- 延续既有 `reusable-python-ci.yml`、原生报告、Artifacts、#52 执行/门禁能力。允许必要的接口和消费者迁移，不增建通用调度器、FSM、Runner、工具注册表、证据数据库。

### Git 变更上下文与执行端交接契约（本 Issue 交付）

- **影响计算位置**：在能够访问消费者 Git 历史、事件基线和工作区状态的宿主/Actions checkout 上计算一次影响集合。ECI 现有 Dagger Source 输入默认排除 `.git`，不能假定容器内可重新计算 Git diff，也不能在 Dagger/Actions 各维护一套映射。
- **对象绑定**：CLI/API 支持本地工作区、明确的 `base/head` Commit、PR 和 `push: main` 的真实事件对象；后者使用事件 `before/after` 而非默认当前 `origin/main`。不具备必需 Git 对象时应报告不可确定或获取所需对象，不能默认为「零变更/零测试」。Git Hook 读取实际推送 ref/SHA，不把任意 push 当成当前 HEAD。
- **机器可读交接**：选择输出包含 Git 对象/基线及 Tree（本地含未提交变更时另明确标识）、变更路径、影响闭包、所需测试/检查 ID、原生框架与执行参数、Identity/Wheel 等前置条件、扩大原因与不适用理由。检查结果另记录实际执行与原生退出码；只选择而未执行不能成为 PASS。
- **Dagger/Actions 消费**：提供将上述选择结果交给消费者 Dagger Checks / Workflow 的真实参数或输入方式，保留 unittest/pytest 原生执行，按选择执行实际所需的前置步骤；沿用现有 `dagger_checks`、报告/Artifact 契约，但该现有参数只选择 Check 名，不能冒充已支持任意 unittest 模块列表。无适用 Check 与缺失必需 Check 必须可区分。
- **分期独立验收**：本 Issue 验收一次真实 Git 变更 → 机器选择结果 → Dagger/Actions 定向执行，核对执行的检查/测试集合与原生状态。ECI #63 后续核验、接纳和跨 Run 复用结果；Knowledge #68 后续切换消费者并删除重复通用实现。两项后续交付不是本 Issue 公共算法/API 可用的先决完成条件。

### Git 输入快照与执行一致性（本 Issue 的生产方契约）

- 对本地 `pre-commit`、`pre-push`、显式 `base/head`、PR 模拟合并 Tree 与 `main` `before/after` 分别识别**被检对象**和当前实际可运行的文件内容；Git Index、工作区和 Commit Tree 不可互相等价。选择器读到的快照必须与执行端运行检查/测试时可验证的输入一致；不一致时不得产出声称覆盖目标提交的 PASS。
- 针对首次推送、多引用/多提交推送、强制推送、无共同祖先、缺失 Git 对象及事件基线不可用，提供明确可解释的范围扩大或失败结果；不能把「基线不可确定」解释为无变更。此处仅生产真实选择/执行事实，不实现 #63 的跨 Run 可信接纳。
- 选择计划包含可核对的快照身份、规则/工具版本和执行必要输入，作为同源 CLI、Dagger 和 Actions 的交接数据。执行端校验计划与实际 Source 对应，并保留本次原生结果；仅有 `dagger_checks` 名称或 `--json` 计划文件不视为执行证据。

## 4. 迁移交付及跨仓接线

1. **基于已合并 PR #67 补完实际缺口**：Source/Check 输入绑定、被选中但未执行的原生 Check/前置条件、Dagger/Actions 真正调用和 Agent 可定位诊断；复用已交付公共 API/CLI 与通用算法。#66 以 ECI 的公共接口和真实 Knowledge 配置/受检输入进行定向消费验证，另由至少一个非 Knowledge Python 仓库或真实项目夹具验证通用性；这里不要求 Knowledge 的正式 Hooks、Dagger、Actions 全部切换。
2. [Knowledge #68](https://github.com/dymzz/engineering-knowledge/issues/68) **作为独立下游交付**消费 ECI：迁移项目规则和调用配置，Hooks/Actions 接线，**删除已经迁出的通用算法和执行入口**。不长期维护 `check_repo_hygiene.py`、`select_tests.py` 两套通用实现；只保留必要的 Knowledge 领域规则/配置。ECI #66 的公共 API/实际非 Knowledge 消费验证完成后即可独立验收，不要求先关闭 #68。
3. ECI #66 提供可交给 #63 的原生执行事实与检查覆盖数据；**本地有效证据接纳、PR→main 历史结果复用和 Required/Release 可信性由 ECI #63 后续独立交付**。不将 #63 尚未实现的受信能力作为 #66 本次完成条件；服务端保护配置未知时说明权限限制，不自行修改规则。

**独立验收交接：** #66 验证公共 ECI 版本可安装、通过公共入口加载真实 Knowledge 配置并对受检输入产生正确原生结果，以及非 Knowledge 项目的通用性；这些可以通过独立 checkout/真实项目夹具和受影响的 Dagger/Actions 公共路径验收。Knowledge #68 才验收本仓实际 Hooks、Dagger、Actions 切换、领域规则接线及旧通用脚本/调用删除。#68 未完成不阻断 #66 已具证据的公共生产方能力，但不能用 #66 的夹具调用宣称 #68 的真实消费者切换已经交付。

迁移期间允许短暂并行供比对，但最终消费者以 ECI 为**唯一通用实现**；历史接口只在有真实外部依赖证据时限期过渡，不因缺少接口而永久保留两套实现。

## 验收标准

- [ ] ECI 公共入口加载真实 Knowledge 配置/输入，核对已有 Hygiene/Tach/Lychee 的正反行为；`tach check-external` 的实际调用及原生失败必须区别于已有 `tach check`。缺工具、诊断失败、来源 SHA-256/**实际 size_bytes**/属性错误均正确传播，Vulture advisory 不被强制升级为 failure。Sources 的 acquired-only 或通用清单适用范围有基于真实消费者的明确裁决，并只对实际扩展范围补定向验收。
- [ ] Knowledge #70 中已验证的 Git/Tach/内容消费者/资源/删除文件/前置条件/动态发现测试场景迁移并保持覆盖，无已知静默漏测；未改变的现有证据复用。
- [ ] 非 Knowledge Python 项目实际消费 Tach 依赖图、Git 变更和测试影响选择；消费者规则不写死在 ECI 源码。
- [ ] unittest 与 pytest 两种原生结果可执行、区分通过与失败；本地调用无需默认启动 Dagger，适用隔离任务可复用 Dagger 的原生缓存。
- [ ] 基于 PR #67 真实完成一条 Git 对象/工作区变更 → 机器选择结果 → **选中原生 Check/必要前置条件确实执行** → Dagger/Actions 真实结果路径。验证无业务测试但存在适用 Hygiene/Tach/Lychee/来源检查，不能因 `no_selected_tests` 跳过检查；对选择后 Worktree 改动及 Dagger Source Tree 尚未核验的情况正确拒绝或重新核验。#63 的本地受信接纳/PR→main 历史复用与 #61 最终 Required/Release 判定（复用既有 Release/Manifest/Run，不新建 Gate）各归原责任，不作为本 Issue 公共生产方完成前置。
- [ ] **Agent 可定位诊断（P2）：** 一次真实失败的原生 Check/前置条件在 Preflight/本地反馈中给出可导航的源/检查定位，并支持定向复检通过；仅有 Plan JSON 或外层 Job 成功不得冒充已诊断。
- [ ] ECI 通用实现与消费者配置边界明确，Knowledge 可据此移除旧脚本；交付公共 API、CLI、Dagger/Workflow 必要修订及消费者迁移说明。Knowledge 实际删除重复入口由 Knowledge #68 验收。
- [ ] 在**全新、非 ECI checkout** 的 Python 环境安装固定且具备已验收 Source/Check 能力的 ECI 公共版本并实际调用 CLI；Dagger/Actions 从同一公共实现消费（无需复制算法）。固定旧 SHA 本身不能证明能力完备，工具、来源 Commit/包版本及消费者规则版本仍可追踪。
- [ ] 新增未在 `tach.toml` 声明的跨模块 import：不允许使用未经有效 `tach check` 验证的图输出错误的窄化测试集；`tach check-external` 的真实结果另行保留。
- [ ] 文档仅位于原 `SOURCE_EXCLUDES` 所排除目录时，Lychee 在实际消费者 Dagger/Actions 路径仍检查有效与失效相对链接，且原生失败传播正确。
- [ ] Index 与工作区内容不同、目标 Commit 与本地文件不同，以及首次/多引用/强制推送或缺失基线时，检查计划及执行事实均绑定真实对象；未知对象不得显示为零变更或通过。
- [ ] 在 ECI 公共入口使用 Knowledge 真实规则/配置与受检输入时，精确路径与前缀优先级、测试消费者、Manifest 领域语义和未登记 acquired 文件等正反结果符合已有效的基线；Identity Commit/Wheel/CLI 的类型化前置条件能被实际执行/复用或明确报告未完成，不能仅输出布尔信号代替验收。Knowledge 仓实际工作流切换、领域规则落地和重复脚本删除的最终验收由 Knowledge #68 负责，不作为 #66 公共生产方完成的循环依赖。
- [ ] 验证以本次**缺口补齐与接线**的正反场景为主；复用 PR #67 及既有有效测试，不为格式/汇总重复扫描，不默认无差别重跑全量，**不重迁**已交付通用实现。

## 不属于本 Issue 的工作

不实现独立质量平台、全局状态机、通用 Runner/下载器、工具 Registry、第二套检查证据账本；不重写 #52、#63 的资产接纳/Required Check/Release 语义；不在本任务新增工程检查器或规定新的 Knowledge 领域语义；**不重新迁移 PR #67 已交付的通用选择/Hygiene/CLI**；不为 #61 新建平行 Gate/聚合器。不以先修改 AGENTS.md 作为迁移前置。
---

## Review 修订：可执行契约与定向验收（2026-11-08 复审）

以下条款补入 §1/§2/§3 与验收，不重写既有设计。

### §1 来源文件集合对账（P1-2）

ECI 公共实现负责：文件枚举、路径规范化、预期/实际集合差（缺失/多余）、重复路径与冲突摘要、真实大小、SHA-256，以及在具备真实 `.git` 的宿主或可核验原生结果上执行 Git attributes 校验。Knowledge 只提供 Manifest 字段解释、来源身份、适用目录/文件、环境管理目录豁免及是否要求 `-text` 的配置。

定向验收：正常来源、缺失、未登记、重复/冲突摘要、摘要不符、`-text` 错误、合法豁免的结论与迁移前等价；通过 ECI 公共本地入口加载 Knowledge 配置并真实执行，不只单测领域函数；失败保留原始类别，不能把缺失或属性错误解释为“不适用”。

### §2 按检查类型独立影响集合（P1-1）与 pytest 多目标（P1-4）

业务 unittest/pytest、Lychee 文档、Tach、Vulture、来源字节各自计算影响/适用集合。选择结果必须区分 `0 tests` 与 `0 applicable checks`，并给出每类的 selected / not_applicable / not_executed、实际输入与原因。

- 纯 Markdown 修改：业务测试可为 0，但 Lychee 文档必须选中；
- 删除/移动被引用文件：未修改的引用文档仍进入链接检查范围；
- 只有确实没有相关输入时，才可报告该类检查不适用；某一类为 0 不代表所有检查为 0。

pytest：多个选中目标可在一次原生 pytest 执行中传入，保留单次原生退出码、JUnit/Artifact 与现有质量策略；子集通过不得自动满足全工程 coverage/`min_coverage`/Required 门禁，必须完成全量执行或消费 #63 已核验且覆盖完整的有效证据。unittest 保持独立原生执行与状态。

定向验收：纯文档修改、删除被引用文件、多目标一次执行、其中一个目标失败、原生 JUnit/退出保真、覆盖率门禁不被子集虚假满足。

### §3 同一公共实现、Dagger 导入与工具装配（P1-3）

- 公共算法只存在于一个可安装包；Dagger 实际安装/导入并调用同一版本，不复制源码，也不依赖同名包偶然解析；明确安装来源、导入路径和版本身份。
- 在 ECI checkout 之外的干净 Python 环境安装固定版本并执行公共 CLI；Dagger 对同一版本成功调用选择/检查算法。
- Tach/Lychee 有可执行且可追溯的获取来源；工具缺失与无适用文件分别表示；Vulture 复用已有 `python_vulture`，不重复扫描。
- Dagger Source 按具体检查构造，保留 docs/来源的实际相对路径；不把 Python 源码检查的 exclude 当作通用排除。
- Git attributes 在有真实 `.git` 的宿主运行，或传递可核验的原生结果。

定向验收：独立安装调用、Dagger 同版本调用、工具缺失/无适用/真实失败三种结果可区分、Vulture 单次执行、docs 与来源输入真实到达容器。

### P2 随实现落实

- `--full` 仅表示“全量适用测试”，不代表 Tach/Hygiene/来源/wheel/Required 全部通过；按 Check/测试清单报告实际状态。
- Git NUL 路径解析不得 `.strip()`；保留合法首尾空格，并补充真实含空格重命名/删除用例。
- Git attributes 的运行位置按 §3 执行，不能因容器无 `.git` 而假称完成。

### 补充验收

- [ ] ECI 公共入口加载 Knowledge 领域配置，来源正反例结论等价。
- [ ] 纯 Markdown 与删除引用场景分别证明业务测试与 Lychee 的独立选择。
- [ ] 干净环境安装固定版本 CLI；Dagger 调用同一公共实现。
- [ ] 多 pytest 目标单次执行，退出码/JUnit 保真，覆盖率门禁不被子集满足。
- [ ] Tach/Lychee 缺失、无适用文件、真实失败三种状态可区分；Vulture 不重复扫描。
- [ ] `--full`、Git 路径保真、Git attributes 宿主位置按上述契约验证。


## 已合并阶段设计与剩余交付校准（2026-11-09）

- 依据：[Knowledge PR #75 已合并实施计划](https://github.com/dymzz/engineering-knowledge/blob/main/design/engineering-verification/implementation-plan.md) P1/P2 与后续生产方交接。本节仅补充真实当前代码状态，保留本 Issue 已有效的来源、Tach、资源映射、原生 unittest/pytest 及跨仓公共能力要求。
- P1 Source/快照修正与 P2 已有原生 Check 接线可以并行实施；声明 Index、Worktree、PR Merge 或 Push 已验证通过时必须有相应真实执行输入证据。Dagger 官方 Pytest/Pants 是否替换只影响选定的真实 Check 家族，不是其他检查的阻断前置。
- 当选择器输出 `requires_identity_commit` 或 `requires_wheel_smoke`，必须公开可执行的类型化前置条件与原生执行/未完成事实；具体固定 Identity Commit、wheel 构建和仓外 CLI 命令仍由消费者配置提供，不在 ECI 硬编码。
- **读法重申：** §1/§2 标题与清单是契约对照表，不是授权重新从 Knowledge 脚本实现通用层；实施 PR 的 diff 应体现对已有 `eci_core`/CLI/Dagger 的缺口修复与接线，而不是平行再迁。
