# 入口维护约定

- scripts 只保留 release.py、download.py 两个用户 CLI；检查和 Actions 内部助手不放 scripts。
- release.py 请求、查询或等待既有 Release Workflow，不执行 Git add/commit/push/PR；download.py 只操作已有 Run，不能 Dispatch。
- Git/PR 使用原生命令，检查使用原生 Dagger Checks；不重建宿主执行图、FSM、缓存收据、通用任务 Store 或下载 Backend。
- Dispatch 只发送一次；响应未知时保留该具体请求的必要意图并停止，不自动重发。
- 两个脚本无参数只显示帮助，交互下载显式使用 --interactive；实现位于 cli/，入口不复制业务逻辑。
- GitHub Actions 的内部调用集中在 engineering_ci.workflow_actions；YAML 负责发布 DAG、权限和门禁。
- 发布策略与证据位于 release/，专项工具位于 tools/。不得用一般 Run 下载结果替代精确发布证据。
- 修改入口或参数时同步维护受影响指南和 Workflow 调用；scripts/README.md 仅维护导航。
- 用户说明见[指南入口](../guides/README.md)，维护遵循[文档约定](../docs/conventions.md)。
- 工作区与认证遵循 [AGENTS.md](../AGENTS.md)，遇到阻碍停止相应操作，不创建替代 checkout。
