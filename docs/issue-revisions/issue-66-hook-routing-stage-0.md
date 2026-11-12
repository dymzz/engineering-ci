# ECI #66 — Git Hooks 路由与规范权威冲突：S0 分阶段审阅提案

> **审计范围：S0 架构裁决 + S0.5 已应用 Issue 文本差分 + S1 待实施机制设计要求。** 实际仅直接更新了 [ECI Issue #66](https://github.com/dymzz/engineering-ci/issues/66) 正文；**Knowledge #68、ECI PR #72、Knowledge PR #78 均未在本轮修改**。此文件不是规范性定义，不表示任何 Hooks 运行时代码已交付；完整 PR #72/#78 设计继续有效。

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

**S0.5：九处原 Issue 句子已按审阅意见定点替换并核对，线上 Issue #66 更新于 2026-11-12T16:06:06Z；S1 机制接口条款于 2026-11-12T16:23:28Z 独立追加，当前正文 34,188 字符 / 292 行。** 每个新句必须同时保留原句已有效的独立验收、安装/平台、Check 范围、Source P0、多 Ref/Index/Worktree/Actions、Dagger、#63 证据和 #66 不新增独立检查器等约束。不得再次压缩完整 PR #72/#78；不因设计文档较多就宣称存在多个规范权威。

## 4. 尚需独立技术裁决的真实接口

1. **规则权威与 Lefthook 路由投影。** #52/Runtime 维护公共 Check/Tool 身份及原生结果契约；#66 维护 Git Source/Selection 和现有 `execute()`；消费者维护项目规则。Lefthook 的 `glob/only/skip` 只是工具路由实现，不是第二份权威 Check 定义。必须明确 Job→Check 追溯，验证共享配置/锁/反向引用变化时必要 Job 确实唤醒；非 Git 动态失效由定期/CI/Release 路径承担；无法表达精确条件时采用保守相关 Job 激活 + ECI 内部细粒度 Analysis，并让漏执行或未覆盖状态可见。先复用现有 Plan/Report/门禁，而非新建另一套检查影响引擎。
2. **先处理事件还是先激活 Job。** pre-commit 的 `GIT_INDEX_FILE`、pre-push 唯一原始 stdin 接收与逐 Ref old→new 不等于 Lefthook 能动态按 ECI 计算结果生成 Jobs。PR #72 要提供固定版本真实可执行的方案，证明条件分发、共享 Source/Plan、并行、不重复 Tach/原始 Check，不能只画概念图。
3. **受检字节和结果归因。** #66 Source P0 保留 Index=A/Worktree=B/临时 Index=C、Linked Worktree、不同 Git Commit/Tree；不存在正确执行/输入证据时不得产生 PASS。#52 Native Check/Report/Enforce；#63 历史受信复用；#61 Required/Release；#86 真正 Dagger DAG/Artifact；Knowledge #68 领域规则和消费者接线。
4. **Knowledge 实际输入与成本。** PR #73 的 `dagger.lock`/Dagger 输入过滤、Hook 文件变更误触发全部测试、旧 `hook_adapter.py` 清理时机，以及本地与 Actions 的真实事件覆盖，应在 Knowledge 自己的 PR 内定点调整，不把领域特例写死进 ECI。

### S1 准入与设计完成核对（接口要求，不是运行时已交付）

1. **Expected Check owner / versioned Plan。** 复用已有 `selection.Config` / `plan_contract`，#52/Runtime 管 Check 身份与受信 Profile，消费者管规则，#66 按 Git Event/每 Ref 在实际 Job Result 产生前投影 Expected Check。最小逻辑字段：`event_ref`、`source_ref`、`check_id`、`applicability`/原因、`rule_ref`、`job_ref`、`requirement_ref`。Required 是独立策略，不是 `applicability` 枚举。如何进入现有 schema 由 S1 版本化，不新增平行 Registry。
2. **执行权威与 Direct Tool 结果。** #66 已交付的公共 `execute()` 不迁走或重建；#52/Runtime 负责新原生 Check/适配和 Report/Enforce。Direct Tool 仅通过现有 Check/Result 适配后按实际 Check ID、Source/配置/输入、退出与范围计入覆盖；无适配的诊断不构成 Required PASS。
3. **Event Prepare 与漏激活证据。** 固定版本实际核验 Lefthook `setup`/`dump`、静态 `jobs/group/parallel`、pre-push stdin 唯一可靠接收、有效本地/extends 配置。不得假定动态 Job 注入。按独立 Expected 集合与生效路由、原生 Result 对账；被过滤的 Job 不能靠自己的 wrapper 记录跳过。Hook 完全被绕过时，本地无法证明它被绕过，服务端 #63/#61 独立判断覆盖。
4. **冻结真实可运行的最小矩阵。** pre-commit Index/临时 Index、pre-push 多 Ref/首次/强推/删除/非 HEAD、三个不同触发机制的适用 Job、独立 Job 并行与依赖、Windows/Unix/Linked Worktree、install/status/update/uninstall、第三方 Hook 共存、直接 Tool Native Failure/缺席、Source/Result 身份、时延与日志。Source P0 可独立实施；矩阵是 S1 机制设计完成条件，不是开始 S1 研究前必须已运行的门槛；完整候选工具家族继续保留。

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
