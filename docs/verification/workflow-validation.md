# 独立工作流验收（v0.6.1）

本记录对应 PR #25 的独立生成/执行实现，区别于早期 `registered-pr-check-reuse/1` 绑定原型。
验证在主 checkout 完成，没有替代工作区、数据库迁移、Workflow Dispatch、合并或正式发布。
操作说明见[生成工作流](../../guides/generated-workflows.md)，契约见
[架构](../architecture/workflow-optimization/generated-workflows.md)。

## 替身及本地核心测试

新增核心与适配器测试共 48 项通过，覆盖独立生成、规范 Schema、实际参数/依赖、JSON Pointer、
类型不匹配、缺失工具/输出、未知能力、环、资源损坏、报告保存失败、旧 CLI 兼容、状态及复用。
隔离测试把核心作为单独 Python 包导入，不加载 engineering-ci、Dagger 或 PostgreSQL。
替换同一契约的适配器不改核心。真实子进程 `os._exit(91)` 验证落盘意图和文件锁释放；
读/检查恢复，未知写入不重做。已知写入拒绝保留原失败，在缺少领域验证时暂停。
共享上下文测试核对 FSM 与调用对象相同，前置视图不能修改权威结果。

报告保存失败采用注入 OSError；原 pytest 退出 1 保留，其他报告不可用不掩盖该失败。
这些替身结果不是 GitHub 发布验证，也不表示已经接入真实写工具。

## 真实 Dagger / Linux

使用仓库固定 Dagger 1.0.0-beta.16 及原 repository-check 执行器，完整检查实际运行结果：

| 验证 | 结果 |
| --- | --- |
| 完整 PR 工具工作流 | 五项通过；1141 tests + 105 subtests，JUnit 共 1246，failure/error/skipped 均为 0 |
| 第二个生成文件的新执行，显式 reuse-from | 五项均复用，未提交新的检查 Artifact 执行 |
| 临时真实 `assert 1 == 2` 测试 | pytest 和工作流均退出 1，原失败日志/JUnit 保存 |
| 同一负向执行 resume | 五项事实均复用，pytest 和工作流仍退出 1 |
| 移除自建临时测试，恢复原输入 | 五项原通过事实复用，不再提交 pytest |
| Ruff → report-inspect | 原报告资源实际传递；接收步骤 input_sha256 等于解析后资源输入摘要，原 freshness 检查通过 |

同参数就绪检查通过一个原执行器批次运行，不按工具数重复初始化 Dagger。
现有 JUnit、原容器报告、环境与文件摘要均通过 `dagger_checks.read_evidence` 读回。
不能识别 Dagger 内部缓存命中时不把它写成成本收益；“未提交新执行”指检查证据复用，
不是仅根据显示时间或工具调用完成推断的结论。

## 实际来源及独立产物

两次生成使用不同 generation_id，具有相同 definition_id：

- 人工展开完整 PR Profile。
- 已登记 Rust4PM 分析和实际投影关联核验后，用户显式选择同一 PR 目标。

登记分析 ID 为 `1a730b2a683b402ef06ce4eb1073505886ec4a4183413f94b81d5b217b6a66d0`，
候选 ID 为 `5de6ca846d52575adae5313249a8ad45f57e03fd34b630563b901de91c098cb4`。
仅查询既有派生记录，不读取原始实录、不重跑 mining、不写数据库。
来源完整性 unknown、历史检查身份 unavailable、optimization_verified=false 保持有效。

本地集中验收文件为 `.test-tools/workflow-v061/acceptance-review.json`，
汇总生成/来源关联、逐项真实回执、失败/复用以及实际数据输入摘要；
原运行文件各在 `.test-tools/workflow-runs/<execution-id>/`，测试日志与命令 JSON
在 `.test-tools/workflow-v061/`。本地文件不作为远端 CI 或发布门禁。

## 交付边界

继续在[现有 Draft PR #25](https://github.com/dymzz/engineering-ci/pull/25)交付。
GitHub PR CI 通过 Push 后的实际 Workflow 记录验收，正式 Release 仍须人发起，未在本轮运行。
原 CI Job、发布门禁、消费者调用及两个 scripts 入口保持原责任。

当前只注册检查和报告查询；没有 Git、Dispatch、Merge 或 Release 执行工具。
跨依赖批次仍调用原 Dagger 入口，长驻 SDK 会话、任意并发、嵌套子流程和动态循环未实现。
native pytest 在完整 JUnit 回执形成前强杀时不能安全复用，将只重新执行证据不完整项。
复杂 Schema 的静态引用推导限于明确的 object/array/type；运行值仍由正式 JSON Schema 校验。
这些边界不能被生成配置当作已经提供的能力。
