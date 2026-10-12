# 交付与执行恢复验收

验收范围为 v0.5.4 的交付链、execution FSM、断点和正式 Release 完成判定。
基线是 PR #18 合并提交 `29eff85bdad5ddaf9d927cb0f860fb5430c187b8`，
与其候选提交 `de5ab7144c5af6df2f8c74d0ce5ff2ffcc1e372e` 代码树一致。
执行日期为 2026-10-12；版本元数据保持 v0.5.4。
恢复契约分别见[执行状态](../architecture/execution/state-and-recovery.md)、
[交付准入](../architecture/release/workflow.md#交付阶段与证据准入)和
[操作结果](../architecture/cli-boundaries.md#操作结果与恢复视图)。

## 确认并修复的问题

现有完成检查调用 `verification.published()` 时未传入预期 SHA。
模拟反例使用实际 Manifest 生成、发布校验和读回函数：请求 SHA 为 A，
随后出现另一 SHA B 的合法同版本回执；旧入口仍返回 0。
回归测试在修复前明确失败。完成检查现传入本次已解析的 `commit_sha`，
错配返回非零，JSON 保留原 Workflow 退出码 0、请求 SHA 和未确认发布状态。
这项反例是注入 GitHub 替身的测试，不是线上错误发布记录。

## 进程强杀验收

`tests/test_release_recovery.py` 使用 multiprocessing spawn 创建独立 Python 进程，
在已确认的操作边界调用 `Process.kill()`，随后在另一新进程恢复。
Windows 使用 TerminateProcess，Linux 使用 SIGKILL；不依赖异常处理或 finally 保存断点。
锁、JSON、ZIP 校验/提取、文件以及 Git 操作为真实执行；GitHub 与传输调用使用替身。
交付测试的检查提供者也是替身，不能视为真实质量检查通过。

pytest 临时目录只构造最小 Git 测试仓库和本地 bare remote，不复制工程或改变开发 checkout。
测试复用已有 Coordinator、StateStore、ArtifactCollector 和 Repository 测试替身。

| 强杀点 | 恢复结果 |
| --- | --- |
| Dispatch 意图已 fsync，但尚未原子替换 | 原 VALIDATED JSON 保持可读取；尚未 POST；新进程仅执行一次 POST |
| 已进入 DISPATCHING、响应尚未确定 | 新进程转 UNKNOWN，不再次 POST；显式确认同一 Run ID 后恢复 |
| Run ID 已持久化、正在等待 | 新进程查询同一 Run；允许再次查询，POST 总数保持 1 |
| COLLECTING，日志已完成、Artifact 尚未完成 | 新进程只补 Artifact；已校验日志只传输一次，不重新等待 Run |
| 本地 Commit 成功、尚未 Push | 保留同一 Commit SHA 和完整提交信息，继续 Push / PR |
| Push 成功、尚未创建 PR | 检查证据按测试提供者复用，同一 SHA 交付 PR；原生 up-to-date Push 可重复 |
| PR 已创建，交付进程未完成 | 从原生 Git 和替身的远端 PR 事实恢复 REVIEW.waiting；不再 Commit / Push / 创建 PR |

上述测试同时验证强杀后可重新取得原文件锁、JSON 仍可读取、
已完成文件按原摘要验证，以及状态文件不保存临时签名 URL。

## 自动化验证

修复前已有交付、FSM、CLI、检查证据和发布异常测试：359 tests、19 subtests 通过。
新增进程强杀与 SHA 回执回归验收：43 tests 通过，其中强杀场景为 7 项。
修复后整体恢复链回归：367 tests、19 subtests 通过，没有 skipped。
统一入口的 Ruff Lint / Format、四处版本及 Workflow 一致性、Lockfile 检查通过。
新增测试文件已加入原 Pipeline 的 Ruff 范围，没有新增 CI Job 或发布门禁。

执行命令：

```powershell
uv run --locked pytest -q tests/test_release_recovery.py tests/test_release_verification.py --junitxml=.test-tools/recovery-acceptance/recovery-junit.xml
uv run --locked engineering-ci checks ruff version lock --executor native --json .test-tools/recovery-acceptance/native-checks.json
```

整体回归在上述两个测试模块外，覆盖 test_release_delivery、test_release_lifecycle、
test_release_transport、test_execution_state、test_execution_integration、
test_unified_cli、test_checks 和 test_manual_release；JUnit 为同目录的 final-junit.xml。

保留的 JUnit 和检查 JSON 位于上述忽略目录。后续修复 PR 的候选 SHA、
实际 Linux CI 链接与最终结果记录在 PR 说明中；PR 检查不替代正式 Release 验证。

## 真实只读验证与限制

公开 status 查询当前 v0.5.4 checkout、Git/PR 事实及历史检查点；没有执行 Commit、Push 或 Dispatch。
公开 verify 查询已发布 v0.5.3 的 Run `38195712769`，核对原始 Attempt 1、
提交 `9c2c875af4c42e7e8dd3d024136a5dbcaa12e618` 与正式 Release Asset / Manifest。
真实 URL：[验证 Run](https://github.com/dymzz/engineering-ci/actions/runs/38195712769)、
[v0.5.3 Release](https://github.com/dymzz/engineering-ci/releases/tag/v0.5.3)。
这些只读结果不证明新的 v0.5.4 已发布，也不证明真实 GitHub 中断恢复已演练。

真实 GitHub POST 响应丢失、远端失败后恢复 Draft、线上 Asset 上传中断本轮仍由替身覆盖。
未触发新的 workflow_dispatch、未合并、未创建或覆盖正式 Tag / Release。
进程强杀测试不等同于断电、文件系统损坏或共享盘锁验收。
本地 Dagger 容器执行尚无本轮验收结果；Windows 原生专项测试与 Linux PR CI 分别记录。
