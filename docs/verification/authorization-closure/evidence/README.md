# A/B 保留证据

本目录保存这次固定快照的解码元数据、机械发现结果及一次性推导输入。Git 属性保护原字节；[SHA256SUMS](SHA256SUMS)覆盖本审阅目录的文件（不含清单本身）。这些摘要用于核对保存完整性，不认证 Issuer 或授权来源。

两个原始 Windows JSON 响应（`github-visibility.json`、`protection-observation.json`）保留 CRLF；仅对这两条路径声明 `whitespace=cr-at-eol`，其他尾随空格仍由原生 diff 检查拒绝。

| 文件 | 内容与边界 |
| --- | --- |
| [GitHub 来源归档](github-source-snapshots.json.gz) | UTF-8 JSON、gzip `mtime=0`；5 个 Issue 固定完整正文、合并/在途 PR 元数据、open PR 元数据、既有 Run Jobs/Steps/Artifact 响应。仅保留相关元数据，不含无关 open PR 正文；不含 token、原始 HTTP stderr 或 Artifact 下载内容 |
| [GitHub 只读响应](github-visibility.json) | 逐 GET 的时间、native exit、安全诊断/HTTP 状态及解码响应；不是原始 wire capture 或服务器签名 |
| [Protection 观察](protection-observation.json) | 复用现有观察器所得 `not_verified`，含不可读 Branch Protection 和未执行 probe 的事实 |
| [机械 AST 归档](mechanical-discovery.json.gz) | 两个版本全部已解析 symbol、imports、调用表达式/解析结果、词法候选及反向调用者；固定路径、blob 与内容 SHA-256 |
| [发现脚本原文](discover-authorization-paths.txt) | 原始临时 Python 脚本；从不可变 Git blob 发现，输出到 ignored `.artifacts/authorization-closure/` |
| [只读观察脚本原文](capture-github-visibility.txt) | 复用仓库 GitHub/Protection API 的临时脚本；强制 GET，保留 native HTTP 401；运行时读取当前凭证可见事实 |
| [推导脚本原文](derive-audit-artifacts.txt) | 从已捕获输入推导候选清单、人工数据图/双向边、固定源码引用与元数据；不是运行时授权注册表 |
| [完整性核验原文](validate-audit-artifacts.txt) | 只验证这批产物的摘要、固定 blob/行号、双向索引、版本分区、引用和已声明静态调用；不验证业务语义 |
| [候选 disposition 校核脚本](check-candidate-dispositions.txt) | 检查全部候选、类型化去向、固定快照代码身份与声明的静态调用链；不验证权威来源，也不改写原 A/B 历史核验结果 |
| [本轮核验结果](validation.json) | 本批文档/快照的完整性结果；不作为 Native、Required、Release 或生产权威验收 |

## 复核与重放

在已获授权且持有两个固定 commit 对象的同一 checkout，可将保存的 `.txt` 复制为 ignored 临时 `.py`，用现有 Python 运行。发现与完整性脚本只需标准库和原生 Git；只读服务端观察复用已安装的项目包与 gh。无需安装新工具。

第一次发现的两个 commit 写在脚本 `SNAPSHOTS` 中；机械归档记录对应完整文件/函数索引。人工图的合同边由推导脚本中的 `links` 和候选段落保存。该列表是本轮审阅笔记的派生输入，不参与任何生产 Allow/Deny。

GitHub 重放会产生**新观察时间**，不能覆盖此处原始服务端快照后仍沿用旧结论。一次性推导脚本读取 ignored `source-snapshots.json`（即解压来源归档后的 JSON）和机械/只读响应，再写本审阅目录；在本基线校核时使用已保存输入，在增量审阅时单独保存新快照并重新核对受影响边。

本轮完成源码/引用/摘要校核，未触发 Dagger、Weaver、OTLP 或完整工程矩阵。CI/Native 历史结果与服务端可见性的实际范围见[基线](../baseline.md)。

上级入口：[A/B 审阅快照](../README.md)。
