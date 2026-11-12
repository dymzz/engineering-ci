# CI Provider Adapter 与 Action-specific Gates

**状态：目标接口，尚非已交付统一 Provider API。** [总览](README.md)

## 1. 权威与接口
项目/组织的**当前生效工程 Profile** 根据 Context × Action × Target 决定 Required。ECI #63 对账原生执行/可信复用与 Expected Coverage；#61 决定 Required/Release 授权并连接平台保护规则。Selector、Knowledge RDF 查询及 GitHub Job success 不能自行删减 Required。

Provider 的可选能力是 `Context`（repo/source/Run/Attempt/环境）、`Dispatch`（请求远端执行）、`Observe`（真实平台状态/时间）、`Artifacts`（取得/保存）、`Publish`（发布 Check/Decision）。本地不要求实现远程能力；不同 Provider 不伪造不支持的状态。

## 2. 执行与结果消费
- **Local/Agent**：直接消费现有 `engineering-ci select/execute` 或原生 `dagger check`，不依赖 GitHub Run ID。当前 `execute-plan`、Host 和原生 Check 的功能不完全等价，目标是统一适用的结果和证据语义而非强迫一个执行容器。
- **GitHub Actions**：作为 CI Provider 实现保留原生 Workflow/Job 并行、权限、Artifact、Check/Release 保护配置。不要仅为单 Root 合并全部 Job。
- **其他 Provider**：仅在实际需要时实现对应能力，不建立全平台虚构状态表。
- **发布**：ECI 已验收结果交给 Provider；Publication 状态独立于 Native Result/Gate。外部副作用不能靠 Dagger 缓存冒充完成。

## 3. 按动作执行的时序
| 动作 | 允许发布的事实 | 最终授权约束 |
|---|---|---|
| 本地 Preflight | 本地 Check/原生诊断/证据候选 | 不冒充 GitHub 受保护 Required |
| PR/Merge Gate | 当前被检 Source/合并 Tree 的适用 Check | 缺失、skipped/neutral、未经受信复用不得假 PASS |
| Run 阶段性报告 | 已完成的 Task/Job/Check 的真实结果 | 不宣称完整 Attempt Terminal 已验收 |
| Post-completion | 同一已完成 Attempt 的 Terminal/Run/Artifact 证据 | 不让原 Workflow 等待自身 post-run |
| Release/Promotion | 对指定晋级目标的完整前置 Run 与 Terminal 证据 | #61 按本次有效 Profile 和覆盖进行最终放行 |

若要求“同一动作必须先证明自己完成后才会生成的终态事实”则是**不可满足的自依赖**；须阻断或由策略权威正式修订，不能伪造时间或绕过该要求。若目标 Release 要求的是此前 Run 的 Terminal，则在此前 Run 完成并验收 Terminal 后再授权。

## 4. 覆盖与回执
应分别记录 Expected Requirements、Plan、实际 Check Result、已接纳历史复用、不适用、未执行、当前 Policy Assessment、Provider Publication。存在 Output/Artifact 不代表 Check 已执行；发布成功不代表 Gate 通过。

[Required #61](https://github.com/dymzz/engineering-ci/issues/61) · [Evidence #63](https://github.com/dymzz/engineering-ci/issues/63) · [现行 Release](../../docs/architecture/release/README.md)