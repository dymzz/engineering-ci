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

## 3. Plan 驱动执行编译
1. 先独立建立并对账当前 Expected Requirements；不能只凭选中列表确定 Required。
2. 由 [证据专题](evidence-and-identity.md)核验受信历史结果、计算未满足 Check 和真实产物闭包。
3. 对每项未满足 Check，绑定确实被读取的 Source、工具版本、配置、输入闭包和必需上游产物。
4. 真实 File/Directory/Container 数据依赖映射为 Dagger 对象引用。独立 Check 不互相等待；Dagger 决定运行次序和并发。
5. 具有宿主/OS/外部副作用约束的检查和动作保留 Host/Provider 原生入口，输出同构结果但不伪装成 Dagger 节点。
6. 消费同一原生结果完成 Check、Report、Artifact、Gate；未执行、取消及基础设施错误不得冒充 PASS。

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