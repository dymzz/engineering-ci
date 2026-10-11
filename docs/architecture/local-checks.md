# 本地检查与 PR CI 一致性

从 engineering-ci 源码仓库运行。入口只使用 Python 标准库，建议使用 Python 3.14；
Actions 的 bootstrap Python 最低为 3.11。实际核心 pytest 固定使用 Python 3.14。

```sh
uv run --locked engineering-ci checks pr --json .test-tools/checks/pr.json
```

单项检查按需选择，与完整 PR profile 使用相同定义：

| 检查 | 命令 |
| --- | --- |
| Ruff Lint 与 Format | `uv run --locked engineering-ci checks ruff` |
| Ruff Lint | `uv run --locked engineering-ci checks ruff-lint` |
| Ruff Format | `uv run --locked engineering-ci checks ruff-format` |
| 核心 pytest | `uv run --locked engineering-ci checks tests` |
| 版本与锁文件 | `uv run --locked engineering-ci checks version lock` |

不安装项目包时，也可从源码直接调用仅使用标准库的检查模块：

```sh
PYTHONPATH=src python3 -m engineering_ci.checks pr --json .test-tools/checks/pr.json
```

上面的 PYTHONPATH 写法用于 Bash；PowerShell 先设置 `$env:PYTHONPATH='src'`，
再执行 `python -m engineering_ci.checks ...`。核心 pytest 及版本检查的锁定环境由 uv 准备。

`pr` 恰好包含 Ruff Lint、Ruff Format、核心 pytest、四处版本一致性与根 uv.lock
一致性；单项和完整 profile 使用相同定义。重复选择不会重复执行同一检查。
版本检查同时核验 Pipeline 与 GitHub Workflow 的既有绑定，范围见
[配置契约](../configuration/pipeline.md#github-actions-一致性)。检查不一致仍通过该真实 Step 失败，
不新增或改名 PR Job。
检查选择不包含 Matrix 或漏洞扫描。Dagger 可作为这些基础检查的 Linux 执行环境，
不增加检查项目或质量阈值。

`checks tests --coverage` 在同一次 pytest 调用中输出 core-junit.xml、
core-coverage.xml 和 core-coverage.json，与 Release 的 workflow_call Coverage
选项一致。不会为了生成本地 JSON 再运行 pytest。失败诊断沿用已有 JUnit 解析器。

## 环境

`config/pipeline.toml` 的 `execution.backend=auto` 在 Windows 使用 Dagger Linux
容器，在 Linux 保留原生执行，包括现有 GitHub PR Jobs。可以显式选择：

```sh
uv run --locked engineering-ci checks pr --executor dagger --json .test-tools/checks/pr.json --reuse
uv run --locked engineering-ci checks tests --executor native --json .test-tools/checks/native.json
```

`release.py publish` 使用同一配置和检查执行器，继续将当前完整检查证据交给原有
Pipeline / FSM。容器准备和身份查询不运行 pytest；报告导出使用一次检查执行。
`checks inspect REPORT` 读取证据并核验当前身份，不执行检查。

Dagger CLI 与 Engine 需要符合 `dagger.json` 的固定版本，并有可用的 Linux Docker
Engine，安装见[Dagger 配置](../configuration/dagger.md)。缺少 Dagger 返回 127，
环境不满足返回 3，显式容器执行失败不会回退到另一环境。

容器使用锁定的 Python 镜像、Pipeline 固定的 uv/Ruff，以及 Git、gh、Bash 和 aria2。
容器内继续调用现有检查执行器。宿主 Git / PR 操作仍在当前 checkout 进行。
源码输入不包含 `.git`、`.test-tools`、虚拟环境、机器专用下载配置或认证文件；
宿主认证环境变量不传给检查容器。Windows 的已跟踪执行位由 Git 模式传入并记录身份。

`repository-check-environment` 和 `repository-check-artifacts` 是显式的 Dagger 函数，
不会加入默认 `dagger check` 的 fixture 列表。正式发布已经分别执行核心 pytest 和
fixture checks，默认列表增加仓库 pytest 会造成重复执行。

统一版本：uv 0.12.23、Ruff 0.16.10、核心 Python 3.14；版本、参数、范围和依赖统一位于
config/pipeline.toml，读取逻辑为 src/engineering_ci/pipeline.py。toolchain.py 保留兼容调用接口；
配置责任见[检查 Pipeline](../configuration/pipeline.md)。Ruff 的 uvx bootstrap 使用 --no-config，隔离机器配置；根 Lockfile 检查保留项目配置。
Ruff 使用隔离配置、py311 目标及既有 fixture/发布
文件范围，并覆盖本轮新增模块和测试，不做全仓库格式迁移。
入口检测实际工具版本；uv 不一致时报告环境错误。

核心测试需要 Git、gh；Linux 还需 Bash。aria2c 是可选下载后端，不是核心 pytest 的前置依赖。
gh 测试使用本地 HTTP 替身，
核心 pytest 不要求触发真实 Workflow 或发布 Release。

aria2 专项集成测试使用现有下载配置、PATH 或可选 `ARIA2C` 覆盖定位可执行文件。
Windows 可以按需配置这个后端，不要求设置环境变量才能运行核心检查：

```powershell
$env:ARIA2C = 'D:\tools\aria2\aria2c.exe'
uv run --locked engineering-ci checks tests --executor native --json .test-tools/checks/windows.json
```

指定 ARIA2C 时应指向 exe 文件；缺失时只将 aria2 专项标为未启用，native 和 auto 回退测试仍执行。
原生 Windows 可以实际运行其支持的
测试；若 Bash 专用测试被跳过，记录为 incomplete，入口返回 3。查看 pytest 原始
输出和 core-junit.xml 获取具体跳过原因。不会把原始 exit 0 + skipped 包装为完整通过。

Ruff 的 POSIX 执行权限规则在 Windows 上无法等效验证。CLI 脚本的执行位需要提交到 Git，
库模块不使用 shebang；最终权限检查以 Linux PR CI 的实际结果为准。

需要完整 Linux 对等验证时，在已配置的 WSL2 Linux 发行版内执行：

```sh
sudo apt-get update && sudo apt-get install -y git
# 按需安装 aria2，覆盖可选后端的真实分段续传测试。
# 安装 gh、uv 0.12.23；使用该 Linux 环境的工具，避免混用 Windows exe。
cd /path/to/engineering-ci
PYTHONPATH=src python3 -m engineering_ci.checks pr --json .test-tools/checks/linux.json
```

设置 PYTEST_ADDOPTS、PYTEST_PLUGINS 或 PYTEST_DISABLE_PLUGIN_AUTOLOAD 会改变 pytest
选择或运行方式，PR profile 明确拒绝这些覆盖；直接调用 pytest 仍可用于个人诊断。

## 本地结果与有效性

JSON 包含全部请求的检查：名称、executed、status、原始 exit_code、input_stable、
passed_for_current_input、实际工具版本、参数与相关输入指纹。保留 stdout/stderr，
不复制终端日志、源码、环境变量值或认证凭据到 JSON。结果按临时文件 + 原子替换保存，
失败时仍保存已有状态；未执行项使用 not_run 或明确的环境错误，exit_code=null。

`execution_status` 表示工具实际执行结果；pytest 的 `completeness` 表示必需测试是否完整执行。
Windows 上 exit 0 可以同时对应 execution_status=passed、completeness=incomplete：已执行的测试通过，
未执行的 Linux Bash 测试仍是 skipped。Linux CI 与 Windows 本地记录分别绑定平台、工具、输入；
不能互相改写状态，也不能用旧提交的 Linux CI 覆盖当前未提交改动。

aria2 专项缺少可执行文件时，JUnit 的 testcase 属性
`engineering_ci.optional_backend=aria2` 明确标识该项的可选范围。
本地 JSON 将其列在 `optional_tests.aria2`，状态为 `not_enabled`，保留测试名称和原始 skipped 数量；
不能称这些测试已通过。只有必需测试实际通过且输入稳定时，核心 pytest 检查才通过。
没有此属性的 skip 仍记为 incomplete；没有实际通过的核心测试也不能通过检查。

工具在预检后消失或进程启动被拒绝时，executed=false，不生成工具退出码；
分别记录 missing_tool 或 environment_error。进程已经返回后，即使后续环境或报告
核验失败，也保留 executed=true 与原始 exit_code，入口仍返回失败状态。

检查会比较执行前后内容及文件权限指纹、文件观察信息和工具身份。执行中输入变化时，即使工具
exit 0，也不会产生有效通过记录。未提交和未跟踪的相关源码都参与指纹，不以 Commit
SHA 代替实际工作区。工具版本、检查参数、配置、相关源码改变会使旧结果失效。

```sh
uv run --locked engineering-ci checks inspect .test-tools/checks/pr.json
```

inspect 重新比较当前输入与工具身份，仅用于查询。freshness 为 current、stale、
unavailable 或 not_run；只有实际执行成功、输入稳定且身份仍匹配时，
passed_for_current_input 才为 true。检查命令每次实际执行，不读取历史报告跳过检查。
查询可能调用工具版本探测及 uv 的锁定环境准备，不重新运行 pytest。

容器证据同时保存本机输入绑定、容器原始检查身份、CLI / 实际 Engine 版本、
准备后的容器身份与工具版本。跨系统输入传递核对内容指纹，宿主执行前后的文件
观察信息继续检测修改后恢复；机器专用下载配置不属于容器输入。
原始检查 JSON、环境 JSON 和 JUnit/Coverage 保存在当前 checkout 的
`.test-tools/checks/dagger/` 下，并记录每个文件的 SHA-256。
stdout/stderr 保存在独立日志文件并原样输出到宿主终端；JSON 只引用文件和摘要。
目录名称由整组导出文件的摘要确定，新执行不会覆盖旧执行的 JUnit 或日志。
查询或复用会核验原始事实与文件摘要；缺失、损坏、执行环境变化不能准入。
顶层 `identity.root` 绑定原 checkout，容器 `/workspace` 身份另存于 `container_identity`。
Dagger 可缓存相同不可变输入的执行图；报告对应真实执行所得的结果，不表示每次命令
都产生新的 pytest 进程。Pipeline 的逐项复用仍决定哪些检查需要提交执行。

需要复用已有执行结果时显式使用：

```sh
uv run --locked engineering-ci checks pr --reuse --json .test-tools/checks/pr.json
```

此模式逐项核验，不要求整套历史结果都通过。只执行缺失或身份不匹配的检查；
匹配的 passed、failed、incomplete 均复用原始状态与退出码，不自动重试失败或平台限制。
`executed=true` 表示曾实际执行，`reused=true`、`executed_this_invocation=false` 表示本次读取已有证据。
修复导致输入变化时仅刷新受影响项；确需在相同输入下重试时，去掉 `--reuse` 或独立运行对应检查。
publish 使用同一逐项复用方式。查询仅比较身份，复用仍做末尾一致性核验，执行期间输入变化不能放行。

交付沿用已有报告的 Coverage 开关和 JUnit 输出位置，不因输出选项回到默认值而重跑
pytest。只接受既有类型化输出参数；检查选择和参数定义仍来自完整 `pr` Profile，
每项证据都必须通过当前输入与执行环境核验。
容器文件导出成功与检查成功分别判定，Dagger 的导出 Trace 显示 PASSED 不代表 pytest
通过。原始失败和信号退出保存在报告中，由宿主传播实际非零状态；Dagger 保留的信号码区间
使用非零导出状态完成文件传递，不改写原始工具退出码。

## 版本准备与证据继承

准备发布版本不是重新执行代码检查的理由。四处本项目版本字段一致且只有版本值变化时：

| 证据 | 处理 |
| --- | --- |
| Ruff | 源码与检查定义未变，复用匹配结果 |
| pytest | 四处本项目版本值按显式策略归一化，复用匹配结果和原始 JUnit 统计 |
| Version Integrity | 对当前四处元数据重新执行，不继承旧版本通过记录 |
| Lockfile Integrity | 对当前根元数据重新执行，确认更新仍满足锁文件 |
| GitHub PR / Release | 保持实际提交 SHA、Run Attempt 与原生证据绑定，不继承另一提交的成功状态 |

pytest 指纹只归一化 `engineering-ci` 自身的版本值，其余元数据字节、依赖版本、源代码、
参数与环境仍参与身份。四处版本不一致、格式异常或无法确认目标字段时不归一化。
该策略在 config/pipeline.toml 显式定义，不能用于排除测试或忽略依赖变化。
新提交进入远程 CI 后，仍由实际 Workflow 决定其状态；本地复用不替代发布门禁。

退出状态：实际工具失败保留原始非零退出码；信号退出转换为常规 shell 状态；
工具缺失为 127；环境不满足、过期、报告不可用或不完整检查为 3；中断为 130。
JSON 写入失败不能把原本失败的检查改成成功，也不会覆盖其原始失败退出码。
输出路径不能覆盖检查输入或已有 JUnit/Coverage 输出。

本地 JSON 是诊断记录。GitHub PR 和正式 Release 继续以真实 Workflow 状态、对应
Commit SHA、Run Attempt 和必需原生证据为权威依据；本地报告不替代远程门禁。
