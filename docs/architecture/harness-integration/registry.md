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

## Provider 来源、检查目标与输出归属

ECI-002 之后同一目录不再同时承担三个角色。它们是三个独立值，默认全部相同，
所以现有调用方、既有 Binding 和既有契约摘要完全不变。

| 角色 | 参数 | 含义 |
| --- | --- | --- |
| Provider 来源 | `root` | 提供 Pipeline 策略、检查实现与 Dagger 模块的 engineering-ci checkout |
| 检查目标 | `target` | 实际被指纹化并执行的消费者源码目录 |
| 输出归属 | `output_root` | 本次结果、报告与日志的落盘位置，由调用方批准 |

公开签名（均为关键字参数，默认 `None`，`None` 表示沿用 `root`）：

```python
engineering_ci.workflow_adapters.provider.catalog(root, *, target=None, output_root=None)
engineering_ci.workflow_adapters.provider.ProviderAdapter(root, specs, *, target=None, output_root=None)
engineering_ci.workflow_adapters.checks.ChecksAdapter(root, *, runner=None, executor=None, target=None, output_root=None)
engineering_ci.workflow_adapters.checks.dagger_catalog(root=checks.ROOT, *, runner=None, target=None, output_root=None)
engineering_ci.checks.execute(selectors, *, root=ROOT, target=None, output_root=None, ...)
engineering_ci.dagger_checks.execute(selectors, *, root=checks.ROOT, target=None, output_root=None, ...)
engineering_ci.workflow_adapters.binding.binding_catalog(provider, root, *, target=None, output_root=None)
```

`registry.provider(name, root)` 仍只传递 `root`，因此既有 Vesryn 调用不变。
`binding_catalog` 在给定 target/output_root 时把它们绑定到已加载的适配器，
再由 Provider 传输带进子进程；契约摘要不变。

执行身份记录三者的关系：未分离时 identity 只有 `root`；分离后 `root` 是被检查的
`target`，并新增 `provider.root` 与 `provider.policy_sha256`。因此报告可以直接证明
被检查的是消费者文件，而策略仍由 Provider 解释。Junit 写入 `target`，报告与
证据写入 `output_root`；结果不会写回 Provider 源仓。

Dagger 侧沿用既有语义：模块仍从工作目录（即 Provider checkout）加载，
函数参数 `--source` 才是被挂载并检查的目录。当 target 与 Provider 不同时，
`--source` 取消费者的绝对路径；未分离时仍为 `.`。

消费者不需要携带任何 Provider 配置。`repository-check-*` 另有 `capability`
参数（默认 `/`，即模块自身 checkout），容器把 Provider 的 `config/pipeline.toml`
与 `src/engineering_ci` 覆盖叠加到被检查树上：消费者的文件优先，策略与实现来自
Provider。策略决定仍在本仓库，不下放到 Vesryn。宿主对 Dagger 调用同时传
`--source <target>` 与 `--capability .`；JUnit 解析器同样从 Provider 读取，
消费者无需携带副本。

分离执行只适用于 `TARGET_PORTABLE`（`pytest`、`lock`）——这两个只依赖被检查树
自身的项目文件。`ruff-lint`、`ruff-format`、`version` 的 scope 写的是本仓自身的
文件，对外部 target 没有答案，因此分离执行时以 `environment_error` 明确拒绝，
不产出看似有效的结果。

CLI 入口新增 `--check-target` 与 `--output-root`：

```powershell
engineering-ci checks pytest --check-target D:/workspace/consumer --output-root D:/workspace/consumer/.test-tools --json D:/workspace/consumer/.test-tools/checks.json --executor native
engineering-ci workflow select --tool python.pytest --provider engineering-ci-dagger --check-target D:/workspace/consumer --output-root D:/workspace/consumer/.test-tools
```

`workflow run/resume/evidence` 的执行目录仍由 `--out` 显式给出，
并须位于 `--output-root` 之内，否则以“approved output root”拒绝。
`checks inspect` 直接读取报告内记录的 roots，重新核验同一次分离。

`ToolResult.outputs` 新增 `check_target`，指向本次结果描述的目录；
`report` 与 `evidence` 的 `path` 相对 `output_root`，不再相对 Provider 源仓。
这是领域工具输出契约的新增字段，旧 Binding 需要显式重新选择，不能冒充既有契约。

旧 `python.*`、`uv.lock-check` 等 Tool ID 是项目检查/证据契约，不是裸 CLI 注册。
保留它们及历史 Workflow 身份；统一目录标明 engineering 领域，不批量改写旧记录。
Vesryn 与本仓库均沿用同一 ToolSpec、ToolAdapter、Catalog，不新增平行类型。

Scope 的 repository/task 只传递上下文；环境、实际工具可用性、效果权限和业务约束
决定排除。没有按 repo 名称的通用工具白名单。发现候选不授予权限，正式 CI Gate 和
检查规则仍由 engineering-ci 决定，CLI 的 grant 参数不能代替用户授权或领域批准。

基础 checks、Release 和下载继续不依赖可选 Vesryn。Python 依赖保持单向，两个
Dagger 模块不互相导入；调用结果和 JSON 交付仍是独立事实。
SciPy Bootstrap/River/Optuna 及自动策略更新仍是后续任务。

## 独立安装：Provider 资产入口

安装后的包不再需要开发者的 checkout。原先 `pipeline.py` 在导入期就读取
`ROOT/config/pipeline.toml`，而安装位置旁边没有该文件，`import engineering_ci.checks`
会直接失败——本地一直用的是 editable 安装，所以这条路径从未被验证过。

现在策略改为**从显式的 Provider root 解析**：

| 模块 | 改动 |
| --- | --- |
| `pipeline.py` | 移除导入期加载；新增 `load_root()` / `bind()`；`PIPELINE` 初始为 `None` |
| `toolchain.py` | 工具链版本随 policy 发布，不再导入期求值 |
| `checks.py` | 新增 `publish()` / `bind()` / `require_policy()` |

检查表仍然写进模块 globals（`report_path()` 等函数读的是裸全局名，PEP 562 的
`__getattr__` 不覆盖这种查找）。`bind(root)` 的语义是"**有就用，没有就保留当前**"，
因此把一个无关的合成目录作为 `root` 传入不会清掉已有策略。
绑定只发生在装配层（`provider.catalog` / `provider.main`），检查库自己不绑定。

`provider_root()` 同时接受两种形态：

- **源码 checkout**：`src/engineering_ci/workflow_adapters/provider.py` 存在
- **解包后的资产目录**：`config/pipeline.toml` 与 `dagger-module.toml` 存在

`ProviderAdapter.request` 只在 checkout 形态下设置 `PYTHONPATH`；安装形态下包本身
即可导入，不再人为插入路径。

### 导出 Provider 资产

```powershell
uv run --locked engineering-ci workflow-adapter-export --out ./provider-assets
# 等价于：
uv run --locked python -m engineering_ci.workflow_adapters.provider export --root . --out ./provider-assets
```

导出内容是 Provider 自己拥有的东西——Pipeline 策略与 Dagger 模块：

```
config/pipeline.toml
dagger-module.toml  dagger.lock  dagger.toml
.dagger/src/**  .dagger/sdk/**  .dagger/pyproject.toml  .dagger/uv.lock
provider-assets.json
```

不含 `.git`、`.venv`、测试夹具或任何开发状态。`provider-assets.json` 记录包版本、
两个 `vesryn.adapters` entry point、逐文件 SHA-256 和整体摘要，供消费方核验
解包内容未变。目标目录必须为空或不存在，避免覆盖已有内容。

消费方解包后把该目录作为 Provider root 传入；安装位置本身既不是检查目标，
也不是默认日志 root。

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
