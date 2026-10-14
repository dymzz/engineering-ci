# Engineering CI 仓库介绍

本页维护项目事实，作为根 [AGENTS.md](../../AGENTS.md) 的初稿与维护依据。
项目边界或入口改变时同步更新；当前任务进度、分支、PR 状态和测试结果放在交付记录。
具体技术契约与执行配置仍以本页链接的文档、源码和 TOML / YAML 为准。

## 目标与职责

engineering-ci 提供可复用的 Python 工程验证与交付能力，供本仓库及消费者通过
Dagger、GitHub Actions 和本地 CLI 调用。主要职责是可靠执行检查、保留原始失败、
识别当前输入的有效证据，并把真实提交、PR、Run 与 Release 关联起来。

它保留 CI/CD 领域策略，不承担通用 Agent 优化机制。可选 Harness 由独立的 Vesryn 提供；
基础检查、版本、发布与下载路径不要求安装它。库实现和当前依赖见
[pyproject.toml](../../pyproject.toml)。

## 已有能力与代码位置

| 能力 | 当前实现位置 | 边界 |
| --- | --- | --- |
| 本地 / PR 统一检查与证据复用 | `src/engineering_ci/checks.py`、`check_operations.py`、`dagger_checks.py` | 复用必须匹配实际输入、工具、参数和环境；报告不替代退出状态 |
| 下载、等待与网络恢复 | `src/engineering_ci/execution/` | 集中 FSM、原子状态和协议后端；不重新实现远端 Job 调度 |
| 开发交付与 Release 证据 | `src/engineering_ci/release/` | 本地交付上下文、版本 / SHA 校验、Manifest、发布操作与恢复 |
| 可复用容器 CI | `.dagger/src/engineering_ci/` | pytest / 质量 / Matrix 能力、单次执行报告与容器检查；不持有完整开发生命周期 |
| GitHub 远端编排 | `.github/workflows/` | PR CI、复用 Workflow 与手动 Release 的 DAG / 权限 / 门禁 |
| CI Harness 适配与领域准入 | `src/engineering_ci/workflow_adapters/` | 注册检查工具、复用证据、批准 / 反馈 / 再审查策略 |
| 既有 Harness 路径兼容 | `workflow_core/`、`tracing/`、通用 `mining/` 与部分 CLI | 委托固定 Vesryn；不是第二份通用核心实现。CI 绑定仍由宿主维护 |

版本一致性和锁文件完整性、已知漏洞扫描是不同检查。锁定私有 Git Commit 不代表当前安全扫描器
已覆盖该来源；现有漏洞扫描缺口不会因分仓、生成报告或修改 Agent 指令而成为通过。
定义见[依赖安全](dependency-security.md)。

## 入口与配置

- 用户入口是 `engineering-ci` CLI，以及 `scripts/release.py`、`scripts/download.py` 两个薄入口。
  Release 交付入口停在审查合并；已授权 Agent 可另行使用 gh 合并。正式 Release 仍由人主动请求。
  Download 只选择 / 恢复 / 收集已有 Run，不 Dispatch。操作见[常用指南](../../guides/common-tasks.md)。
- GitHub Actions 使用 `python -m engineering_ci.workflow_actions`，不把内部业务助手放进 `scripts/`。
- `config/pipeline.toml` 声明已有注册检查的参数、工具链、范围、Profile 与交付绑定；
  YAML 继续决定同一次 Workflow 的静态 Job DAG。不能靠 TOML 授权或绕过发布条件。
- 下载后端和本机环境设置见[配置入口](../configuration/README.md)。机器专用配置不提交。

CLI / FSM / 配置职责分别见[调用边界](cli-boundaries.md)、
[执行与恢复](execution/README.md)、[发布契约](release/README.md)。本页不复制状态表或 Workflow Step。

## 依赖与跨仓库关系

Python 运行时需要 Python 3.14 及项目锁定依赖；本地 / CI 使用既有 uv、pytest、Ruff、Dagger、
Git 和 gh。HTTP 的 aria2 是可选后端，缺少时 auto 使用原生 HTTP，而非把基础下载判为不可用。

Vesryn 通过固定版本与不可变 Git Commit 显式安装，开发组安装其可选 SDK 用于兼容测试。
Vesryn 的 Python 包不依赖 engineering-ci；它的 CI 消费固定 engineering-ci Workflow / Dagger 版本。
这区分运行时依赖和 CI 使用关系，避免两个未发布开发分支互相构建。
固定引用与旧身份兼容见[分仓契约](harness-integration/repository-split.md)。

## 验证、维护与 Agent 初稿

共享检查入口见[本地检查](local-checks.md)；指南给出单项 / 完整 Profile 的实际命令。
测试包括单元、真实子进程、HTTP / 下载故障与显式 PostgreSQL 专项；跳过、失败、不可用和通过
分别记录。具体证据由[验证记录](../verification/README.md)承载，不把历史测试数量写成永久规则。

从本页生成 AGENTS 初稿时提取项目目标、职责边界、入口导航和关键不变量；
共享开发纪律来自全局指令，Actions / Dagger 的专项约束放进对应子树文件。
不要把模块清单、完整安装步骤、Pipeline 定义或当前阶段进度复制到 AGENTS。
层级说明见[Agent 指令](agent-instructions.md)，共享文档约定见[维护约定](../conventions.md)。

自动优化 Loop、Bandit、通用任务平台或新 Agent 工作流并非这份 CI/CD 实现的已有能力。

## Dagger 优先的迁移方向

Dagger 拥有检查执行图、容器调度与缓存；GitHub Actions 拥有远端 Job DAG。
engineering-ci 提供项目质量策略、证据准入、GitHub 交付和跨 Run 恢复。
当前容器 PR 路径仍通过 Python 检查循环执行，pipeline.toml 仍是固定检查配置；
原生工作区和模块配置使用 dagger.toml / dagger-module.toml；原生 PR Checks
执行图迁移尚未实施，现有 CLI、报告与失败契约保持不变。
通用 Harness 的目标边界和技术正文由 [Vesryn 文档](https://github.com/dymzz/vesryn/blob/7ac74b83c4a206446b9c8b52cc88c906a13ad2f5/docs/README.md) 维护，不在本仓库保留平行实现方案。
