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
SciPy Bootstrap/River/Optuna 及自动策略更新仍是后续任务。

## 持久选择 Binding

`workflow select --tool` 通过 Vesryn 的 `vesryn.adapters` 注册加载显式 Provider；
Provider 名称不是可随意填写的标签。默认 `engineering-ci-dagger` 返回仅支持 Dagger
的领域契约，不借用旧 `engineering-ci` Catalog 的同名工具。

每次选择独立生成 `workflow.json`、`provenance.json` 和 `binding.json`，不修改
`pyproject.toml` 或共享配置。Binding schema 1 记录 Provider、Tool ID、契约版本及
摘要、字面参数、Scope、Definition ID、Generation ID 和生成文件位置。开始执行后，
执行目录冻结这三份文件并在 Binding 中加入 Execution ID。摘要用于完整性检查，
不是签名或权限凭据。

调用和恢复共用准入逻辑：

- 校验 Binding schema、摘要、实际加载的 Provider、契约版本与哈希。
- 同时验证选择时的 Scope 与宿主当前 Scope；本 CLI 仅授予工程检查的 `check`
  效果和 Dagger 环境，记录本身不授予更多权限。
- 使用 Vesryn 原有 `load_generation` 校验 Workflow 与 Provenance，再核对两个
  ID、唯一必需步骤、工具、参数及契约。内部有效的另一个 Workflow 也不能替代它。
- `call --binding` 和绑定定义的 `run` 都冻结同一选择；`resume` 在原 Runtime
  执行锁内、状态写入和工具调用之前重新校验执行目录中的 Binding 与 Workflow。
- `status` 只读取记录并检查关联，不执行工具，也不证明当前源码已经通过检查。

底层调度、执行锁、状态机和恢复仍由 Vesryn Runtime 提供；检查证据是否仍匹配
源码、工具和环境仍由原 ChecksAdapter 判断。拒绝准入时不调用工具、不推进执行
状态，也不自动选择最新契约。Schema 1 的合法记录继续使用；此前错误标注 Provider
或关联不一致的记录必须显式重新选择，不能自动改写历史记录。

可操作命令见[生成工作流指南](../../../guides/generated-workflows.md#选择并调用一个领域工具)。

通用契约维护在 Vesryn 的 `docs/architecture/harness/registry.md`。
上级入口：[Harness 集成](README.md)。
