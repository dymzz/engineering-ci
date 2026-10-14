# CI 能力批准、选择与反馈

本页维护首个完整 PR 检查能力的领域准入和反馈契约。
通用 CapabilityStore 实现在 Vesryn，严格批准/反馈 Schema 及语义校验在
`src/engineering_ci/workflow_adapters/contracts.py` 与宿主校验模块。
不能把本页的 CI 条件推广成 Vesryn 对所有领域的默认批准规则。

## 能力批准、版本选择与执行反馈

v0.6.4 的 P2 在现有 CLI/Core/Adapter 上接入首个完整 PR 检查能力。P1 比较文件保持
只读、approved=false；独立批准文件关联它，不改写原结论。操作步骤见
[生成工作流指南](../../../guides/generated-workflows.md#批准启用选择与调用能力)。

### 准入与存储

`workflow approve COMPARISON BASELINE_DIR CANDIDATE_DIR --name NAME --version VERSION`
是显式批准动作，不执行检查、不启用能力。Adapter 锁定原执行，重新计算完整比较并要求
文件一致、原门禁 passed、optimization_verified=true，且定义具有显式候选来源。
historical_check_identity=unavailable 仍保留，不倒填原 Codex Trace 的操作身份。

能力版本如 1.0.0，与项目发行版本 0.6.4 独立。批准绑定精确 definition/generation、原
工具契约、Workflow/Provenance 摘要、比较规则及结果摘要、两侧完整证据快照。Schema
由 `workflow schemas` 导出 capability_approval、capability_catalog、capability_call、
capability_feedback，复用原 JSON Schema 校验。每个版本独立保存 workflow/provenance/
comparison/approval 文件，不汇总进 pipeline.toml，不增加 console-script 或 Tool Catalog。

默认 store 是主 checkout 的 `.test-tools/workflow-capabilities`，可显式 `--store`。它是
机器本地批准记录，Git 忽略，不是签名、账户认证或 GitHub 发布门禁。操作者须保留 store
及原证据；换输入后不能仅凭同版本号声称准入成功。本地批准表示执行了显式命令，
不认证“是谁审查”，Agent 调用仍服从宿主授权。

单一 catalog.json 记录批准索引和 active 指针；文件锁串行化更新与调用准入，原子替换
避免半写入。部分版本文件写入失败不会进入索引，同命令可补齐相同文件后恢复；同一
name/version 对应不同批准则失败，不覆盖历史。索引最多 256 个版本，达到上限明确拒绝。

### 选择与执行

批准与启用分开。activate 再核验定义、当前 Catalog/目标策略及原比较证据，才切换 active
指针。select 只查明确启用版本，没有自动最新规则。capabilities 从显式索引返回有界摘要、
过滤及游标，标明 admission/current input 尚未核验；不扫描目录猜测已批准状态。
选择变化使分页游标失效。换回旧版本须显式 activate，仍接受同样准入。

call 在 catalog 锁下选择并冻结批准版本，通过原 Runtime 创建新执行并保存 capability.json，
再交给原 FSM/Adapter。默认引用已批准候选执行的冻结结果；五个原工具分别核对当前源码、
工具/参数/环境及资源，变化项重跑，原失败保持非零。历史试验不能替代当前代码检查。
原试验入口 workflow run 保持独立，不依赖 P2 批准。

deactivate 只阻止后续选择，不取消已准入执行或删除回执。status/resume 继续使用原检查点、
同一 ExecutionContext 与冻结绑定；之后切换/停用能力也不替换恢复中的版本。停用不等于
撤销外部副作用；当前 Catalog 仍无 Git Commit、Dispatch、Merge 或 Release 写工具。

### 实际执行反馈与 PostgreSQL

call/resume 完成后从原记录提取安全反馈。写入失败单独为 unavailable，原工具退出码不变。
`workflow feedback EXECUTION_DIR` 可补写：同一状态/观测/资源及能力绑定产生同一
feedback_id，新的 attempt/结果生成新记录。不认证任务总体成功、当前输入仍有效或新的
优化收益，不复制日志、Prompt、参数原值、环境变量或 Token。

`feedback --archive` 才写入已有 VESRYN_DATABASE。migration 003 在组件专用 schema 新增
workflow_feedback；反馈不是 Codex 原始事件、projection 或 analysis，不冒用它们的身份。
SQL 事务按 feedback_id 幂等插入并读回校验摘要；冲突、权限不足或网络失败均非 registered，
保留本地反馈和原执行。重复归档不重复检查、不自动迁移。

schema 1/2 保持已有读取/回放语义；反馈要求显式 trace init 升级到 3，保留旧原字节和派生
记录。tracing.toml 格式仍是 schema_version=1。自动调优、跨版本试验调度、任意任务批准、
签名身份认证和远端能力分发尚未实现；原 PR/Release 人工授权与门禁保持独立。

P3-1 的 `workflow feedback-query` 单独只读已归档安全事实，不依赖本地批准目录/执行
文件，不改变上述保存/归档或执行动作；分页、过滤与预算由
[反馈检索契约](feedback.md#已归档能力反馈的只读检索p3-1)定义。

### 反馈文档 v2 与 Attempt 索引

P3-2 的新反馈文档 schema_version=2；它与数据库 schema 3、tracing.toml 版本及项目版本
独立。原 schema_version=1 文档继续校验、查询和归档；不重写历史或用累计数补造索引。
同一执行重新提取 v2 会生成新的内容身份，原 v1 文件/归档仍保留，不重复执行检查。

v2 必须携带 attempt_snapshot，协议为 workflow-feedback-attempts/1。它从已有稳定执行
视图提取 required_step_ids 及安全 Attempt 索引：step_id/attempt、工具/契约、session/batch
身份、原输入/结果与源证据行摘要、观测边界、原检查证据可用性、提交/复用标记及退出结果。
不携带路径、命令、参数原值、日志、Prompt、环境变量或凭据。来源行摘要是已记录引用，
不是签名或远端身份认证；只读汇总不重新打开原执行文件。

核心校验拒绝重复 Step/Attempt、未声明的 Step、complete 索引的编号缺口、缺边界、
不可用检查伪造测量值及累计 metrics 与索引不一致。原工具退出码不因索引保存失败而改变。
观测 incomplete/unknown、测量未知和原失败保持可见；不知道的值不能补成零。
内容受原 2 MiB 文档限制，超限返回反馈保存失败，不截断索引后冒充完整。

索引仅证明所记录的 Attempt 身份及事实。覆盖与汇总见
[P3-2 检索契约](feedback.md#已归档反馈的覆盖与汇总p3-2)，不以计数增长或归档时间推断
执行先后、原成功或现有能力可用性。旧客户端只支持 v1 时不能读取 v2；数据库不需迁移。
