# Dagger 版本与本地环境

本轮使用 **v1.0.0-beta.16**：`dagger-module.toml` 固定模块 Engine 版本，
共享 Workflow 的 `DAGGER_VERSION` 同时固定 CLI 和 Engine 镜像。
调用前核对 CLI 与固定 Engine 版本一致。不使用浮动 `latest`。
安装方式来自 [Dagger 官方文档](https://docs.dagger.io/getting-started/install/)。

共享 Workflow 用 Docker 原生 `--pull=missing` 启动 Engine：先使用本地固定 Tag 的镜像，
仅在缺失时拉取；拉取失败仍使任务失败。宿主与不同 GitHub Hosted Job 不共享 Docker 镜像缓存。

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

Windows 和 Linux 在原 checkout 直接运行 `dagger check`，详细命令与报告语义见
[本地检查](../architecture/local-checks.md)。`dagger.toml` 安装本仓模块作为 entrypoint，
`dagger-module.toml` 固定 Engine 与 `.dagger` 源码。默认检查针对实际根仓，不是 fixture。
`release` 环境显式开启根仓 Coverage，`fixtures` 环境安装发布验证用的 validation 模块别名。
Engine 由 Dagger 原生启动，不再在宿主探测、计算身份或持久化成功收据。
机器上 CLI 与模块固定的版本应一致；Windows 若无终端主题探测阻塞，可在当前进程
设置 `$env:FORCE_DARK_MODE='1'` 后重试，不修改全局环境。

SDK 生成与模块锁文件校验：

```sh
dagger api call --no-load-module module-source --ref-string . \
  generated-context-directory directory --path .dagger export --path .dagger
uv lock --project .dagger --check
```

保留生成的 SDK；不手写、格式化生成文件。消费者接口与凭据规则如下。

## Python 工作区生成限制

beta.16 的官方 Python runtime SDK 不实现工作区 SDK 所需的 `findClientRoot`，
实际注册后 `dagger generate` 失败。因此工作区只注册现有模块 entrypoint，
SDK 更新继续使用上面的模块代码生成 API；不声明无法运行的 SDK scope。
观察与版本信息见[上游使用记录](../../.agent/upstream-observations.md)。


## 公共函数与凭据

`dagger api functions` 发现实际函数；跨仓库调用使用固定模块版本，例如
`dagger api call -m github.com/dymzz/engineering-ci@v0.6.5 python-test-report --source . --python-version 3.14`。
这个已发布基线不包含下文的 v0.6.7 新增 Secret 参数；候选需使用已验证的不可变 Commit，
正式发布后才能改成对应 Tag。

| 函数 | 输入 / 默认值 | 返回与失败 |
| --- | --- | --- |
| `python-lint` | source Directory、python-version、lint-path=`src`，可选 git-token | 输出字符串；Ruff 或准备失败抛出原错误 |
| `python-format-check` | source、python-version、format-path=`src`，可选 git-token | 格式检查输出；不改源文件，失败仍阻断 |
| `python-test` | source、python-version、test-path=`tests`、markers/coverage-source/min-coverage 可选，git-token 可选 | 输出字符串；pytest 非零或显式质量门槛失败则抛出有界摘要 |
| `python-test-report` | 同上，max-failures=`10`（1–50） | JSON 字符串；测试失败仍返回其原始 exit_code，准备失败不伪造报告 |
| `python-test-artifacts` | 同 report | Directory；原始退出码、JSON、JUnit/可用 Coverage、summary。调用者执行导出的 `ci.py gate` |
| `python-test-matrix*` | source、python-versions 列表、显式测试策略；max-concurrency=`1`（1–3） | [Matrix 的三个接口](../architecture/testing/python-matrix.md#choose-one-execution-interface)，没有 git-token 参数 |

source 必须含有效 `pyproject.toml` 与对应 `uv.lock`，消费者负责声明 pytest/Ruff 依赖；
依赖同步、网络、运行时或接口配置错误不是测试通过。容器测试的 Python minor 支持 3.10–3.14，
消费者 requires-python 不满足时 Matrix 保留 unsupported 结果，不能静默少跑一个版本。
报告字段、文件上限及执行/交付区别以[报告契约](../architecture/testing/pytest-reporting.md)为准。
安装 Docker Linux Engine、固定 Dagger 后即可调用，不需要安装宿主 engineering-ci Python 包。
当前 Python 容器使用 minor 版本镜像标签，未按镜像 digest 固定；锁文件与 Engine 固定
不等于整套环境逐字节重现。验收记录实际版本与结果，不承诺未来标签内容不变。

### 本仓检查与消费者检查

本仓默认五项 Checks 检查根仓源码；消费者组合既有 `python-*` Functions，
按自己的源码、范围与策略声明原生 Checks，见[接入示例](../../guides/new-repository.md)。

### 按执行位置选择 GitHub 认证入口

engineering-ci 提供工程检查、CI/CD 策略和结果证据；认证由实际执行位置和调用方配置决定。
开始排查时先记录入口、目标仓库、执行账号、Dagger 阶段及原始退出，不能只报“缺 token”。

| 调用环节 | 使用的身份/凭据 | 不代表什么 |
| --- | --- | --- |
| 宿主 gh、使用 gh 的 host 工具 | 当前执行账号的既有 gh 登录、目标仓访问和宿主许可 | 另一个终端登录成功不证明本进程能读配置或 keyring |
| Dagger 拉取私有模块源 | 调用方 Dagger/Git 对模块/ref 的访问 | 容器 Python git-token 不能证明模块加载已认证 |
| 容器安装私有 Python Git 依赖 | 既有 git-token: Secret，只附加到依赖同步阶段 | 不作为后续业务 GitHub 工具的默认身份 |
| 显式本地 wheel | 所选私有 Python 包不从 Git 下载 | 公共包/镜像仍可能需要网络，其他认证路径并未因此验证 |
| 容器业务 GitHub API | 具体工具声明的身份、Secret 与操作权限 | 不自动继承宿主登录或依赖安装 Secret |
| GitHub Actions | 调用方 Workflow 的 Secret/permissions 与仓库/组织访问规则 | 变量存在不证明可访问全部私有仓 |

在**实际调用的同一执行上下文**先做不显示秘密的只读诊断：

```powershell
gh auth status --active --hostname github.com
$authExit = $LASTEXITCODE
if ($authExit -ne 0) { exit $authExit }
gh repo view dymzz/engineering-ci --json nameWithOwner --jq .nameWithOwner
$repositoryExit = $LASTEXITCODE
exit $repositoryExit
```

配置文件/凭据存储的 Access is denied 属于执行上下文访问被拒，尚不能认定账号无效。
使用宿主正常审批取得该次只读访问后再检查；不修改 ACL、沙箱、账号或重新登录来绕过。
网络被拒、目标仓无权、账号缺失/失效和 Secret 未绑定分别定位；只有确认账号缺失/失效时
才由用户选择正常登录。不要用 --show-token 或 auth token 将令牌打印给 Agent/用户。

本仓核心检查只安装锁定的公共依赖，不读取或转发宿主 gh Token。
消费者调用公共单版本函数时，通过公开 `git-token` 参数或调用方的
`dagger.toml` settings 显式提供 Secret；其作用域限于依赖安装。

官方依据：[gh auth status](https://cli.github.com/manual/gh_auth_status)、
[gh auth login](https://cli.github.com/manual/gh_auth_login)、
[Git credential helper](https://cli.github.com/manual/gh_auth_setup-git)。
以下私有 Python 安装契约与后面的本地 wheel 契约分别适用，不互相代替验收。

### 私有 Python Git 依赖

单版本 `python-lint`、`python-format-check`、`python-test`、`python-test-report` 和
`python-test-artifacts` 接受可选 `git-token: Secret`。它只用于 `uv sync --locked`；
安装之后移除 Secret，并在探测和测试前断言环境中不存在该凭据。Git helper 仅匹配
GitHub HTTPS，配置只含环境变量引用，不保存 token。安装失败保留原始失败。

私有 Git 包的 uv source 必须由消费者显式声明；单版本 Secret 参数不改变 Matrix 接口。

Reusable Workflow 的 Dagger 执行 Step 提供已有的 `ENGINEERING_CI_READ_TOKEN`
环境 Secret。消费者用构造器参数 `git_token: dagger.Secret` 接收凭据，
并在原生 `dagger.toml` 中配置 `[modules.<name>.settings]` 的
`gitToken = "env:ENGINEERING_CI_READ_TOKEN"`，由调用方解析地址后注入对象。
薄适配只把这个 Secret 传给上述方法；`@check` 和单次报告门禁保持原入口。

不要在模块的缓存函数中从 `env:` 创建调用方凭据：beta.16 的真实嵌套调用出现过
模块环境缺少变量和过期 session client binding。直接的函数 Secret 参数可通过
CLI `--git-token env:ENGINEERING_CI_READ_TOKEN` 注入。Python 依赖安装仅获得
Dagger Secret；后续容器测试不会继承宿主环境。

构造器和 settings 的语义见 [Dagger module wiring](https://docs.dagger.io/config/module-wiring/)。

## 显式本地组件制品

既有 `python-lint`、`python-format-check`、`python-test`、`python-test-report` 和
`python-test-artifacts` 接受可选 `dependency-packages: Directory`，其内容是明确选择的
wheel。未提供时继续使用原有锁定同步/Secret 接口；本地模式不同时接受 git-token。

本地模式保留锁内公共依赖，排除已提供的 Git 包，再用 `uv pip install --no-sources` 正常解析
wheel、当前消费者及其默认开发依赖组，最后执行 `uv pip check`。不使用 `--no-deps`，
不获取宿主令牌；未提供的 Git 依赖、重复身份、消费者自身 wheel 或版本冲突在检查前失败。
本地调用方传入当前工作树的过滤快照；排除各层虚拟环境、VCS、测试数据、凭据及生成 SDK。
路径绑定不是读取、执行或修改权限。

`python-test-artifacts` 仍只执行一次 pytest。原有 exit-code.txt/report.json/junit.xml
含义不变；本地模式另附 dependency-packages.json，列出实际安装版本、wheel SHA-256、
消费者清单/锁摘要及 Dagger source_digest。报告导出成功不表示测试通过，调用方必须消费原退出码。
