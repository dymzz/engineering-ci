# Dagger 版本与本地环境

本轮使用 **v1.0.0-beta.16**：`dagger-module.toml` 固定模块 Engine 版本，
共享 Workflow 的 `DAGGER_VERSION` 同时固定 CLI 和 Engine 镜像。
版本一致性测试防止两处出现偏差。不使用浮动 `latest`。
安装方式来自 [Dagger 官方文档](https://docs.dagger.io/getting-started/install/)。

Windows PowerShell 7：

```powershell
Invoke-WebRequest https://dl.dagger.io/dagger/install.ps1 -OutFile "$env:TEMP/install-dagger.ps1"
& "$env:TEMP/install-dagger.ps1" -DaggerVersion 1.0.0-beta.16 -AddToPath
# 已安装 Winget 旧版时，让本终端优先使用刚安装的版本。
$env:PATH = (Join-Path $env:USERPROFILE 'dagger') + ';' + $env:PATH
# 避免已配置的旧 Engine 覆盖新版默认值。
$env:DAGGER_ENGINE = "image://registry.dagger.io/engine:v1.0.0-beta.16"
[Environment]::SetEnvironmentVariable("DAGGER_ENGINE", $env:DAGGER_ENGINE, "User")
# 新开终端后用 Get-Command dagger 检查目录优先级。
dagger version
```

Linux：

```sh
curl -fsSL https://dl.dagger.io/dagger/install.sh -o /tmp/install-dagger.sh
DAGGER_VERSION=1.0.0-beta.16 BIN_DIR="$HOME/.local/bin" sh /tmp/install-dagger.sh
export PATH="$HOME/.local/bin:$PATH"
export DAGGER_ENGINE=image://registry.dagger.io/engine:v1.0.0-beta.16
dagger version
```

需要可用的 Docker Engine。`DAGGER_ENGINE` 优先于旧的
`_EXPERIMENTAL_DAGGER_RUNNER_HOST`；以上配置由 CLI 从指定镜像启动 Engine。
使用已有测试容器时，
显式设置 `DAGGER_ENGINE=container://容器名`，并确认容器镜像是相同版本。
升级 CLI 不会升级另一个正在运行的旧 Engine。

1.0 会生成 `dagger.lock` 固定实际使用的镜像与 Git 引用，应随源码提交。
本仓库保留真实 fixture 执行生成的锁文件；检查输入范围包含该文件，
其变化会使相关旧 pytest 记录失效。更新锁文件使用原生命令
`dagger lock update`，并审查实际变更。

## 现有模块的兼容调用

Windows 本地完整 PR 验证由统一入口调用 Dagger Linux 容器：

```powershell
uv run --locked engineering-ci checks pr --executor dagger --json .test-tools/checks/pr.json --reuse
uv run --locked engineering-ci checks inspect .test-tools/checks/pr.json
```

Pipeline 的 `execution.backend=auto` 已在 Windows 选择该路径，`release.py publish`
同样使用此配置。容器安装真实 Git、gh、Bash 和 aria2；测试使用本地 GitHub HTTP
替身，不需要宿主 token。安装及镜像首次准备需要网络，之后复用 Dagger / uv 缓存。
容器接口 `repository-check-artifacts` 导出原始执行报告，即使检查失败也可取回；
统一入口从报告传播实际非零状态。`repository-check-environment` 只探测环境和源码。
这些接口不注册为默认 fixture checks，避免 Release 重复执行核心 pytest。
检查入口未设置 `DAGGER_ENGINE` 时，按 `dagger-module.toml` 固定版本选择 Engine 镜像；
显式覆盖仍需通过实际版本核验。旧 `_EXPERIMENTAL_DAGGER_RUNNER_HOST` 不决定该默认值。
Dagger 1.0 的目录导入省略 `dagger.lock`，接口将它作为显式 File 输入传入；
容器与宿主仍对锁文件内容做绑定校验。
当前 beta 在部分 Windows 无终端宿主中会阻塞于背景色自动探测；适配器只给 CLI
子进程提供固定主题兜底，不改变全局环境或容器输入。版本探测超时返回环境错误，
不将未执行的检查记为通过。
证据有效性和出口定义见[本地检查](../architecture/local-checks.md)。

工作区 `dagger.toml` 安装本仓库模块作为 entrypoint；模块清单
`dagger-module.toml` 声明名称、Engine、Python runtime 和 `.dagger` 源码。
使用实际 beta.16 的 `dagger module migrate .` 原地生成模块清单，保留 `.dagger/src`。
两个 TOML 和生成 SDK 均纳入检查输入，修改后旧证据失效。消费者已有 `dagger.json`
读取兼容仍保留；不自动迁移消费者仓库。原生模块运行时要求生成的 SDK 随源码交付，`.dagger/sdk` 由 Engine 生成，
不再由 `.dagger/.gitignore` 排除；不手写或格式化这些生成文件。
不运行会移动源码的 workspace 自动迁移。参见[官方迁移说明](https://docs.dagger.io/config/migrate-dagger-json/)。

```sh
dagger check
dagger check -l --format cli
dagger api call fixture-pytest-artifacts --scenario pass export --path .test-tools/pytest-report
```

`@check` fixture 返回 `None`，通过异常保留失败判定；诊断写到执行输出。
普通 `python_test` 和报告函数的返回契约保持不变。
报告模式通过真实检查列表生成原生选择器，只替代明确指定的 pytest 检查。
`remaining-checks.txt` 保留原有模块限定检查身份用于发布证据，原生选择器用于执行。

生成现有目录中的 SDK，并检查模块锁文件：

```sh
dagger api call --no-load-module module-source --ref-string . \
  generated-context-directory directory --path .dagger export --path .dagger
uv lock --project .dagger --check
```

SDK 必须由实际 Engine 生成，不能只检查旧 SDK 的锁文件而声称完成升级。
版本输出读取兼容 0.x 单行和 1.0 多行格式，Manifest 只保存校验后的版本值。
发布门禁和证据关联仍由[发布机制](../architecture/release/workflow.md)定义。

## Python 工作区生成限制

beta.16 的官方 Python runtime SDK 不实现工作区 SDK 所需的 `findClientRoot`，
实际注册后 `dagger generate` 失败。因此工作区只注册现有模块 entrypoint，
SDK 更新继续使用上面的模块代码生成 API；不声明无法运行的 SDK scope。
观察与版本信息见[上游使用记录](../../.agent/upstream-observations.md)。


## 私有 Python Git 依赖

单版本 `python-lint`、`python-format-check`、`python-test`、`python-test-report` 和
`python-test-artifacts` 接受可选 `git-token: Secret`。它只用于 `uv sync --locked`；
安装之后移除 Secret，并在探测和测试前断言环境中不存在该凭据。Git helper 仅匹配
GitHub HTTPS，配置只含环境变量引用，不保存 token。安装失败保留原始失败。

宿主统一检查目前只为清单中固定 SHA 的 `dymzz/vesryn` 与 `dymzz/vesryn-log`
来源绑定既有只读凭据。私有 Git 包的 uv source 必须在每个消费者中显式声明，
不会从另一个包的开发配置传递。单版本 API 的新增参数不改变 Matrix 的现有接口。

消费者需要先升级到包含该接口的已推送 engineering-ci 提交，再同步更新 Dagger
依赖与 reusable Workflow 固定引用；本地模块测试通过不表示远端已部署。


Reusable Workflow 的 Dagger 执行 Step 提供已有的 `ENGINEERING_CI_READ_TOKEN`
环境 Secret。消费者模块可显式用 `dag.secret("env:ENGINEERING_CI_READ_TOKEN")`
绑定并委托上述方法，原有 `@check` 和单次报告门禁无需替换。Python 依赖安装仅
获得 Dagger Secret；后续容器测试不会继承宿主环境。
