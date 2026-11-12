# Knowledge RDF 互操作交接（R2）

**状态：候选跨仓契约；非现有公共 API。** [总览](README.md)

**原则：** RDF 增强工程语义与证据关系，不调度 Dagger，也不成为普通 ECI 检查的强制运行依赖。ECI 核心不得依赖 `engineering_knowledge` 包、`ek:` 术语或 RDF4J；领域消费端通过可选只读 Adapter 将固定结果映射为 ECI 输入。来源资源按实际类型采用 DCAT/DCTERMS/PROV，不能把所有 Source 当 Dataset。

## H-01 Knowledge → ECI 固定只读输入
**拟议适配入口（非已实现 API）**：消费者侧 `resolve_knowledge_inputs(context, requirements, source_ref)`。以下均为按需交接，真实 RDF Check 才需使用：

| 字段族 | 生产权威 | 核验 |
|---|---|---|
| DatasetRevision + GraphRevision/选中图 | Knowledge #7/#8 | 完整固定图成员、默认图、imports/inference/projection |
| Profile/Query/Shapes Revision | Knowledge #10/查询原生产者 | 实际使用版本与图闭包 |
| Admission/Readback Receipt | Knowledge #8 | 授权提交及对应固定读取；Candidate 不冒充 Admitted |
| Knowledge/Property/Capability/Scope | Knowledge #10/#11/#17 | 被检性质、前提、准则、版本、覆盖和条件 |
| Project Adoption/Effective Policy | 项目/组织 | 对应 Context × Action × Target 的实际采纳与 Required，不由 Knowledge 查询生成 |

**拒绝**：策略要求该输入而修订/接纳/查询/范围核验失败，则阻断**依赖该输入的动作**；已经生效的 Required 不删除；其他不依赖 RDF 的检查继续正常执行。查询零行且覆盖未知不等于无要求。固定 Candidate 仅允许候选联调。

## H-02 ECI → Knowledge Native Result
保持原生执行事实权威，建议交接 `subject_ref × criterion_ref × configuration_ref × expected_observation`、原生命令/结果/退出/诊断、Native Result/Artifact、真实运行/受信来源、覆盖和版本。目标字段 `check_key/invocation_id/origin_execution_id/native_result_ref/assessment_id` **拟议且未完整落在当前 Plan/Report**，其生产者/拒绝行为见 [证据与身份](evidence-and-identity.md)。

EARL 评价必须区分：工程对象违规可为 `earl:failed`；检查器在负向 Fixture 上正确报告违规，其**检查器符合性测试**反而可以 `earl:passed`。两者可引用同一次原生执行，不可混合主体/准则。Blocked 是执行状态，不直接等价 EARL outcome；一般工程对象证据不自动进入 M3 工具发布版符合性矩阵。

按 [Knowledge #12](https://github.com/dymzz/engineering-knowledge/issues/12)：(A) 固定真实原生证据与候选关系；(B) 依 #10 Profile、#8 正式授权接纳、固定 Dataset 读回；(C) 真实跨仓消费。现有 M1–M4 离线查询和来源专用 `source-catalog-v3` 接纳不证明 B/C 已交付。每阶段必须有明确成功/拒绝回执。

## H-03 类型化影响与 Action Gate
复用 [Knowledge #18](https://github.com/dymzz/engineering-knowledge/blob/main/design/detailed/typed-impact-and-revalidation.md) 的**三项独立记录**：
1. 原生依赖断言：来源/方向/类型/对象修订/条件，允许 RDF 外先存在。
2. 实际使用 RDF 时的 Query Occurrence：DatasetRevision、图、Profile/Query Revision 与查询范围，不反推原生完整性。
3. 有界 Dependency Coverage Attestation：目标、修订、类型/方向、搜索范围、收集器/方法、遗漏和完整性证据。

零条边只有在**该类型/方向/对象修订/搜索范围**都被证明完整时才支持 `unaffected-in-scope`；否则 `unknown/needs-review`，不自动全量重跑也不默认为无需重验。Knowledge 影响查询不运行 Dagger；ECI 只对受影响的输入、Check 或 Policy 作定向复核。

按动作最终门禁在 [Provider/Action](provider-and-gates.md)；RDF Profile 通过 ≠ Native PASS ≠ ECI Trusted ≠ Required/Release。

**既有权威**：[Knowledge #7](https://github.com/dymzz/engineering-knowledge/blob/main/design/identity-contract.md) · [#10](https://github.com/dymzz/engineering-knowledge/blob/main/design/detailed/shared-rdf-application-contract.md) · [#12](https://github.com/dymzz/engineering-knowledge/issues/12) · [#17](https://github.com/dymzz/engineering-knowledge/blob/main/design/detailed/contextual-applicability-and-action.md) · [#18](https://github.com/dymzz/engineering-knowledge/blob/main/design/detailed/typed-impact-and-revalidation.md)