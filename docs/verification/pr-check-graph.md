# PR 检查执行图验证

## 范围与实现

2026-10-14，基于 `f59c6ec0950cc98478f7c26c82a5f1c938f376b9`，目标 v0.6.6。
保留 `repository-check-artifacts` 和宿主 CLI 请求 / 证据契约。
Dagger 构建共享准备、独立单项执行和汇总目录依赖；Python 不调度多项容器检查。
原生宿主检查循环及 GitHub Job DAG 保留，默认 fixture Checks 没有新增核心 pytest。

## 单元与协议测试

225 tests passed。覆盖选择去重、确定性日志顺序、JUnit / Coverage 复制、
原始失败与信号退出、失败兼过期、缺少证据、错误身份 / 参数、伪造通过、
工具缺失分类及现有证据复用。聚合测试显式拒绝任何再次调用检查执行器。

## 真实负向与恢复

在主 checkout 临时创建唯一 `tests/test_pr_graph_negative_probe.py`，内容为失败断言。
未修改其他测试，也没有改变配置或降低检查条件。

```sh
uv run --locked engineering-ci checks tests --executor dagger \
  --json .test-tools/checks/pr-graph-negative.json
uv run --locked engineering-ci checks tests --executor dagger --reuse \
  --json .test-tools/checks/pr-graph-negative.json
```

- 原始执行：1 expected failure、1079 passed、105 subtests passed。
- CLI / JSON 原始退出码均为 1，JUnit 和日志保留。
- 原始日志中 pytest 调用恰好一次；同输入恢复返回 1，`reused=true`、
  `executed_this_invocation=false`、`input_stable=true`，没有新增测试执行。
- 检查内容一致且不是符号链接后，仅移除本轮探针文件；原始失败证据仍保留。

报告 / Artifact 导出成功没有把这次失败变成通过。

## 完整执行与复用

```sh
uv run --locked engineering-ci checks pr --executor dagger --coverage \
  --json .test-tools/checks/pr-graph.json
uv run --locked engineering-ci checks pr --executor dagger --coverage --reuse \
  --json .test-tools/checks/pr-graph.json
```

完整执行 passed：1079 tests、105 subtests passed，五项检查 passed，输入稳定，
整体退出码 0。JUnit / Coverage XML / JSON 完整保存，日志中 pytest 调用恰好一次。
同输入恢复 passed：五项均 reused=true、executed_this_invocation=false，
没有新的检查或 pytest 执行。源、请求、环境或证据变化使复用失效。
本地证据不替代确定提交的真实 PR CI，也不构成正式 Release 验证。
执行契约见[本地检查](../architecture/local-checks.md#dagger-pr-执行图)。

上级入口：[验证记录](README.md)。
