# ECI #66 — ECI-managed Git Hooks（需求补充提案，待审定）

> 目标 Issue：[ECI #66](https://github.com/dymzz/engineering-ci/issues/66)。这是**增量需求草案**，不是另一个 Issue 全文版本，也不是实施代码。审定后将有效条款并入 #66 §3「同源本地、Dagger、Actions 入口」和验收清单。已有 ECI PR #67 已合并的 `eci_core` 选择/Hygiene/CLI 为基础，不能重新实现一套。

## 1. 工程目标与责任裁定

- **ECI 拥有 Git Hook 的通用实现和分发**：pre-commit/pre-push 原生入口、安装/升级/诊断/卸载、Git 事件与受检 Source 适配、原生检查/前置条件执行、结构化结果和失败传播。下游不得继续维护独立的通用 `hook_adapter.py`、Index/Push 快照引擎、独立 Check 调度/报告判定器。
- **下游拥有项目策略**：使用哪些 Git 事件、哪些实际适用 Checks、代码/非代码测试映射、全量升级条件、Identity Commit、wheel/CLI 命令及配置、特定工具版本。ECI 仅消费配置和执行公共机制，不嵌入 Knowledge 的 RDF/Manifest/业务数据语义。
- **统一消费约定**：采用 ECI Hooks 的仓库由 ECI 安装器管理公共入口，不能要求每仓复制/维护 800 行适配代码。仓库可以保留自己的声明性配置和确有证据必要的项目专用 Hook；不得与 ECI 通用入口重复运行同一验证。
- **强制性分层**：在仓库的工程策略中可把安装并运行 ECI Hooks 定为开发工作流要求；但本地 `core.hooksPath`、`--no-verify` 和用户配置使 Hooks 不具备安全意义上的不可绕过性。合并/Release 真正强制性仍由原生 Actions、ECI #61 的 Required 完整性和经过核实的 GitHub 保护设置实现，不由 Hook 成功与否伪造 GitHub Required 状态。

## 2. ECI 交付接口（CLI 命名待实现期核对；不视为已存在）

推荐复用现有 `engineering-ci = engineering_ci:main` 包入口，提供如下统一子命令，而不是建立新框架：

```text
engineering-ci hooks install --repo .
engineering-ci hooks status --repo .
engineering-ci hooks uninstall --repo .
engineering-ci hooks run pre-commit --repo .
engineering-ci hooks run pre-push --repo .
```

### 2.1 安装与生命周期

1. 安装器核对 ECI 可执行文件及**能力合格**的固定包/Commit 版本、项目规则版本，创建可复用的薄 Hook 启动入口，通过仓库本地 `core.hooksPath` 或等效 Git 原生机制接线。可选 ECI 自管薄入口文件须明确所有权，不得让下游维护逻辑副本。
2. 不静默覆盖、删除已有第三方 Hook 或 `core.hooksPath`；检测冲突并给出显式的保留、链式调用或经确认的迁移决议。安装幂等；`status` 证明当前仓库真实生效的 Git Hook 路径、入口和 ECI 来源；`uninstall` 仅撤销自身变更，恢复已记录的旧设置，不修改不属于 ECI 的文件。
3. 同一入口在 Git for Windows / Unix 工作，尊重仓库实际 Git 顶层、worktree 的 Git config 作用域、路径/空格及原生退出码。调用已安装的固定 ECI，不在每次 Git 事件中运行 `uv sync`、重新安装 CLI、拉取 Dagger Engine 或重新生成 SDK。
4. 不能因为安装过就声称用户所有 Git 操作必经 Hook；检查、报告、Actions 和真实服务端 Required 状态分别记账。

### 2.2 Git 事件到真实执行对象

- **pre-commit** 保留 Git 传入的 `GIT_INDEX_FILE` 等原始事件上下文，读取本次 Index Tree；选择与原生执行必须在可验证的**同一 staged Tree** 上发生（例如以临时 Commit/隔离 worktree 执行），不得在未暂存的不同工作树上冒充已验证暂存内容。构造与清理隔离 worktree 是 ECI 的公共责任。
- **pre-push** 读取 Git 实际提供的全部 `local_ref local_sha remote_ref remote_sha`，逐个验证待推目标。保留有效提交内容身份；既有目标使用真实 old→new（包括强制推送的移除项）；新分支、缺失基线/对象和多引用应给出可核查的扩大检查或阻断，不能 `origin/main == HEAD` 空差异通过。变更中断或缺前置事实不得假 PASS。
- `select`、`execute`、Hygiene、Sources、Tach/工具及前置条件必须消费同一 Source/配置/工具身份；测试目标为空不代表其他工程 Checks 不适用。未执行、失败、未知、不适用与复用结果保持分离。默认只使用必要本地进程；确实需要 Dagger 时才进入对应原生执行路径。

### 2.3 成本与重复消除（避免 Hook 变成全量 CI）

- 分别为 pre-commit / pre-push 声明适用检查及真正的升级规则；Hook 本身变更应有**真实 Hook/事件契约测试映射**，不因未映射而无依据每次运行所有 Knowledge unittest + wheel；未知影响仍需可靠升级，不允许简单忽略 Hook 文件。
- `select --validate-tach` 产生的原始 Tach 事实，输入/工具/配置相同且范围有效时应被后续 Hygiene 直接消费，不得仅为第二份报告再跑 `tach check`；`tach check-external` 是不同检查，按适用范围保留。
- Identity Commit、wheel 构建和仓外 CLI Smoke 是类型化前置条件：只在实际影响/策略要求时运行或**有真实有效证据**时复用；覆盖缺失或无法确定必须阻断适用最终结论，不以降级为「无测试」规避。
- 为独立 worktree、select、execute、Hygiene/Sources、前置条件、清理提供轻量**阶段耗时**及原生诊断；即使子进程输出需保持机器可读，也要有可见阶段进度与最终耗时分解。不得再建第二个缓存/结果数据库或全量性能平台。

## 3. 与现有 Issues 的接口

| 责任 | Owner / 现有资产 |
| --- | --- |
| 公共 Hooks CLI、Git Index/Push 事件适配、Source-bound 执行及定向结果/时长 | **ECI #66**，扩展已合并 PR #67 的 `eci_core` / CLI |
| 原生 Check Report/Enforce、Actions 执行结果出口 | **ECI #52**，不重建 Check Runner |
| 历史结果 T1-b PR→main 与 T2 本地→Actions 受信接纳 | **ECI #63**，本地 Hook 成功不等于已可受信复用 |
| 最终 Required/Release、真实 GitHub Ruleset/Branch Protection | **ECI #61**，不建平行 Gate |
| `ci-selection.toml` 等领域规则、安装消费、PR #73 旧适配器迁移/删除及验收 | **Knowledge #68** |

上述公共 Hooks 生产能力可与 #66 的 Source/Check 修复、#52 Actions、#63 历史接纳并行推进；只有某条路径的集成验收才需要对应已完成的公共接口，**不得设置全 Issue 串行等待**。

## 4. 可执行验收与反例

- [ ] 干净的 **Knowledge** 和至少一个**非 Knowledge** 消费仓库，安装同一固定 ECI Hooks 包、配置并实际触发 pre-commit/pre-push；验证 `status`、重复安装、更新、卸载/恢复、已有第三方 Hook 以及 Windows/Unix 必要路径。
- [ ] 临时 `GIT_INDEX_FILE=B`、默认 Index=A、未暂存工作区=C 时，只验证 B 的原生事实；准确保留 Git 事件原始身份及隔离执行 Tree，非零原生失败阻断提交。
- [ ] 非 HEAD 推送、多引用、旧→新强推包含删除、首次推送、缺失远端对象：正确执行目标内容及有效输入覆盖；不可解析时扩大范围或阻断，不出现空差异成功。
- [ ] 测试目标为空但 docs/source/Tach 适用时真实执行对应原生 Checks；不因 `no_selected_tests` 跳过。
- [ ] Hook 脚本/配置变更选中相关公共/消费者测试；证明**不相关**业务全集和 wheel 不会被误触发，同时相关影响不漏测。
- [ ] 同源 `tach check` 单次执行并复用其有效原生事实；不同的 `check-external`、Sources 和适用 Identity/Wheel 仍如实执行。
- [ ] 记录一次有代表性的真实 pre-commit 耗时分段、适用测试数和重复检查数；无效/缺工具不标 PASS。
- [ ] 安装/运行 Hooks 不能替代 GitHub 端 Required；`--no-verify` 的本地绕过路径在实际服务端验证策略中按其风险独立处理，不宣称无法绕过。

## 5. 待审决策与合入步骤

1. 公共安装是采用 ECI 自管稳定 Hook 目录，还是 ECI 安装时创建**零逻辑薄转发入口**：以 Git for Windows/多 worktree 的可复现性、已有 Hook 兼容和版本升级成本比较，**不提前制造两套实现**。
2. 确认 CLI 子命令名称及配置字段后再进行代码实施，避免在本需求 PR 中宣称现成接口。
3. 审定后将**仅新增/冲突的条款**并入 ECI #66；保留其已交付 PR #67 事实、现有来源/Hygiene/选择器接口及 #52/#61/#63 职责，不复制本草案成另一个常驻规范源。
