# CI 工具适配、证据与受控比较

本页维护 engineering-ci 的 CI 领域适配。通用 Runtime、归档和分析由 Vesryn 维护。
当前 `ChecksAdapter` 仍展开五项检查并共享就绪批次，容器内由 checks.py 执行。
后续将检查执行图交给原生 Dagger Checks；本次只迁移文档，不改变执行行为。
现有 Tool ID、生成定义、结果和失败/恢复语义继续有效。

## 现有系统与新能力的关系

已有[CLI 边界](../cli-boundaries.md)、[本地检查](../local-checks.md)、
[执行恢复](../execution/state-and-recovery.md)和[发布机制](../release/workflow.md)继续有效。

- FSM、Pipeline 与 `ExecutionContext` 管理实际执行及证据准入。
- `OperationResult` 是操作结果视图，不是历史事件存储。不能把它的最终快照当成完整轨迹。
- 轨迹引用既有检查身份、输入摘要、Commit、PR、Run 与 Attempt；不重新维护另一套执行事实。
- OTel 的上下文传播不等于业务 `ExecutionContext`，两者通过既有操作身份关联。
- 轨迹不参与 GitHub 发布资格判断，也不把历史成功结果直接变成本轮检查通过。
- PostgreSQL 存放观察数据，不替代 JSON StateStore，不用事件回放重建或推进业务 FSM。
- GitHub Actions 继续管理远端 Job DAG、权限和 Artifact；Dagger 继续管理容器内执行及其 DAG。

轨迹分析可以指出重复测试、失败恢复或等待成本，但不能自动省略测试、绕过合并授权，
或把 Dispatch UNKNOWN 当成可以重试的证据。

## CI 工具目录

当前已注册检查和报告查询；后续有实际需求时才注册其他工具：

| 工具 ID | 责任 |
| --- | --- |
| python.ruff-lint / python.ruff-format | 已注册：单项静态检查 |
| python.pytest | 已注册：原 pytest 执行及原结构化结果 |
| engineering-ci.version-check / uv.lock-check | 已注册：版本及锁文件完整性 |
| engineering-ci.check-inspect | 已注册：读取实际报告并核验当前输入 |
| git.commit / git.push | 未注册：未来原生 Git 的单项有副作用操作 |
| github.workflow-dispatch / github.run-get | 未注册：请求与查询分开 |
| github.run-download | 未注册：原日志/Artifact 收集 |

pr-checks 等便利入口保留为组合工作流或明确标注的过渡组合工具。
组合仍应暴露子检查真实结果与身份。拆粒度不意味着重复初始化容器或重复执行 pytest。
当前只有实际接入的工具才实现适配器，Git/OCI/MCP 等例子不要求本轮创建空实现。

## PR 目标策略绑定

来源明确选择完整 PR 目标时，provenance.target 记录 registered-profile、Profile 名称、
原 Pipeline 策略摘要及实际 step/tool 映射。来源身份不表示历史工具身份已知。
宿主在 validate/run/resume 核验该策略；配置变化明确拒绝并要求重新生成、审查。
缺少 target 的旧定义仍可读取、执行，但不能冒充已经绑定当前目标策略。
新实现继续读取旧 Schema 1 定义/检查点；旧读取器不认识新增字段时应拒绝，
升级读取器后再使用新产物，不能忽略 target 准入或冒充双向兼容。

## Dagger、上下文与恢复

[Dagger](https://docs.dagger.io/getting-started/introduction/) 提供容器及 DAG 执行能力；
其 [缓存](https://docs.dagger.io/reference/configuration/cache) 与工作流证据复用分别记录。
当前同参数的就绪检查共用原 Dagger 准备与 Artifact 请求。不同参数或依赖批次分别调用
原入口，复用原 Engine 缓存；跨批次长驻 SDK 会话尚未实现。后续会话扩展保持适配器边界。
工具数量不决定 Dagger 初始化次数；原 Git/HTTP/GitHub 实现仍调用成熟组件。
Runtime 不重新实现容器传输、分段下载或 GitHub 同一次 Workflow 的 Job 调度。

## CI 执行证据视图

workflow evidence 显式关联本执行的来源、生成身份、逐次调用和原工具回执/资源摘要；
查询与原执行使用同一文件锁，运行中可用 status 查看检查点；不拼接变化中的回执。
原 check.executed 是历史执行事实，actual_check_executed 来自原 executed_this_invocation，
不凭外层 invoke 推测执行了 pytest。复用来源与实际输入/工具/参数/环境摘要分别核对；
同一次执行的前序 attempt 与 reuse-from 的冻结检查点均保留明确引用。
查询只读，current_input_validity=not_checked；原通过记录不因此获得当前代码的放行资格。
原资源损坏时报告 unavailable，保留当时的原结果。未批准与未证明收益始终分别为 false。

check.details 从经摘要核验的原报告读取：允许公开的 coverage/JUnit 相对路径、实际工具版本、
平台/架构/后端和测试统计。不执行版本探测或工具，也不导出命令、绝对安装路径、环境变量或日志。
未提供的事实保持 null/缺失；配置声明的版本不能替代实际版本。身份仍由原检查模块判定，
查询书签同时绑定原回执事实摘要，资源变化使旧书签失效。

## 完整 PR 检查的受控复用比较

workflow compare 是只读的执行证据比较，不调用 Runtime.resume、Dagger、Mining 或数据库。
输入为两个明确执行目录，同时按稳定顺序持有原执行锁，核验完整观测、回执和资源。
门禁字段来自同一次原报告读取，只用于比较视图，不改变原查询证据身份。

首个目标限定为完整已注册 PR Profile：原必需 Step 不能省略、改为 optional 或更换成功条件。
两侧语义定义、候选来源、逐项工具契约、解析输入以及原检查的完整执行身份必须一致；
输入、参数、工具、环境和 Executor 摘要分别列出差异。固定挖掘结果等价不替代这些检查。
目标策略过期、执行未结束、观测不完整、丢失回执、缺身份或多次 attempt 均保存 not_comparable。
历史和匹配的单项结果仍保留，不删除已验证证据或把一次不匹配扩大为所有数据无效。

基线必须有一次未复用的原检查提交；候选使用既有 run --reuse-from 准入。
候选复用必须指向这份基线的冻结检查点、确切 step/attempt/result 摘要及同一资源，
其他执行的通过记录不能冒充本次控制样本。同一执行不能同时当作独立基线和候选。
重复 resume 的全历史保留并显示计数，当前不自动选择有利的一次 attempt；需要独立样本时
由调用者使用已有 run 新建执行，UNKNOWN 写操作仍遵守原恢复规则。

统计区分原 required_checks、observed_check_attempts、原报告标记的 check_submissions、
reused_checks 和 Runtime adapter_batches。提交数依据 executed_this_invocation，
不是操作系统进程数或 Dagger 缓存未命中数。两侧不禁用或清空原缓存，不改变既有执行策略；
cache_policy=backend-managed，命中状态未观测。因此不作速度、CPU、费用或整体 Agent 效果归因。

原报告、原 ToolResult 和最终执行必须都是完整通过，才满足 recorded_quality_gate=passed。
不同测试统计拒绝等价；失败、未执行、缺必需结果或输入执行时变化都不能由比较报告放行。
可比且质量通过、提交确实减少时，optimization_verified=true 仅限定为
verified_scope=check-submission-reuse。无减少仍可比较，但不宣称收益；失败复用保留原非零结果。

workflow-comparison 为独立本地报告；ID 绑定两份精确证据与比较版本、目标和范围。
缺乏可比性或质量不满足时仍保存报告，命令退出 3；已比较且原质量通过时退出 0，
退出 0 不意味着 optimization_verified 一定为 true。--out 必须新文件；--json 是另存的操作回执，
不能覆盖报告或原执行目录。保存失败单独非零并返回源执行和查询动作，不改原 FSM/门禁。
报告始终 current_input_validity=not_checked、approved=false，不作为当前代码或 Release 准入，
也不修改生成定义内的优化/批准状态。本轮不建立试验调度或批准存储。
