# D：独立语义、对抗和权威来源审阅

本轮交付 [Issue #125 §2-D](https://github.com/dymzz/engineering-ci/issues/125) 的三视角审阅及**先汇总、后修订**的裁决输入。确认 **1 个语义缺陷、1 个跨模块合同缺口**，另保留 **5 组生产来源/接线欠账**。本 PR 只增加审阅证据与接口修订建议；运行时代码没有修复，#125 的整体完成条件尚未满足。

| 分区 | 固定版本 | 本轮边界 |
| --- | --- | --- |
| main | `adeeb4b15e47072c5091a31c048253f60471ff1b` | 包含已合并 #126 A/B 和 #127 C；运行时/测试/Workflow/配置/依赖相对 A/B 原 main `4bb346c` 没有变化 |
| #124 候选 | `4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5` | 独立 Draft；没有把候选 Binder 当作 main 已交付能力 |

2026-11-18 **04:32:33 UTC** 再读 #125/#52/#66/#63/#61，现行正文更新时间均与本轮启动读取相同；#124 HEAD 未变，#127 已合并，原修订 HEAD 为 `6ca9e0a`。各 API 响应的观察时间独立保留，不以汇编时间冒充所有事实同时发生。源数据见 [review-context](evidence/review-context.json.gz) 和 [原生源码比较](evidence/runtime-source-comparison.json.gz)。

- [统一缺陷与接口清单](findings.json)：唯一的去重清单，含原生诊断、根因、受影响消费者、归属、接口提案与正反验收条件。
- [审阅裁决](assessment.md)：解释两个根因、证据边界及后续原子 PR 顺序。
- [权威来源与真实运行观察](authority-sources.md)：治理/Issuer/Runner/Artifact/服务端事实分别结算。
- [机械对账](mechanical-reconciliation.json)：独立 AST 结果与 A/B 93 个节点、86 条边逐项关联；原动态外部欠账继续可见。
- [定向案例索引](cases.json)：82 个真实 API 探针，77 个符合合同预期，5 个反例归并为两个根因，0 个未解决的探针构造错误。成功正例只证明受控合同可达。
- [证据与重放说明](evidence/README.md)：固定源码文件摘要、完整输入/返回值/原生异常、独立脚本、GET 观察和校验方法。

三个视角分别从固定源码 AST、实际 API 输入/输出、真实只读服务端响应与原始 Producer 代码出发；C 的分类和旧测试断言不作为 D 的预期结果。探针复用既有测试 helper 构造输入，独立定义判定；#124 构造在旧测试的第一次 Binder 调用前截获输入，再调用实际 Binder。所有 Fixture、Native Result 模型和回调的范围写进原始结果。这里没有真人或外部 Issuer 的独立审批，也没有生产全链 PASS。

原 [A/B](../README.md) 与 [C](../coverage/README.md) 的观察范围继续保留。D 给相关边增加更强的反例/接线观察，不把组件合同、格式一致或成功 CI 升级为来源认证。既有 [Native 工具](../../native-tool-contract/README.md)、[Weaver](../../weaver.md)、[属性映射](../../cicd-telemetry-attribute-mapping/README.md)、[原生 OTel](../../dagger-native-telemetry.md) 证据仍只适用于其原始输入/实现/运行。

本轮没有调用 agent hooks、安装工具、执行 Dagger/Weaver/OTLP 或整个工程检查矩阵，也没有发布 Check、修改分支保护、发起 Release、Disposition 或合并/关闭操作。
