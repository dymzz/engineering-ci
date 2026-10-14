# 统一工具目录与 Dagger Provider

通用工具定义、Scoped Discovery、候选排序和通用调用协调由 Vesryn 统一维护。
engineering-ci 只提供工程检查策略、Dagger 执行和领域结果，不注册第二份共享工具。
Registry 是进程内现有 ToolSpec/Catalog 的装配，不要求中央服务。

## 双向连接

可选 `engineering-ci workflow registry` 委托 Vesryn 的统一目录：

```powershell
engineering-ci workflow registry --transport dagger --dagger-module D:/workspace/vesryn/dagger-tools --vesryn-source D:/workspace/vesryn --repo engineering-ci --grant check search --operation tool.registration-verify
```

共享 Dagger 模块没有 engineering-ci Python/模块依赖，实际契约来自 Vesryn 注册源。
当前共享工具是 Codex Events、Hypothesis、Hyperfine；新增 CLI 三组工具逐组扩展。
本仓库不重复其版本、ToolSpec 或匹配规则。配置中的模块/package source 是显式开发
输入，不是本地 Python 包依赖；包依赖继续使用已核验的不可变 Vesryn Git 提交。

反向路径由显式 `engineering-ci-dagger` entry point 提供现有五项工程检查。
它通过领域仓的公开 CLI Provider 调用显式 primary checkout，复用原 Catalog 与检查
代码；不要求安装的 wheel 隐式携带业务配置，结构化结果与进度输出分开。
Vesryn 通过 Provider 加载 ToolSpec 和绑定，经原 ChecksAdapter 调用 Dagger 执行器。
原 Pipeline、检查结果、报告和领域质量策略继续在本仓库解释。该 Provider 拒绝 native
请求；原 `engineering-ci` entry point 和原 checks/release 的行为保持兼容。

旧 `python.*`、`uv.lock-check` 等 Tool ID 是项目检查/证据契约，不是裸 CLI 注册。
保留它们及历史 Workflow 身份；统一目录标明 engineering 领域，不批量改写旧记录。
Vesryn 与本仓库均沿用同一 ToolSpec、ToolAdapter、Catalog，不新增平行类型。

Scope 的 repository/task 只传递上下文；环境、实际工具可用性、效果权限和业务约束
决定排除。没有按 repo 名称的通用工具白名单。发现候选不授予权限，正式 CI Gate 和
检查规则仍由 engineering-ci 决定，CLI 的 grant 参数不能代替用户授权或领域批准。

基础 checks、Release 和下载继续不依赖可选 Vesryn。Python 依赖保持单向，两个
Dagger 模块不互相导入；调用结果和 JSON 交付仍是独立事实。
持久选择 Binding、SciPy Bootstrap/River/Optuna 及自动策略更新仍是后续任务。

通用契约维护在 Vesryn 的 `docs/architecture/harness/registry.md`。
上级入口：[Harness 集成](README.md)。
