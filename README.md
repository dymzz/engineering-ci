# Pytest reporting

个人开发采用本地优先检查与手动完整发布验收：[命令与发布流程](docs/release.md)。

脚本调用方式、运行目录及兼容入口：[scripts 使用说明](scripts/README.md)。
安装后的命令和 Workflow 菜单：[统一 CLI](docs/unified-cli.md)。

See [single-execution pytest reports](docs/pytest-reporting.md) for the opt-in
reusable workflow, JSON contract, Job Summary and native artifact delivery.

See [explicit test strategies and quality gates](docs/test-quality-gates.md)
for markers, opt-in coverage, exact thresholds and consumer examples.

Python 多版本兼容性验证（显式启用）：[接口、报告契约与性能验证](docs/python-compatibility-matrix.md)。

依赖锁一致性与已知漏洞门禁（默认关闭）：[使用说明](docs/dependency-security.md)。

CI 性能基线与原生 uv 包缓存：[测量和使用说明](docs/ci-performance.md)。
