# ECI #66 — Git Hooks 路由与规范权威冲突：S0 分阶段审阅提案

> **仅供审阅；不是规范、不是工具配置、不是实现验收。** 本 PR 不直接修改 [ECI #66](https://github.com/dymzz/engineering-ci/issues/66)、[Knowledge #68](https://github.com/dymzz/engineering-knowledge/issues/68)、[ECI PR #72](https://github.com/dymzz/engineering-ci/pull/72)、[Knowledge PR #78](https://github.com/dymzz/engineering-knowledge/pull/78) 的正文。本次只展示冲突裁决和建议文字 Diff；不能将此草稿合并当作实现完成。

## 1. 审阅基线

- ECI #66 读取时间戳 `2026-11-12T14:46:03Z`，正文 28542 字符、276 行；见同 PR 的 [逐行替换提案](./issue-66-hook-routing-proposed.diff)。
- ECI main 已合并 #67/#70/#71/#73/#79/#88；当前公共实现为 `engineering_ci`，已存在 Selection/Hygiene/Sources/Execution、部分 Dagger/Actions 接口。Source-bound staged/Push 执行与完整 Hook Job 调度尚未验收。
- ECI PR #72 / Knowledge PR #78 均为完整未合并 Draft；PR #72 的工具矩阵、阶段与 F/P/R/D/E 分类，PR #78 的消费者映射都不是可删的“历史冗余”。
- Knowledge PR #73 的旧 Hook Adapter/真实 consumer 配置仍在待验收阶段。此次不合并、不关闭、不覆盖已有实现，也不触发全量 CI。

## 2. 需要裁决的规范与执行边界

**唯一规范性权威 ≠ 单一文档。** 同一概念、语义与契约有一个权威定义/维护源；不同设计方案、实施说明、项目规则与验收证据可各有独立文档，并应以引用代替复制同义规范。ECI #66 承担公共 Hook/Source 工程契约，Knowledge #68 承担项目特有规则与消费者验收；已有上游 standards/semantics 定义必须引用，不能借 Hook 设计再造。

**Lefthook 是受规范约束的成熟工具调度器，不是规范来源。** 它可以按阶段、路径、项目/工具条件路由、分配并激活多个 Tool Jobs，安排安全并行、必要顺序、生命周期与诊断。**公共 Check 契约由 ECI 维护，项目适用规则由 Knowledge 等消费者维护；Lefthook Job 配置是该权威契约的可检查路由映射。** ECI 对已激活 Check 的实际 Git Source、每 Ref 输入、细粒度 Analysis、原生结果与失败归因负责；必要时 Job 直接调用 Tool/ECI Check/Dagger Function，但计入 ECI 结果时必须具备完整原生 Source/Check/Exit 关联。既不默认启动全部工具再在单个 ECI 入口跳过，也不允许漏唤醒必要 Job 后仍宣称 PASS。

**Lefthook 优先评估，不提前宣称选型定案。** PR #72 必须核实固定版 `jobs/group`、条件与扩展配置、`use_stdin`、Windows/Git for Windows/Unix/Linked Worktree、`core.hooksPath` 第三方 Hook 共存、安装幂等、升级/卸载恢复、诊断与原生结果归因。不可假设存在未核实的运行时动态 Job 注入 API；可比较声明式 Jobs + ECI 真实适用性判定、事件准备和共享结果、prek 或具有同等多工具能力的方案。**完整事件输入接收≠单个 Source/Plan/串行 Job**。#66 Source/Execution 生产方的独立验收不等待 Hook 框架最终选型，完整公共 Hooks 验收仍须覆盖这些成熟调度功能。

### 唯一权威的可执行完整性条件

- **规范来源不重叠：** ECI 公共 Check/Source 及输入规则、Knowledge 等消费者项目适用性、Lefthook 运行配置各有确定角色；Job 路由不是第二份规范性 Check 定义，设计文档可各自保留具体方案和验收证据。
- **必要 Job 覆盖：** 对一次事件/每个推送 Ref 的预期适用 Check，要能核对哪些 Job 应被唤醒、哪些实际被激活、哪些产生具有 Source/Check/原生退出身份的结果；漏执行不得成为 `passed`。这可以由已有 ECI 计划/报告及适用门禁实现，**不预设新的数据存储、全局调度器或动态 Job 接口**。
- **原生能力保持：** 独立可并行 Job 不因共享 Source/Plan/Tach 被强制串行；Direct Tool/ECI Check/Dagger Function 只要报告要求的真实 Source、覆盖与原生结果，即可纳入同一 Check 结论。
- **完成边界分离：** #66 Source P0 可独立验收；Hook 完整交付才要求成熟工具路由、安装生命周期和多工具故障诊断。#66 不负责新增独立工程检查器；新 Native Tool/Check 沿用 #52/现有 Runtime 的交付。

## 3. 逐条冲突索引（精确旧句和新句见 .diff）

| 编号 | ECI #66 原文行 | 问题 | 定点目标 |
| --- | ---: | --- | --- |
| C01 | 56 | 多工具路由弱化 | 恢复路由并保留 Source 切片独立验收、安装与平台条件 |
| C02 | 63 | Check 输入/Job 配置可能分叉 | ECI 公共规则 + 消费者项目规则唯一维护，Lefthook 路由映射可核对 |
| C03 | 94 | 多工具被单个全量入口替代 | 保留完整 Job 并行、安装/更新/卸载、跨平台、原生结果和 Required 边界 |
| C04 | 95 | 必要 Job 可能漏唤醒 | 事件预期 Check→Job 激活→原生结果可核对，缺失不冒充 PASS |
| C05 | 105 | 执行顺序默认薄 Hook | 改为成熟调度适配，与 Source P0 独立并行 |
| C06 | 127 | Required/Release 职责表达混乱 | #52/#61 交付边界明确，但完整保留 #63 证据/Required/Release 要求 |
| C07 | 170 | Git Hook 只能触发单个 ECI CLI | 支持多 Job 和直接 Tool，但执行结果必须遵守 Source/Check/Exit 契约 |
| C08 | 172 | 不重建调度器被误读成禁用 Lefthook | 保留成熟调度与多文档设计，单一规范性维护来源 |
| C09 | 219 | 反重复 Runner 被误读为否定成熟调度 | 保留 #66 不新增独立工程检查器，仍可跨 Issue 批量接入 Native Checks |

**本轮提案仅拟替换上述九个 Issue 句子，不直接修改线上 Issue。** 每个新句必须同时保留原句已有效的独立验收、安装/平台、Check 范围、Source P0、多 Ref/Index/Worktree/Actions、Dagger、#63 证据和 #66 不新增独立检查器等约束。不得再次压缩完整 PR #72/#78；不因设计文档较多就宣称存在多个规范权威。

## 4. 尚需独立技术裁决的真实接口

1. **规则权威与 Lefthook 路由投影。** ECI 的公共 Check 契约和消费者领域规则各有唯一规范性维护来源，Lefthook 的 `glob/only/skip` 等仅是路由实现。必须明确 Job→Check 追溯，验证共享配置/锁/反向引用变化时必要 Job 确实唤醒；无法表达精确条件时采用保守相关 Job 激活 + ECI 内部细粒度 Analysis，并让漏执行或未覆盖状态可见。先复用现有 Plan/Report/门禁，而非新建另一套检查影响引擎。
2. **先处理事件还是先激活 Job。** pre-commit 的 `GIT_INDEX_FILE`、pre-push 唯一原始 stdin 接收与逐 Ref old→new 不等于 Lefthook 能动态按 ECI 计算结果生成 Jobs。PR #72 要提供固定版本真实可执行的方案，证明条件分发、共享 Source/Plan、并行、不重复 Tach/原始 Check，不能只画概念图。
3. **受检字节和结果归因。** #66 Source P0 保留 Index=A/Worktree=B/临时 Index=C、Linked Worktree、不同 Git Commit/Tree；不存在正确执行/输入证据时不得产生 PASS。#52 Native Check/Report/Enforce；#63 历史受信复用；#61 Required/Release；#86 真正 Dagger DAG/Artifact；Knowledge #68 领域规则和消费者接线。
4. **Knowledge 实际输入与成本。** PR #73 的 `dagger.lock`/Dagger 输入过滤、Hook 文件变更误触发全部测试、旧 `hook_adapter.py` 清理时机，以及本地与 Actions 的真实事件覆盖，应在 Knowledge 自己的 PR 内定点调整，不把领域特例写死进 ECI。

## 5. 分阶段 PR / 审阅计划

| 阶段 | 提交对象 | Diff 范围 | 裁决与完成条件 |
| --- | --- | --- | --- |
| **S0 本 PR** | ECI 新增审阅提案与九段拟议正文 Diff | 仅新增两份 review-only 文件；不改 Issue | 独立逐项审阅语义冲突/责任/遗失要求；决定采纳或拒绝哪些 hunk |
| **S1** | ECI #66 / PR #72 | 经审阅的现有 Issue 逐段替换、PR #72 全文保留并细化真实 Job/ECI 接口 | 旧条款不再与新职责相矛盾；方案可实际实施，不把审阅等同选型定案 |
| **S2** | Knowledge #68 / PR #78（独立 Knowledge PR） | 项目 Job 条件、`dagger.lock`/Workflow、领域映射、旧 Adapter 过渡 | 跨仓不复制规范来源，项目 Job 对应真实工具与输入；本仓独立 Diff 审阅 |
| **S3** | ECI #66/#52/#86 与 Knowledge #68 实施 PR | 精确 Source、Lefthook Job 配置与生命周期、Native Result、必要 Dagger | 定向验证正确工具激活/未激活、并行/诊断、Source、失败和执行成本；不重复已有 CI |

阶段不是跨 Issue 全局串行：S0 审阅决定规范冲突，Source P0、原生工具和 Dagger 现有独立实施可继续并行。**不为每项工具另开微型 PR；按实际可审阅的接口与能力批次拆分。**

## 6. Review 验收清单

- [ ] 逐项审阅 C01–C09 的“旧句 → 新句”，确认没有遗漏任何有效需求或引入相反职责。
- [ ] 保留 Lefthook 成熟条件路由/激活/并行、完整安装生命周期，不被单个 ECI 全量入口或串行化替代；同时不强制 #66 Source P0 等待完整调度方案。
- [ ] 检查 ECI 公共规则、Knowledge 项目规则、Lefthook 路由映射和预期 Check→激活 Job→原生 Result 的完整性，统一规范权威不等于单文档。
- [ ] 未虚构 Lefthook 动态 API，也未把单一 Git stdin 接收简化为单 Plan/单 Job。
- [ ] #52/#61/#63/#66/#86/Knowledge #68 的完整职责仍有效，特别是 #63 的证据完整性/Required/Release 主题和 #66 不新增独立工程检查器；所有原工具矩阵与验收保留。
- [ ] 审阅通过后再提出 S1、S2 精确修改；**未经再次对照实时 Issue 全文，不把这个文件当作可盲目应用的补丁**。
