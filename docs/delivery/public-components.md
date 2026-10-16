# 公共组件与支持边界

Engineering CI 的公共交付包含以下已有接口。Public 表示本项目承诺维护的调用边界，
不表示仓库已公开、包已上传 PyPI，或所有可导入的内部模块都成为稳定 API。
安装及固定引用见[消费者接入](../../guides/new-repository.md#选择公共组件与固定版本)。
正式版本的版本、Tag、SHA 和验证 Run 由[发布机制](../architecture/release/workflow.md)关联。

| 接口 | 定位与消费方式 | 契约归属 |
| --- | --- | --- |
| `engineering_ci.execution` 的 `ExecutionCoordinator`、`ExecutionFSM`、`StateStore`、`InvalidTransition`；配套的身份、TaskRecord 与类型化错误 | 公共 Python API；安装固定版本包后直接导入。不依赖调用方读取本仓库 src 或 `.venv` | [执行与恢复](../architecture/execution/state-and-recovery.md#python-组件消费) |
| Dagger `python_lint`、`python_format_check` | 公共单版本容器检查；输入 Directory、Python minor、检查路径及可选 Secret，返回输出字符串或抛出执行错误 | [Dagger 配置与消费](../configuration/dagger.md#公共函数与凭据) |
| Dagger `python_test`、`python_test_report`、`python_test_artifacts` | 公共单版本 pytest；分别为严格门禁、信息 JSON、单次执行的证据目录 | [Pytest 报告](../architecture/testing/pytest-reporting.md) |
| Dagger `python_test_matrix`、`python_test_matrix_report`、`python_test_matrix_artifacts` | 公共多版本 pytest；输入版本列表和显式策略，逐版本保留实际结果 | [Python Matrix](../architecture/testing/python-matrix.md) |
| `.github/workflows/reusable-python-ci.yml` | 公共 `workflow_call`；固定同一 Engineering CI 版本的 Dagger 模块和 Workflow，原始检查决定 Job 结果 | [消费者接入](../../guides/new-repository.md)、[报告模式](../architecture/testing/pytest-reporting.md#opt-in-reusable-workflow)、[依赖安全](../architecture/dependency-security.md) |
| 安装包的 `engineering-ci download` | 公共已有 Run 查询/下载；gh 认证、HTTP 后端和安全解压，禁止 Dispatch | [CLI 参考](../../guides/cli-reference.md)、[下载配置](../configuration/downloads.md) |
| `engineering-ci checks/version/release` 与两个 `scripts/` 入口 | 本仓库操作接口；需要完整 engineering-ci checkout、配置和锁文件，不是对任意消费者目录的通用检查或发布 API | [常用操作](../../guides/common-tasks.md) |
| `engineering-ci`、`engineering-ci-dagger` Providers，Binding/Capability | 可选 Harness 集成；依赖固定 Vesryn，批准与调用受实际 Scope 和证据准入约束 | [目录与 Binding](../architecture/harness-integration/registry.md)、[能力与反馈](../architecture/harness-integration/capabilities.md) |
| `workflow_core/`、通用 `tracing/` / `mining/` 旧导入路径 | 兼容层；portable 实现分别由 Vesryn / vesryn-log 维护，不据此承诺另一套独立 Harness | [分仓契约](../architecture/harness-integration/repository-split.md)、[日志边界](../architecture/cli-boundaries.md#默认任务采集与日志记录) |
| `workflow_actions`、Release prepare/verify/publish 助手与注册信息 | 内部 Actions 接口；由已发布 Workflow 调用，不是新的用户 CLI 或跨项目发布框架 | [CLI 边界](../architecture/cli-boundaries.md) |
| Dagger `fixture_*`、`container_echo`、`grep_dir`、`repository_check_*` | 自测/示例或本仓库适配接口，不作为通用消费者稳定契约。消费者不依赖 fixture 名称或内部文件布局 | [本地验证](../architecture/local-checks.md) |

## 运行条件与结果

Python 包要求普通 CPython >=3.14；Dagger 消费者使用固定 Engine/CLI 1.0.0-beta.16，
单版本与 Matrix 支持范围以[Matrix 契约](../architecture/testing/python-matrix.md)为准。
消费者自身 Python 版本由其项目声明，不能把宿主包的 Python 要求误认为所有容器都必须 3.14。
基础 Python API 不要求安装 Harness、日志 SDK、PostgreSQL 或挖掘库。
可选 extras 的私有 Git 源需要通过 checkout 中明确的 uv sources 安装，不承诺 PyPI 分发。

每个结果仍以其原始执行状态为准。报告、ZIP 下载或 Artifact 上传成功，不表示检查成功。
未执行、跳过、取消、不可用和 incomplete 不能用于代替必需的通过结果。
下载失败 Run 的证据仍保留远端失败；CLI / Dagger 的非零、异常与结构化状态详见各契约。
只有调用者显式配置的测试范围和 Coverage 阈值生效，不自动替消费者删测试或降低门槛。

## 版本与演进

当前采用 `vMAJOR.MINOR.PATCH` Tag，对应四处项目元数据和不可变提交。
可使用经过验证的固定 SHA 验收候选，但该 SHA 不是已发布版本；正式升级必须核对 Release Manifest。
同时更新消费者的 Workflow 引用、Dagger 模块引用和原生锁文件，审查生成的差异后运行消费者 CI。
Renovate 更新仍由现有配置负责，不增加跨仓库编排。

v0.6.7 保持 pytest 报告字段、严格门禁、旧消费者默认参数和脚本路径；
单版本 Dagger 增加可选 `git_token`，不改变未提供该参数的调用。
Matrix 当前没有这个新增参数，不能暗示已经支持同样的私有依赖安装路径。
Git 安全策略是显式启用的已知 Commit 漏洞查询，不是私有源码审计。
固定版本及 Dagger beta 本身的已知限制见[配置](../configuration/dagger.md)。

后续拆分只能从这些已核验的公开契约出发；本次不搬迁 Release、Manifest 或执行实现。
本轮候选身份、实际消费验证与 C1–C8 结论保存在 PR 交付记录，缺少证据的路径明确记为 NOT VERIFIED。
