# 本地检查与 PR CI 一致性

从 engineering-ci 源码仓库运行。入口只使用 Python 标准库，建议使用 Python 3.14；
Actions 的 bootstrap Python 最低为 3.11。实际核心 pytest 固定使用 Python 3.14。

```sh
uv run --locked engineering-ci checks pr --json .test-tools/checks/pr.json
uv run --locked engineering-ci checks ruff
uv run --locked engineering-ci checks ruff-lint
uv run --locked engineering-ci checks ruff-format
uv run --locked engineering-ci checks tests
uv run --locked engineering-ci checks version lock
```

不安装项目包时，也可从源码直接调用仅使用标准库的检查模块：

```sh
PYTHONPATH=src python3 -m engineering_ci.checks pr --json .test-tools/checks/pr.json
```

上面的 PYTHONPATH 写法用于 Bash；PowerShell 先设置 `$env:PYTHONPATH='src'`，
再执行 `python -m engineering_ci.checks ...`。核心 pytest 的锁定环境仍由 uv 准备。

`pr` 恰好包含 Ruff Lint、Ruff Format、核心 pytest、四处版本一致性与根 uv.lock
一致性；单项和完整 profile 使用相同定义。重复选择不会重复执行同一检查。
不执行 Dagger、Matrix 或漏洞扫描，不新增质量阈值。

`checks tests --coverage` 在同一次 pytest 调用中输出 core-junit.xml、
core-coverage.xml 和 core-coverage.json，与 Release 的 workflow_call Coverage
选项一致。不会为了生成本地 JSON 再运行 pytest。失败诊断沿用已有 JUnit 解析器。

## 环境

统一版本：uv 0.12.23、Ruff 0.16.10、核心 Python 3.14；定义位于
src/engineering_ci/toolchain.py。Ruff 的 uvx bootstrap 使用 --no-config，隔离机器配置；根 Lockfile 检查保留项目配置。
Ruff 使用隔离配置、py311 目标及既有 fixture/发布
文件范围，并覆盖本轮新增模块和测试，不做全仓库格式迁移。
入口检测实际工具版本；uv 不一致时报告环境错误。

核心测试需要 aria2c、Git、gh；Linux 还需 Bash。gh 测试使用本地 HTTP 替身，
核心 pytest 不要求触发真实 Workflow 或发布 Release。

Windows 可以在 PATH 中提供 aria2c，或显式指定文件路径：

```powershell
$env:ARIA2C = 'D:\tools\aria2\aria2c.exe'
uv run --locked engineering-ci checks tests --json .test-tools/checks/windows.json
```

ARIA2C 必须指向 exe 文件，不能指向工具目录。原生 Windows 可以实际运行其支持的
测试；若 Bash 专用测试被跳过，记录为 incomplete，入口返回 3。查看 pytest 原始
输出和 core-junit.xml 获取具体跳过原因。不会把原始 exit 0 + skipped 包装为完整通过。

Ruff 的 POSIX 执行权限规则在 Windows 上无法等效验证。CLI 脚本的执行位需要提交到 Git，
库模块不使用 shebang；最终权限检查以 Linux PR CI 的实际结果为准。

需要完整 Linux 对等验证时，在已配置的 WSL2 Linux 发行版内执行：

```sh
sudo apt-get update && sudo apt-get install -y aria2 git
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

退出状态：实际工具失败保留原始非零退出码；信号退出转换为常规 shell 状态；
工具缺失为 127；环境不满足、过期、报告不可用或不完整检查为 3；中断为 130。
JSON 写入失败不能把原本失败的检查改成成功，也不会覆盖其原始失败退出码。
输出路径不能覆盖检查输入或已有 JUnit/Coverage 输出。

本地 JSON 是诊断记录。GitHub PR 和正式 Release 继续以真实 Workflow 状态、对应
Commit SHA、Run Attempt 和必需原生证据为权威依据；本地报告不替代远程门禁。
