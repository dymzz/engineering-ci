# 审阅裁决与修订边界

完整结构化记录以 [findings.json](findings.json) 为准；本页是其解释与顺序。所有源码引用由固定 commit 解析，原始 API 输入/输出可按 [cases.json](cases.json) 的 case ID 在压缩 JSON 中定位。

## D-F01：Terminal/Phase 证明清除了独立的 Domain 缺口

**P1，#124 候选合同缺陷。** `terminal.compound_open_domain` 由实际 `selection.plan()` 生成 Expected，重新关联原 Plan/Git/Root、Profile 摘要、Completed Run Subject 和 Phase Witness；没有手工修改输出中的 `status/reason`。当 Profile 的 `domain_complete=False` 时，Producer 返回 `unknown / applicability_or_changeset_incomplete`、`definitions_status=requested_names_complete`、`phase_status=unverified`。Binder 最终返回 `bound / authorized=True`，`reasons=[]`。原测试只声明部分配置域的构造也能得到同一结果。

合法对照 `terminal.compound_closed_domain` 将声明域补全到实际配置的 Hygiene 名称，仍有单独的 Terminal 未确定条件；正确匹配的 Terminal/Phase 事实可以使其 `authorized=True`。因此修复必须保留这条可达路径。`producer.phase_only` 与 `producer.phase_and_open_domain` 进一步显示：仅 Phase 未验证和 Phase+Domain 未闭合都会得到相同标量 `partial / effective_profile_phase_unverified`，但 definitions/applicability 原始事实不同。

根因位于两个相连职责：

- [B2 `_derive_v2_from_profile`](https://github.com/dymzz/engineering-ci/blob/adeeb4b15e47072c5091a31c048253f60471ff1b/src/engineering_ci/expected_checks.py#L1770) 通过优先级选择单个 `status/reason`。本例中完整域事实没有消失，仍在 Profile/definitions/applicability 中；这些并存条件没有成为可逐项解除的明确合同。
- [候选 `_terminal_applicability_is_sole_unknown`](https://github.com/dymzz/engineering-ci/blob/4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5/src/engineering_ci/terminal_expected.py#L261) 及 `_partial_is_phase_only` 核对 authority、conflicts、missing 和其他 Check，却没有保留独立的 Domain 不完整条件；Binder 因而把复合缺口视作 Terminal/Phase 自身可以解决的缺口。这违反该模块“只清除 sole Terminal / Phase-only 条件，其他不完整仍阻断”的合同。

`_derive_v2_from_profile` 所在 Producer、公开 `derive_expected_checks` 及 `validate_projection` 在两分区使用相同源码；D 的独立函数 AST 摘要也一致。**已复现的错误允许性出口限于候选 Binder。** main 的其他 Expected 消费者需要按其证明范围同步演进，不能据相同 Producer 字节断言它们已错误放行。

归属 **#66-B2 + #52-D4/#124**。I01 提议在现有 Producer/Validator 明确多项条件及证明范围，Binder 只解除确实得到证明的条件。同一个不可分割合同可以在一个原子 PR 同步修订。不得让 #52/#63/#61 各写一套治理域猜测，也不得以全部拒绝 `unknown/partial` 获得假收敛。当前匹配回调是受控替身；本反例不证明真实生产 Issuer 已遭绕过。

## D-F02：实际 Admission 与 R1 的归一化合同没有接通

**P1，已确认的跨模块合同缺口；当前表现为拒绝。** 三个集成探针共享同一个 v2 Expected/Universe：

| 探针 | 实际 Producer 状态 | 原样交给实际 R1 的原生结果 |
| --- | --- | --- |
| `interop.t1_pass_to_r1` | T1-a `admitted`、Origin verified、当前 Receipt 匹配 | `RequiredAssessmentError: admission_phase_invalid` |
| `interop.t1_fail_to_r1` | 认证 Native FAIL、Origin verified、Obligation unsatisfied、admitted false | `RequiredAssessmentError: admission_phase_invalid` |
| `interop.t2_pass_to_r1` | T2 `admitted`、历史来源及当前等价性经受控验证 | `RequiredAssessmentError: admission_kind_invalid` |

[T1-a 返回对象](https://github.com/dymzz/engineering-ci/blob/adeeb4b15e47072c5091a31c048253f60471ff1b/src/engineering_ci/admission.py#L923) 使用 `engineering-ci-evidence-admission` v1，没有 R1 要求的 `phase` 和 `admission_ref`；T2 使用独立 Historical Reuse kind。R1 的 [公开输入合同](../../../architecture/required-assessment.md#assessment-shape) 与 [`_validate_admission`](https://github.com/dymzz/engineering-ci/blob/adeeb4b15e47072c5091a31c048253f60471ff1b/src/engineering_ci/required_assessment.py#L1341) 要求冻结的 `post_run_evidence_to_gate_projection`，且 Coverage 成功 Witness、Origin/Issuer/适用范围、Failure 映射的字段也不同。改两个常量不能完成交接。

这并不否认各组件本来成立的行为：`t1.fail` 正确保留认证失败，`t2.fail` 不产生成功覆盖；R1 的预归一化合法单域、空域、历史 PASS 与当前失败冲突等合同可独立工作。T1 的 `accepted_coverage_by_instance` 非空也不等于成功，必须检查具体行的 `admitted/obligation_status`。本轮没有观察到 Native FAIL 被改成 PASS；断点发生在合法完整的 Gate 投影尚未交付。

归属 **#63 Gate 投影 + #61 R1**。I02 要求实际 Producer 输出经显式且有来源的投影进入 R1，同时保留失败/冲突、真实历史身份、范围与时效。合法 T2 复用不应被迫生成当前 Receipt 或再次 Native 执行。不得在测试中手工添加 kind/phase、`verified=True` 或 Witness 字段后宣称接线通过。

## 机械边界与尚未签发的结论

独立解析 main 的 **70 个 Python 文件、1392 个函数/方法/模块入口符号**及候选的 **66 个文件、1377 个符号**，包含 `src`、`.dagger/src`、`scripts`，并检查两个版本各 4 个 Workflow。模块级 CLI shim 和 `__main__` 也计入入口；符号数包含为模块级语句生成的 `__module_entry__` 记录。

| 分区 | 调用表达式 | 语法可解析调用 | 参数/回调接收者调用 | 扩展出口候选 |
| --- | --- | --- | --- | --- |
| main | 18720 | 2941 | 2293 | 24 |
| #124 | 18701 | 2942 | 2333 | 24 |

24 是 seeds 加字面字段扩展的审阅候选数，不是全部业务允许性出口的数量。`conclusion` 还出现在 `cicd_telemetry.produce`、`release.verification.validation_state` 和 `tools.performance.summarize`；它们记录远端/工具结论，没有由此获得 Required/Release 签发职责。Check 构造、Missing Assessment failure 构造、实际 Check POST、实际 Release 写入及受保护工具 Policy 决策分别保留。

独立结果之后才与 A/B 对账：[93 个节点、86 条边](mechanical-reconciliation.json)的内部符号全部定位，24 条原有动态/外部边原样保留。函数值传递、返回闭包、参数 Provider、Receiver 类型、环境入口、GitHub 服务端与跨仓调用未被静态解析。关键公开合同本轮仅找到自身/包装器或模块入口的调用，不能因此宣称外部消费者不存在。`contract_input` 边也不能由函数名存在或调用语法代替真实数据交接；D-F02 就是这样的反例。

extra execution/Policy 路径已在独立 AST 中定位到嵌套闭包 `execution._build_workflow_admission_fn.admit -> security_policy.decide`。这部分只完成机械与源代码职责核对，真实 anchor/Release/API/持久状态环境及 adversarial execution 尚未验收，显式计入 D-G05。真实 Release 写入和服务端执行同样没有实测；详见 [来源账](authority-sources.md)。本次 82 个案例不是全部输入空间或生产路径的穷尽证明。

## 修订顺序

1. **原子 PR 一：I01。** #66-B2 多原因/证明范围与 #124 Binder 同步修复，先解决共享根因；以真实 Producer 输出验证复合缺口、合法闭域和 Phase-only 正例。
2. **原子 PR 二：I02。** #63 Gate 投影与 R1 消费合同一起修订，加入真实 T1/T2 输出交接以及认证失败冲突传播；与 I01 的身份/范围使用同一集成基线。
3. **生产接线按原 Issue 分批验收。** D-G01～D-G05 明确需要治理、Issuer、执行、Publisher/服务端、Release/Post 等哪些实际事实。未提供的来源保持 `unverified`，不新增第二套授权系统、不重新实现既有 P0/Native/Receipt，也不因邻接缺口作废原有效证据。

本 PR 交付 D 审阅及该顺序，不实施上述运行时修订，不替任何子 Issue 签发生产验收。
