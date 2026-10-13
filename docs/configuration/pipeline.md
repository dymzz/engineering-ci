# 检查与交付 Pipeline 配置

`config/pipeline.toml` 是提交到 Git 的独立检查及交付定义。路径相对 engineering-ci 源码根目录，
本地入口、Workflow 内部入口和发布交付使用同一份配置，不受调用者当前目录影响。
没有机器专用覆盖；更改检查策略需要显式修改这份文件并审查。

| 配置段 | 责任 |
| --- | --- |
| `toolchain` | uv、Ruff、核心 Python 版本；toolchain.py 向现有调用者提供兼容常量 |
| `arguments` | 既有 Ruff target 与 pytest 参数；JUnit、Coverage 输出位置由调用决定 |
| `profiles` | 完整 PR 和单项检查选择；重复选择同一项只执行一次 |
| `execution.backend` | 本地基础检查执行环境：auto / native / dagger |
| `inputs.common` / `scopes` | 内容、权限与缺失文件参与执行身份；范围必须位于仓库内 |
| `inputs.release_metadata` / `checks.pytest.version_only_reuse` | 固定四处本项目版本字段；仅 pytest 可复用版本变化前的代码检查证据 |
| `checks` | 检查范围、必需工具、Linux 工具和原有 CI job / step 名称 |
| `optional_backends` | 可选 aria2 集成测试工具及 JUnit 标识 |
| `delivery` | 本地交付使用的完整检查 Profile |
| `delivery.release` | 既有 Release Workflow、发布分支及手动触发约束 |

配置只声明现有检查和交付目标，不执行任意 Python 表达式或 shell 模板，不定义远端 Job 调度。
schema_version=1；缺失、非法配置或未知实现明确失败，不静默使用另一套默认定义。
PR profile 保留既有五项检查。工具的实际版本和当前输入仍需执行时核验，配置不能作为通过证据。

显式 Handler 绑定引用当前已校验配置的 canonical JSON 摘要和完整 `pr` 顺序，
不复制 TOML 中的工具、命令、权限或检查定义。配置变化后重新生成绑定；实际检查结果仍逐项核验。
绑定解析模块参与当前 `inputs.common`，使相关执行定义变更使原结果失效。
绑定不是新的 Pipeline 配置段，不改变 schema 1 或 Actions Job DAG；契约见
[Handler 固化](../architecture/workflow-optimization/design.md#首个-handler-固化基础-pr-检查复用v061)。

## 操作契约与编排边界

`check_operations.py` 集中注册五项已实现的基础检查，提供 argv 构造、最小工具依赖和必要输入。
TOML 的检查名称引用这些操作；不允许指定 Python 导入路径、可执行文件、任意 Handler 或 shell。
本地 native、Dagger 容器和 Actions 入口共用这些命令构造函数，工具探测与实际 Ruff 调用
也使用同一份 bootstrap 定义。Ruff 实际执行范围读取对应检查的 `scope`，与证据中的输入范围一致。

未知顶层字段，以及 `arguments`、`inputs`、`checks` 中未实现的字段都在配置读取时拒绝。
例如 `command`、`handler`、`needs`、`permissions`、`env` 不属于当前契约，不能通过添加字段
改变执行行为。未被任何检查引用的 Scope 或可选 Backend 同样拒绝。路径不能逃出仓库，
不能以工具选项开头或包含控制字符。已有 schema 1 的三个迁移规则保持有效：缺少整个
`delivery`、缺少整个 `execution`、旧 `lock` 缺少 CI 映射；显式错误不会被这些迁移掩盖。

Profile 不能与注册检查重名，不能把直接调用 `pytest` 改为其他操作；`tests` 必须指向 pytest，
`pr` 必须包含全部五项检查。空 Profile、空调用及不足以覆盖实际执行的输入范围明确失败。
工具依赖可声明已有支持的工具，但不能删除操作实际需要的最小依赖，例如 pytest 的 Git、gh 和 Linux Bash。
aria2 仍是可选专项：配置引用它时容器提供该工具；若取消该专项，需同时移除 pytest 的
`optional_backends = ["aria2"]` 引用和对应 `[optional_backends.aria2]` 表，其他检查保持执行。

三种依赖分别处理：工具依赖由配置及注册操作核验；交付步骤顺序由既有层次化 FSM 管理；
证据准入由共享 ExecutionContext 和 DeliveryPipeline 核验。任务先后完成不能代替输入、版本、
SHA 和执行结果的一致性。失败、跳过、未执行及过期结果不会因报告生成或聚合而变为通过。
GitHub Job 的 `needs`、权限、条件和 Artifact 上传生命周期仍由 YAML 管理。
本轮不新增配置任务依赖图或远端调度器。

## 交付与发布目标

```toml
[delivery]
checks_profile = "pr"

[delivery.release]
workflow = "release.yml"
branch = "main"
trigger = "manual"
```

此轮将已有固定配置迁入 TOML，允许值仍限定为上面已有行为。
`checks_profile` 必须引用完整 `pr` Profile，不能改为 `ruff` 或减少必需检查。
`workflow`、`branch`、`trigger` 分别只支持 `release.yml`、`main`、`manual`。
未知字段、缺少字段、错误类型或其他值均在检查、Git 和 GitHub 操作前拒绝。
`trigger` 表示已有 Workflow 必须手动请求，不会注册定时器或修改 Workflow 的触发方式。

保留 `schema_version = 1`：新增段可选，旧配置完全没有 `delivery` 时按原有固定值迁移。
只要显式写了 `delivery`，其全部字段必须有效，不能用局部默认值掩盖拼写错误。
现有检查段的格式和必需项保持不变。

TOML 不能授予 GitHub 权限、代替人工合并或正式发布请求，也不能跳过远端完整检查。
版本、SHA、Run / Attempt、Manifest 与 Release Asset 的原有核验继续执行。
若以后增加操作类型，应先映射到已注册的 Python Handler，不直接执行配置中的 shell。

## 实际调用接入

```toml
[execution]
backend = "auto"
```

`auto` 在 Windows 使用 Dagger Linux 容器，在 Linux 使用原生工具；`native` 显式使用
当前主机，`dagger` 显式使用容器。CLI 的 `checks --executor` 可覆盖这次执行。
Actions 内部入口明确使用 YAML 已准备的 native Linux 环境；本地配置不改变远端 Job 的执行环境。
缺少工具或容器执行失败不会自动改用另一环境。旧 schema 1 配置完全缺少此段时，
保留旧版 native 行为；显式段必须只包含有效 `backend`，不接受命令模板。
执行与证据绑定见[本地检查](../architecture/local-checks.md#环境)。

`engineering_ci.pipeline.PIPELINE` 在进程启动时读取和校验配置，其他模块复用该对象。
配置调整后由新一次命令读取，不提供进程内热重载。

### Dagger 环境准备

Dagger 读取同一份源码及 TOML，在 Python 基础镜像内调用标准库入口
`python -m engineering_ci.checks_container plan`，复用完整配置校验并取得确定性的环境准备参数。
此步骤不执行 lint、pytest 或工具探测，失败时不会继续安装工具或执行检查。基础镜像仍由
Dagger 获取，镜像、Engine 和 CLI 的既有 pin 与核验保持不变。

环境准备清单从完整 `pr` Profile 的工具和可选 Backend 引用产生；Dagger 的 argv 形式
`with_exec` 执行固定安装操作，配置不能提供任意命令或包名。工具层独立于源码验证分支，
继续使用 Dagger 执行缓存和 uv CacheVolume；该行为见 [Dagger Container API](https://docs.dagger.io/reference/api/container/)。
独立检查与部分证据刷新使用相同的完整环境，避免只重跑 version / lock 时改变容器身份，
进而错误废弃 pytest 的有效证据。环境准备清单只用于执行，不是检查通过记录。

原 `repository-check-environment` / `repository-check-artifacts` 接口继续提供工具、来源、
原始退出码、JSON、JUnit 和 Coverage 证据；取得多个表现形式不重复调用 pytest。
这两个接口不注册为默认 `dagger check` 的额外检查，Release 的现有 fixture 检查清单不变。

| 读取位置 | 配置如何参与执行 |
| --- | --- |
| `release/pipeline.py` 的 `current_checks()` | 将 `delivery.checks_profile` 传给 `checks.run_checks()`，按 `execution.backend` 选择环境并逐项复用有效结果 |
| `DeliveryPipeline.checked()` 与 `ReleaseLifecycle` | 按对应 `profiles` 的检查清单核验完整证据，不能以部分报告准入 |
| `release/delivery.py` | 从 `delivery.release.branch` 获取 PR base、fetch 目标及发布分支身份 |
| `cli/release.py` | 将 `delivery.release.workflow` 传入既有执行入口，绑定 Dispatch 身份与恢复状态；默认分支同样来自配置 |
| `pipeline.py` 的配置校验 | 校验 `trigger=manual`；实际触发机制仍由已有 Workflow 与 GitHub API 决定 |
| 既有 `version check` 及 Workflow 的发布操作 | 调用 `workflow_consistency.py`，核验 TOML 与实际 YAML 的绑定及发布门禁 |

当前测试核对调用参数来源及下述证据过期规则，使用可注入的检查执行器和 GitHub 替身，
不触发真实发布。
测试替换读取结果用于核对调用来源，不表示生产配置已允许其他 Profile 或 Workflow。
上下文及实际协调关系见[交付阶段与证据准入](../architecture/release/workflow.md#交付阶段与证据准入)。

## GitHub Actions 一致性

`ci_job` 是 GitHub 实际显示的 Job 名称，`ci_step` 是该 Job 内唯一的 Step 名称，
不是用于搜索 YAML 文本的标签。`version` 与 `lock` 共同对应
`version-metadata` 中的 `Validate release metadata and root lock consistency`，
该 Step 仍执行两个真实检查；不增加 Job，也不代替其他 Job 的执行结果。
`pytest` 是检查身份，`tests` 是已有 Profile 别名，CI 要求它完整指向 `["pytest"]`，
防止改变别名后漏跑测试；完整 `pr` 的排列顺序仍可调整。

`checks version` 的锁定子进程执行四处版本一致性及 Workflow 配置校验；
完整 PR Profile 与 Release 复用该入口。
发布 Workflow 的 `prepare`、`verify`、`publish` 在 Git / GitHub 操作前校验，
使用 uv 锁定环境中的 [PyYAML 6.0.3](https://pypi.org/project/PyYAML/6.0.3/) 安全解析 YAML。
版本检查需要配置指定的 uv；标准库工具版本读取步骤不需要安装 PyYAML。
版本检查身份记录实际锁定 Python 与 PyYAML 版本，变化后的历史结果不能复用。
在原 checkout 运行 `uv sync --locked` 或既有 `uv run --locked` 即可安装此依赖，
Windows 与 Linux 使用相同依赖，不修改 `.dagger` 依赖。

校验跨层契约和发布不变量，不复制完整 Workflow 实现。强制核验人工触发与显式发布授权、
验证只读权限、关键依赖和准入条件、TOML 共享参数、候选 SHA，以及 Artifact / Manifest 引用。
普通日志、安装细节、Summary、非契约 Step 的名称及超时由 YAML 维护；Dagger 内部任务编排由
Dagger 负责。关键检查与发布 Step 仍须调用约定入口、传入必要参数并传播失败，不能只凭名称认定执行。

三个既有 PR Job 和六个关键 Release Job 必须存在，但不要求 Workflow 只能包含这些 Job。
`release/registry.py` 的 `RELEASE_DEPENDENCIES` 声明发布门禁需要的最小直接输入，
静态校验及发布入口共用它；它不是完整 DAG，不参与调度或拓扑排序。
关键直接依赖不能删除，新增依赖可以保留；运行时传入的全部 `needs` 必须真实成功，
新增依赖的 failure、skipped、cancelled 或缺失状态不会被忽略。只有发布 Job 可获得既有写权限，
额外 Job 的权限必须只读或关闭；普通 Job 无需复制关键 Job 的工具准备、矩阵或执行条件。

Coverage Step 使用 `checks tests --coverage-from-env`；Actions 内部入口将
`EXPORT_COVERAGE` 的 `true`、`false` 或 PR/push 的空值转换为既有 `--coverage` 调用，
只执行一次检查。非法值在执行前失败；未显式选择此参数时不会读取环境中的 Coverage 开关。
不在校验器中维护对应的 Bash 实现。

Artifact 输出必须指向唯一的上传 Step，关键消费者使用准确的原生 Artifact ID 引用。
生产 Step 的显示名称和 ID 可以调整，只要输出引用同步有效；Artifact 的契约名称仍需一致。
Manifest 使用已有注册表核对实际 Job / Step，不建立第二份检查注册表。
Release 操作还核对实际 `GITHUB_WORKFLOW_REF`、`GITHUB_WORKFLOW_SHA` 与候选 SHA。

静态绑定通过只说明配置满足执行契约。实际 Run / Attempt、Commit SHA、Artifact 身份、
原始检查状态和发布资格仍由现有 Manifest 及发布验证模块核验；静态结果不能替代运行证据。
GitHub 在运行前解析完整 Job DAG、权限和条件；Python 不会动态改变它们。
安全敏感的准入条件保留明确支持的表达式，不能仅从待校验 YAML 推导通过标准。
此校验器不解释任意条件表达式或证明不同表达式等价；更改关键契约需维护相应故障测试，
普通实现调整不要求同步复制到 Python。回归测试同时覆盖允许的普通调整和必须拒绝的门禁绕过。
重复 YAML 键、错误结构或不安全标签拒绝；错误信息不打印原始 YAML 内容。

`schema_version = 1` 保留。旧 `lock` 段完全没有 CI 映射时，从已有 `version` 映射迁移；
部分或错误映射不能通过 Workflow 校验。
公共输入必须包含一致性实现、Manifest 注册表及三个 Workflow，缺少时要求补齐配置，
不能用缺失的输入范围复用历史结果。

核心 pytest 需要 uv、Git、gh，Linux 需要 Bash；aria2 是可选专项。
aria2 不存在时 native 和 auto 测试仍执行，专项 JUnit 标识为未启用。
必需测试跳过仍为 incomplete。可执行路径沿用[下载配置](downloads.md)，不在 Pipeline 中复制机器路径。

配置文件和读取模块参与所有检查的输入指纹。更改版本、参数、范围、依赖或完整 Profile 顺序后，
旧结果不能作为当前准入依据；检查过程中发生变更也不能产生有效通过记录。
Workflow、绑定校验或 Manifest 注册信息变化同样使所有基础检查的旧证据过期。
PyYAML 的依赖 / 锁变化属于版本、锁文件和 pytest 输入；Ruff 按其实际工具及源码范围判断，
不因单纯准备发布版本而重跑。
查询保留原始历史记录，下一次执行只允许复用仍匹配当前身份的结果。
四处元数据只有一致的本项目版本值变化时，pytest 指纹保留其他全部内容，
版本与锁文件检查仍使用完整元数据。依赖、测试配置或源码变化照常使相关结果过期。
运行、结果与环境限制见[本地检查](../architecture/local-checks.md)，阶段证据传递见
[发布机制](../architecture/release/workflow.md#交付阶段与证据准入)。
