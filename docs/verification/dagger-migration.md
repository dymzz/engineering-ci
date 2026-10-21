# Dagger 原生配置迁移验证

## 范围

2026-10-14，基于 `963d809a295f` 的主 checkout 工作区验证，目标版本 v0.6.6。
原地将 `dagger.json` 转为 `dagger-module.toml`，工作区 `dagger.toml`
安装相同模块作为 entrypoint；保留 `.dagger/src`、公开函数与检查执行契约。
生成 SDK 随原生模块交付，两个 TOML 和 SDK 纳入证据输入。
当时未迁移 PR 检查执行或 GitHub Job DAG；当前检查实现以[原生 Checks](../architecture/local-checks.md)为准。

## 已执行结果

| 验证 | 状态 | 证据 |
| --- | --- | --- |
| 原地迁移预览及应用 | passed | beta.16 `dagger module migrate . --no-apply` / `--auto-apply`；未移动源码 |
| 配置及 Dagger 适配测试 | passed | 141 tests |
| 扩大配置 / 检查 / Workflow 回归 | passed / skipped | 306 passed、6 Windows 环境限制 skipped |
| 真实模块加载 | passed | `dagger api functions` 保留原有公开函数 |
| 正向 pytest fixture | passed | `dagger api call fixture-test` |
| 负向 pytest 报告 fixture | passed | `dagger api call fixture-failure-report` 验证预期失败事实 |
| 模块 SDK 生成及 lock 检查 | passed | 下列模块生成 API、`uv lock --project .dagger --check` |
| 首次完整 Linux PR 执行 | stale | 1066 tests、105 subtests passed，工具退出码为 0；宿主输入变化使 pytest 证据过期，整体拒绝放行 |
| 随后恢复预检 | failed | `src/engineering_ci/workflow_adapters/AGENTS.md` 与容器源不一致，未开始新的测试 |
| 中间完整 Linux PR 检查 | passed | 当时输入稳定；1066 tests、105 subtests passed，五项检查 passed，整体退出码 0 |
| 提交前最终工作区验证 | stale | 测试仍全通过；独立的 pyproject.toml / 指令更新改变了输入，当前工作区整体退出码 3，不作为放行依据 |
| 文档路径与锚点 | passed | 含固定 Vesryn Commit 的跨仓库链接检查无错误 |

上述本地结果不替代候选提交的远端 PR CI 或正式发布验证。
独立指令、技术清单和 wheel-exclude 改动未纳入本阶段代码提交；确定提交由 PR CI 验证。

## 生成兼容性限制

锁定 beta.16 的 Python runtime SDK 用作工作区 SDK 时，实际生成失败：
`required function findClientRoot is not implemented`。不保留该无效 scope。
模块生成 API 在新清单下真实通过：

```sh
dagger api call --no-load-module module-source --ref-string . \
  generated-context-directory directory --path .dagger export --path .dagger
uv lock --project .dagger --check
```

该观察不能证明所有 Python workspace SDK 都不可用，详见
[上游观察](../../.agent/upstream-observations.md)。原生 Checks 执行图迁移仍是下一阶段，
不得将本阶段的配置成功记作执行图迁移完成。

上级入口：[验证记录](README.md)。
