# ECI #66 — Git Hooks 路由与规范权威冲突：S0 分阶段审阅提案

> **仅供审阅；不是规范、不是工具配置、不是实现验收。** 本 PR 不直接修改 [ECI #66](https://github.com/dymzz/engineering-ci/issues/66)、[Knowledge #68](https://github.com/dymzz/engineering-knowledge/issues/68)、[ECI PR #72](https://github.com/dymzz/engineering-ci/pull/72)、[Knowledge PR #78](https://github.com/dymzz/engineering-knowledge/pull/78) 的正文。本次只展示冲突裁决和建议文字 Diff；不能将此草稿合并当作实现完成。

## 1. 审阅基线

- ECI #66 读取时间戳 `2026-11-12T14:46:03Z`，正文 28542 字符、276 行；见同 PR 的 [逐行替换提案](./issue-66-hook-routing-proposed.diff)。
- ECI main 已合并 #67/#70/#71/#73/#79/#88；当前公共实现为 `engineering_ci`，已存在 Selection/Hygiene/Sources/Execution、部分 Dagger/Actions 接口。Source-bound staged/Push 执行与完整 Hook Job 调度尚未验收。
- ECI PR #72 / Knowledge PR #78 均为完整未合并 Draft；PR #72 的工具矩阵、阶段与 F/P/R/D/E 分类，PR #78 的消费者映射都不是可删的“历史冗余”。
- Knowledge PR #73 的旧 Hook Adapter/真实 consumer 配置仍在待验收阶段。此次不合并、不关闭、不覆盖已有实现，也不触发全量 CI。

## 2. 需要裁决的规范与执行边界

**唯一规范性权威 ≠ 单一文档。** 同一概念、语义与契约有一个权威定义/维护源；不同设计方案、实施说明、项目规则与验收证据可各有独立文档，并应以引用代替复制同义规范。ECI #66 承担公共 Hook/Source 工程契约，Knowledge #68 承担项目特有规则与消费者验收；已有上游 standards/semantics 定义必须引用，不能借 Hook 设计再造。

**Lefthook 是受规范约束的成熟工具调度器，不是规范来源。** 它可以按阶段、路径、项目/工具条件判断、路由、分配并激活多个 Tool Jobs，安排安全并行/必要顺序、生命周期与诊断。ECI 负责**被激活的** Check 所需实际 Git Source、每 Ref 输入、细粒度 Selection、原生结果与失败归因。必要时 Job 直接调用 Tool/ECI Check/Dagger Function；**不能每次 Git Hook 先无条件调用“所有工具”再由单个 ECI 入口逐项跳过**。

**Lefthook 优先评估，不提前宣称选型定案。** PR #72 必须对固定版 `jobs/group`、条件与扩展配置、`use_stdin`、Windows/Linked Worktree、第三方 Hook 共存和原生结果归因做真实适配验证。Lefthook 若无运行时动态插入 Jobs API，不能虚构该能力；可比较固定 Jobs + ECI 细粒度参数/事件准备、prek 或真正功能等价的方案。单一 pre-push stdin 接收者不是单 Plan 或单进程。

## 3. 逐条冲突索引（精确旧句和新句见 .diff）

| 编号 | ECI #66 原文行 | 问题 | 定点目标 |
| --- | ---: | --- | --- |
| C01 | 56 | 将多工具路由/生命周期放入可选附件 | 明确工具路由必需，不预选产品 |
| C02 | 63 | Trigger/Analysis/Invalidation 与 Lefthook Job 可能双重维护 | 单一 Check 输入规范，Job 配置为执行投影 |
| C03 | 94 | 虽说多工具，却可能退回 ECI 单入口 | Lefthook Tool Job 激活 vs ECI 已激活 Check 范围 |
| C04 | 95 | 漏触发共享配置、删除引用目标时只依赖事后选择 | 保证相关 Job 已唤醒且受检输入一致 |
| C05 | 105 | 执行顺序仍指向薄 Hook | 改为成熟多工具调度接线 |
| C06 | 127 | 将 Required/Release 混写给 #63 | #52 原生 Check、#63 受信复用、#61 最终 Required |
| C07 | 170 | “Git Hook 仅触发 ECI CLI”与多 Job 冲突 | 允许 Lefthook 各 Job 条件启动、并行 |
| C08 | 172 | “不增建调度器”可被误读为不能使用 Lefthook | 禁自建重复调度器，保留成熟能力 |
| C09 | 219 | 排除通用 Runner 可能变成整体否定调度 | 明确反重复建设并非反成熟工具 |

**本轮提案不修改以上九个位置以外的 Issue 正文。** 每项保留原有验收、工具范围、Source P0、多 Ref/Index/Worktree/Actions、Dagger 和可信证据边界。不得再次将完整 PR #72 压缩为概要。

## 4. 尚需独立技术裁决的真实接口

1. **规则权威与 Lefthook 路由投影。** 统一 Check 输入/适用性要求；实际 Lefthook `glob/only/skip` 等是工具配置。若仅靠路径无法表达共享配置、锁、引用关系和动态外部输入，须通过定向反例验证保守 Job 唤醒与 ECI 内部细粒度 Analysis，不另写平行影响引擎。
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
- [ ] 确认 Lefthook 真实工具级路由/激活/并行在设计中保留，且不默认由单一 ECI 全量入口接管。
- [ ] 确认“统一规范权威”未被理解成只能有一份文档，Lefthook 未被称作规范。
- [ ] 未虚构 Lefthook 动态 API，也未把单一 Git stdin 接收简化为单 Plan/单 Job。
- [ ] #52/#61/#63/#66/#86/Knowledge #68 责任与现行仓库代码一致，原有完整工具矩阵和验收保存。
- [ ] 审阅通过后再提出 S1、S2 精确修改；**未经再次对照实时 Issue 全文，不把这个文件当作可盲目应用的补丁**。
