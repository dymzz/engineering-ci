# Engineering CI 仓库介绍

本页维护项目职责与实际入口，作为根 [AGENTS.md](../../AGENTS.md) 的维护依据。
当前任务进度与测试证据放在对应 Issue / PR。

## 目标与职责

engineering-ci 提供 Python 工程验证与交付能力，供本仓库及消费者通过 Dagger、
GitHub Actions 和本地 CLI 调用。质量门禁保留原始失败；JSON、Summary 与 Artifact
只是执行结果的表现形式，不决定测试是否通过。

Dagger 负责容器、执行图、并发与缓存；GitHub Actions 负责远端 Job DAG、权限与发布条件。
工程验证的目标是：供消费者复用实际工程检查；对适用 Source 执行正确的原生验证；保持结果、身份、
来源与证据范围真实；按有权策略完成交付和发布；通过原生缓存、适用的选择、产物与可信证据复用减少重复计算；
保持组件职责清晰与消费者可维护性。这些是职责目标，不代表各个候选接口或生产授权链已经交付。
具体设计与实施范围分别见[架构索引](README.md)、[跨组件执行图](../../design/execution-graph/README.md)
及对应 Issue；任务接续与证据复用见[Agent 指令](agent-instructions.md#task-continuity-and-evidence-reuse)。

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
| 本仓原生检查 | `.dagger/src/engineering_ci_module/main.py`、`repository.py` | 实际源码上的五项 Dagger Checks，原生 DAG 与缓存 |
| 可复用容器检查 | `.dagger/src/engineering_ci_module/` | Lint、Format、pytest、质量策略、Matrix、单次执行报告 |
| Plan 原生结果 | `.dagger/src/engineering_ci_module/plan_execution.py`、`plan_result.py` | 独立原生分支收集、同一 Directory 的 Report/Artifact/Enforce；不授予 Source/Trusted 接纳 |
| 共享检查实现 | `src/engineering_ci/{selection,execution,hygiene,sources,cicd_telemetry}.py` | CLI 与 Dagger 共同消费的选择、执行、来源和遥测实现 |
| 本地 Hook 调度 | `lefthook.yml`、`ci-selection.toml`、`ci-hygiene.toml`、`config/hook-profiles/` | Lefthook 2.2.2 生命周期与 Job 调度；本仓已启用 `pre-commit`、`pre-push` 与显式 `verify` |
| Hook 来源与对账 | `src/engineering_ci/{hooks,hook_checks,git_source,dagger_plan,reconciliation}.py` | Git Event/Index/Push 捕获（含采集与消费复核共享的 `index.lock` 安全快照回退）、blob 隔离物化、Dagger 交接、排他激活与非排他首次成功保留的阶段耗时 sidecar（`.timing-*`；在 stage `finally:` 出口写入并由 `run_push_checks` 写入完整 `per_ref`，`run_checks` 在后验 Source 拒绝前保留 `.execution` 与 `.source-rejection`）、`inspect_invocation_offline` 纯只读离线核验与 #66-C 对账（本地 Receipt 保持 `trusted=false`、`required=false`） |
| 验证计时与只读报告 | `cli/verify.py`、`verify_report.py`、`hook_checks.py`、`pytest_contracts.py` | 在显式 Lefthook Verify 中保留原始退出、计时及同次 Dagger pytest 产物。复用 Hook 原生记录校验及 Dagger 的 pytest/JUnit 计数规则。查询分别表达实际执行、成员、内容和上下文关系。 |
| GitHub Run 操作 | `src/engineering_ci/github_cli.py`、`cli/` | 原生 gh 查询、等待、Artifact 与日志下载 |
| 发布请求及证据 | `src/engineering_ci/release/` | 具体 Dispatch 意图、版本、Manifest、身份核验与发布操作 |
| 远端编排 | `.github/workflows/` | PR CI、共享 Workflow、手动 Release 的权限和门禁 |

旧版宿主检查 Runner、input-hash `passed` 收据缓存、通用执行 FSM / Store / Backend 以及本地 Git/PR 交付生命周期均已删除；本地 Hook 仅通过 `lefthook.yml` 与 `config/hook-profiles/` 生成结构性自用 Receipt，不授予服务端 `trusted` 接纳。
Git/PR 由原生工具操作；仅具体 Dispatch 保留防重复请求的未决意图，不实现通用任务持久化。
基础检查定义见[本地检查](local-checks.md)，公共消费者接口见[支持范围](../delivery/public-components.md)。

## 入口与配置

- 用户 CLI：`engineering-ci version / release / download`。
- 两个脚本：`scripts/release.py` 与 `scripts/download.py`；使用方式见[用户指南](../../guides/README.md)。
- 本地 Git Hook 与显式检查：`uv run --locked --no-sync lefthook install / validate / run verify`；配置位于 `lefthook.yml`、`ci-selection.toml`、`ci-hygiene.toml` 与 `config/hook-profiles/`。
- Actions 内部操作：`python -m engineering_ci.workflow_actions`。
- Dagger 模块：`dagger.toml`、`dagger-module.toml` 与生成的 SDK / 锁文件。
- 标准 Ruff 设置和锁定开发工具：根 `pyproject.toml`、`uv.lock`；原生 Check 与容器定义在 Dagger 模块中。
- Run 下载：原生 gh 参数，由两个用户入口显式传入；没有另一个下载配置来源。

Python 包使用 Python >=3.14 标准库，无第三方运行时依赖；开发工具为 pytest / pytest-cov / Ruff / Lefthook。
Dagger 模块有独立 SDK 环境；GitHub 操作使用现有 Git / gh 认证，不额外配置下载后端。
私有消费者依赖的 Secret 由调用方显式提供，基础开发环境不再需要 Vesryn 仓库的读取凭据。

## 维护边界

版本与 Lockfile 一致性、已知漏洞扫描是不同检查，见[依赖安全](dependency-security.md)。
发布与质量门禁不接受 skipped、incomplete 或报告生成成功替代必需执行通过。

现有检查和发布契约分别见[调用边界](cli-boundaries.md)及[发布](release/README.md)。消费者直接组合公共 Dagger Functions 和共享 Workflow，
本仓库不维护通用建仓或 Git 仓库生命周期程序。

文档维护遵循[约定](../conventions.md)；Agent 指令的作用域见[说明](agent-instructions.md)。
