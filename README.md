# Engineering CI

Engineering CI 通过 Dagger、GitHub Actions 与本地工具提供 Python 工程检查、测试报告、
版本校验、发布证据与 Workflow 产物下载。Dagger 负责容器执行、依赖图和缓存；
GitHub Actions 负责远端编排与权限；本仓库维护质量策略和发布门禁。

Agent 本地工程调用与 GitHub 认证排查见[常用操作](guides/common-tasks.md#github-认证与工程调用入口)；复用当前执行账号的既有登录，并按调用位置区分凭据边界。

## 文档入口

- [用户指南](guides/README.md) — 首次使用、常用操作、命令和输出参考。
- [技术文档](docs/README.md) — 文档约定、架构、接口契约和验证记录。
- [用户脚本](scripts/README.md) — Release 与 Download 两个入口的范围和维护说明。
