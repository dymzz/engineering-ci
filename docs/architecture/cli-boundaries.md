# CLI 与 Workflow 调用边界

## 职责

```text
scripts/release.py → cli/release.py → Release 身份与证据核验 / gh API / gh run watch
scripts/download.py → cli/download.py → gh run view / gh run download
GitHub Actions → workflow_actions.py → Release / Manifest / Security 内部操作
dagger check → 原生 Checks → Dagger 执行图、容器与缓存
```

用户 CLI 为 `engineering-ci version / release / download`；两个脚本只是对应入口。
Git add、commit、push、PR 创建、审查与合并由原生 Git / gh 完成。
不维护本地交付 FSM、通用 Coordinator、StateStore、阶段收据或第二套检查调度。
GitHub Actions 定义发布 Job DAG、权限和门禁；内部 Actions 操作不是用户发布请求。

## Release 请求与查询

`publish` 请求 main 上的正式发布 Workflow；`validate` 在显式 ref 上请求非发布验证。
请求前读取远端实际 SHA，核对四份元数据文件中的第一方版本（含模块共享依赖）与 Tag 冲突。获得 Run ID 后再核对该 Run 的
Workflow、仓库、版本与提交；不按最新 Run 推测请求结果。

`status` / `verify` 对显式 Run 只读，`wait` 使用 `gh run watch --exit-status` 后核验同一 Run。
等待前后 SHA 或 attempt 变化时拒绝沿用结果。权限不足时报告失败，不换认证，不另建轮询引擎。
验证成功与发布成功分别报告：只有实际 Release、Tag、Manifest 和原始 Actions 证据一致，才是 `published`。
原始 Manifest 发布准入保持在[发布机制](release/workflow.md)，CLI 不复制它。

## 一次性 Dispatch 保护

`release/request.py` 只保存一项具体请求的意图：仓库、版本、Tag、SHA、ref、模式、场景、Run ID。
文件位于调用目录的 `.test-tools/release-{mode}-v{version}.json`；排他创建先于唯一一次 POST。
明确 4xx 拒绝（408 除外）移除该意图，允许修正；204、响应丢失、格式错误、5xx 或中断均保留未决意图并停止。
有效 200 响应的 Run ID 立即原子保存。已知 Run 复用该 ID；身份不同、文件损坏或结果未知均不重发。
这不是任务历史库，不读取或迁移旧 execution 状态。跨目录、跨机器不能依靠它防止重复请求。
不保存 Token、原始 HTTP 响应或签名 URL。

## 结果与文件边界

Release JSON 包含 `operation / repository / version / result / publication / exit_code / next_commands`，
已知 Run 时包含 ID、URL 及已确认的 SHA、attempt、状态和结论。未知结果给出意图位置与按 SHA 检索的命令，
候选必须由操作者确认，不能自动选取。工具失败保持非零；请求受理的 0 不能作为已发布证明。

Download 仅操作显式指定或菜单选定的已有 Run，无 POST 路径。日志固定 attempt，
Artifact 使用 gh 原生 Run 范围（API 不提供 attempt 过滤），结果明确标注 `artifact_scope=run`。
一般下载不能替代发布 Manifest 的精确 Artifact ID / attempt 核验。

文件位于 `--out/RUN_ID/`；日志通过临时文件成功后创建 `run.log`，拒绝覆盖已有日志；
Artifact 的网络传输、解压和原生覆盖规则交给 `gh run download`。不再管理 ZIP 下载、分段、
Backend、资源收据或字节续传。失败后按明确资源重新下载；选择新的输出目录避免混入上次部分文件。
日志先保存，Artifact 下载失败不删除已经成功取得的日志。

用户参数、退出码和操作顺序分别见[CLI 参考](../../guides/cli-reference.md)及[常用操作](../../guides/common-tasks.md)。
