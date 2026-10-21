# Engineering CI 仓库介绍

本页维护项目职责与实际入口，作为根 [AGENTS.md](../../AGENTS.md) 的维护依据。
当前任务进度与测试证据放在对应 Issue / PR。

## 目标与职责

engineering-ci 提供 Python 工程验证与交付能力，供本仓库及消费者通过 Dagger、
GitHub Actions 和本地 CLI 调用。质量门禁保留原始失败；JSON、Summary 与 Artifact
只是执行结果的表现形式，不决定测试是否通过。

Dagger 负责容器、执行图、并发与缓存；GitHub Actions 负责远端 Job DAG、权限与发布条件。
Agent Harness、Mining、通用 Workflow 和日志存储、归档、查询由外部项目维护。
本仓库不提供其兼容转发或旧 Python Provider / Catalog。

## 依赖与跨仓库关系

engineering-ci 不得直接或间接依赖 `vesryn`、`vesryn-workflow`。两者可以消费本仓
公开的 Dagger / Workflow CI 能力；CI 的构建、验证和发布不依赖被验证的 Harness 或
Workflow Runtime，从而避免反向依赖和引导循环。

实际 CI 工具契约与调用可以直接使用 `vesryn-tool-runtime`；需要工程事件观测时，
本仓只负责最小插桩，由 `vesryn-log` 负责写入和通用日志能力。这两个依赖的传递闭包
同样不能引入 Harness 或通用 Workflow。确需跨越边界时先调整职责或提取公共组件，
不能恢复兼容层来绕过依赖方向。

当前消费者使用公共 Dagger Functions，共享 Workflow 调用这些原生接口；基础 CLI
使用本仓检查、Git/gh 和发布模块，不消费旧 Catalog 或默认任务日志。
因此当前不声明 Log / Tool Runtime 依赖，删除旧采集包装而保留工具原始输出及 Dagger
原生执行轨迹。以后出现实际 CI 调用需求时可以直接采用这两个组件，无需重建 Provider。

## 当前实现

| 能力 | 代码位置 | 职责 |
| --- | --- | --- |
| 本仓原生检查 | `.dagger/src/engineering_ci/main.py`、`repository.py` | 实际源码上的五项 Dagger Checks，原生 DAG 与缓存 |
| 可复用容器检查 | `.dagger/src/engineering_ci/` | Lint、Format、pytest、质量策略、Matrix、单次执行报告 |
| GitHub 执行与下载 | `src/engineering_ci/execution/` | 已有 Dispatch、等待、恢复、HTTP 下载与安全解压 |
| 交付及发布证据 | `src/engineering_ci/release/` | Git / PR 交付、版本、Manifest 与发布操作 |
| 远端编排 | `.github/workflows/` | PR CI、共享 Workflow、手动 Release 的权限和门禁 |

宿主检查 Runner、Profile 配置、输入散列及 passed 收据已删除。GitHub 执行恢复层仍保留，后续按 #41 收敛。
基础检查定义见[本地检查](local-checks.md)，公共消费者接口见[支持范围](../delivery/public-components.md)。

## 入口与配置

- 用户 CLI：`engineering-ci version / release / download`。
- 两个脚本：`scripts/release.py` 与 `scripts/download.py`；使用方式见[用户指南](../../guides/README.md)。
- Actions 内部操作：`python -m engineering_ci.workflow_actions`。
- Dagger 模块：`dagger.toml`、`dagger-module.toml` 与生成的 SDK / 锁文件。
- 标准 Ruff 设置和锁定开发工具：根 `pyproject.toml`、`uv.lock`；原生 Check 与容器定义在 Dagger 模块中。
- 下载设置：见[配置入口](../configuration/README.md)，机器专用配置不提交。

Python 包使用 Python >=3.14、transitions 和 filelock；开发测试使用 pytest / pytest-cov / Ruff / Ruff。
Dagger 模块有独立 SDK 依赖。aria2c 是可选下载后端；GitHub 操作使用现有 Git / gh 认证。
私有消费者依赖的 Secret 由调用方显式提供，基础开发环境不再需要 Vesryn 仓库的读取凭据。

## 维护边界

版本与 Lockfile 一致性、已知漏洞扫描是不同检查，见[依赖安全](dependency-security.md)。
发布与质量门禁不接受 skipped、incomplete 或报告生成成功替代必需执行通过。

现有检查和发布契约分别见[调用边界](cli-boundaries.md)、[执行恢复](execution/README.md)
及[发布](release/README.md)。消费者直接组合公共 Dagger Functions 和共享 Workflow，
本仓库不维护通用建仓或 Git 仓库生命周期程序。

文档维护遵循[约定](../conventions.md)；Agent 指令的作用域见[说明](agent-instructions.md)。
