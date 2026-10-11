# 入口维护约定

- scripts 只保留 release.py、download.py 两个用户 CLI；检查和 Workflow 内部助手不放 scripts。
- release.py 请求并等待现有 release.yml，不初始化本地下载；download.py 只附着已有 Run 并收集文件。执行与恢复继续复用 execution。
- 两个脚本无参数只显示帮助；交互下载显式使用 --interactive。帮助不依赖已安装的运行依赖。
- GitHub Actions 的内部调用集中在 engineering_ci.workflow_actions；YAML 是发布 DAG 的唯一来源。
- 检查定义位于 checks.py，工具版本位于 toolchain.py，发布策略和证据位于 release/，专项工具位于 tools/。
- CLI、Workflow dispatcher 或注册表不得重新实现业务逻辑。
- 修改入口、参数或位置时同步维护 scripts/README.md、对应使用文档和 Workflow 调用。
- 不引入第二份 pipeline 配置重复描述现有 Workflow DAG 与检查定义。有实际需求时再增加配置。
- 工作区、Git/gh 认证与失败处理遵循根目录 AGENT.MD；不得用替代 checkout 绕过阻碍。
