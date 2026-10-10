# ghwf 本轮验证记录

此文保留初次实现的历史证据及旧脚本名。v0.4.11 的当前入口为 release.py、download.py；
E2E 改为 `uv run --locked python -m engineering_ci.tools.workflow_e2e`。
当前使用与旧函数迁移以 [scripts 说明](../scripts/README.md) 为准。

验证日期：2026-10-09（用户时区 Asia/Shanghai）。

## 分阶段证据

| 阶段 | 执行内容 | 结果 |
| --- | --- | --- |
| P0-1 | 状态 / 身份 / 原子写入 / 锁 / UNKNOWN 恢复 / 旧同步工具；保留 gh 原传输 | 22 passed，6 subtests passed |
| P0-1 入口复查 | 原脚本路径 `--help`，再运行状态及同步测试 | 保留全部原参数；22 passed，6 subtests passed |
| P0-2 | 独立运行 HTTP / ZIP 测试；真实 aria2c + 本地支持及不支持 Range 的 Server | 15 passed，10.37s；没有 skip |
| P0-3 | 状态、Client / Collector 整合、CLI / 缓存迁移 | 33 passed |
| P0-3 首轮全量 | 全部现有及新增测试，含实际双进程互斥 | 60 passed，6 subtests passed |
| 最终锁定环境 | 补全旧输出迁移、资源变化和 Windows 缓存目录保护后，全量回归 | **64 passed，13.95s；没有 skip** |

后续阶段均在前一阶段测试通过后开始；中间失败已修正并重新验证，没有将失败结果当作交付。
早期环境的 pytest 单独报告 unittest subtests；最终使用 pytest 8.4.2，其输出不单列这些子场景。

最终执行命令：

```powershell
uv sync --group dev
.venv/Scripts/python.exe -m pytest -q --tb=short
.venv/Scripts/python.exe -m compileall -q src/engineering_ci/execution `
    scripts/gh_workflow_artifacts.py scripts/ghwf_e2e.py
uv lock --check
git diff --check
```

测试输出：

```text
64 passed in 13.95s
```

编译检查和 `git diff --check` 通过；`uv lock --check` 解析 10 个锁定包并通过。
Git 的 LF / CRLF 提示不影响检查结果。

## 验证环境

- Windows，Python 3.14。
- transitions 0.9.3、filelock 3.20.3、pytest 8.4.2：最终回归使用 `uv.lock` 对应 `.venv`。
- aria2c 1.37.0，官方 Windows x64 便携包；保留在本地忽略目录 `.test-tools/aria2/`。
- HTTP Server 绑定 `127.0.0.1`，数据 8 MiB；第一次主动中断连接，第二次使用新的 Adapter 实例。
  Range 模式校验非零偏移请求及最终 SHA256；无 Range 模式校验完整重下及明确降级原因。
- 双进程测试使用 Windows `multiprocessing` spawn，只有一个进程可进入 Dispatch。
- GitHub CLI 及官方请求全部用注入替身，涵盖 POST、422、TLS 故障、302 临时地址、
  Artifact 元数据刷新及 Run attempt 变化；没有真实 GitHub Token / Workflow 请求。

## 额外检查

- `scripts/gh_workflow_artifacts.py --help` 可用，原参数和新增下载配置都存在。
- `scripts/ghwf_e2e.py` 缺少 `--resume` 或显式 `--allow-dispatch` 时在执行前拒绝（退出码 2）。
- JSON Schema 通过 Draft 2020-12 检查；文档示例及正常收集、v1 输出迁移的实际 JSON 检查点
  通过 `Draft202012Validator` 和 `FormatChecker` 验证。
- 没有修改 Dagger 核心、`.github/workflows/` 或消费者 Workflow。
- 没有 Push、Tag、Commit 或真实 GitHub Actions Run。
- 工作过程中出现的 `1.txt.ps1` 删除不属于本次重构，已保持原工作区变化。

## 待人工验收

按 [ghwf-execution.md](ghwf-execution.md) 配置 gh、aria2 和 Python 环境后，先用已有 Run 执行
`uv run --locked python -m engineering_ci.tools.workflow_e2e --resume RUN_ID`，确认认证、官方日志 attempt 地址、真实 Digest、代理 / TLS
和消费者 Artifact 目录。只有人工决定创建新 Run 时，再使用 `--allow-dispatch`。

本轮未运行真实 E2E；GitHub API 线上行为、GHES、共享盘锁、断电和突然强杀尚不能由本地测试证明。
