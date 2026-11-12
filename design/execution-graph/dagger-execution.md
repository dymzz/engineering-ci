# Verification Graph → Dagger Computation DAG

**状态：目标设计；未声称统一执行图已经交付。** [总览](README.md) · [实施](plan.md)

## 1. 图的权威与依赖种类
- **Engineering Verification Graph**：Expected Check、Subject、Applicability、Input Closure、真实前置条件、Required；由 ECI 决定需要什么，不调度内部计算。
- **Dagger Computation DAG**：函数、Container、Directory、File、Prepared Result 的实际计算依赖；由 Dagger 构建、调度、缓存、并行和观测。
- **Execution Evidence Graph**：Native Result、Run、Artifact、Trace、Assessment 与来源的可核验关系；由 ECI 消费原生结果，不作第二套任务队列。

| 关系 | 处理 |
|---|---|
| Data Dependency | 真实 Dagger File/Directory/Container 产物，或明确的 Host/外部 Artifact 输入 |
| Execution Prerequisite | 在依赖 Check 执行前判断；区分数据存在与前置 Check 必须 PASS |
| Policy Dependency | 仅参与当前 ECI Gate，不制造不必要的计算边 |
| Evidence Reference | 只用于归因/读取，不启动新扫描 |

## 2. 现有事实与待补的映射
现有 `selection.plan()` 输出 `generator/git/selection/check_scope/prerequisite_actions/inputs`；`engineering_ci.execution.execute()` 包含 Host 执行，且部分 `prerequisites` 在测试/其他检查之后运行。不能直接把既有 `prerequisites` 列表当作实际 DAG 前置关系。

`EngineeringCi.execute_plan()` 已校验 Source Tree、导出 Directory 到临时目录、运行部分 Dagger 测试、调用 Host `execution.execute()` 并逐项汇合 Weaver；**并非**所有选定检查已组成单一 Dagger 计算图。

已有 `@check` 及原生产物：`_repository_tests()` → `tests()` / `repository_test_artifacts()`；`weaver_artifacts()` → `weaver_report()` / `weaver_enforce()` / `weaver_live()`。复用这些节点，禁止为报告重跑扫描器。

### 2.1 现行 prerequisite 实例到真实依赖的映射（A 的交付）

已核对的生产/执行路径：项目配置 `ci-selection.toml` 的 `prerequisites: name → tests[]` 由 `selection.py` 按模式选择。**targeted** 路径仅当选中测试集合与配置 `tests[]` 相交时选中对应名称；**full/escalated** 路径通过 `selection._escalated()` 直接选中 `frozenset(config.prerequisites)`（包括显式 `--full`、输入不完整及其他升级情形），不再逐项依赖测试交集。`prerequisite_actions: name → {type, ...}` 被写入 Plan；Host `execution.execute()` 目前先运行 tests、Hygiene/Source，再执行 `_run_prerequisites()`。这说明现有配置表达的是**选择与动作关联**，不是已验证的 Dagger 前置边或成功门禁。

| 现有动作/记录 | 实际生产者和事实 | 需确认的消费方及依赖类别 | 失败与执行时点 |
|---|---|---|---|
| `git_commit_present` | 配置指定 Commit，Host `git cat-file -e <commit>^{commit}` 检查是否存在；不生成 Directory | 对应 `prerequisites[name] = tests[]` 只提供候选测试消费集合；需确认该 Check 是否真的要求该 Git 身份在**运行前**存在，还是仅为后置验收 | 真正 Pre-execution 时先核验；未通过只阻断依赖者，其余 Check 仍可运行 |
| `command` | 配置 `argv[]`，Host 通过原生 Runner 执行并记录退出与 stdout/stderr | 不得按名称推断；逐项裁决为产物生成（Data）、前置 PASS、后置验证或 Policy；若产生文件须声明真实产物/消费路径 | 数据依赖以真实 File/Directory 衔接；前置失败时消费者不启动；后置/Policy 不反向串行 |
| 缺失参数/未知动作 | 现有 `_run_prerequisites()` 返回 `not_executed` 和具体原因 | 不能据此授予任何 Required 成功；不得造一个空产物满足 Data Dependency | 保留未执行/阻断原因；独立 Check 继续执行 |

**A 阶段首项交付：逐真实配置实例填充映射矩阵** `prerequisite_name → source_config/ref → action_spec/version → selected_by → consumer_check(s) → edge_kind → requires_pass / output_ref → execution_phase → failure_scope`。`selected_by` 必须区别 **targeted: selected_tests ∩ config.prerequisites[name].tests** 与 **full/escalated: 全部 config.prerequisites 名称**，并保留具体升级理由；现有 `tests[]` 关联仅提供候选消费关系，不得直接上升为“所有 selected Check 的全局前置”。尚未核实的实际消费者标注 unresolved；不能猜测为 Pre-execution 或在计划里静默删除该要求。

**B 阶段执行不变量：** 真正的前置成功条件必须在其消费者启动前判定；失败时消费者记录 blocked/not_executed 且不会启动其原生工具，独立 Check 仍能实际执行。共享准备和数据生产优先表达为 Dagger 原生函数/产物依赖；Host 前置保持真实来源，不能为表达依赖而实现 ECI 调度器。B 仅核对本次原生执行、严格 Check/Enforce 与报告结果；#61 最终 Required/Release 聚合归 [F 阶段](provider-and-gates.md)，不作为 B 的独立验收前置。

## 3. Plan 驱动执行编译
1. 先独立建立并对账当前 Expected Requirements；不能只凭选中列表确定 Required。
2. 由 [证据专题](evidence-and-identity.md)核验受信历史结果、计算未满足 Check 和真实产物闭包。
3. 对每项未满足 Check，绑定确实被读取的 Source、工具版本、配置、输入闭包和必需上游产物。
4. 真实 File/Directory/Container 数据依赖映射为 Dagger 对象引用。独立 Check 不互相等待；Dagger 决定运行次序和并发。
5. 具有宿主/OS/外部副作用约束的检查和动作保留 Host/Provider 原生入口，输出同构结果但不伪装成 Dagger 节点。
6. 在同一次共享计算节点/Bundle 范围内，严格 `@check`/Enforce、Report 和 Artifact 消费同一原生结果；其严格失败传播属于 B。#61 的最终 Required/Release Gate 在 F 阶段消费已核验结果，不能在 B 阶段被声明为已交付；未执行、取消及基础设施错误不得冒充 PASS。

**不实现：** 自研 Graph Executor、拓扑调度器、统一 Node FSM、Dagger Cache Key 或镜像计算图。

## 4. 两种入口与增量影响
- **原生入口** `dagger check --engineering-ci --check=tests` 等：遵守 Dagger 原生 DAG、缓存和 `@check`；**不会因为本设计自动获得 #63 提前证据短路**。
- **Plan 入口** `engineering-ci select ...` / `engineering-ci execute --plan ...` 及 Dagger `execute-plan`：目标是先 Evidence Resolution，再调用必要计算；这是尚需集成的设计，不是既有 CLI 能力。
- **Test Impact ≠ Execution DAG**：Dagger 不自动推导全部 Python/RDF/Java 测试影响。保留 Tach/Selection 当前实现以及 [Knowledge 对 Pants 作为 Python 增量引擎的选型讨论](https://github.com/dymzz/engineering-knowledge/blob/main/design/engineering-verification/overall-design.md)；是否采用 Pants 需以实测收益与减少重复代码为依据，非本设计的强制前置。

## 5. 输入和失败语义
Dagger Source 过滤须覆盖实际配置、依赖、文件和工具；全仓检查保留必要全仓视图。模块 `dagger-module.toml` 目前 include `pyproject.toml`、`README.md`、`src/engineering_ci/**`；在证明模块运行依赖与 Function/Exec 缓存失效范围后才能收紧。

可保留报告的 Check 优先复用已有 `with_exec(expect=dagger.ReturnType.ANY)` 与共享 bundle；规则违规为 Check FAIL，镜像/环境/工具启动故障需按原生原因区分 blocked/not_executed，不能抹除诊断。

**边界：** 执行前置条件和真实数据依赖必须满足；已通过 Check 的历史 Verdict 不等于相关 Directory 在当前 Dagger Session 已存在。见 [证据与产物闭包](evidence-and-identity.md)。

[已交付事实](../../docs/architecture/local-checks.md) · [Selection 现状](../../docs/architecture/selection.md)