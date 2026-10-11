# 架构

本目录定义现有组件的职责、接口契约及执行约束。

- [CLI 边界](cli-boundaries.md) — 用户入口、Actions 内部调用及模块职责。
- [本地检查](local-checks.md) — 统一 PR 检查、执行身份、结果有效性与平台限制。
- [依赖安全](dependency-security.md) — 锁文件完整性、安全扫描及质量门禁。
- [执行与恢复](execution/README.md) — Workflow 执行 FSM、持久化与恢复契约。
- [发布](release/README.md) — Release Workflow 的门禁、证据与异常恢复。
- [测试](testing/README.md) — Pytest 报告、显式质量策略及 Python 兼容性。

上级入口：[技术文档](../README.md)。
