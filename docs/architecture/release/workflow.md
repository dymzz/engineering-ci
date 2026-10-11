# 发布门禁、证据与恢复

当前定位为个人开发：本地获得完整反馈，GitHub 提供快速 PR 检查，
正式版本由人主动发起完整验收。无需团队审批环境、常驻服务或额外调度器。
功能 PR 由人审查和合并；本地通过不能替代正式发布验收。
本文解释发布机制；用户命令、执行顺序和迁移方式统一维护在[使用说明](../../../guides/README.md)。

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

## 检查定义与执行环境

用户检查命令见[使用说明](../../../guides/common-tasks.md#本地检查与开发-pr)。

GitHub PR CI 调用 `python -m engineering_ci.workflow_actions checks ...`，与上述用户命令
使用同一套 Python 实现。工具版本、范围、参数及依赖由 `config/pipeline.toml` 集中定义；
`toolchain.py` 保留调用接口，`checks.py` 执行工具并产生真实结果。
直接调用 uv、pytest、Ruff 仍可用于诊断，不限制工具的正常使用。

原生 Windows 的 Bash 测试无法完整执行时，pytest 原始退出码和 skipped 数量
保留在结果中，入口返回 3，不能将此记录作为完整 PR 通过。缺少 Git、gh
时明确报告工具缺失。完整 Linux 验证可在 WSL2 中使用相同入口。
详细环境准备、JSON 状态与历史结果校验见 [本地验证](../local-checks.md)。
交付 Pipeline 分别持有代码检查和当前版本证据。版本准备不会无差别丢弃代码检查：
只有本项目四处一致的版本值变化时，保留同环境的 Ruff / pytest 结果，重新核验版本和锁文件。
缺少当前版本证据、源码 / 依赖已变化或结果不完整时仍阻断准入；远程门禁继续绑定实际提交。

完整检查需 Docker 与 Dagger CLI 1.0.0-beta.16（匹配 dagger.json 和 Workflow Engine）。
安装及 SDK 生成方式见 [Dagger 配置](../../configuration/dagger.md)。
Release Workflow 的 `dagger check` 已包含 quality 和 matrix fixture。

quality fixture 验证既有显式阈值与失败传播；matrix fixture 实际运行
Python 3.12、3.13、3.14 及既有负向场景。它们验证模块能力，不能解释成根项目
（requires-python >=3.14）支持 Python 3.12。核心 pytest 使用 3.14。
不新增或降低阈值；根项目 Coverage 输出是证据，不增加百分比门禁。

真实项目安全检查独立于锁一致性：

```sh
uv run --locked python -m engineering_ci.tools.security --project . --mode enforce --public-index https://mirrors.aliyun.com/pypi/simple --out .test-tools/security --cache .test-tools/security-bin
```

发布扫描真实根 uv.lock 的公共 PyPI 锁定依赖（含 dev / 传递依赖），
不使用 dependency-security fixture。Aliyun 显式声明为公共 PyPI 镜像。
enforce 阻断所有未豁免漏洞、网络/扫描错误和扫描缺口；不自动增加豁免或改锁。
本轮真实扫描发现 pytest 8.4.2 的 tmpdir 漏洞，因此将根测试依赖升级到
最小修复版本 9.0.3。
上游记录：[GHSA-6w46-j5rx-g56g](https://osv.dev/vulnerability/GHSA-6w46-j5rx-g56g)。
.dagger/uv.lock 含本地生成 SDK，现有扫描器无法完整扫描该来源；
本轮只对该锁文件执行一致性校验，不能宣称完成其漏洞扫描。
这是发布安全范围的明确限制。消费者默认安全策略 off 不变。

## 发布 Run 的身份与执行范围

正式发布要求 workflow_dispatch、refs/heads/main、pass 场景，并核对实际提交及四处版本元数据。
完整检查、Manifest 与正式发布均关联同一个 Release Run 的原生执行证据；
PR 轻量通过和本地历史通过记录不能替代这些门禁。
必需检查失败、跳过、证据缺失或上传失败均不发布。

非发布模式运行同一完整检查，但不创建 Tag / Release。它是可选的独立验证，
不是默认发布的前置 Run。既有 Workflow 不跨 PR / Run 借用通过记录。
触发方式仍为手动；GitHub 页面、CLI 使用相同的 version / publish / pytest_scenario 输入，
不新增证据 ID 等必填参数。正常操作步骤只在[使用说明](../../../guides/common-tasks.md#默认发布流程)维护。

安全负向验证：候选分支使用 pytest_scenario=assertion、publish=false，
真实 pytest 退出 1，发布验收失败，已有 Artifact 保留，不创建 Tag/Release。
完整验收没有自动 push/PR 触发路径。

旧 ci.yml 的 pytest_probe_scenario / quality_probe_scenario / matrix_probe_scenario
不再作为 workflow_dispatch 输入。报告与负向门禁函数全部保留，可以本地直接调用
fixture-pytest-artifacts、fixture-quality-artifacts、fixture-matrix-artifacts
并传入原有 --scenario；发布路径统一使用上述 pass / assertion 输入。
[性能测量记录](../../verification/performance/measurements.md)中的 v0.4.7 数字是旧完整 CI 基线，不是当前 PR 工作负载。

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

同 SHA 的 Tag 已创建、Release 未创建时，保留可恢复状态；
上传或 API 失败会留下可恢复的 Tag / Draft，不会提前转为正式版本。
已有同 SHA 的正式 Release 必须有一致的 Manifest，才返回原 URL，不重复创建。
已有正式版本缺少或冲突的证据时明确失败，不补写、覆盖历史版本。
祖先提交上的同版本 Tag 不能代替当前提交验证。注解 Tag 比较最终 commit SHA。

## v0.4.9 发布证据

每次手动验证将 Actions 原始 job / step 结果写入
`release-manifest.json`，保存为 `release-manifest` Artifact（30 天）；
Job Summary 同时显示版本、SHA、验证 Run 链接与各检查状态。
机器可读结构见 [JSON Schema](manifest.schema.json)。
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
当前 attempt 缺少必需检查或原生输出时拒绝发布，不借用其他 attempt 的通过记录。
验证 Artifact 有保留期；正式 Release 的 Manifest Asset
提供持续追溯入口。重复发布还需要原始 Actions 元数据；历史记录删除导致
无法核验时明确拒绝，不修改已发布版本。

Schema 约束字段、固定检查来源、状态、工具 scope/name 和必需证据数量；
Python 校验进一步核对版本与 Tag、仓库与 URL、SHA 及原始 API 结果等
跨字段和远端事实。通过 Schema 不等于已有可信的发布验证。

正式发布沿用完整验证与 create-only Tag，然后用 GitHub 原生自动 Release Notes
创建 Draft。填入实际 Release URL，上传 Manifest，再下载并核验原始检查、
版本 / SHA 和上传内容，最后才将 Draft 转为正式 Release。
任一步失败均不覆盖 Tag 或已上传 Asset，也不伪造成功。
GitHub 上游失败可能留下 size=0、state=starter 的空 Asset；恢复仅在
核对同一 SHA / Tag / Draft，并读回确认仍是该空 Asset 后删除并重新上传。
正式 Release 或已上传文件不执行此清理。读回失败、权限不足、上传错误均
阻断转正式；HTTP 错误只保留安全状态码，不输出原始响应。
同 SHA 的 Draft 已有有效 Manifest 时保留原始验证 Run 的证据并核验后恢复；
已有正式版本的重复调用核对原有证据，不修改已有版本。
只有 publish job 有写权限；证据收集不新增 pytest / Dagger 调用。
完整检查与 Artifact 上传仍可能因外部服务失败而拒绝发布。

验证层次与剩余风险见 [v0.4.9 审查记录](../../verification/release-risk-review.md)。

GitHub 无法原子绑定 main 比较与 Release 创建。验证完成、创建 Tag 前、
创建 Release 前都会核对 main；最后 API 调用仍有极短竞争窗口。
发布期间 main 变化会使当前证据与发布目标不一致；不强推或自动撤销 Tag。

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


## 交付阶段与证据准入

`release/lifecycle.py` 使用 transitions 的 HierarchicalMachine，auto_transitions=False。
主阶段是 DELIVERY、REVIEW、PUBLICATION、BLOCKED；DELIVERY 内部依次执行
pending → checking → checked → committing → committed → pushing → pushed。
pytest、CI Job、Artifact 的具体结果作为证据保存，不组合成 FSM 的平面状态。

FSM 与 `release/pipeline.py` 的 DeliveryPipeline 持有同一个 ExecutionContext 对象。
上下文包含仓库、版本、checkout、实际 Git / PR 事实和不可变阶段证据。
业务操作产生证据，FSM 只按事件和准入条件改变阶段；后续步骤不能仅凭前一阶段名称认为完成。

| 阶段输出 | 下一阶段必需输入与准入 |
| --- | --- |
| 本地检查 | 完整当前 PR profile、每项执行身份、报告 SHA256 与工作区指纹；准入提交 |
| Commit | 精确 Commit SHA 绑定检查报告指纹，提交后重新比对相关输入；准入 Push |
| Push | 实际远端 ref 与已准入 Commit SHA 相同；准入创建或更新 PR |
| PR | OPEN PR 的 head 与本地及远端提交一致；进入等待人工审查 |
| 人工 Merge | 实际 Merge SHA、被审查 head、远端 main 的祖先关系；准入发布目标 |
| 发布目标 | main SHA、版本和已有 Merge 证据一致；交给原 execution FSM 请求同一 Release Workflow |

输入过期、PR head 改变、报告替换或证据缺失均不能越过准入；已合并 PR 留有本地改动进入
BLOCKED.merged，不能被旧 Run 或原 PR 的绿色检查覆盖。错误输出包含当前阶段和接续命令。
重启时从原生事实重新识别阶段，已确认的 OPEN PR 可以恢复 REVIEW.waiting，不伪造本地检查记录。
Git/PR 修改失败保留已产生的真实提交；远端 UNKNOWN 仍禁止自动再次 POST。
PR 交付完成与 CI 通过分别记录。handoff 读取当前 head 的真实检查状态：必需结果失败、跳过或
尚未完成时停在 PR CI，不建议 Ready / 合并；只有配置中全部必需 Job 实际成功且可合并时，
才给出人工合并提示。缺失状态不能当作通过。

Workflow 的 bootstrap 检查只需要标准库。下载配置类型与执行依赖分开，读取可选工具配置不会
提前导入 transitions / filelock；实际执行能力仍由锁定项目环境加载，不降低其依赖要求。

## 调用职责

Actions 通过 `engineering_ci.workflow_actions` 调用 `release prepare/verify/publish` 与
`manifest facts/tools`；GitHub Actions 继续定义并执行发布 DAG。
这些内部动作处理 Actions 上下文及发布门禁，不是用户请求 Workflow 的命令。

Release 业务模块位于 `src/engineering_ci/release/`；用户脚本是 `scripts/release.py` 和
`scripts/download.py`。`release/delivery.py` 协调共享上下文、FSM 与阶段操作，串接检查、提交、Push 和人工合并停点，
不承担远端 DAG 调度。请求和等待复用既有 execution FSM；只读停点查询与核验不创建执行状态，
产物下载仍交给既有收集器。发布 Manifest Schema 与原有发布准入语义保持不变。
模块调用关系见[CLI 与 Workflow 调用边界](../cli-boundaries.md)，命令及恢复方式见[使用说明](../../../guides/README.md)。
