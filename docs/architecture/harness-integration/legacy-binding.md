# 早期 PR 检查绑定兼容契约

本页保留仍由 `mining/bindings.py` 和 checks --binding 读取的历史兼容桥。
它不是通用 Workflow 格式，也不证明历史冗余或优化收益。

## 首个 Handler 固化：基础 PR 检查复用（v0.6.1）

本节保留 PR #25 早期证据绑定原型的历史定义及兼容行为。
当前独立生成、核心与工具解耦及配置执行以[独立生成与编排](../workflow-optimization/generated-workflows.md)为准。

用户选定“基础 PR 检查的同输入复用”，绑定现有 `pr` Profile 中的五个注册操作。
首个来源候选是已登记分析中连续两次 `command_execution` 的调查项。
该投影没有命令、参数、检查输入或环境身份，不能证明历史上重复执行了 pytest，
也不能证明某一步可删除。`mapping_basis=explicit_target_selection` 表示用户选择的新执行目标；
`historical_check_identity=unavailable` 和 `optimization_verified=false` 继续保留。

`mine bind` 从文件加实际原投影或登记分析 ID 加父关联读取已有证据，
复用候选的成员、统计、模型核验；不读取原始会话、不调用发现算法或模型，不写数据库。
必须显式给出候选 ID 和 `--profile pr`；仅接受连续 command 活动候选。
文件输出拒绝覆盖，写入失败返回错误和新输出路径的继续命令。

早期原型产物是小型 JSON 绑定，不是新工作流定义：

| 字段 | 契约 |
| --- | --- |
| `schema_version` / `binding_version` | `1` / `registered-pr-check-reuse/1`，与归档 schema 和项目版本分开 |
| `source` | 候选报告/候选/分析/投影/快照 ID，候选和派生文档摘要、来源完整性 |
| `target` | 固定 `checks.pr`、当前完整 Profile 顺序、归一化已校验 Pipeline 的 SHA-256、`matching_execution_identity` |
| `mapping_basis` / `historical_check_identity` | 固定显式目标选择 / 历史检查身份不可用 |
| `optimization_verified` / `llm_used` | 均为 false；不新增历史质量或收益结论 |
| `binding_id` | 除自身外全部字段的 canonical JSON（含末尾换行）SHA-256 |

候选再生成使用 limit=100、默认见证上限，报告 ID 反映这些参数；候选 ID 不受输出上限影响。
未知字段、任意命令、导入路径、部分 Profile、其他 Handler、改写复用策略或伪造优化批准均拒绝。
绑定读取限制 64 KiB；内容摘要检测修改，不是签名、身份认证或发布授权。
执行时离线核验绑定和当前 Pipeline，不重新查询数据库。
配置变化使绑定过期，需要用同一实际候选重新生成并审查；代码/工具/参数变化按原检查身份决定执行。

`checks --binding FILE --reuse --json REPORT` 只解析为已有 `pr` 选择，
仍调用原 native/Dagger 执行器，不增加检查注册表、重试循环、调度器或 FSM。
JUnit、Coverage、executor 参数继续沿用原 CLI；它们参与原执行身份，不能被绑定忽略。
结果不得覆盖绑定，JSON 增加 `handler_binding` 来源关联；实际状态仍取逐项原退出码与当前输入有效性。
Dagger 原始执行、环境、JUnit 和文件摘要的读回保持有效，GitHub CI 和 Release 不读取绑定作为放行依据。

首次调用正常执行或使用已经匹配的证据；再次调用只复用匹配项，保留 failed/incomplete 状态。
修复或相关输入变化后重新执行受影响项，执行中变化仍为 stale；不自动删测试。
如最终来源关联保存失败，保留已经生成的检查报告及非零工具退出状态，成功检查返回 3 表示关联未保存。
进程在最终关联写入前停止时，报告可能只有检查事实；再次用同一绑定和报告继续，先核验事实再补关联。
以上历史原型只证明受控 PR 检查复用路径；当前新增配置生成与执行的验收另行记录，
两者均不声称已验证历史冗余或全自动优化循环。
