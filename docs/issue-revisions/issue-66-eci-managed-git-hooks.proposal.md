# ECI PR #72 — Git Hooks 设计决议与交接（归档记录）

> **权威要求：[ECI Issue #66](https://github.com/dymzz/engineering-ci/issues/66)**。本 PR 是讨论/设计收口记录，**不包含可运行的 Hooks CLI、Source 物化或新工具的实现**；不得合并此文件制造第二份长期规范。实施工作直接从 Issue #66 和新的实际代码 PR 推进。

## 2026-11-12 审阅结果

1. **已吸收至 #66：** ECI 统一 Hook 生命周期与真实 Git 事件适配；暂存 `GIT_INDEX_FILE`、Commit/Push/PR 真实受检字节；现有 `engineering_ci` 公共 Selection/Execution/Hygiene/Sources/Plan/Report；Host/Dagger 同源增量许可与原生结果；Trigger/Analysis/Invalidation 的实际失效条件。已合并 #67/#70/#71/#73/#79/#88 必须复用，不从 `eci_core` 重建。
2. **Event/Source/Ref 决议：** 一个 Hook Event 共享 Git stdin、配置、工具版本和启动上下文，允许每个 ref 具有独立 old→new、Source、Selection/Plan 和 Check 影响范围；`changeset_multi` 不能直接代表单一执行 Source。相同新 Tree 不保证比较/兼容性检查的输入等价；同次有效原生 Result 可按实际输入与 Check 范围复用，跨 Run 受信历史才由 #63 接纳。
3. **Linked Worktree 修复位置：** ECI `selection.tree_content_id()` 当前依赖 `root / '.git'` 形态。新实施必须以 Git 原生 `rev-parse --absolute-git-dir` / `--git-path index` 等解析真实上下文及 ODB，在临时 Index/Linked Worktree 下物化并验证真实工具读取的字节。不得以 Git 配置改动或一个布尔 `tree_verified` 代替实际 Source 证明。
4. **Hook 选型决议：** Git 原生薄 Hook 是不增加额外生命周期调度器的实施基线；固定版 Lefthook/prek 仅在实测生命周期、现有 Hook 共存、独立任务并行、诊断/启动开销收益足够且不丢 Git stdin/Index 语义时择一采用。不能双安装或重复执行。
5. **工具能力覆盖不删减：** Python、Secrets、文档、架构、静态/类型、安全、Rust/Java/frontend、API/Protobuf、形式化/属性、fuzz/mutation、在线链接等完整候选保留在 #66 的能力规划；按实际仓库启用和真实 Check 需求成批接入，新增原生执行属于 #52/Runtime，不把全部候选强制安装作为 #66 退出条件。

## 实施接口与后续验收

- **ECI #66**：P0 真实 Source/Index/Commit/Push/Linked Worktree 与被工具读取字节一致；P1 Host/Dagger Tach 许可、Git 多 ref 与 Hook 生命周期、实际 Source/Prerequisite 生产回执。
- **ECI #86**：真实 Dagger DAG、Data/PASS 消费、产物闭包与同次 Result 计算拓扑；**#52**：原生 Check/Enforce/Actions；**#63**：跨 Run 受信接纳；**#61**：Required/Release。
- **Knowledge #68**：消费固定合格 ECI 包、项目规则/Workflow、替换旧 Hook Adapter、保留领域正反例；设计交接见 [Knowledge PR #78](https://github.com/dymzz/engineering-knowledge/pull/78)。
- **必要反例**：临时 Index=B/默认 Index=A/Worktree=C；Linked Worktree；两个相同新 Tree 但不同 old→new 的 refs；首次/强推/非 HEAD/删除 Ref；变更工具规则/配置/锁文件/被引用目标；正确失败、未执行及同次有效计算复用。

## 归档方式

有效规范已进入 Issue #66；原始详细工具目录、分类矩阵、Lefthook 示例及多轮设计在本 PR 的**历史提交**中保留可追溯，不作为第二份规范。此 Draft 应以 `superseded by Issue #66` **关闭而不合并**，避免再次增加永久设计文档。后续实现 PR 直接引用 #66 的相应验收条款；文档收口并不代表代码通过。
