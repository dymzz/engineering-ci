# v0.4.9 关键风险审查

本文是 v0.4.9 合并前的审查与验证记录，其中待验证事项描述当时状态。
当前命令见[使用说明](../../guides/README.md)，发布机制见[发布说明](../architecture/release/workflow.md)。

范围为 PR #10 的发布证据正确性；不增加质量检查，不改变消费者调用，
不以代码行数决定重构。正式合并和发布仍由人发起。

## 已确认的问题与修正

| 风险 | 修正与依据 |
|---|---|
| Schema 与 Python 注册表漂移 | 固定检查来源、状态、8 个工具 scope/name、4 种 Artifact 和聚合条件；契约测试核对注册表。跨字段与 GitHub 事实仍由 Python 校验。 |
| Run 级 Artifact 不能证明 attempt | 上传步骤的原生 Artifact ID 经 outputs 传到所有下载步骤；核对 Jobs API 的 Run / attempt / SHA 和 Artifact API 的 ID / Run / SHA。缺少精确 ID 时拒绝。 |
| Draft 上传失败后留下空 starter Asset | GitHub 官方 API 明确说明 502 可能留下此类资产。只清理重新核对过的同一 Draft 中 size=0 / starter 文件；已上传或正式文件不覆盖。 |
| Draft 与正式页面 URL 不稳定 | Manifest 使用 GitHub 返回的稳定 Release API 资源 URL；Summary 使用实际页面 URL。 |
| 重复操作及响应丢失 | 原有同 SHA / Tag 的 Draft、有效原始 Manifest 可以恢复；正式 Release 只读核验，不重复创建。上传或转正式的响应丢失先报错，下次按真实状态恢复。 |
| 发布前状态变化 | 再次核对 main、Tag、Release ID 和 Draft 状态；发生变化就拒绝 PATCH。 |

真实 Run Attempt 数据佐证：
[Run 38004924269](https://github.com/dymzz/engineering-ci/actions/runs/38004924269)
的两个 attempt 在同一 SHA 下留下同名 `pytest-report`，原生 ID 分别为
11650981480、11651251232。Artifact API 没有 attempt 字段，不能用名称和 SHA
代替生产步骤的原生 ID。

API 行为依据：
[GitHub 官方 REST OpenAPI](https://github.com/github/rest-api-description/blob/main/descriptions/api.github.com/api.github.com.json)，
[upload-artifact 原生输出](https://github.com/actions/upload-artifact/blob/v7.0.2/action.yml)。

## 验证层次

- 单元测试使用可注入 API / 进程替身，覆盖失败、冲突、读回损坏、权限不足、
  响应丢失、原始 attempt 恢复和空 Asset 清理条件。它们没有创建真实 Release。
- 原生 `gh` 对本地 HTTP 替身验证 200 / 302 下载及跨主机重定向不传递认证头。
  使用虚拟 Token，没有真实 GitHub 写操作。
- 只读真实 GitHub Asset 下载：`cli/cli` 的 Asset 599854636，1971 字节，
  原生 `gh api` 成功，内容 SHA-256 与实际 API digest 相同。这验证真实读取，
  不证明本仓库上传权限、私有 Draft 读回或转正式已经通过。
- 候选提交的 `publish=false` Workflow 验证记录在 PR #10；正向与断言负向
  分别记录，并核对 attempt 和不同 Artifact ID。报告中的
  `fixture-invocations.txt=1` 验证证据收集没有重复执行 fixture pytest。

该审查时正式 v0.4.9 尚未由 Agent 发起；本仓库 `GITHUB_TOKEN` 的上传权限、
Draft Asset 读回及转正式当时缺少真实发布证据。

## 剩余限制

- GitHub 没有原子绑定 main 比较、Asset 上传与 Release PATCH 的事务。
  多次核验缩小竞争窗口，不能排除最后一次请求中的外部并发修改。
- 当前 attempt 缺少完整结果或原生输出会拒绝，不借用另一 attempt 的成功检查。
- Actions 元数据存在保留期；删除后重复发布无法重新核验原始事实时拒绝。
  Release Manifest 自身持久保留，但不能伪造已删除的服务端证据。
- 安全扫描范围为根 uv.lock；Dagger SDK 锁只做既有一致性检查。
  Matrix / Coverage 是既有模块 fixture，不能扩大为根项目兼容范围。

## 下轮维护记录

检查、工具注册表与 Schema 的静态约束仍有重复声明；当前契约测试负责防漂移。
下轮依据具体需求评估从简单注册表生成静态 Schema，以及将检查来源和可选原因
集中声明。发布决策与证据校验的函数边界也可以局部整理。
本轮不新建发布状态机或通用框架，不把减少行数作为重构理由。
