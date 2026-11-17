# B：授权出口与双向关系

下列出口从固定源码、AST 候选、测试及 Actions/Dagger/CLI 配置扩展得出。函数的准确行号、blob、SHA-256、固定 commit 链接及静态调用者见[源码引用](source-index.md)；[机械归档](evidence/mechanical-discovery.json.gz)保留所有已解析函数与调用表达式。[人工图](dependency-map.json)中的 `contract_input` 是声明的数据边；`unresolved` 是尚需真实观察的接口，不按直接调用计数。

## 发现方法与范围

分别对两个不可变 Git 版本的 `src/engineering_ci/`、`.dagger/src/engineering_ci_module/`、`scripts/` 解析 AST，再搜索测试、Actions Workflow 与包入口。main/#124 分别解析 **70/66 个运行时文件、各 1,259 个函数/方法**，初次得到 **97/95 个词法候选**；人工扩展后各记录 127 个候选/入口，搜索文件共 **420/415** 个。这些数字表示搜索范围，不代表业务授权出口覆盖率。

发现清单还独立枚举 Dagger 的 **50/48 个装饰入口**：其中各有 14 个 `@check`，含 `weaver_registry/live/terminal`、`lint`、`format_check`、`tests`、`version`、`lock`，以及 6 个明确的 fixture 检查；其余是 Function/API 入口。它们的原生失败沿 Check/Report/Enforce 路径追踪，不当作新的 Required 决策器。没有执行这些入口。

机械调用图以“调用者 → 被调用者”为方向；人工图以“事实/生产方 → 校验/消费方”为方向。人工图中共有 **93 个节点、86 条边**，节点同时保留已解析调用者和入/出边，便于双向核对。测试替身的直接调用位置单独保留；本轮只读审阅 `hook_coordinator`，未调用 Agent/Git hooks。

## main 的出口枚举

| 出口/消费者 | 反向 Producer 与 Validator | 正向消费者及当前接线边界 |
| --- | --- | --- |
| `expected_checks.derive_expected_checks` / `validate_projection`；Expected 的身份、域/Phase 状态 | `selection.plan`、EffectiveProfile、Selection/Hygiene/Sources 规则；`validate_effective_profile`、`_verify_profile_authority`、逐 Ref 重派生 | `reconciliation`、R1 `_verify_expected_scope`；当前权限/治理/Phase Producer 需继续追到真实批准来源。Profile 构造器和自签 digest 不是这个来源 |
| `selection.verify_per_ref_source_snapshot` / `verify_materialized_source_snapshot`、`expected_checks.verify_per_ref_source_handoff`；Source 绑定结果 | Git ODB/树对象、实际物化目录字节、Ref/快照元数据；Tree OID 与目录 Content ID 分别重算 | Host/Expected/下游 Source 交接；实际调用位置保留在机械图。跨环境证明与认证权威分开；不能直接比较不同语义的 digest |
| `execution.build_pre_execution_source_proof`、`compare_pre_execution_to_historical_result`；当前预执行输入证明/等价结果 | 冻结 Plan/Expected、实际 Source、Rule/Tool/Baseline/生成输入；各引用与闭包校验 | T2 `admit_historical_reuse` 声明消费；P0 provenance verifier 为独立边。当前昂贵 Check 执行后 Result 不倒写此预执行证明 |
| `execution.execute`、Host Report 的 `plan_authorized` / Native 满足结果 | `validate_plan`、Source/输入闭包、先决条件与实际工具 Exit；`check_contract.validate_result` / `evaluate_expected_satisfaction` | CLI/已激活 Job/对账接收方；Report 仍保留来源未验证标识，单项 PASS 不直接成为 Required 或 Admission |
| Dagger `EngineeringCi.plan_execution`、`PlanExecution.enforce` | 真实 caller Source、Dagger 内部执行图、原生工具结果；Plan/输入边验证和共享 Report | Report/Artifacts/Enforce 使用同一结果对象。`execute_plan` **只返回 Report**；API export/read 成功不是严格 Enforce 通过。SDK/Engine 动态调度需实际 Native 观察 |
| `hook_coordinator._require_runner_produced_report`、`execute_activated_job` 的接收边界 | Runner 原始 Report、`plan_authorized`、Check Contract、Run/Attempt/runner/result-instance 绑定 | 已激活 Job 的 Collect/对账路径；这是静态审阅到的消费者。本轮未执行 Hook/Agent，Report 形状校验不代行 Issuer 认证 |
| `reconciliation.reconcile_expected_results`；结构性 `matched`/缺失对账 | 冻结 Expected、Routing/Job Receipt、原始 Native Result、独立 Source/Rule/Baseline resolution；`derive_strict_expected` 与满足关系选择器 | `admit_evidence`、R1 的 Receipt 输入；`validate_receipt` 仅验证其合同。Receipt 不签发可信接纳或 Required/Release PASS |
| `admission.admit_evidence`；`accepted_coverage_by_instance` / `remaining_work` | 当前 Expected + #66-C Receipt + 独立 raw Reports/Job Receipts；`_authenticate_evidence`、严格重派生、`authority`、`resolution_authority`、批准的 implementation | R1 `_validate_admission` 声明消费 Admission v1；未发现公共接纳 API 在运行时根中的直接静态调用者，实际接收/减小工作量路径保留 `unresolved`。测试调用单列 |
| `admission.admit_historical_reuse`；当前 Coverage / Remaining Work | 历史原始结果/Origin、当前 P0、当前授权和复用 Policy；OriginAccepted、InputEquivalent、ResultEligible、ScopeCovered、PolicyPermitsReuse 分项核对 | 复用文档与实际减小执行集合的消费者仍需接线观察。历史 Run/Attempt/Result 不改标成当前执行；T2 自身的输出 kind/phase 不能仅因字段相似便当成 R1 的 T1-a 输入 |
| `build_local_handoff_package` / `verify_local_handoff`；受控材料与授权范围/Remaining Work | Package/材料摘要、独立 consumer scope；Issuer、Execution、Authorization 三个 verifier；执行时间及批准有效期 | Actions 接收方 → 后续 T1-a/T2 是独立接线。`verified_materials` 不等于已 Admission；bare `True` 执行证明缺少经认证执行时间，自报工作站不变成受控 Runner |
| `required_assessment.assess_required_obligations`；Required `passed` | #66-B2 Universe、Expected v2、#63 Admission、Receipt、Non-Check Evidence；`_validate_universe`、`_cross_check_universe`、`_authority_fact`、`_check_domain_fact`、`_universe_fact`、逐义务判定 | `validate_assessment` 的实际消费者包括 Check 构造、R4、R5；公共评估 API 未发现直接运行时静态调用者。完整 Universe/治理域/真实 Phase 证明的 Producer 是独立上游边 |
| `build_check_publication` / `publication_for_missing_assessment` / `resolve_conclusion`；Check Conclusion | R1 Assessment、发布上下文；`validate_assessment` / `validate_publication` | 形成请求文档；成功/失败/缺失 Assessment 的分支均有自己的结果。文档构造不是 GitHub Check 写入；sample 名称与生产稳定名称不同 |
| `publication_context_from_actions` / `verify_publisher_identity` / `workflow_publish_authority` / `authorize_and_publish`；G2 发布授权 | Actions runtime、批准的 Workflow/issuer、执行身份、有效期；独立 `approval_authority` 与请求绑定的嵌套 `publish_authority` | `authorize_and_publish` 直接调用 `publish_check_run`；高层对非成功发布也验证 publisher。环境变量和自报 approval digest 不能代行真实批准来源 |
| `publish_check_run`；实际 `POST check-runs` | `validate_publication`；生产 success 另需 `normalize_publishing_authorization` 和 `publish_authority(...) is True` | 默认 `GitHub`/原生 gh，或注入的 transport → GitHub Check/App → 服务器消费者。底层非成功分支不经过 success 专用回调；高层与底层的可调用面分别保留。未在当前 Workflow 找到这些新 API 的直接调用 |
| `inspect_server_protection` / `validate_protection_observation`；G3 配置观察 | 当前凭证读取 branch-applicable rules、Rulesets、Branch Protection；分页及可读性验证 | 返回观察文档；实际 Check 来源 App、bypass、服务器 deny 执法仍是外部边。当前 HTTP 401 / `not_verified` 见[基线](baseline.md) |
| `reconcile_release_assessment` / `validate_release_reconciliation` / `require_reconciliation_manifest`；R4 `verified` / `release_authorized` | R1 + Manifest v1 + publish context；当前 Policy、完整 Artifact obligations、provider、handoff 权威；canonical Manifest Validator 和精确 Manifest identity | provider 工厂返回的回调复用原 `verification.evidence` / `publishing.published_evidence`。这些 R4 公共 API 无直接运行时静态调用者；当前 `release/publishing.py` 未导入它们，实际发布接线保留未知 |
| `release.request.dispatch` / Release CLI；Workflow 请求、等待与状态展示 | 版本/ref/请求身份；原生 gh Workflow dispatch/Run 查询与去重不确定性处理 | GitHub Actions 调度 → `release.yml`；这是请求入口。真实发布还由下述 Workflow/Manifest/写入路径决定，不由 CLI 输出替代 |
| `release.manifest.manifest` / `validate`、`verification.completed_run/evidence/validation_state`；Manifest/Run 验证结果 | GitHub Run/Attempt、Job/Step、Artifact ID、原生执行事实与规范 Manifest 字段；现有验证器 | `publishing.published_evidence`、CLI inspect/verify、R4 provider 回调。固定 Manifest 检查不自动扩展成完整 Required Universe |
| `workflow_actions release publish` → `publishing.main` → `publish_release`；真实 Tag/Release 写入 | `release.yml` 的 publish/needs/权限、request/main/candidate、`require_jobs`、当前 Run/Attempt/repository、Manifest、`published_evidence`、不可变 Tag 检查 | `POST git/refs`、`POST releases`、Manifest asset upload、最终 `PATCH draft=false`；既有 final Release 走验证后复用分支。每个重试/已存在分支保留原身份检查；本轮只静态追踪，未运行发布 |
| `reconcile_post_stage`；后置 Required/Disposition | prior/post R1、治理身份/生命周期/候选、Phase-order、trigger、due window、evaluation time；四类外部 authority gate | 返回 pair reconciliation 与 Disposition 文档；实际 retain/revoke 等执行者未观察。此切片不会置 `lifecycle_all_phases_passed=True`；未来阶段不能由旧 PASS 代签 |
| `security_policy.receive` / `decide`；工具准入 `allowed` | 发布物/Manifest/批准 anchor/原生 provider；持久 accepted revision、snapshot digest、有效窗口、deny 优先匹配 | `execution._build_workflow_admission_fn` 返回的嵌套 admit 回调 → workflow-script 执行；`security_policy.main` 也可输出判定。实际 anchor/state/provider 所有权须独立观察，策略判定不等于工程 Required |
| `cicd_telemetry.produce`、`dagger_telemetry.correlate_capture` / `verify_a1_suite`；运行事实/关联/A.1 Gate | Run/Job provider、固定 Attempt、OTLP/Matcher/Registry/Advice 等原始输入；身份/时间及原生 gate 验证 | Workflow 自身的 Check、Terminal 计划/原生 Weaver 消费。相关性、Task PASS、上传成功或 A.1 通过不签发 #63 OriginAccepted；#74/#75/#87 仅沿这些输入边延伸 |

性能汇总与 Download CLI 中的 `bound` / `conclusion` 词法命中分别是测量词汇与结果展示；它们在发现清单中保留排除原因。Hygiene/workflow-analysis 私有 helper 的原生结果沿 Native/Report 路径收录，没有重复视为新的 Required 授权器。

## #124 候选分区

仅使用 `4a41b4e2ef53bcd7b6194c9d390bdb0b01cf44b5` 的源码/测试/调用关系；不与 main 新增的 R5 等模块自动组合。

| 出口/消费者 | 反向来源和校验 | 正向关系/未解析边 |
| --- | --- | --- |
| `seal_phase_witness` | 结构性 `accepted/status/domain_complete` 字段与重算 digest | `bind_terminal_expected` 的候选输入；构造器注释明确字段可伪造，需要独立 Phase authority |
| `bind_terminal_expected`；`bound` / `authorized` | 原始 frozen Expected、**原始** Expected Plan/Git/root 上现场 `validate_projection`；Completed Run authority、Expected authority、Phase Witness authority；Terminal Plan 与当前闭包 | 直接静态调用者为 `terminal_expected.post_completion_with_expected`；不是 `selection.post_completion_plan`。真实 Phase/Run/批准来源与授权消费入口继续 `unresolved` |
| `post_completion_with_expected` | 调用 `selection.post_completion_plan`，随后调用 Binder | 返回 Plan 与 Binding；未发现此 wrapper 在运行时根中的直接静态调用者。已有 Terminal Plan 入口不能据此认定已经接入 Binder |
| `validate_terminal_expected_binding` | 重算 binding digest、pure consistency、entry/plan/completed-run 身份 | 是结构一致性消费者；重新认证 Issuer/有效授权的运行边不能从这个纯 validator 推导 |

## Producer / Validator 的正向对账

以下把反向链重新按生产/校验侧检查。全部直接静态调用者可在机械归档按 `nodes[symbol].callers` 查询；人工图保留 `incoming_edges` / `outgoing_edges`，数据合同不能因为正反两处出现就被升级为已接通。

| Producer / Validator 组 | 实际调用/声明消费者 | 仍需实际观察的边 |
| --- | --- | --- |
| Profile/规则/`derive_expected_checks` | `selection.plan` 调用 Expected 派生；结构对账、R1 接受其输出；候选 Binder 接受原 Expected | 谁批准哪些受管/委托/继承/排除域及 Phase、哪个完整 Universe 对当前 evaluation 有权 |
| P0、Source snapshot/resolution | Host/Dagger/Expected 的 Source 校验、T1-a 的 resolutions、T2 当前输入证明 | 实际目录/ODB/生成输入来源与当前 Runner、批准的 verification implementation/provenance |
| Native Result Validator/满足关系 | Report、#66-C 的 witness selection、T1-a/T2 的逐实例判定 | native Result 的真实 issuer/runner/transport；跨运行有效性与实施批准不由结构 validator 证明 |
| #66-C Receipt Validator | T1-a、R1 分别消费；测试有直接调用 | Receipt 与 raw Report/Job Receipt 的实际同次交接；未见独立 Receiver 时保留未知 |
| T1-a / T2 / T1-b | T1-a v1 → R1 输入 validator；T2/T1-b 的同名字段属于各自合同 | 真正减少执行工作、Actions 接收材料、Approved issuer/current authorization callbacks |
| R1 / `validate_assessment` | G2 publication builder、R4、R5 调用其 validator；Assessment builder 本身为公开 API | 真实 Universe/Non-Check 生产方、实际调用 Assessment 的平台入口 |
| G2 Publisher verifier/授权 closure | 高层授权器调用底层 Check publisher；低层也可直接调用 | 实际调用者/Workflow 配置、批准 Issuer、token/App 与服务器消费该稳定 Check 的事实 |
| G3 观察器 | 返回文档及 consistency validator | controlled deny/App/bypass 的执法观察；当前可见性不完整 |
| Manifest/Run/Artifact 验证器 | 当前 Release publish/CLI verification；R4 provider 工厂返回回调 | 真正发行场景的最新 provider/权限、R4 与实际写入方的消费接线 |
| R4 / R5 / 候选 Binder 的 validator | 纯输出一致性验证和相应合同测试 | 授权结果的真实执行者、撤销/到期处理、跨版本/Phase 集成接收方 |
| Tool Policy receive/decide | 现有工具执行的嵌套 callback 和 CLI | 批准 anchor/持久状态防回退在实际运行环境的来源 |
| Telemetry / Native Weaver / Dagger | Actions 原生检查、Terminal configured closure、Report/Artifact/Enforce | 外部 Run/Attempt、Root/Task/Origin 边界；旧 Native 证据只在原 Source/版本使用 |

## 身份、时间与失败传播的核查锚点

| 链 | 类型/时间边界 | 当前代码中应继续核查的传播点 |
| --- | --- | --- |
| Expected → Native → Receipt → Admission | `scope_id + expected_check_ref`、ExpectedSet、Profile revision；Git tree OID / directory SHA / Rule / Tool / Baseline；Run/Attempt/result-instance | `result_matches_expected` / satisfaction、`derive_strict_expected`、原生 failed/skipped/not_executed、认证失败与 `authenticated_failures_by_instance`。单个 witness 不闭合 Check Domain/Universe |
| T2 复用 | 当前预执行输入/Action×Target×Phase×Ref，与历史 origin/result 身份分别保存；Policy/授权有效性 | Origin/Input/Result/Scope/Policy 分项；未认证/不等价不减小覆盖。实际减小 Remaining Work 的调度边仍未观察 |
| T1-b | grant 对比**独立 consumer scope**；产生时间、经认证 execution time、evaluation time分别核对 | `declared_trusted` 忽略为权威；自报/不受控执行、过期/未生效授权保留阻断原因 |
| R1 | Universe 的治理来源/Profile/Action/Target/Phase/Ref/Candidate、evaluation 输入、authority window | `_assemble` 的 authority/check-domain/universe/evidence 四个事实；单项证据失败与全域不完整分别保存。上游状态/多原因语义的独立对抗核验留在 D |
| G2/G3 | Assessment subject/head SHA、Workflow ref/event、Run/Attempt、执行身份、批准窗口、Check App | builder、publisher、POST 与服务器强制拦截是不同关口；缺批准/错 repository 在 publisher 处拒绝。实际服务端观察为 `not_verified` |
| R4/实际 Release | exact Manifest identity、repository/tag/head、validation/publication Run/Attempt、policy evaluation time、Artifact obligations | `verified` 与 `release_authorized` 分开；validation 本身不授权 publication。当前 publisher 仍走自己的 Manifest/Jobs/Tag/Release API 路径；不能据 R4 绿测试推导接线完成 |
| R5/Terminal | prior/post 的共同治理/候选/生命周期、Phase 顺序、trigger/due/evaluated_at；Completed Attempt 与原 Expected；Registry/Matcher/Advice/OTLP/Expectation 闭包 | pending/not-yet-due 不冒充最终通过，pair 不成为全生命周期授权；Terminal 在完成后消费，不能成为当前尚未终结 Workflow 的自等待前置 |

这些是 A/B 的实际依赖和代码条件记录；尚未构造 D 阶段的组合伪造、撤销/过期、错主体/时间等反例，也未据此裁定缺陷修复方案。外部事实不可得时，下一次应补对应 `needed_observation`，而不是用测试回调 True、字符串归一化或相邻 CI 绿代签。

## 机械可解析范围与环境差异

- AST 会解析模块级 import、同模块和部分 `self` 调用。函数内 import、嵌套 callback、接口对象方法/别名、Dagger SDK 调度、Actions reusable Workflow 和服务端权限无法由此自动闭合。图中的这些边由人工源码锚点或 `unresolved` 记录承接。
- Host 使用实际本地 Source/ODB/持久 policy state；Dagger 使用 caller Directory、容器工具/Engine cache；Actions 使用 Workflow/Run/Attempt、Artifact/凭证。三者同名字段不自动具有相同权限或来源。
- 测试中的 fixture issuer、注入 API、mock True 只证明合同路径与当前断言；发现清单保留它们的调用位置，本轮未重新运行。已有 Native/CI 证据的真实范围见[基线](baseline.md)。
- 除上述运行时根、测试和 Workflow/入口文件外的外部消费者、下游仓库、已安装包调用方未被静态穷举。没有搜索到的调用以“所列根中无直接静态调用者”表述，不能写成“不存在”。

上级入口：[A/B 审阅快照](README.md)。
