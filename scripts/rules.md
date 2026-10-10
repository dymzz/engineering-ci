# scripts 开发约定

- 入口用途、参数和兼容名称维护在 [README.md](README.md)，完整契约链接到对应专题文档。
- 统一检查、Release 和 Workflow 的可复用逻辑位于 `src/engineering_ci/`；已有兼容脚本保持轻量。专项脚本是否提取模块按实际维护需要决定，不仅依据代码行数。
- `ghwf.py` 与 `gh_workflow_artifacts.py` 调用同一 execution 入口，执行状态、步骤和恢复由已有 FSM/Coordinator 管理。GitHub Actions 继续负责发布 DAG，不在脚本中再实现远程调度。
- GitHub 认证和 API 调用复用 gh 及既有客户端；HTTP 下载复用 aria2/native 后端，保留资源校验与恢复语义。只在发现实际重复职责时提取共享逻辑。
- 成熟第三方库按实际需要引入，不因入口整理增加额外依赖。需要持续运行的监控功能另行评估，不强行合并到 ghwf。
- 定期任务优先使用 GitHub Actions 或系统任务计划；不提前建设统一服务管理器、消息队列或数据库。
- 参数、运行目录或副作用发生变化时同步更新使用说明与相关验证，保留非交互调用能力和原始失败语义。
