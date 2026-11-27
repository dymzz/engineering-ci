# CI 检查适用性（#167 P0b / AC13）

Status: **P0b 首个切片**；只决定既有 CI 检查是否适用，不接纳证据、不产生 Required
判定。测试目标选择仍由 [#66](https://github.com/dymzz/engineering-ci/issues/66)
Selection 负责；候选中 Tach/非代码映射由
[#165](https://github.com/dymzz/engineering-ci/pull/165) 合并后消费。

## 职责与边界

`engineering_ci.ci_applicability` 从实际累积变更集决定
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) 中既有 Dagger Checks
的候选义务，供原生 Actions 条件使用。它：

- 只读取 Git 变更集与 `ci-applicability.toml` 策略，不执行 Check、不重跑测试、
  不写第二套证据账本；
- 不选择 pytest 目标，不替代 Selection/Plan，也不把“不适用”当作通过；
- 不做证据复用：既有 Run/Attempt 结果是否可再次使用仍属 #63 T1-a/T2 接纳；
- 不产生 Required、Release 或授权结论（#61）。

## 三层分离

| 层 | 负责方 | 实际内容 |
| --- | --- | --- |
| 1. Workflow 触发 | GitHub Actions | `push` main、`pull_request` main、`workflow_dispatch`、`workflow_call`；不新增触发事件 |
| 2. 候选义务 | 本模块 | 按**累积**变更集判定 5 个既有 Check 的 `applicable` / `not_applicable` 及原因 |
| 3. 执行范围 | Workflow Job/step | 适用才调用原生 `dagger check`；不适用时 step 记录 `not_applicable`，不伪造退出码或报告 |

三个层不能互相代替：触发成功不证明候选义务，候选义务也不证明原生 Check 已执行。
PR 与 push 的累积差分确定候选完整义务；相对既有有效证据的“实际增量”当前**未计算**，
报告显式记录 `increment.status = unavailable`、原因 `trusted_evidence_admission_unavailable`
与 owner #63。因此现有实现不会仅因“上一次曾通过”跳过当前检查。

## 策略文件

根 [`ci-applicability.toml`](../../ci-applicability.toml) 是唯一策略来源，严格解析：
未知键、未知 schema、重复 Check、缺少输入或歧义输入直接拒绝；拒绝时 Workflow 决策
Job 失败，依赖 Job 因 fail-closed 条件仍执行既有检查。

- `[toolchain]`：改变工具、依赖或 Workflow 环境的路径（`pyproject.toml`、
  `uv.lock`、Dagger 配置、`.github/workflows/`、策略自身、`.gitignore`/
  `.gitattributes`）使**所有** Check 适用。未知影响升级为全量，而不是按扩展名排除。
- `[documentation]`：Dagger 测试容器实际排除的文档/元数据路径（`guides/`、
  `design/`、`docs/**/*.md`、`AGENTS.md`、`.agent/` 等，见
  [`.dagger/src/engineering_ci_module/repository.py`](../../.dagger/src/engineering_ci_module/repository.py)
  的 `SOURCE_EXCLUDES`）。只有**全部**变更路径都属于该集合时，pytest Check 才不适用。
  `docs/` 下的 Schema、脚本等非 Markdown 文件不在排除集内，仍触发 pytest；该一致性由
  `tests/test_ci_applicability.py` 对 Dagger 常量做结构校验。
- `[[check]]`：每个既有原生 Check 的名称、所属 Job、Dagger Check 与输入类。
  `suffixes`/`files`/`prefixes` 表达路径输入；`source_scope = true` 表示“Dagger
  仓库 Source 内任一非文档输入”。`version` 与 `lock` 读取同样四个文件，因此总是同时
  适用；Workflow 使用一次 `--check=version --check=lock` 调用，保持原生报告合同。

## 决策规则

| 情形 | 结果 |
| --- | --- |
| 显式全量（`workflow_call`、`workflow_dispatch`、未知事件） | 全部 `applicable`，原因 `explicit_*` |
| 变更集不完整 / base/head 不可解析 | 全部 `applicable`，原因 `changeset_incomplete`（fail-closed） |
| 空变更集 | 全部 `not_applicable`，原因 `no_changed_paths` |
| 含 toolchain 输入 | 全部 `applicable`，原因 `toolchain_inputs` |
| 仅文档/元数据输入 | 全部 `not_applicable`；pytest 原因为 `documentation_only_changes` |
| Python 变更 | Ruff lint/format 与 pytest 适用；version/lock 仅在四个版本/锁文件变更时适用 |
| 混合变更 | 各 Check 义务取并集，不因存在文档变更而缩小 |

`suffixes` 只匹配 Python 源文件；Ruff 额外读取 `.ruff.toml`/`ruff.toml`。任何
Git 读取失败或策略错误都不会生成“跳过”。

## Workflow 接线

`ci.yml` 新增 `change-applicability` Job，运行本模块并输出每个 Check 的布尔值；
`python-ci`、`sync-tool-tests`、`version-metadata` 通过 `needs` 消费。执行条件采用
**fail-closed 比较**：

```yaml
if: ${{ !cancelled() && (needs.change-applicability.result != 'success'
      || needs.change-applicability.outputs.tests != 'false') }}
```

即只有决策 Job 成功且该 Check 明确输出 `false` 时才跳过原生 step；决策 Job 失败、
输出缺失或命名漂移都会重新执行检查。Job 本身仍启动并完成，Check 名称不变；被跳过的
step 没有原生退出、报告或 Artifact，真实状态只存在于适用性报告与 Job Summary，不能
当作 `PASS` 引用。正式 Release 通过 `workflow_call` 强制全量，`core_artifact_id`、
`core-*`、Ruff 工具 Artifact 与覆盖合同不变；手动 Dispatch 也保持显式全量。

Workflow 级 `paths`/`paths-ignore` 仍不使用：跳过整个 Workflow 会让未来配置的
Required Check 长期 pending。当前仓库分支保护与 Ruleset 经 GitHub API 核查为**未启用**
（`branches/main.protection.enabled = false`，无 required checks；`/rulesets` 为空），
因此 Required 状态与接纳配置仍是**未知**，交维护者与 #61 决定，本切片不新建聚合器。

`dagger-native-telemetry` 的离线 A.1 合同回归是 `tests/test_dagger_native_telemetry.py`
的一次 pytest 执行，因此同样只在 `tests` 义务适用时运行；不适用时 Job Summary 记录
`not_applicable`，不生成离线报告 Artifact。A.1 live 捕获仍由其既有 telemetry 范围与
显式 `[reverify-a1]` 决定，不受本策略缩小。

## 报告与查询

- Actions Artifact `ci-applicability`（保留 7 天）保存结构化报告；
- `change-applicability` Job Summary 打印每个 Check 的状态、原因与输入数；
- 本地只读复算：

```sh
uv run --locked --no-sync python -m engineering_ci.cli ci-applicability \
  --event pull_request --base origin/main --head HEAD --json
```

报告字段：`layer_1_trigger`、`layer_2_candidate`、`layer_3_execution`、`increment`。
查询历史 Run 只下载既有 Artifact，不重跑 Workflow 或 pytest。

## 触发策略核查（同一 Issue R6）

- `push` main 与 `pull_request` main 都在合并前后执行，合并后 push 会再次验证同一内容；
  由于证据接纳尚不可用，本切片不据旧 PR 结果跳过 push Check，重复成本保持可见并记录。
- 不新增自动触发，不改分支保护或治理策略；`workflow_dispatch` 仍是显式全量入口。
- 文档链接检查（Lychee）当前未在本仓 CI 采用，因此纯指南改动没有可执行的文档 Check；
  报告如实显示全部 `not_applicable`，不新增未采用义务。

## 验证

`tests/test_ci_applicability.py` 以真实临时 Git 仓库和最小反例覆盖：纯指南改动、
`docs/` 下合同 Schema、Python 变更、混合变更、toolchain 升级、Workflow 变更、
不完整/空变更集、显式全量与 Release `workflow_call`、策略拒绝、CLI 报告/输出、
Dagger Source 排除一致性及 Workflow 输出漂移。不启动 Actions、不运行完整 CI。

真实仓库复核（2026-11-27）：

| 候选 | 变更 | 结果 |
| --- | --- | --- |
| `70c4cfe...54fcd75` | 仅 `guides/common-tasks.md`（+4/-4） | 5 个 Check 全部 `not_applicable` |
| `2ec8736` | `docs/verification/review-experiment/validate.py` | lint/format/tests 适用，version/lock 不适用 |
| `384b981` | `src/` 与 `tests/` Python 变更 | lint/format/tests 适用，version/lock 不适用 |

同一 docs-only 候选上以 Git Bash 原样执行 Workflow 决策脚本，得到 `lint=false`、
`format_check=false`、`tests=false`、`version=false`、`lock=false` 与
`candidate_status=not_applicable`，并生成 Job Summary。`actionlint -shellcheck=` 与
`zizmor --config .github/zizmor.yml --offline` 对 `ci.yml` 无发现（Windows 上
actionlint 的 shellcheck 集成不可用，决策脚本单独经 shellcheck 校验）；原生
`dagger check --engineering-ci --check=lint --check=format-check` 实际通过。
本地热环境决策耗时 0.49–0.84s（含 `uv run --locked --no-sync` 包装）；Actions Job
含 checkout 与 `uv sync --locked` 的启动成本尚未实测，首次真实 Run 时按实际 Job
耗时记录，不用本地值代替。

以上是本地只读决策与原生静态检查证据。实际 Actions 验证（Draft PR
[#171](https://github.com/dymzz/engineering-ci/pull/171) 首次 Run，head `220e76a`）：
Run [`46060510318`](https://github.com/dymzz/engineering-ci/actions/runs/46060510318)。

| 事实 | 结果 |
| --- | --- |
| `change-applicability` Job | `success`，18s；Artifact `ci-applicability` 显示 11 个累积路径、`toolchain_inputs`、5 个 Check 全部 `applicable` |
| 其余 Job | `python-ci / checks` 79s、`version-metadata` 66s、`sync-tool-tests` 4m12s、`dagger-native-telemetry / a1-gate` 111s，均 `success` |
| 本地完整原生检查 | `dagger check --engineering-ci --check=tests` 通过（4m03s），`lint`/`format-check` 通过 |

该 PR 自身含 `.github/workflows/ci.yml` 与策略文件，累积差分必然升级为全部 Check，
因此不能同时充当“纯文档候选不启动核心检查”的 Actions 反例。该反例应在合并后由真实
纯文档 PR 产生（本次不额外启动 Workflow）；本地决策与 Workflow 决策脚本 dry-run 已
覆盖该路径。完整本地运行结果为 4m03s，不等于 Linux Actions Job 耗时，二者分别记录。

本文件自身的一次文档增量会形成新的 PR head，但因 #63 接纳尚不可用，累积变更判定仍
执行全部 Check，不会仅凭上一次 Run 的代码结果跳过。该重复执行是当前明确的边界，
不是已实现的增量复用。

## 后续边界

1. #165 合并后，`tests` 义务应改为消费实际 Selection Plan 的
   `selection.status`/`check_scope`，替换当前保守的“文档-only vs 全部”粗粒度判断；
   本文件与策略中的对应项同时迁移，不新增第二个测试选择器。
2. 相对既有有效证据的实际增量复用仍依赖 #63 接纳；在此之前不跳过当前 Required 义务，
   只记录 unavailable 边界。
3. Required Check/Ruleset 配置缺失状态由具有权限的维护者核实；实际 Actions 运行证据
   在获授权后补充。
