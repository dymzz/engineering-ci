# Selection Binding 准入与恢复验收

本记录针对 v0.6.7 的局部正确性修复，基线为 `8dc51e8c2524`。
验证日期为 2026-10-15。操作契约见[统一目录与 Binding](../architecture/harness-integration/registry.md)，
操作命令见[工作流指南](../../guides/generated-workflows.md#选择并调用一个领域工具)。

## 原问题与修复边界

抽验 PR #31 后发现三项宿主接入错误：

1. `select --tool` 使用旧 Catalog，却把任意 Provider 名称记入 Binding。
2. Binding 只验证自己的摘要，没有核对将要执行的 Workflow；有效的另一个生成文件可以被替换进去。
3. CLI `resume` 先执行 Runtime，之后才检查 Binding，可能在报告拒绝前已经执行工具。

修复复用 Vesryn 的注册加载、`load_generation`、Runtime 执行锁和状态机，
没有新增注册表、调度器、自动契约升级或发布权限。

## 自动化回归

使用 pytest 注入检查 Runner 或 Provider 的最终执行传输；真实安装的 Provider 发现路径
不再被 Catalog 替身覆盖。负向案例断言调用次数为零，且原状态字节不变。

覆盖 Provider 不存在或不匹配、契约变更、Scope 收缩或丢失、Binding 摘要变化、
Workflow 替换、参数变化、额外步骤、Generation/Execution ID 不匹配，以及执行锁内
的准入时序。既有失败传播、同输入复用、中断恢复和旧 Capability 路径继续回归。

```powershell
uv run --locked pytest -q tests/test_selection_binding.py tests/test_unified_registry.py tests/test_workflow_adapter.py tests/test_workflow_capabilities.py tests/test_workflow_comparison.py tests/test_pipeline.py
```

第一轮扩展回归为 **199 passed**。随后补充 Scope 与摘要案例，统一入口的实际
Dagger/Linux 完整检查为 **1124 passed、105 subtests passed、0 skipped**；
Ruff Lint、Ruff Format、pytest、版本与 Lockfile 五项均 passed / 0。
该工作区包含未提交的独立 Task Observation 测试，因此最终交付提交的准确结果以
本次 PR 的同 SHA CI 为准。Mock 成功不计为实际 Dagger 执行成功。

## 真实 CLI / Provider / Dagger Linux

以下通过真实 CLI 和已安装 `engineering-ci-dagger` 入口执行，没有替换 Catalog、
Provider 或 Dagger。CLI/Engine 为 `1.0.0-beta.16`，Ruff 为配置锁定版本 `0.16.10`。

| 场景 | 实际结果 |
| --- | --- |
| Ruff Format 存在未格式化代码 | `failed`，原始退出码 **1**；保留容器报告及日志摘要 |
| 格式修复后执行 | 原 Provider 返回 `passed`，原始退出码 **0** |
| RUNNING 时强杀本次创建的 CLI 进程 | 主状态保持 RUNNING；原 Provider 完成独立领域结果 |
| 对同一执行目录 `resume` | **passed / 0**；Execution ID 不变，`reused=true`，复用原检查证据 |

失败执行 ID：`5c31ac06063447ec8f9e577fa334325e`。
强杀及恢复执行 ID：`4dcb7457ffdd4719a39a0c816dde8ecd`。
恢复复用的容器报告摘要为
`3ce1871f0da45df88edaec113d9bd1bdfb88e8efdbc716a0e7c94618d9fe7cbc`。
原始结果在本机忽略目录 `.test-tools/binding-v067/`；它们不是 GitHub Release Assets。

这些本地验收发生在含独立 Task Observation 改动的工作区。该功能未随本修复交付，
验收后还做了局部准入和测试完善。因此这里的真实执行记录证明上述路径曾实际运行，
不替代最终提交的完整 CI，也不作为最终源码或正式 Release 的放行证据。

## 剩余限制

- 强杀只覆盖宿主 CLI；没有模拟 Docker/Engine 同时崩溃或整机断电。
- Binding 摘要不是签名；不防御能持续改写整个受信任工作区的攻击者。
- Schema 1 中错误标注 Provider 的旧记录会明确拒绝，须审查后重新选择，不自动迁移。
- 私有 Git 依赖的正式 Security Gate 限制保持不变，本轮没有创建 Tag 或 Release。

上级入口：[验证记录](README.md)。
