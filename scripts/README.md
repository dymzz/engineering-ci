# scripts 入口使用说明

`scripts/` 保留命令行入口及现有专项工具。统一检查和 Release 的业务逻辑位于
`src/engineering_ci/`；GitHub Actions 使用 `python -m engineering_ci.cli` 调用同一实现。
脚本不是另一套检查定义或发布调度器。

## 运行环境与目录

以下相对路径示例从 **engineering-ci checkout 根目录**执行。统一入口的 bootstrap
需要 Python 3.11+；项目锁定环境和核心 pytest 使用 Python 3.14。
使用前确认当前 checkout 包含所调用入口；功能 PR 合并前，新入口位于对应功能分支。
本地 PR 检查还需要指定版本的 uv，以及对应测试所需的 Git、gh、aria2c；
Windows/WSL2 限制与工具版本见 [本地验证](../docs/local-verification.md)。

```sh
python scripts/checks.py --help
python scripts/checks.py pr --json .test-tools/checks/pr.json
```

使用项目依赖环境运行其他脚本时：

```sh
uv run --locked python scripts/ghwf.py --help
uv run --locked engineering-ci --help
```

入口通过自身文件位置定位 `src/`，不需要写死仓库路径或创建额外副本。
`checks.py` 和 `release_version.py` 检查的是入口所属的 engineering-ci checkout，
并不因为调用者当前目录位于消费者仓库，就改为检查消费者。
发布辅助脚本应在对应验证 checkout 根目录执行。核心 JUnit/Coverage 位于
checkout 根目录；跨目录调用 ghwf 的输出与仓库识别规则见下文。

## 入口索引

| 脚本 | 用途与调用方式 | 详细说明 |
|---|---|---|
| `checks.py` | `pr`、`ruff`、`ruff-lint`、`ruff-format`、`tests`、`version`、`lock`；`inspect REPORT` 查询历史结果有效性 | [本地检查](../docs/local-verification.md) |
| `release_version.py` | `check` / `show` 读取四处版本；`set vX.Y.Z` 修改两份 pyproject.toml 和两份 uv.lock | [版本与发布](../docs/release.md) |
| `ghwf.py` | 触发或恢复一个 Workflow Run，收集日志和 Artifact；非交互参数与原入口一致 | [执行与恢复](../docs/ghwf-execution.md)、[下载配置](../docs/ghwf-downloads.md) |
| `gh_workflow_artifacts.py` | `ghwf.py` 的旧名称兼容入口，现有 PowerShell 函数可继续使用 | [统一 CLI](../docs/unified-cli.md) |
| `manual_release.py` | `prepare` / `evidence` / `verify` / `publish`：供 release.yml 调用的候选校验、证据核验及发布操作 | [发布流程与证据](../docs/release.md) |
| `release_manifest.py` | `tools --scope core\|ruff --output FILE` 记录工具；`facts --directory DIR --output FILE` 整理已有证据，不重跑测试 | [发布流程与证据](../docs/release.md) |
| `dependency_security.py` | `--project DIR --mode off\|report\|enforce --out DIR`；锁一致性与已知漏洞结果分别记录 | [依赖安全](../docs/dependency-security.md) |
| `ci_performance.py` | `--repo OWNER/REPO --run RUN_ID --out FILE`；读取实际 Run 测量数据，可传 `--dagger-log FILE` | [性能测量](../docs/ci-performance.md) |
| `sync_consumer.py` | `--consumer PATH` 默认只显示引用更新计划；`--apply` 更新引用并运行 Dagger，`--commit` / `--push` 显式提交和推送 | 下方消费者同步说明 |
| `ghwf_e2e.py` | 真实网络验证入口：默认要求 `--resume RUN_ID`；只有显式 `--allow-dispatch` 才允许新 Dispatch | [执行测试](../docs/ghwf-execution.md) |

表中的 FILE、DIR、RUN_ID、OWNER/REPO 和版本占位符必须替换为实际值。
各入口的完整参数以 `python scripts/对应脚本.py --help` 为准；需要依赖时使用
`uv run --locked python`。专项工具未全部并入统一 CLI，不要假定存在同名 CLI 子命令。

## 日常检查与版本准备

```sh
python scripts/checks.py ruff
python scripts/checks.py tests --coverage
python scripts/checks.py version lock
python scripts/checks.py inspect .test-tools/checks/pr.json
python scripts/release_version.py check
python scripts/release_version.py show
```

`--coverage` 在同一次 pytest 调用中生成既有 JUnit 和 Coverage；本地 JSON 不复制日志。
`inspect` 只查询输入与工具身份，不重新执行 pytest，也不让下一次检查跳过执行。
检查入口区分原始工具退出码与整体结果：失败返回非零，工具缺失返回 127，环境、
过期或不完整检查返回 3，中断返回 130。Windows 的 skipped 不等于完整通过。

准备本轮版本时可以执行 `python scripts/release_version.py set v0.4.11`。
该命令只修改四处版本元数据，不创建 Tag 或 Release。正式发布按
[手动发布流程](../docs/release.md) 由人启动 release.yml；不要把本地检查报告当作发布凭据。
`manual_release.py publish` 具有真实发布副作用，依赖 Workflow 上下文，不能作为
缺少实际 Run/Commit/证据时的本地发布快捷方式。

## 从消费者目录调用 ghwf

在 PowerShell 中定义函数时，可使用锁定的 engineering-ci 环境：

```powershell
function ghwf {
    uv run --project 'D:\workspace\engineering-ci' --locked python 'D:\workspace\engineering-ci\scripts\ghwf.py' @args
}
ghwf --repo dymzz/engineering-ci --workflow release.yml --ref main --show-state
```

函数中的两个路径应指向包含新入口的实际 checkout。旧函数使用
`python 'D:\workspace\engineering-ci\scripts\gh_workflow_artifacts.py' @args` 仍兼容，
但该 Python 环境必须已安装项目依赖。
`--project` 选择依赖环境，不改变调用者当前目录；ghwf 省略 `--repo` 时继续按
当前目录 Git origin 和既有缓存规则识别仓库，`--out` 的相对路径也属于调用者目录。

恢复已有 Run 时，使用实际 ID 和该任务的实际 ref：

```sh
uv run --locked python scripts/ghwf.py --repo dymzz/engineering-ci --workflow release.yml --ref main --resume RUN_ID --save-log --out workflow-artifacts
```

省略 `--resume` 的普通执行路径可能发起 Dispatch；`--show-state` 查询状态，
`--resume RUN_ID` 恢复既有 Run，`--new-run` 显式创建新任务。UNKNOWN 状态下不得
自动再次 Dispatch，应先确认远端 Run ID。下载的配置优先级仍为 CLI > 环境变量 >
仓库自身 config/ghwf.local.toml > 默认值；Windows/Linux 环境示例见下载文档。

交互选择 Workflow、Run、Artifact 使用统一 CLI，而非短脚本的额外参数：

```sh
uv run --locked engineering-ci workflow --interactive --repo dymzz/engineering-ci
```

## 消费者同步与专项验证

`sync_consumer.py` 的 `--consumer` 相对 engineering-ci checkout 定位。默认只读显示
更新计划，但需要联网查询 Git 远端；源仓库须处于指定分支（默认 main）、工作区干净，
且 HEAD 与远端该分支一致。

```sh
python scripts/sync_consumer.py --consumer ../engineering-ci-smoke
```

`--apply` 会修改消费者 Workflow/Dagger 引用、执行 dagger develop 和 dagger check，
需 Dagger 与 Docker；`--commit` 必须同时使用 `--apply`，`--push` 必须同时使用
`--commit`。消费者有未提交修改时拒绝覆盖；推送还要求消费者处于指定分支并已同步
远端。它按源分支实际提交同步，不等同于 Renovate 的正式版本更新流程。

`ci_performance.py` 通过已认证的 gh 读取已有 Run，不触发测试；
`ghwf_e2e.py` 则实际访问网络并下载输出。用已有 Run 验证时传 `--resume RUN_ID`，
不要为了测试入口而追加 `--allow-dispatch`。依赖安全的 report/enforce 会执行扫描，
网络或扫描错误不能解释为安全通过；完整参数与门禁契约沿用对应专题文档。

## 维护约定

新增、改名或改变脚本参数时，同步更新本索引、对应专题文档及 `--help`，
明确运行目录、依赖、输出、退出状态和实际写入操作。统一调用层的兼容入口不复制
业务实现；Workflow 引用统一模块，脚本名称兼容关系在这里维护。
开发职责约定见 [rules.md](rules.md)。
