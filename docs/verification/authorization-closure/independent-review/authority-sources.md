# 原始来源与实际运行观察

本页与语义探针独立，从固定源码的 Producer/Verifier 边界和真实 GET 响应核对证据来源。未将测试里 `accept_all`、`authority_verified=True`、哈希重算或 `contract_sample=False` 作为真实认证。去重欠账与归属只维护在 [findings.json](findings.json) 的 D-G01～D-G05。

| 实际链 | 原始有权事实/验证边界 | 本轮实际证据 | 未验证部分 |
| --- | --- | --- | --- |
| B2 Expected / R1 Universe | 原治理范围、批准/委托/继承/排除、版本与窗口、Phase 和全义务枚举 | Producer/Validator 代码；受控 normalized 输入的单域/空域、expired/revoked/多缺口探针 | 完整有权 Producer、真实 Phase/Universe 及实时撤销；R1 接口假定输入事实已由上游验证 |
| T1-a / Local Handoff | 原 Report/Job Receipt、受控执行边界、批准验证实现、Origin/Resolution/Grant verifier | 实际 API；缺 verifier、篡改原报告、错 Attempt/Target、过期、uncontrolled 的受控反例 | 实际受信 Issuer/Receiver、真实 transport/运行边界与执行消费者 |
| T2 | 历史每个 Result Origin、当前 P0 来源、Current Authorization 的范围/时效/撤销 | 原 v1/v2 API、Source/Rule mismatch、缺三个 verifier、认证 Native FAIL；历史身份仍在输出中 | 当前真实 grant/Target 映射、复用 Issuer、跨 Run 传输与 Remaining Work 真实执行节省 |
| G2 | 受治理 Publisher approval、固定 evaluator/policy/workflow、实际 Run/Attempt/App/Check | API 受控正例与缺/拒绝 approval、错实际 Actions Attempt 的阻断；真实 Check/Run 元数据见下 | 实际 Required Check Publisher/当前批准源及受信服务端 Gate |
| R4 / 当前 Release | Publication-time Policy、全 Artifact obligations、原 Manifest Provider/实际 Run+Attempt+Job+Artifact 与 validation→publication handoff | R4 API 正反例；实际 `release.publishing.main -> publish_release` 源码 | 真实 R4 consumer、当前候选 Release Run/Artifact/Policy；没有调用任何写 API |
| R5 | 独立 Phase order、Trigger、Due Window 与必要 Disposition authority | 受控 API 的合法 pair、before-due、过期失败、错 Candidate、缺 Trigger 观察 | 实际 Post-stage producer/消费者、真实 disposition effect、完整 lifecycle 义务全集 |
| 受保护工具执行 | Policy Release/anchor、原资产/API、持久 accepted state 与 selection/revision | AST/源代码确认 `receive/decide` 和 execution 内闭包关系 | 当前实际受保护环境；本轮未执行 Hook、Tool Runtime 或 Policy 状态写入 |

T2 的 `delegated_target` 专门避免错误归因：旧 Expected v1 没有独立的当前 consumer Target，权威 verifier 接受另一个 Target 时，单凭 `accept_all` 不能证明错 Target 绕过。`rejected_target` 和 `rejected_revocation` 只证明 verifier 拒绝可以阻断；`unsupported_revoked_field` 只证明未知字段被 schema 拒绝。真实 current grant/撤销检查继续留 D-G02。

## 当前 main 的真实 CI / Check / Runner / Artifact

全部查询限定 SHA `adeeb4b15e47072c5091a31c048253f60471ff1b`。Run/Check 列表由 connector GET 获取，原始 JSON 的 `total_count` 分别为 2、5，实际返回数相同，查询 `per_page=100`。Job 使用明确的 Engineering CI Run、Attempt 1 再读真实 Runner 元数据；Artifact wrapper 只返回第一页，不以其列表证明其他 Artifact 不存在。

| Run / Attempt | 已观察事实 | 本轮可得结论 |
| --- | --- | --- |
| [Engineering CI 44231664562 / 1](https://github.com/dymzz/engineering-ci/actions/runs/44231664562) | 4 Jobs success；Job 134557922821、134557923128、134557923150、134557923203；实际 Runner 分别为 GitHub Actions 1000003716/3718/3717/3719，组 GitHub Actions，标签 ubuntu-24.04 | 证明该 SHA 的对应 Job 状态与运行主体元数据；不证明治理/Admission/Required 已接通 |
| [Telemetry 44231904586 / 1](https://github.com/dymzz/engineering-ci/actions/runs/44231904586) | produce candidate telemetry success | 只证明该 telemetry 任务的对应运行事实 |

5 个实际 Check 均由 GitHub Actions App `15368` 报告 success：`produce candidate telemetry`、`python-ci / checks`、`version-metadata`、`sync-tool-tests`、`dagger-native-telemetry / a1-gate`；本次查询结果中没有 `engineering-ci / required-assessment`。这只对该查询 SHA/时刻成立。

Engineering CI 的真实 A.1 capture/gate、安装 Dagger CLI 与原生 A.1 evidence 上传步骤为 **skipped**；offline artifact 上传存在。Artifact 元数据：

- `14395003958`，`dagger-native-telemetry-a1-offline`，603 bytes，`sha256:416ca9ec7b90de4e90995fa7aa740cd61aefaea6a78b65be3318567a06b4b433`，所属 Run 44231664562。
- `14394713868`，`cicd-telemetry-44231664562-1`，11691 bytes，`sha256:441532e4e4f17ad53be1ca0e02385ed424c0a5ec80aafa5e2090ddbc78fab0d4`，所属 Run 44231904586。

摘要是 GitHub 返回的 metadata，未下载 ZIP/日志、未校验归档内容/签发者。不能把 offline gate Job 的 success 写成新 Native capture，更不能写成全链授权。原 A/B 中记录的 #119 真 Native A.1 Run 和 #52/#74/#75 的原证据保留原 SHA/输入，不重复执行。

原始材料：[Runs](evidence/source-observation-4.json.gz)、[Checks](evidence/source-observation-5.json.gz)、[明确 Attempt 的 Runner](evidence/source-runner-metadata.json.gz)、[Engineering Jobs](evidence/source-run-44231664562-jobs.json.gz)、[Artifacts](evidence/source-run-44231664562-artifacts.json.gz)、[Telemetry Jobs](evidence/source-run-44231904586-jobs.json.gz)、[Artifacts](evidence/source-run-44231904586-artifacts.json.gz)。Runner 查询时间为 **2026-11-18 04:33:55 UTC**；其余响应各有自身捕获锚点和服务端时间。

## 当前 G3 观察与原生诊断

实际运行固定 main 的 `inspect_server_protection()`，外包强制 GET、禁止请求 body 的 native `gh` 记录器。最终固定 blob 重放的运行时间 **2026-11-18 04:45:53–04:46:02 UTC**：

| 请求 | 原生结果 | 解释 |
| --- | --- | --- |
| `rules/branches/main?per_page=100&page=1` | `[]` | authoritative branch-applicable 规则首段完整，可见列表为空 |
| `rulesets?per_page=100&page=1` | `[]` | supplemental Ruleset 列表可见且为空 |
| `branches/main/protection` | exit 1 / HTTP **401**，`GitHub request failed (HTTP 401)` | `visibility.branch_protection=forbidden`；不能推导保护缺失 |

实际观察器输出 `enforcement.status=not_verified`，`probe=not_performed`；理由为 `branch_protection_not_readable`、`incomplete_protection_observation`。此前 connector 同一路径返回 HTTP **403 / Resource not accessible by integration**。两种 transport/凭据上下文分别记录，未把它们混成一个响应或全局账号权限判断。未修改服务器规则、未测试有副作用的拒绝/绕过。

见 [实际 G3 调用和结果](evidence/live-server-observation.json.gz)、[connector 403](evidence/source-observation-2.json.gz)、[connector Rulesets](evidence/source-observation-3.json.gz) 和 [单次 native gh 有效规则查询](evidence/source-gh-active-rules.json.gz)。

## 顺带遇到的上游工具观察

GitHub connector `github_fetch` 的说明列出 Ruleset/branch-protection GET，但实际 `https://api.github.com/repos/dymzz/engineering-ci/rules/branches/main` 被 URL allowlist 拒绝，返回 `INVALID_ARGUMENT / GitHub Fetch URL is not an allowed public GitHub repository or search endpoint`。同一只读路径由原生 `gh api --method GET` 成功返回 `[]`。这是一项可复现的 connector 能力/说明不一致，原始响应在 [source-active-rules](evidence/source-active-rules.json.gz)；没有为此修改项目或安装替代工具。

该上游观察不属于 ECI 授权缺陷，也没有对第三方发 Issue/消息。
