# 开始使用 Engineering CI

安装本地环境并查看两个用户入口。实际操作见[常用操作](common-tasks.md)，命令与输出见[CLI 参考](cli-reference.md)。

如果是在另一个新 Python 仓库使用共享 CI，直接从[新仓库接入](new-repository.md)
开始；本页下面的安装和 `checks pr` 面向 engineering-ci 自身的主 checkout。

## 环境与目录

在 `D:\workspace\engineering-ci` 使用当前分支开发，不需要额外 worktree。
根项目要求 Python 3.14、uv。安装项目锁定环境、GitHub CLI，并确认 `gh auth status` 和目标仓库权限：

```powershell
Set-Location D:\workspace\engineering-ci
uv sync --locked --group dev
gh auth status
uv run --locked python scripts/release.py --help
uv run --locked python scripts/download.py --help
```

上面是开发环境，dev 组安装固定版本的 Vesryn，执行领域兼容测试。两个仓库目前私有，
Git 使用当前已登录的 gh 凭据读取锁定源码；认证或权限失败时停止，不自动登录或换账号。
仅使用基础 checks/version/release/download 时，可使用 `uv sync --locked --no-dev`，
并通过 `.venv` 中的 Python 运行入口，或使用 `uv run --locked --no-dev`。

Linux 使用相同的 uv 命令。aria2c 可选，auto 缺失时使用标准库 HTTP；
具体配置见[下载配置](../docs/configuration/downloads.md)。
输出 --out 相对于调用者目录；机器配置相对于 engineering-ci checkout。

Windows 的完整本地 PR 检查默认在 Dagger Linux 容器内执行，需要 Docker Linux
Engine 和固定版本的 Dagger，安装见[Dagger 配置](../docs/configuration/dagger.md)。
安装后仍在原 checkout 执行：

```powershell
uv run --locked engineering-ci checks pr --json .test-tools/checks/pr.json
```

完整开发测试需要私有 Vesryn 依赖时，执行器读取现有 gh 登录凭据（或已设置的
`ENGINEERING_CI_READ_TOKEN`），通过 Dagger Secret 仅传给 `gh auth setup-git` / `uv sync`。
只接受配置中固定 Commit 的 `dymzz/vesryn` 来源；安装完成后用 SDK 移除 Secret，并在容器中
确认凭据变量不存在，再执行预检与测试。Token 不进入命令参数值、文件或结果 JSON。
Actions 对应使用已配置的同名 Secret，仅供依赖安装步骤。

`--executor native` 可诊断原生 Windows；必需 Linux 测试
被跳过时结果为 incomplete，不能用于交付准入。

## 可选轨迹能力

v0.6.0 在既有 CI 能力之外提供离线轨迹归档、标准投影、非 LLM 分析，以及可选 OTLP 出口和候选报告。
基础安装与 release/download 不要求 PostgreSQL、Rust4PM 或 Jaeger；按实际需要安装：

| 需求 | 安装及后续入口 |
| --- | --- |
| 宿主归档、查询、SDK 投影 | `uv sync --locked --extra trace`；[归档配置](../docs/configuration/tracing.md)与 [trace 命令](cli-reference.md#原始轨迹) |
| 生成离线变体/DFG | `uv sync --locked --extra mining`；[标准化与分析](cli-reference.md#标准化与离线分析) |
| 显式发送已有投影 | `uv sync --locked --extra telemetry`；[本地遥测](cli-reference.md#可选遥测与结果比较) |
| 比较文件、生成候选或工具工作流 | `uv sync --locked --extra harness`；[比较](cli-reference.md#可选遥测与结果比较)与 [人工审查](cli-reference.md#工作流候选与人工审查) |

同时使用多个能力时，在同一 sync/run 命令中列出需要的各个 --extra；dev 组按开发需要保留。
归档使用既有宿主 PostgreSQL 和 VESRYN_DATABASE，显式 trace init 才建立/升级专用 schema。
当前已核对的来源是 Codex 原生 rollout 0.162.0-alpha.2；不扫描所有会话，不自动执行历史工具。
能力范围与未实现事项见[版本设计](../docs/verification/harness-development.md#版本升级依据)，
实测记录见[验证记录](../docs/verification/trace-validation.md)。

v0.6.6 起，上述通用实现由 Vesryn 单独维护；旧命令通过兼容层调用固定版本，
CI 检查策略、批准与反馈门禁仍留在 engineering-ci。依赖边界见
[分仓契约](../docs/architecture/harness-integration/repository-split.md)。
