# 授权依赖审阅：A/B 快照

本记录交付 [Issue #125](https://github.com/dymzz/engineering-ci/issues/125) 的 **A：版本化快照**与 **B：出口枚举、双向依赖追踪**。审阅对象是 main `4bb346c57f99e4767eea78082639f0b367745140`，另设 #124 候选 `4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5` 分区。后续提交不自动继承本快照的观察。

- [基线与服务端观察](baseline.md)：Issue 段落、合并/在途 PR、CI 与已有 Native 证据的身份和适用范围。
- [授权出口与双向关系](dependencies.md)：实际入口、Producer、Validator、消费者、身份/时间/失败边界，以及待观察的外部边。
- [源码引用](source-index.md)：依赖图中函数的固定 commit、行号及静态调用者。
- [发现清单](discovery-inventory.json)：两个版本分别搜索的文件、词法候选、人工扩展入口、测试/Workflow 引用。
- [候选入口逐项去向](candidate-disposition.json)：两版本各 127 项均逐项记录同分区图节点、静态调用链、相同源码的跨版本复用、明确排除或 `unresolved`；[独立一致性检查](evidence/check-candidate-dispositions.txt)核查候选集合、固定源码、调用链和分区引用。这是 **A/B 发现覆盖记账**，不是 C/D 的语义授权结论。
- [双向依赖图](dependency-map.json)：Producer/fact → Validator/Consumer 的审阅数据，含每个节点的入边和出边。边类型区分直接调用、人工派发、数据合同和 `unresolved`。
- [基线元数据](baseline-snapshot.json)与[保留证据](evidence/README.md)：可解压的原始归一化响应、只读 GitHub 观察、机械 AST 结果及一次性推导脚本。
- [C 阶段 Coverage/Gap 索引](coverage/README.md)：基于本 A/B 固定快照的逐边证据范围与未调查关系；与本页原 A/B 结算分别记录。
- [D 阶段独立审阅](independent-review/README.md)：固定后续 main/候选版本，独立核对机械关系、语义反例和真实来源；去重缺陷与公共接口修订清单先于运行时修复。

## B 阶段候选覆盖的实际边界

本轮 254 个候选均已有可核验的 disposition；main：55 项图节点、45 项以候选静态调用链覆盖、3 项明确非授权出口、**24 项 unresolved**；PR #124 分区：8 项图节点、4 项静态调用链、65 项基于相同源码字节的实现复用、3 项非授权出口、**47 项 unresolved**。跨版本源码相同**不证明**运行时消费者/外部调用路径相同，65 项仍需在 C/D 按受影响链路核查消费者；以上数字不能用作业务授权覆盖率。未解析项是清单中明确保留的发现欠账，不能因为存在 `route` 分类就视为图已闭合。图既有 24 条 `unresolved` 边与候选未解析项是两种不同粒度，不互相替代。

本 PR 的交付边界为 A/B。C 的 Coverage/Gap 裁决、D 的独立语义/对抗验收、公共接口修订及生产授权验收仍按 #125 后续阶段推进。既有证据维持原适用范围，Issue 完成项、分支保护和发布权限不由这份审阅图改变。

上级入口：[验证记录](../README.md)。
