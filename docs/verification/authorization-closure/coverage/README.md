# C：派生 Coverage/Gap 索引

本目录交付 [Issue #125 §2-C](https://github.com/dymzz/engineering-ci/issues/125) 的证据范围索引，基于已合并 [PR #126](https://github.com/dymzz/engineering-ci/pull/126) 的审阅修订 HEAD `363bb19d381146890b57341f686b03f311fd0d0a`。本轮分支基线为 main `990cd59f83153a1ad3978dc933f89af5ca4829e3`；运行时源码/测试/Workflow/依赖相对 A/B 的 main `4bb346c` 未变，#124 候选仍为 `4a41b4e`。原服务端观察保留原时间，两个源码快照分别解释。

- [证据范围与逐边结论](assessment.md)：五类覆盖状态、86 条边的简表、已知缺口及未调查范围。
- [逐边机器索引](index.json)：每条边的允许性出口、不变量、固定调用/数据路径、Producer/authority、Validator/trust boundary、Consumer、类型身份/Phase/时间、失败行为、直接证明对象/层次/断言与上下文、状态及原 Issue/PR。
- [候选关系](candidate-relations.json)：逐项承接 A/B 254 项 disposition，同分区 helper 调用链、相同实现引用和未知消费者分别记录。
- [推导输入与核验证据](evidence/README.md)：当前 Issue/PR 观察、一次性推导/校核原文、原生结果及摘要清单。

本索引按既有实现与证据派生，仅描述该固定范围内的证据层次。C 没有执行 D 的独立语义/对抗/来源三视角验收，也不改变运行时接口、Policy、Expected、Required 或发布决策。

按 [PR #127 review](https://github.com/dymzz/engineering-ci/pull/127#pullrequestreview-5943207575) 修订后，组件合同、交接合同、来源认证、生产接线分别记录；组件测试或 CI 的存在不能单独支持某边合同已验证。逐边范围表链接到确切断言和固定调用/数据路径。

上级入口：[A/B 审阅快照](../README.md)。
