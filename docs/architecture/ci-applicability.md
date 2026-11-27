# CI 检查适用性（#167 P0b / AC13）

Status: **P0b 实施切片，Revise 后修订中**。只决定既有 CI Check 的计划范围，
不接纳证据、不产生 Required 判定，也不是已观察的执行事实。测试目标选择仍由
[#66](https://github.com/dymzz/engineering-ci/issues/66) Selection 负责；候选中
Tach/非代码映射由 [#165](https://github.com/dymzz/engineering-ci/pull/165) 合并后消费。

## 职责与边界

`engineering_ci.ci_check_inputs` 是本仓五个既有 Dagger Check 的实际输入合同：
Dagger 模块从它构造容器 Source 排除集，`engineering_ci.ci_applicability` 从同一
声明决定候选需要执行哪些 Check。后者只是 CI 适配层（事件、原生 Actions outputs、
计划报告），不另建路径选择权威。

- 只读取 Git 变更集与共享输入合同；不执行 Check、不重跑测试、不写证据账本。
- 不选择 pytest 目标，不替代 Selection/Plan，也不把“不适用”当作通过。
- 不做证据复用：既有 Run/Attempt 结果能否再次使用仍是 #63 T1-a/T2 接纳。
- 不产生 Required、Release 或授权结论（#61）。
- 报告是**计划**（`layer_3_planned_scope`），不是 Job/step 实际开始、跳过、失败或
  取消的事实；实际执行由既有 Run/Job/Attempt 与原生 Result 权威（#52/#75/#86）记录。

## 三层分离

| 层 | 负责方 | 实际内容 |
| --- | --- | --- |
| 1. Workflow 触发 | GitHub Actions | `push` main、`pull_request` main、`workflow_dispatch`、`workflow_call`；不新增触发事件 |
| 2. 候选范围 | `ci_applicability` | 按**累积**变更集决定 5 个既有 Check 的计划范围与原因 |
| 3. 执行范围 | Workflow Job/step | 只有计划为适用且本 Job 前置步骤成功时才调用原生 `dagger check`；不适用仅记录，不伪造结果 |

PR 与 push 的累积差分确定候选完整义务；相对既有有效证据的“实际增量”当前**未计算**，
报告显式记录 `increment.status = unavailable`、原因
`trusted_evidence_admission_unavailable` 与 owner #63。现有实现不会仅因“上一次曾通过”
跳过当前检查。

## 共享输入合同

[`src/engineering_ci/ci_check_inputs.py`](../../src/engineering_ci/ci_check_inputs.py)
同时被 [`.dagger/src/engineering_ci_module/repository.py`](../../.dagger/src/engineering_ci_module/repository.py)
（Source 排除集）与 CI 计划消费，避免第二套路径权威。逐 Check 输入：

| Check | 计划适用的实际输入 |
| --- | --- |
| `lint` | 非 Source 排除路径中的 `.py`/`.pyi`/`.ipynb`；任意层级的 `pyproject.toml`、`ruff.toml`、`.ruff.toml`、`.gitignore` |
| `format-check` | 同上，另加 `.md`（含 Markdown 代码块；Dagger Source 内的 Markdown 才可被容器读取） |
| `tests` | Dagger Repository Source 内任一非排除路径（完整 pytest 义务） |
| `version` | 两个项目的 `pyproject.toml`/`uv.lock`、`src/engineering_ci/release/`、`_paths.py`、包 `__init__.py` |
| `lock` | 任意 `pyproject.toml`（含 workspace 成员）与两份 `uv.lock` |

已用锁定 Ruff `0.16.10` 实测（2026-11-27）：`ruff check .` 解析 `.py/.pyi/.ipynb`
与 Ruff 配置、遵循 `.gitignore`，但不 lint Markdown；`ruff format --check .` 还会格式化
Markdown 代码块。故 `format-check` 与 `lint` 的 Markdown 输入不同，不按扩展名猜测。

文档/元数据语义由 `ci_check_inputs` 的 Source 排除子集表达：`guides/`、`design/`、
`docs/**/*.md`、`AGENTS.md`、`.agent/`、`scripts/*.md` 等。只有全部变更都属于该子集时，
`tests` 才不适用；`docs/` 下 Schema、脚本等非 Markdown 文件仍触发 pytest。子集与
`SOURCE_EXCLUDES` 的一致性由 `tests/test_ci_applicability.py` 校验。

共享执行基础设施（`.dagger/src/engineering_ci_module/`、`.dagger/sdk/`、Dagger/根
manifests、Workflow、`ci_check_inputs.py`/`ci_applicability.py`）变化时升级为全部 Check；
无法精确判定的非文档输入至少触发完整 `tests`，不会静默跳过。

## 决策规则

| 情形 | 结果 |
| --- | --- |
| 显式全量（Release caller、手动 Dispatch、未知事件） | 全部 `applicable`，原因 `explicit_caller_full` / `explicit_workflow_dispatch` 等 |
| 变更集不完整 / base/head 不可解析 / diff 不可用 | 全部 `applicable`（fail-closed） |
| 空变更集 | 全部 `not_applicable`，原因 `no_changed_paths` |
| 含 toolchain 输入 | 全部 `applicable`，原因 `toolchain_inputs` |
| 含自验证输入 | 全部 `applicable`，原因 `self_verification_inputs` |
| 仅 Source 排除文档 | 全部 `not_applicable`；pytest 原因为 `source_excluded_changes` |
| Python 变更 | lint/format/tests 适用；version/lock 仅在各自输入变化时适用 |
| 混合变更 | 各 Check 义务取并集，不因存在文档变更而缩小 |

Git 路径身份原样保留：POSIX 上文件名中的反斜杠不是分隔符，也不做无条件归一化。
目录前缀以 `/` 结尾，`guides/` 不会误匹配 `guides-other/`。

## 自验证不可由候选策略取消

自验证路径（`ci_check_inputs.py`、`ci_applicability.py`、`tests/test_ci_applicability.py`、
`.github/workflows/ci.yml`、`.dagger/src/engineering_ci_module/`）在**Workflow 内硬编码**：
即使候选把自身改判为文档，`change-applicability` 仍强制 `--full
--full-reason self_verification_inputs`。模块自身也对同一集合升级，二者独立。验证消费者
`tests/test_ci_applicability.py` 同时校验该硬编码列表，防止删除。

## Workflow 接线

`ci.yml` 新增 `change-applicability` Job，输出每个 Check 的布尔值；`python-ci`、
`sync-tool-tests`、`version-metadata` 通过 `needs` 消费。语义分三层：

- **Job 回退**：三个下游 Job 使用 `if: ${{ !cancelled() }}`，决策 Job 失败或跳过时仍启动，
  并把原生 Check 全部执行（保留决策失败事实，Workflow 仍为失败）。
- **本 Job 前置**：每个 checkout/安装/Check step 使用 `success() && !cancelled()`，本 Job
  的 checkout 或工具安装失败后不会继续尝试 Check。
- **计划跳过**：仅当决策成功且该 Check 明确输出 `false` 时才跳过；输出缺失、命名漂移或
  决策失败都重新执行（fail-closed）。

`dagger-native-telemetry` 不受计划 gate：A.1 离线合同回归是宿主 pytest，输入域不等于
Dagger Source，不能借 Source 排除减少；其 live/offline 选择仍由既有 telemetry 范围与
显式 `[reverify-a1]` 决定。

Release 通过 `workflow_call.full_verification: true`（默认 true）显式请求全量；手动
Dispatch 也保持全量；`coverage: true` 额外强制全量。`core_artifact_id`、`core-*`、Ruff
工具 Artifact 合同不变。Workflow 级 `paths`/`paths-ignore` 不使用：跳过整个 Workflow 会
让未来配置的 Required Check 长期 pending。

## 报告与查询

- Actions Artifact `ci-applicability`（保留 7 天）保存计划报告；
- `change-applicability` Job Summary 打印每个 Check 的计划状态、原因与输入数；
- 本地只读复算：

```sh
uv run --locked --no-sync python -m engineering_ci.cli ci-applicability \
  --event pull_request --base origin/main --head HEAD --json
```

报告字段：`layer_1_trigger`、`layer_2_candidate`（含 `requested_base`/`requested_head`/
`identity_status`）、`layer_3_planned_scope`、`execution_observation`、`increment`。
`execution_observation` 明确 `not_observed_in_this_report` 并指向 #52/#75/#86；查询历史 Run
只下载既有 Artifact 与 Job 日志，不重跑 Workflow 或 pytest。

## 触发策略核查

- `push` main 与 `pull_request` main 都会执行，合并后 push 会再次验证同一内容；由于证据
  接纳尚不可用，本切片不据旧 PR 结果跳过 push Check。
- 不新增自动触发，不改分支保护或治理策略；手动 Dispatch 仍是显式全量入口。
- GitHub API 实查（2026-11-27）：`branches/main` 返回 `protected=false`、protection
  `enabled=false` 且 required checks 为空；`/rulesets` 为 `[]`。Required/接纳配置因此为
  **未知/缺失**，交维护者与 #61，本切片不新建聚合器。
- 文档链接检查（Lychee）当前未在本仓 CI 采用，纯指南候选如实显示全部 `not_applicable`。

## 验证

`tests/test_ci_applicability.py` 以真实临时 Git 仓库覆盖：纯指南/设计文档、`docs/` 合同
Schema、根 Markdown、Notebook、嵌套 Ruff 配置与 `.gitignore`、Python/version/lock
实现与 workspace 成员输入、Dagger 模块与 toolchain 升级、自验证输入、混合变更、
不完整/空变更集、显式全量与 Release call、路径身份/目录边界、Workflow 输出与硬编码
自验证一致性、Dagger Source 合同消费、CLI 报告/输出。不启动 Actions、不运行完整 CI。

实际 Actions 证据（Draft PR #171）在修订后由新 Run 重新采集；历史 Run
`46060510318`（head `220e76a`）与 `46061866069`（head `059510f`）上的两次完整执行
只作为旧候选事实保留，不能用来说明本修订的行为。纯文档候选的 Actions skip 反例仍需
合并后的真实纯文档 PR，本切片不额外启动 Workflow。

## 后续边界

1. #165 合并后，`tests` 义务改为消费实际 Selection Plan 的
   `selection.status`/`check_scope`，替换当前保守的 Source 级判断；不新增第二个测试选择器。
2. 相对既有有效证据的实际增量复用仍依赖 #63 接纳；在此之前不跳过当前 Required 义务。
3. Required Check/Ruleset 配置缺失状态由具有权限的维护者核实；真实失败/跳过 Workflow
   反例与正式 Release 适用合同需在获授权后以定向 Run 验证。
