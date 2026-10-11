# 检查 Pipeline 配置

`config/pipeline.toml` 是提交到 Git 的独立检查定义。路径相对 engineering-ci 源码根目录，
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

配置只声明现有检查，不执行任意 shell 模板，不定义远端 Job 调度。
schema_version=1；缺失、非法配置或未知实现明确失败，不静默使用另一套默认定义。
PR profile 保留既有五项检查。工具的实际版本和当前输入仍需执行时核验，配置不能作为通过证据。

核心 pytest 需要 uv、Git、gh，Linux 需要 Bash；aria2 是可选专项。
aria2 不存在时 native 和 auto 测试仍执行，专项 JUnit 标识为未启用。
必需测试跳过仍为 incomplete。可执行路径沿用[下载配置](downloads.md)，不在 Pipeline 中复制机器路径。

配置文件和读取模块参与所有检查的输入指纹。更改版本、参数、范围或依赖后，
旧结果不能作为当前准入依据；检查过程中发生变更也不能产生有效通过记录。
四处元数据只有一致的本项目版本值变化时，pytest 指纹保留其他全部内容，
版本与锁文件检查仍使用完整元数据。依赖、测试配置或源码变化照常使相关结果过期。
运行、结果与环境限制见[本地检查](../architecture/local-checks.md)，阶段证据传递见
[发布机制](../architecture/release/workflow.md#交付阶段与证据准入)。
