# CI 性能测量与执行复用

性能记录用于比较耗时，不决定测试或安全门禁是否通过。pytest 原始退出码、显式质量策略和安全扫描结果继续决定 CI 状态。

## v0.4.6 基线

采样于 2026-10-10，使用 GitHub hosted Ubuntu 24.04、Dagger 0.21.10。完整时间戳、提交 SHA、步骤和原生 Dagger 观测记录见 [JSON 基线](performance/v0.4.7-baseline.json)。

| 样本 | 检查执行窗口 | 提交到检查完成 | Engine 拉取与启动 | CLI 安装 | Dagger 检查阶段 |
| --- | ---: | ---: | ---: | ---: | ---: |
| [v0.4.6 首个 PR 样本](https://github.com/dymzz/engineering-ci/actions/runs/38032201841) | 152s | 155s | 11s | 1s | 133s |
| [v0.4.6 最终 PR 样本](https://github.com/dymzz/engineering-ci/actions/runs/38032667287) | 162s | 166s | 16s | 2s | 135s |
| [v0.4.6 main](https://github.com/dymzz/engineering-ci/actions/runs/38033929141) | 162s | 165s | 15s | 2s | 136s |
| [Smoke v0.4.5 main](https://github.com/dymzz/engineering-ci-smoke/actions/runs/38027538357) | 123s | 126s | 9s | 2s | 106s |

前三行运行相同的六个核心检查；报告导出替代 `fixture-test`，剩余五个检查保持执行。三次检查窗口中位数为 162s，Dagger 阶段为 135s。Smoke 使用不同的测试项目、Python 3.12 和 coverage 策略，仅作为消费者基线，不能与核心 CI 直接计算性能变化。

基线定位：

1. Dagger 首次 workspace/type definitions 加载约 50–51s（Smoke 59.8s）。这两个原生 span 重叠，不能相加。
2. 核心 Matrix 检查约 65s，包含要求执行的不同 Python 版本及负向 fixture。保留这些执行。
3. 同一 Job 的后续 CLI session 仍有初始化：核心 `check -l` 约 3.2–3.4s，Smoke 11.3s。原生 `uv` 输出也证明报告与剩余检查阶段发生了独立依赖准备，不能只按源码调用次数认定重复。
4. Docker 拉取和启动共 11–16s；CLI 安装 1–2s。安全检查在独立 Job 并行运行约 12–15s，Artifact 上传约 1–2s。

Runner 排队/调度约 3–4s，独立于检查执行窗口。Job 并行资源耗时之和不等于完整 CI 延迟。

## 原生 session 复用

共享工作流将报告导出、检查发现和剩余检查放在一个 `dagger run` 中。使用固定 Dagger 0.21.10 的 `--load-workspace-modules`，在父 session 加载 workspace，再让子 CLI 通过原生 session 环境连接它。这个 flag 在该版本属于隐藏选项；升级 Dagger 时必须重新验证嵌套 CLI 的检查发现、报告导出和退出码。

原生实现可核验：[run.go](https://github.com/dagger/dagger/blob/v0.21.10/cmd/dagger/run.go) 注入 session 并传出子进程退出码；[engine/client/client.go](https://github.com/dagger/dagger/blob/v0.21.10/engine/client/client.go) 连接已存在的 session。

报告模式仍只导出一次测试结果；精确检查名验证、剩余检查计划、独立 pytest gate、Summary 和 Artifact 交付规则继续生效。默认模式仍执行全部检查。计时包装返回原始命令退出码，计时字段不参与门禁判定。

复用边界：

- 每个 hosted Job 仍创建自己的 Engine/volume；旧实现已经在同一 Job 使用同一个 Engine。
- 新实现延长同一 session 内图与模块状态的存活，不导入跨 Job Engine 缓存。
- `_uv_container()` 和 `uv sync --locked` 保持原样。不同源码、锁文件、Python 版本的原生图身份分别计算，不跨 Python 版本共享虚拟环境。
- 没有新增缓存存储、依赖下载器或凭据缓存。session token 只由 Dagger 传给子进程，不写入性能报告。
- 既有安全工具缓存独立于 Dagger；缓存缺失时仍下载并校验工具，安全扫描不会因 Matrix 增加执行次数。

## 复现测量

在 Python 3.11+ 和已登录的 `gh` 环境中：

```bash
# 只发起 GET 请求，不创建 Workflow Run
python scripts/ci_performance.py --repo dymzz/engineering-ci --run RUN_ID --out performance.json

# 可选：附加指定 Dagger Job 的原始日志，以读取原生 span 和 CI_TIMING 标记
gh api --method GET repos/dymzz/engineering-ci/actions/jobs/JOB_ID/logs > dagger.log
python scripts/ci_performance.py --repo dymzz/engineering-ci --run RUN_ID --dagger-log dagger.log --out performance.json
```

PowerShell 7 同样可运行上述命令。Windows PowerShell 5.1 保存文本日志时指定 UTF-8，避免默认 UTF-16 编码。原始日志留在本地，不提交到 Git。

JSON 区分：

- `checks_execution_window_seconds`：最早 Job 开始到最后 Job 完成。
- `submission_to_checks_complete_seconds`：Run 创建到最后 Job 完成，包含排队/调度。
- `parallel_runner_seconds`：并行资源时间之和。
- `first_failed_step_completion_seconds`：API 中第一个失败步骤完成时刻，实时诊断可能更早；成功样本为 `null`。
- `dagger.commands`：报告导出、发现检查、执行检查的 `CI_TIMING` 耗时和原始命令退出码。
- `dagger.native_spans` / `uv_preparations_observed`：选定的原生观测；嵌套或并行项不能求和当作完整耗时，丢弃的进度日志可能使观测不完整。

比较时记录 source SHA、检查选择、Python 版本、coverage/security 设置和缓存条件。区分新 Engine 冷启动、同 session 原生图命中、正常 PR；安全工具的 Actions 缓存命中不能代表 Dagger Engine 变暖。优先比较多次同工作负载运行的中位数，不设置默认时间硬门禁。

初始优化收益需由候选 PR 的完整真实 CI 验证后补充。失败路径继续收集完整证据，不为提前出现红灯而取消其他必要检查。
