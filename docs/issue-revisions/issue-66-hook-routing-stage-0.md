# ECI #66 — Git Hooks 路由与规范权威冲突：S0 分阶段审阅提案

> **审计范围：S0 架构裁决 + S0.5 已应用 Issue 文本差分 + S1 待实施机制设计要求。** 实际仅直接更新了 [ECI Issue #66](https://github.com/dymzz/engineering-ci/issues/66) 正文；**Knowledge #68、ECI PR #72、Knowledge PR #78 均未在本轮修改**。此文件不是规范性定义，不表示任何 Hooks 运行时代码已交付；完整 PR #72/#78 设计继续有效。

> **S1 本轮定点复审（ECI #66 `2026-11-12T16:48:41Z`）：** S1-A/B/D/E 依据当前 `engineering_ci` 代码修订，线上 Issue 正文 **36,506 字符 / 292 行**。先前 16:36 的 **35,333 字符**为历史快照，非现行版本。S0.5 applied Diff 仍专门记录 16:06 的九项历史替换，**不**混入 16:23、16:36 或 16:48 的 S1 追加/修订。旧 L56/63/94/95/105/127/170/172/219 属于 14:46 的 276 行基线，不能作为当前 Issue 行号。

## 1. 审阅基线

- ECI #66 读取时间戳 `2026-11-12T14:46:03Z`，正文 28542 字符、276 行；见同 PR 的 [S0.5 已应用九处 Diff 审计](./issue-66-hook-routing-applied.diff)。
- ECI main 已合并 #67/#70/#71/#73/#79/#88；当前公共实现为 `engineering_ci`，已存在 Selection/Hygiene/Sources/Execution、部分 Dagger/Actions 接口。Source-bound staged/Push 执行与完整 Hook Job 调度尚未验收。
- ECI PR #72 / Knowledge PR #78 均为完整未合并 Draft；PR #72 的工具矩阵、阶段与 F/P/R/D/E 分类，PR #78 的消费者映射都不是可删的“历史冗余”。
- Knowledge PR #73 的旧 Hook Adapter/真实 consumer 配置仍在待验收阶段。此次不合并、不关闭、不覆盖已有实现，也不触发全量 CI。

## 2. 需要裁决的规范与执行边界

**唯一规范性权威 ≠ 单一文档。** 同一概念、语义与契约有一个权威定义/维护源；不同设计方案、实施说明、项目规则与验收证据可各有独立文档，并应以引用代替复制同义规范。ECI #66 承担公共 Hook/Source 工程契约，Knowledge #68 承担项目特有规则与消费者验收；已有上游 standards/semantics 定义必须引用，不能借 Hook 设计再造。

**Lefthook 是受规范约束的成熟工具调度器，不是规范来源。** 它可以按阶段、路径、项目/工具条件路由、分配并激活多个 Tool Jobs，安排安全并行、必要顺序、生命周期与诊断。**#52/Runtime 管新增 Native Tool/Check 身份、适配与 Report/Enforce；#66 管 Source/Selection、已交付的公共 `execution.execute()` 和输入/执行事实关联；消费者维护项目适用规则；Lefthook Job 配置是受控路由映射而非新规范。** ECI 对已激活 Check 的真实 Source、每 Ref 输入、细粒度 Analysis 与公共执行事实负责；必要时 Job 直接调用 Tool/ECI Check/Dagger Function，但计入 ECI 结果时必须具备完整原生 Source/Check/Exit 关联。既不默认启动全部工具再在单个 ECI 入口跳过，也不允许漏唤醒必要 Job 后仍宣称 PASS。

**Lefthook 优先评估，不提前宣称选型定案。** PR #72 必须核实固定版 `jobs/group`、条件与扩展配置、`use_stdin`、Windows/Git for Windows/Unix/Linked Worktree、`core.hooksPath` 第三方 Hook 共存、安装幂等、升级/卸载恢复、诊断与原生结果归因。不可假设存在未核实的运行时动态 Job 注入 API；可比较声明式 Jobs + ECI 真实适用性判定、事件准备和共享结果、prek 或具有同等多工具能力的方案。**完整事件输入接收≠单个 Source/Plan/串行 Job**。#66 Source/Execution 生产方的独立验收不等待 Hook 框架最终选型，完整公共 Hooks 验收仍须覆盖这些成熟调度功能。

### 可执行完整性要求（S1 尚待验证的合同）

- **规范来源不重叠：** ECI 公共 Check/Source 及输入规则、Knowledge 等消费者项目适用性、Lefthook 运行配置各有确定角色；Job 路由不是第二份规范性 Check 定义，设计文档可各自保留具体方案和验收证据。
- **必要 Job 覆盖（待运行时落地）：** 受控 Check/Profile 和消费者配置必须在 Job 运行结果之外独立确定事件/每 Ref 的预期适用检查，核对有效 Lefthook 配置与 Job 触发、跳过及原生 Result。主配置不一定等于实际有效配置；Job 完全未启动也必须在有权威门禁时识别为缺席，不得仅凭已执行 Job 反推出预期集合。#63 提供历史证据/覆盖有效性，#61 负责最终 Required；本地 Hook 完全绕过时不能在本地自行产生漏执行证据。**现有 Plan/Report 尚不能声称已完成全工具完整性核对**；S1 机制设计须核定实际执行入口，不预设新存储或动态 Job API。
- **原生能力保持：** 独立可并行 Job 不因共享 Source/Plan/Tach 被强制串行。Direct Tool 必须经 #52 现有 Check/Result 适配，保留 Check ID、Source/配置与输入、原生退出及覆盖才能计入 Check 结论；未适配的输出仅为诊断，不能据此完成 Required。
- **完成边界分离：** #66 Source P0 可独立验收；Hook 完整交付才要求成熟工具路由、安装生命周期和多工具故障诊断。#66 不负责新增独立工程检查器；新 Native Tool/Check 沿用 #52/现有 Runtime 的交付。

## 3. 逐条冲突索引（精确旧句和新句见 .diff）

| 编号 | ECI #66 原文行 | 问题 | 定点目标 |
| --- | ---: | --- | --- |
| C01 | 56 | 多工具路由弱化 | 恢复路由并保留 Source 切片独立验收、安装与平台条件 |
| C02 | 63 | Check 输入/Job 配置可能分叉 | #52 Check、#66 Source/Selection、消费者规则分别维护；Lefthook Job 是可核对的路由映射 |
| C03 | 94 | 多工具被单个全量入口替代 | 保留完整 Job 并行、安装/更新/卸载、跨平台、原生结果和 Required 边界 |
| C04 | 95 | 必要 Job 可能漏唤醒 | 独立预期集合→生效 Job 配置→实际激活→原生结果；非 Git 失效另行调度 |
| C05 | 105 | 执行顺序默认薄 Hook | 改为成熟调度适配，与 Source P0 独立并行 |
| C06 | 127 | Required/Release 职责表达混乱 | #52/#61 交付边界明确，但完整保留 #63 证据/Required/Release 要求 |
| C07 | 170 | Git Hook 只能触发单个 ECI CLI | 多 Job 与直接 Tool 保留，结果核对与 C04 同源且须符合 Source/Check/Exit 契约 |
| C08 | 172 | 不重建调度器被误读成禁用 Lefthook | 保留成熟调度与多文档设计，单一规范性维护来源 |
| C09 | 219 | 反重复 Runner 被误读为否定成熟调度 | 保留 #66 不新增独立工程检查器，仍可跨 Issue 批量接入 Native Checks |

**S0.5：** 九项原句于 16:06 定点替换并有独立审计；16:23 的 S1 机制接口、16:36 的 Expected/Routing 解耦及 **16:48 的 S1-A/B/D/E 定点修订**均属于后续独立修改。现行 Issue #66 为 **36,506 字符 / 292 行**，审计文件没有也不应伪称记录了后续 S1 Diff。保留原 Issue 的 Source P0、Hook 生命周期与多工具能力、#52/#63/#61/#86 职责和完整跨生态矩阵；PR #72/#78 不得再被压缩或当成历史冗余。

## 4. 尚需独立技术裁决的真实接口

1. **规则权威与 Lefthook 路由投影。** #52/Runtime 维护公共 Check/Tool 身份及原生结果契约；#66 维护 Git Source/Selection 和现有 `execute()`；消费者维护项目规则。Lefthook 的 `glob/only/skip` 只是工具路由实现，不是第二份权威 Check 定义。必须明确 Job→Check 追溯，验证共享配置/锁/反向引用变化时必要 Job 确实唤醒；非 Git 动态失效由定期/CI/Release 路径承担；无法表达精确条件时采用保守相关 Job 激活 + ECI 内部细粒度 Analysis，并让漏执行或未覆盖状态可见。先复用现有 Plan/Report/门禁，而非新建另一套检查影响引擎。
2. **先处理事件还是先激活 Job。** pre-commit 的 `GIT_INDEX_FILE`、pre-push 唯一原始 stdin 接收与逐 Ref old→new 不等于 Lefthook 能动态按 ECI 计算结果生成 Jobs。PR #72 要提供固定版本真实可执行的方案，证明条件分发、共享 Source/Plan、并行、不重复 Tach/原始 Check，不能只画概念图。
3. **受检字节和结果归因。** #66 Source P0 保留 Index=A/Worktree=B/临时 Index=C、Linked Worktree、不同 Git Commit/Tree；不存在正确执行/输入证据时不得产生 PASS。#52 Native Check/Report/Enforce；#63 历史受信复用；#61 Required/Release；#86 真正 Dagger DAG/Artifact；Knowledge #68 领域规则和消费者接线。
4. **Knowledge 实际输入与成本。** PR #73 的 `dagger.lock`/Dagger 输入过滤、Hook 文件变更误触发全部测试、旧 `hook_adapter.py` 清理时机，以及本地与 Actions 的真实事件覆盖，应在 Knowledge 自己的 PR 内定点调整，不把领域特例写死进 ECI。

### 2026-11-12 16:48 S1 四项接口修订核对（不是新增运行时交付）

| 项目 | 代码/现有合同证据 | 已写入 Issue #66 的设计修订 | 后续实施裁决 |
| --- | --- | --- | --- |
| **A：Expected Check 身份与真实生产函数** | `selection._check_scope()` 以 `name` 表示有限的 docs/tach/source 等；`plan_contract.PLAN_VERSION=2` 验证当前状态；`execution._selected_checks()` 按名称选择。这些不能证明稳定跨生态 `check_id` 或完整 Expected | #52/Runtime 核定稳定 Check ID/Profile/版本；#66 经现有 Selection/Plan 映射，并明确每 Git Event/Ref 的生产函数与版本化输出；Expected 不含 Job，Routing Binding 后绑定且支持多对多 | 核实 #52 已有身份，缺口在原 Check/Result 合同扩展；不能把 `name` 简单重命名为 ID，不能另建 Registry |
| **B：Effective Config 绑定** | `lefthook dump` 等可供固定版实测，但仅查看配置快照不证明实际 Hook 使用了同一配置 | 路由身份必须对应**该次 Hook 真实版本、合成配置、环境/目录、Job 条件与启动事实**；失配或不可核实则 `unknown` | 核实 `setup/dump` 实际行为、pre-push 完整 stdin、扩展与本地配置；不虚构动态 Job API 或增加配置存储服务 |
| **D：状态域兼容** | `execution.execute()` 已在 `tach_graph_unvalidated`、`coverage_evidence_required`、`required_checks_not_executed` 返回 Execution Report `blocked` | Routing / Check+Applicability / **Execution Report** / #61 Final Required Gate 分开；保留既有执行层阻断，不冒充最终 Gate | 不改旧执行状态语义；缺路由的 Expected 保留，缺失检查不得被 Hook exit=0 当作 PASS |
| **E：基础矩阵与 D/E** | Tach、Lychee、Sources 的真实工具实例适合 F/P/R 基础路由；现有 Push ChangeSet、pytest/前置及执行路径可覆盖另外两类风险 | 明确三 Job 基础通过不代表完整 F/P/R/D/E；D 补真实 old→new/跨 Ref 比较与复用；E 复用测试/执行/必要前置路径 | 只针对新增接口补反例，不新建扫描器或重跑已有效工具矩阵；非 Git 动态变化按 CI/周期/Release 验收 |

**设计审批口径：** 以上四项修订已进入 #66 的 S1-A/B/D/E，是可实施合同和验证要求，**不是已交付接口**。S0.5 历史审计不受影响。独立 Expected、Routing、Native Result、Source 真实字节和最终 Gate 的运行时证据仍须在 #72/#52/#66/#61 对应实施中分别提供。

### S1 准入与设计完成核对（接口要求，不是运行时已交付）

1. **Expected Check 独立于调度器路由。** #52/Runtime 提供稳定 Check 身份与受信 Profile；[Runtime #27](https://github.com/dymzz/vesryn-tool-runtime/issues/27) 已定义 `ToolAsset.tool_id`、`ToolGroup.group_id`、`ToolAccess.access_id`，但三者都不等同 `check_id`，也不等同 Job/Requirement ID；#52 若尚无稳定版本化 Check ID 需在既有 Check/Result 契约扩展。消费者管版本化项目适用规则，#66 按 Git Event/每 Ref 在相关 Job 执行前以现有 Selection/Plan 产生不依赖调度器和已执行结果的 Expected Check：`event_ref`、`source_ref`、`check_id`、`applicability`/原因、`rule_ref`、可选 `requirement_ref`；**绝不包含 `job_ref`**。路由确定之后再建立独立 Routing Binding：`expected_check_ref`、`scheduler_ref`、`effective_config_ref`、可为空的 `job_refs[]`、`mapping_status`。Check↔Job 为多对多；无路由也必须保留预期 Check。Required 是独立策略，版本化接口由 S1 决定，不另造平行 Registry/schema。
2. **执行权威与 Direct Tool 结果。** #66 已交付的公共 `execute()` 不迁走或重建；#52/Runtime 负责新原生 Check/适配和 Report/Enforce。Direct Tool 仅通过现有 Check/Result 适配后按实际 Check ID、Source/配置/输入、退出与范围计入覆盖；无适配的诊断不构成 Required PASS。
3. **Event Prepare、有效配置及四类事实状态。** 固定 Lefthook 版本核实 `setup`/`dump`、合成配置、真实生效环境和 pre-push 原始 stdin；不可宣称静态 dump 与运行配置自动一致或虚构动态 Job 注入。Expected 独立于 Job/Result 产生，与同次有效配置的 Routing Binding 和原生 Result 对账；无 Job → Routing `unrouted`、Check `not_executed`；证明不适用 → `not_applicable`；缺事实 → `unknown`，不得 PASS。**已有 `execution.execute()` 的 Execution Report `blocked` 属执行层拒绝事实，不是 #61 最终 Required Gate 的 `blocked`；二者分层但都保留。** Job 在启动前过滤，内部 wrapper 不会自行报告跳过；本地完全绕过 Hook 时只能由适用服务器事实核验，不能本地臆造检测。
4. **冻结基础三 Job、D/E 及平台验收范围。** Tach（含适用的 `check-external`）、Lychee、Sources 是 F/P/R 的可执行调度样本，**并不宣称已证明 D/E 或全部生态能力**。D 类复用真实多 Ref old→new 和不同比较基线下的选择/复用；E 类复用已有 pytest/原生执行/必要前置与环境范围核对，非 Git 动态变化归定期/CI/Release。另保留 Index/临时 Index、强推/新建/删除/非 HEAD、Windows/Unix/Linked Worktree、install/status/update/uninstall、第三方 Hook、直接 Tool Result 及诊断/并行成本。仅补未覆盖的跨 Ref/跨 Job 反例，现有可信单项证据不重验。#63 维护证据/覆盖有效性，#61 负责最终 Required 聚合；矩阵是批准 S1 实施方案的依据，不是开始设计的门槛，也不缩减 #72 完整工具目录。

> 唯一权威和完整条款见 [ECI #66](https://github.com/dymzz/engineering-ci/issues/66) `S1 机制接口与冻结验收基线`（2026-11-12T16:23:28Z）。本段只是 S0/S1 审阅导航，不另建一份规范性 Schema。

## 5. 分阶段 PR / 审阅计划

| 阶段 | 提交对象 | Diff 范围 | 裁决与完成条件 |
| --- | --- | --- | --- |
| **S0 架构裁决** | PR #91 / ECI #66 | C01–C09 九项原文冲突审阅 | 9/9 审阅和原意保留核对通过 |
| **S0.5 文本应用** | ECI #66 / PR #91 审计记录 | 九处句子应用及独立 S1 接口补充；GitHub Issue 不属于 PR 文件 Diff | 实际 Issue 正文、时间戳、审计内容与 Review 一致 |
| **S1 机制设计** | ECI PR #72 / 后续独立实施 PR | 完整 Lefthook/ECI Event/Job/Result 接口与冻结验收矩阵 | 核验现有 `setup/dump` 等能力，独立 Expected Check、Direct Tool 接纳、完整性核对及平台条件可实施；未通过前不将设计当作运行时验收 |
| **S2** | Knowledge #68 / PR #78（独立 Knowledge PR） | 项目 Job 条件、`dagger.lock`/Workflow、领域映射、旧 Adapter 过渡 | 跨仓不复制规范来源，项目 Job 对应真实工具与输入；本仓独立 Diff 审阅 |
| **S3** | ECI #66/#52/#86 与 Knowledge #68 实施 PR | 精确 Source、Lefthook Job 配置与生命周期、Native Result、必要 Dagger | 定向验证正确工具激活/未激活、并行/诊断、Source、失败和执行成本；不重复已有 CI |

阶段不是跨 Issue 全局串行：S0 审阅决定规范冲突，Source P0、原生工具和 Dagger 现有独立实施可继续并行。**不为每项工具另开微型 PR；按实际可审阅的接口与能力批次拆分。**

## 6. Review 验收清单

- [ ] 逐项审阅 C01–C09 的“旧句 → 新句”，确认没有遗漏任何有效需求或引入相反职责。
- [ ] 保留 Lefthook 成熟条件路由/激活/并行、完整安装生命周期，不被单个 ECI 全量入口或串行化替代；同时不强制 #66 Source P0 等待完整调度方案。
- [ ] 检查 ECI 公共规则、Knowledge 项目规则、Lefthook 路由映射和预期 Check→激活 Job→原生 Result 的完整性，统一规范权威不等于单文档。
- [ ] 未虚构 Lefthook 动态 API，也未把单一 Git stdin 接收简化为单 Plan/单 Job。
- [ ] #52/#61/#63/#66/#86/Knowledge #68 的完整职责仍有效，特别是 #63 的证据完整性/Required/Release 主题和 #66 不新增独立工程检查器；所有原工具矩阵与验收保留。
- [x] ECI #66 于 S0.5 实施 C01–C09 九处定点替换，2026-11-12T16:06:06Z 的正文与本 PR 审计 Diff 逐字一致。
- [x] ECI #66 随后于 2026-11-12T16:23:28Z 添加独立 S1 机制接口要求；本审计文件不将后续追加误写成原九行修改。
- [ ] S1 真实接口、冻结矩阵、Lefthook 有效路由与 Expected/Result 完整性仍需审阅/验证；Knowledge #68 保持原状，进入独立 S2。
