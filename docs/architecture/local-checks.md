# 本仓原生 Dagger Checks

在 engineering-ci 当前 checkout 执行。Windows 和 Linux 使用同一组 Dagger Functions、
Linux 容器和锁定依赖。需要 Docker Linux Engine 与 `dagger-module.toml` 声明的
Dagger 1.0.0-beta.16；安装见[Dagger 配置](../configuration/dagger.md)。

```sh
dagger check -l --format cli
dagger check
dagger check --engineering-ci --check=lint
dagger check --engineering-ci --check=format-check
dagger check --engineering-ci --check=workflow-analysis
dagger check --engineering-ci --check=tests
dagger check --engineering-ci --check=version --check=lock
```

默认六项原生 Dagger 检查针对根仓实际源码：Ruff Lint、Ruff Format、
Workflow 分析、核心 pytest、四处版本一致性、两份 uv.lock 完整性。
Workflow 分析使用固定版本 actionlint，覆盖 `.github/workflows` 下全部
Workflow 文件；覆盖范围、输入清点与强制判定见
[Workflow 与 PowerShell 分析](testing/script-analysis.md)。
单项筛选使用 Dagger 原生选择器，不存在宿主 `checks` 命令、
Profile 注册表、`--reuse` 或历史 passed JSON 准入。

## 定义与输入

`.dagger/src/engineering_ci/main.py` 的 `@check` 声明检查；`repository.py` 只构造共享容器。
Dagger 自行管理依赖图、并发、目录输入和执行缓存，不在宿主复制调度或计算内容指纹。
未提交和未跟踪的源码属于当前目录输入；修改后下次调用由 Dagger 重新计算对应节点。
执行过程中继续编辑时，应在编辑完成后重新调用检查；先前快照的成功不证明后续内容通过。
正式发布始终要求真实 Workflow 的 Commit SHA、Run Attempt 和发布证据，不使用本地历史记录放行。

根 `pyproject.toml` 保存标准 Ruff 配置和锁定开发依赖。先复制清单与 uv.lock、准备依赖，
再加入源码；`.git`、虚拟环境、机器下载配置、认证目录和本地输出不进入检查容器。
只有 pytest 分支安装 Git 与 gh，用于原生命令及本地模拟 Release API 测试；
测试不会触发真实 Workflow。其他检查分支不安装这些工具，下载不再依赖 aria2。

## 原始退出与报告

检查失败通过 Dagger 的非零退出传播。报告可成功生成一份失败结果，不能将检查变绿。
需要 Coverage 与 JUnit 时显式使用原生 release 环境：

```sh
dagger --env release check --engineering-ci --check=tests
dagger --env release api call repository-test-artifacts export --path .test-tools/core-evidence
```

严格 `tests` 与报告导出引用同一个 pytest 容器节点；输入和设置相同时复用 Dagger 执行缓存，
不为 JSON、Coverage 或 Summary 再运行 pytest。失败时也可单独导出 bundle，
`exit-code.txt` 保留原退出码，`ci.py gate` 可核验导出的原始事实。
`core-junit.xml`、`core-coverage.xml`、`core-coverage.json` 保留 Release Artifact 名称；
Coverage 是显式开关，根仓没有额外增加覆盖率阈值。

发布用 fixture 单独放在 `dagger.toml` 的 `fixtures` 环境，通过
`dagger --env fixtures check --module validation --check=fixture-test` 等明确检查名 选择；默认根仓检查不运行它们。
静态分析 fixture 使用 `--check=fixture-static-analysis`，其原生退出语义见
[Python 静态分析](testing/python-static-analysis.md)。
公共消费者继续调用既有 `python-*` Functions，见[公共接口](../delivery/public-components.md)。

PR CI 的既有 Job 调用以上原生定义与 Windows PowerShell 分析；YAML 管远端依赖与权限，Python 不再镜像整份 Workflow。
PowerShell 分析在 `windows-2025` Job 中执行，规则与环境只在该 Runner 上成立；
本仓同时是该能力的宿主，因此直接调用仓内实现，不再检出第二份 helper。
依赖安全扫描、Python Matrix 和四个静态分析检查仍由正式发布的显式策略调用，未在默认检查中暗中增加。

## 必需检查的定义与绑定

`src/engineering_ci/requirements.py` 是唯一来源：每项必需检查记录其键、
执行它的 Job 与 step、它覆盖的输入范围，以及它是否是一个原生 Dagger 检查。
发布证据契约、CI Workflow 与漂移测试都读取该表，因此新增或重新划定范围
只需改一处，再由机器校验绑定关系。

`tests/test_required_checks.py` 负责机器校验：

- 每项必需检查在其所属 Workflow 中确实存在对应的 Job 与 step；
- 每项必需的原生 Dagger 检查确实被声明并被该 step 选中；
- 声明范围与 Workflow 实际传入的参数一致；
- 仓内每个 `.ps1`/`.psm1` 要么在声明范围内，要么在显式排除清单内，
  新增脚本不会静默漏检；
- 共享排除清单不会遮蔽必需范围内的文件，否则被检输入集会被静默缩小。

必需检查的定义变化因此由 PR 检查发现，而不是等到正式发布才暴露。
