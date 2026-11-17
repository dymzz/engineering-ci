# 授权依赖审阅：A/B 快照

本记录交付 [Issue #125](https://github.com/dymzz/engineering-ci/issues/125) 的 **A：版本化快照**与 **B：出口枚举、双向依赖追踪**。审阅对象是 main `4bb346c57f99e4767eea78082639f0b367745140`，另设 #124 候选 `4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5` 分区。后续提交不自动继承本快照的观察。

- [基线与服务端观察](baseline.md)：Issue 段落、合并/在途 PR、CI 与已有 Native 证据的身份和适用范围。
- [授权出口与双向关系](dependencies.md)：实际入口、Producer、Validator、消费者、身份/时间/失败边界，以及待观察的外部边。
- [源码引用](source-index.md)：依赖图中函数的固定 commit、行号及静态调用者。
- [发现清单](discovery-inventory.json)：两个版本分别搜索的文件、词法候选、人工扩展入口、测试/Workflow 引用。
- [双向依赖图](dependency-map.json)：Producer/fact → Validator/Consumer 的审阅数据，含每个节点的入边和出边。边类型区分直接调用、人工派发、数据合同和 `unresolved`。
- [基线元数据](baseline-snapshot.json)与[保留证据](evidence/README.md)：可解压的原始归一化响应、只读 GitHub 观察、机械 AST 结果及一次性推导脚本。

本 PR 的交付边界为 A/B。C 的 Coverage/Gap 裁决、D 的独立语义/对抗验收、公共接口修订及生产授权验收仍按 #125 后续阶段推进。既有证据维持原适用范围，Issue 完成项、分支保护和发布权限不由这份审阅图改变。

上级入口：[验证记录](../README.md)。
