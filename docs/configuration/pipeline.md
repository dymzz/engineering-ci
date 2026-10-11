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

校验涵盖 CI 触发分支、三个现有 Job、Step 与实际检查命令的对应关系、
工具版本读取、Coverage 调用和原生 Artifact 输出绑定；
同时核对手动 Release 输入、候选 SHA、六个现有 Job 的依赖 / 条件 / 权限、
本提交内的复用 Workflow，以及既有单次 pytest、安全 enforce 和模块锁门禁。
Manifest 使用原有注册表核对实际 Job / Step，不建立第二份检查注册表。
Release 操作还核对实际 `GITHUB_WORKFLOW_REF`、`GITHUB_WORKFLOW_SHA` 与候选 SHA。

GitHub 在运行前解析 Job DAG、权限和条件；Python 不会动态改变它们。
配置或 YAML 单独修改而不满足上述既有契约时明确失败，不忽略不一致策略。
这不是任意 YAML 策略解释器；改变既有 DAG 或调用方式时，需要同时维护绑定校验和故障测试。
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
