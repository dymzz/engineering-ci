# 独立 Provider 安装验收

本记录对应 [ECI-003 / #36](https://github.com/dymzz/engineering-ci/issues/36)，
在原 [PR #38](https://github.com/dymzz/engineering-ci/pull/38) 收口。
包版本为 `0.6.8` 候选，不声称已经正式发布；公开契约与调用示例见
[Provider 接入](../architecture/harness-integration/registry.md#独立安装provider-资产入口)。

## 已确认原因与修复

- PR 的 Ruff Format 失败来自 assets.py，未调整规则或缩小检查范围。
- 重复绑定配置时替换嵌套对象，使 Release 持有旧配置引用。现在原位刷新嵌套配置，
  同步更新已安装工具链的 policy 引用，保留原身份及值校验。
- Git 忽略 `.test-tools/`，Dagger beta.16 实际导入空源码目录，报 `no source to trust`。
  同一原始导出模块在独立、未忽略目录可显式加载；不是 SDK 字节码导致，也未关闭 trust。
- 交付缺少容器检查代码。资产现包含 `src/engineering_ci`；宿主使用 wheel，
  不把这份容器代码插入宿主 PYTHONPATH。Dagger 以 `--load-module .` 显式加载资产。
- 非 Git 消费者可缺省 Git execute-bit 覆盖；明确的 Git 访问错误仍拒绝。
- 安装模式下 Provider 与目标在容器中分别挂载，避免 Provider 文件污染消费者输入。
  环境预检与实际 Artifact 调用均显式传递 capability；原输入 hash 比较继续生效。
- 容器环境预检原来从消费者路径构造 PYTHONPATH，执行阶段则使用 Provider 路径，
  两份环境身份不同。现在两者使用相同 Provider 导入环境，同时仍在消费者目录探测工具、
  计算输入摘要；未放宽聚合校验或把不同环境当作一致。
- 宿主动态导入 JUnit 解析器时曾向资产写入 `.pyc`。现使用标准库 `runpy.run_path`
  读取原解析器，保留既有分类逻辑，并核对整个 Provider 目录树不产生新增或修改。

## 验证边界

专用目录 `D:/workspace/eci003-acceptance` 经用户单独批准，仅存放独立环境、交付资产、
最小消费者及证据；不创建 Git 仓库，也不替换项目 checkout。
宿主普通 CPython 3.14.3，Dagger CLI/Engine 固定 1.0.0-beta.16。
运行时依赖从项目 lock 导出，未安装可选 Vesryn，未使用 editable 或邻仓源码。

消费者显式声明 pytest 9.0.3 与 pytest-cov 7.0.0；后者是现有工程 pytest 环境探测
要求的工具元数据，即使不请求 Coverage 也要提供。本轮不降低该探测。

| 验证 | 当前证据 |
| --- | --- |
| Pipeline、分离、Dagger 及检查图回归 | 186 passed；JUnit 位于 `.test-tools/eci003/final/targeted-final-junit.xml` |
| 只读解析修复与 checks 回归 | 96 passed；JUnit 位于 `.test-tools/eci003/final/read-only-junit.xml` |
| 独立安装和公开 entry point 发现 | wheel 安装、无 Vesryn、无 PYTHONPATH 注入已核对 |
| 独立正常 Dagger 检查 | 实际消费者 pytest：1 passed，原始退出码 0；executed/complete/current 均为 true，reused=false |
| Provider 只读与输出归属 | 整个目录树无新增或修改，逐文件 hash 未变；报告、JUnit 和工具日志均位于消费者批准的输出目录 |
| 原仓基础 CI | Ruff、版本和锁文件本地通过；当前 Head 的三个 PR Job 结果在 PR #38 维护 |

原始失败与后续执行保存在 `.test-tools/eci003/final/` 及获批消费者的 evidence 目录。
报告生成、环境预检、Artifact 导出与真正检查通过分别记录；不把环境错误移交下游当作完成。

## 本次交付身份与证据

本次独立调用使用 `vesryn.adapters/engineering-ci-dagger`，实际导入位置是
`D:/workspace/eci003-acceptance/env/Lib/site-packages/engineering_ci`。
Provider 为 `assets-v5`，检查目标为 `consumer`，输出为 `evidence-v5`；三者相互独立。
`ToolRuntime.invoke()` 返回的 `DispatchReceipt.results["pytest"]` 保留实际领域结果。

| 交付项 | SHA-256 |
| --- | --- |
| `engineering_ci-0.6.8-py3-none-any.whl` | `48b9f9cbb89e1d4690327ec5102e656a55c7d259eeb3fe0c8b12b7edb3d6eb61` |
| Provider 清单内文件集合 | `f34345e00e0f24f854a7cb25903c556f37c9e3b6f3ce6f7e7a30444c3bd06ded` |
| `engineering-ci-0.6.8-provider-assets.zip` | `457a4aead3596ca4f9e592622f04379525b802f7d1619a0fae48dc03e646234a` |
| `eci003-installed-provider-evidence.zip` | `b606b0d14e9d5bc58bcf57ca780a36cbf79a2550cbc1f4bbee57706a7c5af26f` |
| `python.pytest` ToolSpec | `fd9c63f600f5804ab174ffb5734fb5111e2afe1a11e3f81771eb1a754180687f` |

wheel 位于 `.test-tools/eci003/final/dist-v5/`，完整验收记录为
`D:/workspace/eci003-acceptance/evidence-v5/acceptance.json`。
记录包含报告路径/hash、原始 pytest 退出码、当前输入证据、工具版本和容器身份。
两个 ZIP 同在该 dist 目录：资产包含清单及 181 份原文件，证据包含验收记录、原报告、
JUnit 和日志。交付前已核对全部资产字节与本 PR 源码一致。
实际容器为 Linux Python 3.14.8，镜像摘要是
`python:3.14-slim@sha256:00ebdcac87ebf7fd62576d8bbe8f8f1e94198befcc4e38c4db9671e61e0dfcfe`。
该摘要是本次实际观测，不表示已将可变的基础镜像 tag 改为固定 digest。
Dagger CLI 使用本仓已安装的 beta.16 可执行程序；没有从开发 checkout 加载 Python 代码、
策略或 Dagger 模块，也没有向消费者复制 Provider。

## 复现入口

以下是候选构建流程，不创建 Tag 或 Release。使用新的、已获批准且不被父 Git 仓库忽略的
安装目录；不要用这些命令覆盖已有环境或资产。

```powershell
# 在本 PR checkout 构建。只导出运行时依赖，不安装可选 Harness。
uv build --wheel --out-dir .test-tools/eci003/final/dist-v5
uv export --locked --no-dev --no-emit-project --no-hashes --no-annotate `
  --format requirements-txt --output-file .test-tools/eci003/final/runtime-requirements.txt
uv venv --python 3.14.3 D:/workspace/eci003-acceptance/env
uv pip install --python D:/workspace/eci003-acceptance/env/Scripts/python.exe `
  -r .test-tools/eci003/final/runtime-requirements.txt `
  .test-tools/eci003/final/dist-v5/engineering_ci-0.6.8-py3-none-any.whl

# 用安装后的包显式导出同一候选的模块、策略及容器代码。
& D:/workspace/eci003-acceptance/env/Scripts/python.exe -I `
  -m engineering_ci.workflow_adapters.provider export --root . `
  --out D:/workspace/eci003-acceptance/assets
```

最小消费者只需自己的 `pyproject.toml`、`uv.lock` 与测试文件。本次配置为：

```toml
[project]
name = "consumer-demo"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = []

[dependency-groups]
dev = ["pytest==9.0.3", "pytest-cov==7.0.0"]
```

`tests/test_consumer.py` 包含一个真实的 `assert 1 + 1 == 2` 测试。
先在该消费者目录执行 `uv lock`，然后使用已固定的 Dagger CLI/Engine，调用安装入口：

```powershell
& D:/workspace/eci003-acceptance/env/Scripts/engineering-ci.exe checks pytest `
  --provider-root D:/workspace/eci003-acceptance/assets `
  --check-target D:/workspace/eci003-acceptance/consumer `
  --output-root D:/workspace/eci003-acceptance/evidence `
  --executor dagger --json D:/workspace/eci003-acceptance/evidence/checks.json
```

公共发现/ToolRuntime 调用及签名见[Provider 接入示例](../architecture/harness-integration/registry.md#安装后调用)。
本次实际验收经过该发现和调用链，CLI 是同一领域实现的直接入口。
依赖安装的 Git 源认证、Dagger 模块加载及容器依赖安装属于不同层：本次宿主成功安装固定
Git 依赖；模块来自显式交付资产；最小消费者只安装公共 PyPI 依赖，未注入容器 Secret。
其他消费者若有私有依赖，仍须单独配置该层认证。
