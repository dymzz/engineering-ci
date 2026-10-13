# 入口维护约定

- scripts 只保留 release.py、download.py 两个用户 CLI；检查和 Workflow 内部助手不放 scripts。
- release.py publish 贯穿本地检查、提交、Push、同一 PR 与人工合并停点，合并后请求并等待现有 release.yml 完成正式发布。status 统一查询停点；validate 主动请求独立验证；verify 只读指定 Run。download.py 只附着已有 Run 并收集文件。请求执行与恢复继续复用 execution。
- 用户说明见指南入口，文档组织与维护遵循[文档约定](../docs/conventions.md)。参数或行为变化须同步受影响的指南及 PR 描述，不在入口索引重写操作流程。
- 两个脚本无参数只显示帮助；交互下载显式使用 --interactive。帮助不依赖已安装的运行依赖。
- GitHub Actions 的内部调用集中在 engineering_ci.workflow_actions；YAML 是发布 DAG 的唯一来源。
- 检查定义集中在 config/pipeline.toml，checks.py 执行工具，toolchain.py 提供兼容版本接口；发布策略和证据位于 release/，专项工具位于 tools/。
- CLI、Workflow dispatcher 或注册表不得重新实现业务逻辑。
- 修改入口、参数或位置时同步维护受影响的指南和 Workflow 调用；scripts/README.md 仅维护入口索引。
- 本地交付 FSM 与 Pipeline 必须共享同一 ExecutionContext，使用前一阶段产生且身份匹配的证据作为下一阶段准入；远端 DAG 继续由 Workflow 定义，不在配置中复制。
- 工作区、Git/gh 认证与失败处理遵循根目录 [AGENTS.md](../AGENTS.md)；不得用替代 checkout 绕过阻碍。
