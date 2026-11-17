# C 阶段推导与核验

本目录保存 C 索引的派生输入和完整性核验，不认证新的生产权威。[SHA256SUMS](SHA256SUMS)覆盖 C 目录文件；A/B 的原始图、候选表、机械/服务端快照仍使用其原保存字节，原 A/B 清单只更新两个导航文档的摘要。

| 文件 | 责任 |
| --- | --- |
| [输入快照](inputs.json) | 当前 #125 正文、#126 审阅回执/合并 HEAD、#124 当前身份、源码无变化的定向 Git 原生观察；归一化 API 数据，没有发起生产写入 |
| [一次性推导原文](derive-index.txt) | 人工逐边证据分类、原合同/CI/源码引用、完整 AST helper 路径及候选关系的派生输入；仅生成本审阅文档 |
| [独立校核原文](check-index.txt) | 只读检查索引字段、86 条关键边、254 个候选、调用链、分区、代码/证据 SHA、CI 绑定与本地链接；不重新发现全仓、不执行原生检查 |
| [本轮核验结果](validation.json) | 上述完整性校核的实际原生结果；语义/对抗与实际来源验收仍未执行 |
| [字节清单](SHA256SUMS) | C 文件 SHA-256；自检只保证索引完整性，不产生 Allow/Deny |

## 证据类别与重放

`index.json` 的 `source_catalog` 将证据分为固定函数、合同测试 fixture、既有 CI suite、解码 GET、存量 Native fixture 和验证摘要。测试中的真实命名、mock True、摘要一致性、CI 通过均保留各自范围。原 Run `44068954853` / `44085810641` 的 core suite 成功与对应源码/测试 SHA 绑定；这里没有新的逐 case 执行或 D 的独立验收，未归档的逐 case 输出也没有补造成证据。

`contract_input` / 人工 dispatch / 外部 `unresolved` 边不能当成直接调用。两端各自有通过的合同测试，仍可能缺组合验证；本轮逐边将这些情况保留为“尚未调查”。记录为“已验证合同但真实权威未接通”的条目，也只在所列 API/fixture/原 suite 范围成立。

在持有固定 Git 对象的已授权同一 checkout，可直接运行：

```powershell
python docs/verification/authorization-closure/coverage/evidence/check-index.txt
```

校核脚本只需现有 Python 标准库和 Git，输出 JSON 与原生退出码，不修改文件。它验证当前 C 文件及原 A/B 输入引用；没有安装依赖或 CI 接线。复核图或证据变化时，先按 `inputs.json` 的固定身份判断受影响子图，再编辑一次性推导输入并核对相应记录。保留脚本是本轮证据附件，不是强制每次扫描或运行时授权工具。

固定输入下也可用 `python docs/verification/authorization-closure/coverage/evidence/derive-index.txt` 重算 `index.json`、候选关系和范围表；它直接读取本目录 `inputs.json`，不依赖未保存的临时文件。原输入未变时输出应逐字节相同；新增观察或编辑分类后应作为新修订核对并更新 C 摘要与核验记录。

本地只执行这批索引的校核与 Git diff 检查，未手动运行 Dagger/Weaver/OTLP 或完整工程矩阵。自动 PR CI 的结果在 PR 中单独报告，不回写原 A/B 的观察时间。

上级入口：[C 阶段索引](../README.md)。
