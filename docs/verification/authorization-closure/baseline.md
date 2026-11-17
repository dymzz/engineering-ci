# A：版本化审阅基线

本快照的服务端观察发生于 **2026-11-17 14:02:06–14:02:32 UTC**；随后读取的 Issue/PR/CI 归一化响应于 **14:14:44 UTC**汇编保存。每条响应保留自己的更新时间/运行身份；汇编时间不是所有事实同时发生的证明。

## 不可变代码身份

| 分区 | 固定版本 | 含义 |
| --- | --- | --- |
| main | `4bb346c57f99e4767eea78082639f0b367745140` | PR #123 合并后的实际 main；本 PR 的代码基线 |
| #124 候选 | `4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5` | Draft；`codex/eci-052-d4-terminal-expected-20261117` |
| #124 当时的 base | `f3f5a7c806c7fcf7e20a6b6dd257c9b5318a0e25` | 不能将候选与更新的 main 自动拼成已集成版本 |
| #119 最终实现 | `0752f1ea619d44b28fe35152403662f0b574dc9d` | 已合并的 #86 输入/缓存修订；旧证据保留自己的实现身份 |

以下合并状态由 GitHub PR 元数据核对；完整 head/merge SHA 见 [baseline-snapshot.json](baseline-snapshot.json)。合并事实不扩大各合同切片的原验收范围。

| 归属 | 已合并 PR | 本轮使用的事实 |
| --- | --- | --- |
| #66 | [#111](https://github.com/dymzz/engineering-ci/pull/111)、[#112](https://github.com/dymzz/engineering-ci/pull/112) | P0 预执行证明及 B2 Phase/身份初步投影；完整有权治理域/Universe/Phase Producer 仍按 Issue 交付 |
| #63 | [#113](https://github.com/dymzz/engineering-ci/pull/113)、[#116](https://github.com/dymzz/engineering-ci/pull/116)、[#120](https://github.com/dymzz/engineering-ci/pull/120) | T1-a、T2、T1-b 公共合同；真实 Issuer、transport、实际复用消费者单独追踪 |
| #61 | [#114](https://github.com/dymzz/engineering-ci/pull/114)、[#118](https://github.com/dymzz/engineering-ci/pull/118)、[#122](https://github.com/dymzz/engineering-ci/pull/122)、[#123](https://github.com/dymzz/engineering-ci/pull/123) | R1、G2/G3、Release 对账及 R5 代码切片；服务器事实、发行接线与完整生命周期分别核查 |
| #52 | [#124](https://github.com/dymzz/engineering-ci/pull/124)（在途） | Terminal Expected Binder；仅按独立候选版本记录 |

当时 open PR 搜索还返回 #105、#106、#68、#82、#91、#65，元数据已保留。这些不是本次实现基线：B 阶段仅沿现有 Source/Native/Hook/Terminal 等实际依赖记录其接口；没有将未合并设计或工具扩展当作已交付事实。标题含 `125` 的在途 PR 搜索结果为空，本轮建立一个合并 A/B 的新 PR。

## 现行 Issue 段落

固定正文及 `body_sha256` 分别保存在[归一化响应归档](evidence/github-source-snapshots.json.gz)与[元数据](baseline-snapshot.json)。正文 hash 按 connector 返回的 UTF-8 字符串计算；归档不是 GitHub 签名或原始 HTTP 字节。

| Issue / 更新时间 UTC | 采用的当前段落 | 在 A/B 中的用途 |
| --- | --- | --- |
| [#125](https://github.com/dymzz/engineering-ci/issues/125)，11-17 13:46:07 | §0、§1、§2 A/B、§4 权限边界 | 确定本轮范围、证据层次、未知动态边及版本分区；不执行 C/D 裁决 |
| [#52](https://github.com/dymzz/engineering-ci/issues/52)，11-17 12:10:16 | “当前交付结算与 #52/#66/#63/#61 交接”、D4/后置 Terminal 与 Native Result 归属 | 追踪原生 Producer 与候选 Binder；保留 Task-only/Terminal 义务区别 |
| [#66](https://github.com/dymzz/engineering-ci/issues/66)，11-17 12:11:20 | “已交付 / 未完成事实矩阵”、P0、S1 Expected/Phase/义务交接 | 区分 Source/Expected 结构证明、有权治理来源和完整 Universe |
| [#63](https://github.com/dymzz/engineering-ci/issues/63)，11-16 15:08:37 | “当前职责交接与已合并基线”、§3、§4A T1-a/T1-b/T2 | 同执行接纳、受控交接、跨 Run 复用的输入、验证器和消费者 |
| [#61](https://github.com/dymzz/engineering-ci/issues/61)，11-17 10:54:02 | §0、§1、§2 G1/G2/G3、§3 Release/R5 | Required PASS、Check 发布、服务端保护、Release 与后置授权的职责 |

历史描述仍在原正文中；采用优先级由这些当前交接段落及实际代码共同限定。#48/#55/#107 的专项职责不复制到本审阅入口。

## 已有 CI 与 Artifacts

通过 connector 读取已有 Run 的最新 Attempt Jobs 和第一页 Artifact 元数据，没有触发新运行或下载 Artifact。每个返回页分别有 4 个 Jobs、1 个 Artifact；这些是可见返回，不是任意历史 Attempt 的穷举。Job/Step 结论与 Artifact digest/过期时间均保留在元数据中。

| 固定提交 / Run | 可见结果 | Artifact 与适用范围 |
| --- | --- | --- |
| main `4bb346c` / [44068954853](https://github.com/dymzz/engineering-ci/actions/runs/44068954853) | 4 Jobs success；A.1 offline 合同步骤成功，live capture skipped | `14322365336`，`dagger-native-telemetry-a1-offline`；main CI 事实，不是实际全链授权或本轮新 Native 验收 |
| #123 head `2057463` / [44066114637](https://github.com/dymzz/engineering-ci/actions/runs/44066114637) | 4 Jobs success；A.1 offline | `14320458265`；R5 PR 原 head 的 CI，不能改标成 main 运行 |
| #124 head `4a41b4e` / [44085810641](https://github.com/dymzz/engineering-ci/actions/runs/44085810641) | 4 Jobs success；A.1 offline | `14329571284`；候选合同 CI，不能证明真实 Phase Issuer/Completed Run authority 已接通 |
| #119 head `0752f1e` / [44048556796](https://github.com/dymzz/engineering-ci/actions/runs/44048556796) | 4 Jobs success；真实 A.1 capture/gate 步骤成功 | `14313340313`，`dagger-native-telemetry-a1`；仅保留该固定输入/实现的原 CI 事实 |

`fetch_commit_workflow_runs(main)` 的包装器按 PR-triggered Run 过滤且仅第一页，返回空列表；**这不能证明 main 没有 CI**。实际 Check Runs 的 `details_url` 暴露 main Run `44068954853`，因此另行读取了该 Run。原空结果及包装器限制一并保留。

本仓已有 Native 证据直接引用，不复制执行结果、不重新运行：

- [#86 Dagger 输入/缓存](../dagger-input-cache/README.md)及[混合 Plan](../dagger-input-cache/mixed-plan/README.md)：各 bundle/receipt/Source/实现/CLI 与 SHA256SUMS 有自己的原身份；部分明确 `trusted=false`、来源归属未验收，不能转换成 #63 生产接纳。
- [#52 Native 工具合同](../native-tool-contract/README.md)：原生 Exit/Report/Enforce 和多工具满足关系，不代行完整 Required。
- [#74 Weaver](../weaver.md)、[#75 属性映射](../cicd-telemetry-attribute-mapping/README.md)、[#87 原生 OTel](../dagger-native-telemetry.md)：仅在 Terminal 输入/Run-Job facts/A.1 的依赖处引用；Task-only 合法性与 Terminal Root/终态要求分别保留。

本轮仅核对这些存量证据的路径、版本索引和声明范围；未解包重验旧 bundle、未将其批准为当前输入的可复用 Coverage。

## 真实 Check 与服务端可见性

GitHub connector 未暴露 Check Runs / Branch Protection / Rulesets 的专用读接口，因此复用仓库 `inspect_server_protection()` 与默认原生 `gh api` transport，外包一层强制 GET 的记录器。[请求与解码响应](evidence/github-visibility.json)、[观察器输出](evidence/protection-observation.json)保留具体 UTC、native exit、HTTP 状态和安全原生诊断。

| GET 观察 | 实际结果 | 能支持的结论 |
| --- | --- | --- |
| `rules/branches/main` | 可读，空列表 | 当前凭证在该 API 的观察；不是全部保护缺席的证明 |
| `rulesets` | 可读，空列表 | 同上；不代替 Branch Protection 和旁路/App 权限调查 |
| `branches/main/protection` | native exit **1**，HTTP **401**，`GitHub request failed (HTTP 401)` | 观察器映射为 `forbidden`；`enforcement.status=not_verified`，原因包含 `branch_protection_not_readable` / `incomplete_protection_observation` |
| main Check Runs | 11 条，均 completed/success，App `github-actions`（15368） | 4 个 CI Jobs 与 7 次 `produce candidate telemetry`；不是 11 种 Required 检查 |
| #124 Check Runs | 4 条，均 completed/success，同一 App | 固定候选的可见 Check 事实 |
| #123 head Check Runs | 4 条，均 completed/success，同一 App | 原 PR head 的可见 Check 事实 |

Check 查询 `per_page=100&page=1`，返回数量小于 100；未截断这几个响应内的 Check 列表。可见列表没有 `engineering-ci / required-assessment` 或其 contract-sample 名称。这只描述查询的三个 SHA，不外推其他提交、token/App 的可见性。

观察器明确保留 `probe=not_performed`。本轮没有 controlled-deny 测试、配置写入或 App 所有权验证，因此不签发 G3 enforcement 已成立的结论。

上级入口：[A/B 审阅快照](README.md)。
