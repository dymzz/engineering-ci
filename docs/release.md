# 本地优先开发与手动发布

当前定位为个人开发：本地获得完整反馈，GitHub 提供快速 PR 检查，
正式版本由人主动发起完整验收。无需团队审批环境、常驻服务或额外调度器。
功能 PR 由人审查和合并；本地通过不能替代正式发布验收。

| 触发 | 实际执行 | 发布 |
|---|---|---|
| PR / push 到 main | 既有 Ruff fixture + 发布脚本 Lint/Format、全部核心 pytest、四处版本检查、根项目 uv lock --check | 不发布 |
| 手动 Release，publish=false | 上述检查加 Coverage 证据、完整 Dagger checks、既有质量与 Matrix fixture、真实根项目安全 enforce、报告下载核验 | 不创建 Tag/Release |
| 手动 Release，publish=true | 同一完整验收；main SHA 与版本检查通过后才发布 | 精确提交 |

PR Ruff 保留原有 tests/fixtures/python-app 范围，并覆盖发布脚本和测试；
不是全仓库格式化迁移。全部 tests/ pytest 仍执行，包括真实 pytest 子进程、
下载与恢复测试；Linux 安装 aria2 执行既有 HTTP 集成测试。
PR 不启动 Dagger，不做漏洞扫描或完整 Matrix。
30–60 秒是目标；排队、解释器安装、apt 更新会影响实际耗时，不以删测试达到目标。

## 本地命令

仓库根目录执行，需 uv；根项目要求 Python 3.14。PowerShell / Bash 均可使用：

```sh
python scripts/checks.py pr --json .test-tools/checks/pr.json
python scripts/checks.py ruff
python scripts/checks.py tests --coverage
python scripts/checks.py version lock
python scripts/checks.py inspect .test-tools/checks/pr.json
```

GitHub PR CI 调用 `python -m engineering_ci.cli checks ...`，与上述兼容入口
使用同一套 Python 定义。工具版本由 `src/engineering_ci/toolchain.py` 提供；
Ruff 范围、参数和检查名称在 `src/engineering_ci/checks.py` 中集中维护。
直接调用 uv、pytest、Ruff 仍可用于诊断，不限制工具的正常使用。

原生 Windows 的 Bash 测试无法完整执行时，pytest 原始退出码和 skipped 数量
保留在结果中，入口返回 3，不能将此记录作为完整 PR 通过。缺少 aria2c、Git、gh
时明确报告工具缺失。完整 Linux 验证可在 WSL2 中使用相同入口。
详细环境准备、JSON 状态与历史结果校验见 [本地验证](local-verification.md)。

完整检查需 Docker 与 Dagger CLI 0.21.10（匹配 dagger.json）：

```sh
dagger check
dagger call fixture-quality-gates
dagger call fixture-python-matrix
dagger develop
uv lock --project .dagger --check
```

quality fixture 验证既有显式阈值与失败传播；matrix fixture 实际运行
Python 3.12、3.13、3.14 及既有负向场景。它们验证模块能力，不能解释成根项目
（requires-python >=3.14）支持 Python 3.12。核心 pytest 使用 3.14。
不新增或降低阈值；根项目 Coverage 输出是证据，不增加百分比门禁。
默认一次 dagger check 已包含两个 fixture，无需每次重复单独调用。

真实项目安全检查独立于锁一致性：

```sh
uv run --locked python scripts/dependency_security.py --project . --mode enforce --public-index https://mirrors.aliyun.com/pypi/simple --out .test-tools/security --cache .test-tools/security-bin
```

发布扫描真实根 uv.lock 的公共 PyPI 锁定依赖（含 dev / 传递依赖），
不使用 dependency-security fixture。Aliyun 显式声明为公共 PyPI 镜像。
enforce 阻断所有未豁免漏洞、网络/扫描错误和扫描缺口；不自动增加豁免或改锁。
本轮真实扫描发现 pytest 8.4.2 的 tmpdir 漏洞，因此将根测试依赖升级到
最小修复版本 9.0.3，并重跑全部测试。
上游记录：[GHSA-6w46-j5rx-g56g](https://osv.dev/vulnerability/GHSA-6w46-j5rx-g56g)。
.dagger/uv.lock 含本地生成 SDK，现有扫描器无法完整扫描该来源；
本轮只对该锁文件执行一致性校验，不能宣称完成其漏洞扫描。
这是发布安全范围的明确限制。消费者默认安全策略 off 不变。

## 版本与人工发布

1. 隔离分支执行 `python scripts/release_version.py set v0.4.11`，
   对齐两份 pyproject.toml 与两份 uv.lock。
2. 一个功能 PR，轻量检查通过后人工审查、合并。不要手工推 Tag。
3. 合并后从 main 手动验证，默认不发布：

```sh
gh workflow run release.yml --repo dymzz/engineering-ci --ref main -f version=v0.4.11 -f publish=false -f pytest_scenario=pass
gh run list --repo dymzz/engineering-ci --workflow release.yml --limit 5
gh run view RUN_ID --repo dymzz/engineering-ci
```

4. 核对完整验收、Artifact 与 SHA。人工决定正式发布时：

```sh
gh workflow run release.yml --repo dymzz/engineering-ci --ref main -f version=v0.4.11 -f publish=true -f pytest_scenario=pass
```

正式发布再次完整验收，不借用另一轮验证结果。无需额外团队审批层。
发布要求 workflow_dispatch、refs/heads/main、pass 场景；
候选分支只能验证 publish=false。GitHub 页面 Run workflow 使用同样输入。

安全负向验证：候选分支使用 pytest_scenario=assertion、publish=false，
真实 pytest 退出 1，发布验收失败，已有 Artifact 保留，不创建 Tag/Release。
完整验收没有自动 push/PR 触发路径。

旧 ci.yml 的 pytest_probe_scenario / quality_probe_scenario / matrix_probe_scenario
不再作为 workflow_dispatch 输入。报告与负向门禁函数全部保留，可以本地直接调用
fixture-pytest-artifacts、fixture-quality-artifacts、fixture-matrix-artifacts
并传入原有 --scenario；发布路径统一使用上述 pass / assertion 输入。
ci-performance.md 中的 v0.4.7 数字是旧完整 CI 基线，不是当前 PR 工作负载。

## 保护与恢复

完整验收运行既有全部 Dagger checks，包括断言失败传播、13 个质量场景、
三版本 Matrix 的正负场景；Probe 被跳过不能代表通过。
单次 pytest bundle 只替换精确的 fixture-test，其余 check 不变。
核心 JUnit/Coverage、Dagger pytest、安全 Artifact 必须下载并核验；
成功生成报告不能把测试失败变成成功。

验证阶段只有 contents:read，最后 publish job 才有 contents:write。
必需 job 必须实际 success，skipped / cancelled / 缺失均拒绝。
main 前进、版本不一致、Tag 指向其他 SHA、报告缺失或检查失败均不发布。
发布 workflow 串行执行；Tag 仅使用 create-only API，禁止覆盖。

同 SHA 的 Tag 已创建、Release 未创建时，重新人工运行可安全恢复；
上传或 API 失败会留下可恢复的 Tag / Draft，不会提前转为正式版本。
已有同 SHA 的正式 Release 必须有一致的 Manifest，才返回原 URL，不重复创建。
已有正式版本缺少或冲突的证据时明确失败，不补写、覆盖历史版本。
祖先提交上的同版本 Tag 不能代替当前提交验证。注解 Tag 比较最终 commit SHA。

## v0.4.9 发布证据

每次手动验证将 Actions 原始 job / step 结果写入
`release-manifest.json`，保存为 `release-manifest` Artifact（30 天）；
Job Summary 同时显示版本、SHA、验证 Run 链接与各检查状态。
机器可读结构见 [JSON Schema](release-manifest.schema.json)。
同一组输入产生相同核心 JSON；不复制日志、环境变量、Token 或临时下载 URL。

身份来自实际 checkout、四处版本元数据、GitHub Run API 与当前 attempt 的
Jobs API。Manifest 的 `result=passed` 表示必需验证结果与证据齐全，
不是进行中的整个 Workflow 已结束，也不是已经发布成功。
`mode=validation` 时 `release.release_url=null`：没有虚构 Release URL。
正式证据中的 `release_url` 使用 GitHub 实际返回的 Release API 资源 URL，
其 ID 在 Draft 转正式时保持稳定；Workflow Summary 提供正式页面链接。
失败验证可保存 `result=failed` 的 Manifest；API 不可用时保留失败 job 日志，
不能伪造缺失状态。检查失败、skipped、cancelled、timed_out、unavailable
都阻断发布。可选未启用项目记为 `not_enabled`。

Ruff、核心 pytest、版本锁一致性、安全 enforce、Dagger 与原始 pytest Gate
关联既有 job / step；Coverage / Python Matrix 关联实际执行清单中的
`fixture-quality-gates` / `fixture-python-matrix`，不再执行一次。
它们是既有模块能力 fixture，不能当作根项目多版本兼容或根 Coverage 阈值。
根 Coverage 无阈值和 Smoke 专用负向 Probe 都明确未启用。

工具记录读取实际环境：根 Python / uv / pytest / pytest-cov、Ruff runner
的 uv / Ruff、既有 `dagger version`、已核验的 OSV Scanner 版本。
scope 区分执行环境；不把根工具版本当作 Dagger 容器内部的版本。
Artifact 条目仅保留原生 ID、可用 digest 和 API 元数据链接。
Artifact API 不提供 attempt，因此证据下载使用本次生产步骤的原生
`artifact-id`，经 job / reusable workflow outputs 传递；不按同名文件猜测，
不枚举整个 Run 后选最新条目。API 另行核对 Artifact ID、名称、Run 与 SHA，
Jobs API 核对当前 attempt 和 SHA。facts、Manifest 也用精确 ID 下载。
只重跑部分 job 时，缺少当前 attempt 的检查或输出会拒绝；请完整重跑
或重新手动验证。验证 Artifact 有保留期；正式 Release 的 Manifest Asset
提供持续追溯入口。重复发布还需要原始 Actions 元数据；历史记录删除导致
无法核验时明确拒绝，不修改已发布版本。

Schema 约束字段、固定检查来源、状态、工具 scope/name 和必需证据数量；
Python 校验进一步核对版本与 Tag、仓库与 URL、SHA 及原始 API 结果等
跨字段和远端事实。通过 Schema 不等于已有可信的发布验证。

正式发布沿用完整验证与 create-only Tag，然后用 GitHub 原生自动 Release Notes
创建 Draft。填入实际 Release URL，上传 Manifest，再下载并核验原始检查、
版本 / SHA 和上传内容，最后才将 Draft 转为正式 Release。
任一步失败均不覆盖 Tag 或已上传 Asset，也不伪造成功；修复后重新人工运行。
GitHub 上游失败可能留下 size=0、state=starter 的空 Asset；恢复仅在再次
核对同一 SHA / Tag / Draft，并读回确认仍是该空 Asset 后删除并重新上传。
正式 Release 或已上传文件不执行此清理。读回失败、权限不足、上传错误均
阻断转正式；HTTP 错误只保留安全状态码，不输出原始响应。
同 SHA 的 Draft 已有有效 Manifest 时保留原始验证 Run 的证据并核验后恢复；
重复正式发布同样验证原记录，不修改已有版本。
只有 publish job 有写权限；证据收集不新增 pytest / Dagger 调用。
完整检查与 Artifact 上传仍可能因外部服务失败而拒绝发布。

验证层次与剩余风险见 [v0.4.9 审查记录](release-review-v0.4.9.md)。

GitHub 无法原子绑定 main 比较与 Release 创建。验证完成、创建 Tag 前、
创建 Release 前都会核对 main；最后 API 调用仍有极短竞争窗口。
发布期间避免再推 main；变更后重新验证，不强推或自动撤销 Tag。

## 兼容性与状态检查

Smoke / OBridge 的共享 workflow 调用与 Dagger API 不变。
新增可选 verify_module_lock 默认 false，仅主仓库发布启用。
安全 helper 默认保留已发布 v0.4.7，本仓库发布显式使用候选 SHA。
Renovate 仍检测正式 Tag。本轮不新增 Consumer Contract、跨仓库候选检查、
业务代码修改、自动合并或额外发布框架。

PR 保留 sync-tool-tests、version-metadata、python-ci / checks 三个真实状态名。
python-ci / dependency-security 移到手动发布；
若是 Required Check，需移除旧 PR 要求，不能提供假的成功占位。
当前私有仓库套餐的 branch protection / rulesets 返回 403，无法启用 Required Checks；
须人工核对三个状态后合并，不能宣称服务器已阻断失败合并。


## 统一模块与 CLI

Actions 通过 `engineering_ci.cli` 调用 `release prepare/verify/publish` 与
`manifest facts/tools`；GitHub Actions 继续定义并执行发布 DAG。
Release 业务模块位于 `src/engineering_ci/release/`，原 scripts 只做兼容转发。
发布 Manifest Schema 与发布准入语义保持不变。

交互式 Workflow、Run 和 Artifact 名称选择，以及 Codex 非交互调用，见
[统一 CLI](unified-cli.md)。ghwf 执行 FSM、缓存、Run ID 恢复和下载后端保持兼容。
