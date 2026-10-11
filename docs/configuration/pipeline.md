# 检查与交付 Pipeline 配置

`config/pipeline.toml` 是提交到 Git 的独立检查及交付定义。路径相对 engineering-ci 源码根目录，
本地入口、Workflow 内部入口和发布交付使用同一份配置，不受调用者当前目录影响。
没有机器专用覆盖；更改检查策略需要显式修改这份文件并审查。

| 配置段 | 责任 |
| --- | --- |
| `toolchain` | uv、Ruff、核心 Python 版本；toolchain.py 向现有调用者提供兼容常量 |
| `arguments` | 既有 Ruff target 与 pytest 参数；JUnit、Coverage 输出位置由调用决定 |
| `profiles` | 完整 PR 和单项检查选择；重复选择同一项只执行一次 |
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

`engineering_ci.pipeline.PIPELINE` 在进程启动时读取和校验配置，其他模块复用该对象。
配置调整后由新一次命令读取，不提供进程内热重载。

| 读取位置 | 配置如何参与执行 |
| --- | --- |
| `release/pipeline.py` 的 `current_checks()` | 将 `delivery.checks_profile` 传给 `checks.execute()`，保留按检查复用有效结果的行为 |
| `DeliveryPipeline.checked()` 与 `ReleaseLifecycle` | 按对应 `profiles` 的检查清单核验完整证据，不能以部分报告准入 |
| `release/delivery.py` | 从 `delivery.release.branch` 获取 PR base、fetch 目标及发布分支身份 |
| `cli/release.py` | 将 `delivery.release.workflow` 传入既有执行入口，绑定 Dispatch 身份与恢复状态；默认分支同样来自配置 |
| `pipeline.py` 的配置校验 | 校验 `trigger=manual`；实际触发机制仍由已有 Workflow 与 GitHub API 决定 |

当前测试核对调用参数来源及下述证据过期规则，使用可注入的检查执行器和 GitHub 替身，
不触发真实发布。
测试替换读取结果用于核对调用来源，不表示生产配置已允许其他 Profile 或 Workflow。
上下文及实际协调关系见[交付阶段与证据准入](../architecture/release/workflow.md#交付阶段与证据准入)。

核心 pytest 需要 uv、Git、gh，Linux 需要 Bash；aria2 是可选专项。
aria2 不存在时 native 和 auto 测试仍执行，专项 JUnit 标识为未启用。
必需测试跳过仍为 incomplete。可执行路径沿用[下载配置](downloads.md)，不在 Pipeline 中复制机器路径。

配置文件和读取模块参与所有检查的输入指纹。更改版本、参数、范围、依赖或完整 Profile 顺序后，
旧结果不能作为当前准入依据；检查过程中发生变更也不能产生有效通过记录。
查询保留原始历史记录，下一次执行只允许复用仍匹配当前身份的结果。
四处元数据只有一致的本项目版本值变化时，pytest 指纹保留其他全部内容，
版本与锁文件检查仍使用完整元数据。依赖、测试配置或源码变化照常使相关结果过期。
运行、结果与环境限制见[本地检查](../architecture/local-checks.md)，阶段证据传递见
[发布机制](../architecture/release/workflow.md#交付阶段与证据准入)。
