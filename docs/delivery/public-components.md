# 公共组件与支持边界

Engineering CI 的公共交付包含以下已有接口。Public 表示本项目承诺维护的调用边界，
不表示仓库已公开、包已上传 PyPI，或所有可导入的内部模块都成为稳定 API。
安装及固定引用见[消费者接入](../../guides/new-repository.md#环境与固定引用)。
正式版本的版本、Tag、SHA 和验证 Run 由[发布机制](../architecture/release/workflow.md)关联。

| 接口 | 定位与消费方式 | 契约归属 |
| --- | --- | --- |
| Dagger `python_lint`、`python_format_check` | 公共单版本容器检查；输入 Directory、Python minor、检查路径及可选 Secret，返回输出字符串或抛出执行错误 | [Dagger 配置与消费](../configuration/dagger.md#公共函数与凭据) |
| Dagger `python_test`、`python_test_report`、`python_test_artifacts` | 公共单版本 pytest；分别为严格门禁、信息 JSON、单次执行的证据目录 | [Pytest 报告](../architecture/testing/pytest-reporting.md) |
| Dagger `python_test_matrix`、`python_test_matrix_report`、`python_test_matrix_artifacts` | 公共多版本 pytest；输入版本列表和显式策略，逐版本保留实际结果 | [Python Matrix](../architecture/testing/python-matrix.md) |
| Dagger `python_vulture`、`python_pyright`、`python_deptry`、`python_pylint` | 公共静态分析；各自独立入口、显式路径与原生配置，返回保留原生退出与结果、不自行门禁的 JSON 报告 | [Python 静态分析](../architecture/testing/python-static-analysis.md) |
| `.github/workflows/reusable-python-ci.yml` | 公共 `workflow_call`；固定同一 Engineering CI 版本的 Dagger 模块和 Workflow，原始检查决定 Job 结果 | [消费者接入](../../guides/new-repository.md)、[报告模式](../architecture/testing/pytest-reporting.md#opt-in-reusable-workflow)、[依赖安全](../architecture/dependency-security.md) |
| 安装包的 `engineering-ci download` | 公共已有 Run 查询/下载；原生 gh，禁止 Dispatch，不承诺字节恢复 | [CLI 参考](../../guides/cli-reference.md) |
| `engineering-ci version/release/download` 与两个 `scripts/` 入口 | 本仓库操作接口；需要完整 engineering-ci checkout、配置和锁文件，不是对任意消费者目录的通用检查或发布 API | [常用操作](../../guides/common-tasks.md) |
| `workflow_actions`、Release prepare/verify/publish 助手与注册信息 | 内部 Actions 接口；由已发布 Workflow 调用，不是新的用户 CLI 或跨项目发布框架 | [CLI 边界](../architecture/cli-boundaries.md) |
| Dagger `fixture_*` 与根仓 `@check` / `repository-*` 报告函数 | 自测/示例或本仓库适配接口，不作为通用消费者稳定契约。消费者不依赖 fixture 名称或内部文件布局 | [本地验证](../architecture/local-checks.md) |

## 运行条件与结果

Python 包要求普通 CPython >=3.14；Dagger 消费者使用固定 Engine/CLI 1.0.0-beta.16，
单版本与 Matrix 支持范围以[Matrix 契约](../architecture/testing/python-matrix.md)为准。
消费者自身 Python 版本由其项目声明，不能把宿主包的 Python 要求误认为所有容器都必须 3.14。
基础 Python 包只安装其清单声明的 CI 依赖，不依赖 Harness、日志归档或工具运行时组件。
Dagger SDK 使用独立模块环境；消费者私有源码的读取权限由调用方配置。

每个结果仍以其原始执行状态为准。报告、ZIP 下载或 Artifact 上传成功，不表示检查成功。
未执行、跳过、取消、不可用和 incomplete 不能用于代替必需的通过结果。
下载失败 Run 的证据仍保留远端失败；CLI / Dagger 的非零、异常与结构化状态详见各契约。
只有调用者显式配置的测试范围和 Coverage 阈值生效，不自动替消费者删测试或降低门槛。

## 版本与演进

当前采用 `vMAJOR.MINOR.PATCH` Tag，对应四处项目元数据和不可变提交。
可使用经过验证的固定 SHA 验收候选，但该 SHA 不是已发布版本；正式升级必须核对 Release Manifest。
同时更新消费者的 Workflow 引用、Dagger 模块引用和原生锁文件，审查生成的差异后运行消费者 CI。
Renovate 更新仍由现有配置负责，不增加跨仓库编排。

公共 pytest 报告字段、严格门禁和消费者默认参数保留；
单版本 Dagger 增加可选 `git_token`，不改变未提供该参数的调用。
Matrix 当前没有这个新增参数，不能暗示已经支持同样的私有依赖安装路径。
Git 安全策略是显式启用的已知 Commit 漏洞查询，不是私有源码审计。
固定版本及 Dagger beta 本身的已知限制见[配置](../configuration/dagger.md)。

v0.7.0 删除旧 execution Python API、下载 Backend 和本地 Git/PR 自动交付接口，不提供兼容层。
一般下载使用原生 gh；跨 Run 的版本、Tag、SHA、attempt 与 Manifest 证据核验继续由发布层负责。
本轮候选身份、实际消费验证与 C1–C8 结论保存在 PR 交付记录，缺少证据的路径明确记为 NOT VERIFIED。
