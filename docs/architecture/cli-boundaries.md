# CLI 与 Workflow 调用边界

用户入口与 Workflow 内部操作分开。scripts 只保留 release.py 和 download.py，
当前用户命令与旧路径迁移见[使用说明](../../guides/README.md)。

```text
scripts/release.py publish → release/delivery.py（检查/提交/Push/PR/人工合并停点）→ execution Coordinator → release.yml
scripts/release.py status → 当前 Git/PR、本地结果与已有 execution checkpoint → GitHub GET
scripts/release.py validate → execution Coordinator（独立完整验证请求/等待）→ release.yml
scripts/release.py verify → release/verification.py → GitHub GET（已有 Run 与证据）
scripts/download.py → cli/download.py → execution Coordinator（已有 Run）→ ArtifactCollector
engineering-ci checks/version → checks.py / release/version.py
GitHub Actions → workflow_actions.py → checks.py / release/ / tools/security.py
专项工具 → python -m engineering_ci.tools.<name>
```

发布的 job 依赖、Coverage、Matrix、Security 与证据收集仍由 YAML 定义。
检查版本、范围、参数与依赖集中在[独立 Pipeline 配置](../configuration/pipeline.md)，
checks.py 负责调用既有工具；Release Manifest 引用相同 CI step 名称。
本地交付层次化 FSM 管阶段准入，已有 execution FSM 管 Dispatch、等待和恢复；
两者不替代 Actions 的远端 DAG。

## 公开调用契约

操作步骤、参数示例和退出码统一维护在[使用说明](../../guides/README.md)。
安装后的 `engineering-ci` 和两个脚本调用相同实现；模块运行使用
`python -m engineering_ci.cli`，需要已安装项目或设置 PYTHONPATH。

Release 交付模块读取当前 checkout 和 PR 事实，复用检查定义与结果身份校验，调用原生 Git/gh 完成提交和 PR 交付。
人工合并前输出停点、链接、SHA 和继续命令，不执行 Release Dispatch。合并后才交给已有 Coordinator 请求、等待。
status 使用相同层次化 FSM 从实际 Git / PR 事实识别停点；已有 Run 来自原 execution checkpoint。
本地阶段不新增状态文件，恢复时读取原生 Git / PR 证据；不建立任务注册表。
公开 verify 必须指定实际 Run，只读取远端状态与证据，不调用 Coordinator 或写 FSM。
GitHub Actions 负责完整检查和发布决定，JSON 生成或文件收集不能覆盖原始失败。
Release 不初始化下载后端或读取下载配置；只有 download 负责收集日志及 Artifact。

## 交互与非交互下载

显式 --interactive 后依次选择 Workflow、最近 30 个 Run 与未过期 Artifact 名称。0 收集所有名称，q
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
这些操作不会触发另一轮 pytest 或 Dagger。内部 release verify 生成和校验 Manifest；
公开 release verify 只读已有 Run，不能将两者混作触发命令。

Security 默认 helper ref 仍为已发布的 v0.4.7，调用参数与消费者保持兼容。
匹配的新 helper 使用 workflow_actions security；旧 helper 继续调用其已发布的脚本。
入口只按文件是否存在选择；执行失败直接传播，不切换到另一入口重试。

普通 Workflow 的底层 Dispatch 参数保留在 engineering_ci.execution.cli，可非交互调用；
有真实 POST 副作用。旧 PowerShell 函数迁移与专项工具调用见[使用说明](../../guides/README.md)。
