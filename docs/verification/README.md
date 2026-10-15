# 验证记录

本目录保存已执行验证的结果、风险审查及性能测量证据。

- [执行验证](execution-validation.md) — 执行组件初次重构的分阶段故障测试与真实执行记录。
- [发布风险审查](release-risk-review.md) — v0.4.9 发布证据审查、验证层次及当时剩余风险。
- [恢复验收](recovery-validation.md) — v0.5.4 强杀进程、跨阶段恢复及最终 Release SHA 绑定验证。
- [Harness 历史设计](harness-development.md) — 版本依据、原阶段安排与设计审查的历史范围。
- [原始轨迹验收](trace-validation.md) — 来源、PostgreSQL 事务及进程恢复的阶段验证与未完成范围。
- [独立工作流验收](workflow-validation.md) — 生成、工具数据引用、检查复用与故障恢复的实际证据。
- [Vesryn 分仓验收](repository-split.md) — 独立安装、旧证据兼容、私有依赖与两仓库真实 CI。
- [Dagger 原生配置迁移](dagger-migration.md) — 模块加载、生成兼容性与输入稳定性验证。
- [PR 检查执行图](pr-check-graph.md) — Dagger 分支、原始失败、Coverage 与证据复用验收。
- [Binding 准入与恢复](selection-binding.md) — Provider、实际 Workflow 关联、执行前拒绝及真实 Dagger 强杀恢复。
- [性能测量](performance/README.md) — CI 性能测量、原生缓存改造及采样数据。

上级入口：[技术文档](../README.md)。
