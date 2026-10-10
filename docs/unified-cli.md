# CLI 与 Workflow 调用边界

用户入口与 Workflow 内部操作分开。scripts 只保留 release.py 和 download.py，
旧路径迁移见[scripts 使用说明](../scripts/README.md)。

```text
scripts/release.py  → cli/release.py  → execution Coordinator → release.yml
scripts/download.py → cli/download.py → execution Coordinator（已有 Run）
engineering-ci checks/version → checks.py / release/version.py
GitHub Actions → workflow_actions.py → checks.py / release/ / tools/security.py
专项工具 → python -m engineering_ci.tools.<name>
```

发布的 job 依赖、Coverage、Matrix、Security 与证据收集仍由 YAML 定义。
检查选择与命令集中在 checks.py；Release Manifest 注册表引用同一组 step 名称。
不建立另一套 pipeline 或执行生命周期状态机。

## 用户命令

```sh
uv run --locked engineering-ci checks pr --json .test-tools/checks/pr.json
uv run --locked engineering-ci checks ruff
uv run --locked engineering-ci version check
uv run --locked engineering-ci release verify --version v0.4.11 --ref codex/local-verification-v0.4.10
uv run --locked engineering-ci download --interactive --repo dymzz/engineering-ci
uv run --locked engineering-ci download --repo dymzz/engineering-ci --run RUN_ID --save-log
```

也可用 python -m engineering_ci.cli，需已安装项目或设置 PYTHONPATH。
Release verify 会创建真实验证 Run；publish 明确请求正式发布，只允许 main/pass。
原始失败仍返回非零；完整发布安全检查位于现有 Workflow 的 release/ 业务模块。

## 交互与非交互下载

交互依次选择 Workflow、最近 30 个 Run 与未过期 Artifact 名称。0 收集所有名称，q
取消；不自动挑选默认值。已完成且无 Artifact 时收集日志，未完成时等待并收集最终输出。
同名 Artifact 保持原名称过滤语义，不按最大 ID 猜测最新 attempt。

Codex 使用 --run RUN_ID；ID 必须为实际正整数。真实 API 提供 Workflow ID 与 ref，
--workflow / --ref 仅作约束。无法确认身份时拒绝调用 Coordinator。
--resume RUN_ID 是别名；下载入口拒绝 --input、--new-run 和无 ID 的 --resume。
原生进度恢复、auto/aria2/native、Digest、ZIP 校验与输出目录沿用原有实现。

首次使用安装 gh 并完成认证。省略 repo 时按当前目录 origin 与缓存识别仓库。
交互需要终端；两种模式都只查询/恢复已有 Run。

## Workflow 内部调用

```sh
python3 -m engineering_ci.workflow_actions checks ruff-lint
python3 -m engineering_ci.workflow_actions checks tests --coverage
python3 -m engineering_ci.workflow_actions release prepare
python3 -m engineering_ci.workflow_actions manifest facts --directory .release-evidence --output release-facts/facts.json
```

Actions 设置 PYTHONPATH 为实际 checkout 的 src。内部 release prepare/verify/publish
读取 Actions 上下文、真实 SHA、Run Attempt 和必需证据，不用于本地请求发布。
这些操作不会触发另一轮 pytest 或 Dagger。

普通 Workflow 的底层 Dispatch 参数保留在 engineering_ci.execution.cli，可非交互调用；
有真实 POST 副作用。旧 PowerShell 函数迁移与专项工具调用见入口索引。
