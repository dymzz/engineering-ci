# Execution Graph — v0.4 consolidated draft

**状态：设计草稿；未实施、未接纳。** 2026-11-12。跨 ECI、Dagger、CI Provider 的总体架构；RDF 是可选领域交接，**不是**执行控制平面。

**核心路径：** 有效项目 Profile + 实际 Source/Context → 独立 Expected Requirements → ECI Selection/Plan 对账 → 受信 Evidence Reuse → Remaining Work + Artifact/Prerequisite Closure → Dagger 原生计算 DAG / 必要 Host Checks → 同一原生结果 → ECI Coverage/Policy Assessment → Provider Publication。终态补验为独立后置阶段。

**不可改变的裁决：**
1. Dagger 拥有函数/数据 DAG、并发、容器与计算缓存；ECI 不开发第二套调度器。
2. ECI 拥有验证选择、输入绑定、检查/证据复用及 Coverage 核验；受信 Required 集合来自项目/组织当前有效 Profile，不能由 Selector 或 RDF 空结果缩小。
3. `Dagger Cache Hit ≠ ECI Trusted Evidence`；复用 Verdict 不得切断尚需执行的下游数据产物。
4. Native Check、Artifact、Report 与 Gate 消费**同一原生结果引用**，不重复扫描。
5. Dagger Invocation、ECI Verification Session、CI Provider Workflow Attempt 是不同生命周期；Dagger OTel 不冒充 GitHub Pipeline Root。
6. 本地/Agent 和 CI Provider 共用工程验证语义；GitHub Actions 是可选平台适配实现；无需 GitHub 即可执行适用的本地检查。
7. Knowledge RDF、PROV、EARL、SHACL 与类型化影响关系仅作为固定、按需消费的领域输入/证据表示；ECI 公共核心不依赖 Knowledge 的内部包或 RDF4J。
8. 直接 `dagger check` 是原生检查入口；Plan 驱动入口的历史证据短路**未自动赋予**原生 `dagger check`。

**详细设计（规范性细则唯一位置）：**
- [Dagger DAG 与执行入口](dagger-execution.md) — Verification Graph → Native DAG、真实依赖、现有入口、并发、Host。
- [证据复用、产物闭包与身份](evidence-and-identity.md) — D-01～D-09、拒绝/冲突、结果/执行身份、缓存。
- [OTel、Trace、Root 与 SemConv](telemetry.md) — 原生导出、上下文、执行归因、Provider Terminal。
- [CI Provider 与按动作门禁](provider-and-gates.md) — Provider 能力、Expected/Required、阶段性和最终发布。
- [Knowledge RDF 跨仓交接](rdf-interop.md) — H-01/H-02/H-03、真实受控接纳及拒绝规则。
- [实施计划与验收](plan.md) — 基线差距、先后依赖、可并行工作、验收与不重复检查。

**已交付实现的事实权威：** [Dagger Checks](../../docs/architecture/local-checks.md)、[Selection](../../docs/architecture/selection.md)、[Pytest 报告](../../docs/architecture/testing/pytest-reporting.md)、[CI/CD Telemetry Producer](../../docs/architecture/cicd-telemetry.md)、[Release](../../docs/architecture/release/README.md)。

此文档只提供导航；术语细则和拒绝条件在上面各专题定义。