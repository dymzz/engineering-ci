# 验证记录

本目录保存已执行验证的结果、风险审查及性能测量证据。

- [发布风险审查](release-risk-review.md) — v0.4.9 发布证据审查、验证层次及当时剩余风险。
- [Dagger 原生配置迁移](dagger-migration.md) — 模块加载、生成兼容性与输入稳定性验证。
- [安全策略快照](security-policy.md) — 可信策略输入、接收/防回退与判定契约的本地验证。
- [变更影响选择](selection.md) — Git 对象、Tach 闭包、plan 身份与原生 unittest 的本地验证。
- [仓库 Hygiene](hygiene.md) — whitespace/forwarding/docs/Vulture/Tach 原生结果与 CLI 的本地验证。
- [来源集合对账](sources.md) — Manifest 投影、集合差/冲突/摘要/属性及 Dagger 调用的本地验证。
- [Weaver 原生检查](weaver.md) — 固定 Registry、OTLP 回放、终态证据与 Dagger Check/Artifact 契约。
- [CI/CD 遥测](cicd-telemetry.md) — GitHub Run/Job 事实 → Pipeline/Task 产物与负例本地验证。
- [Weaver](weaver.md) — Registry/Live/Terminal 原生检查与 [证据摘要](weaver-evidence/README.md)。
- [性能测量](performance/README.md) — CI 性能测量、原生缓存改造及采样数据。

上级入口：[技术文档](../README.md)。
