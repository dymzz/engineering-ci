# 查询已有轨迹与候选

从 `D:\workspace\engineering-ci` 执行。查询不会运行历史工具或重新挖掘；
登记模式需已有 PostgreSQL 归档、`VESRYN_DATABASE` 和 trace extra。
Linux 使用相同 CLI 参数。下面的 PowerShell 变量直接来自真实返回结果。

## 从概览逐层定位

```powershell
$summary = uv run --locked --extra trace engineering-ci trace query summary --limit 10 --max-bytes 8192 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw $summary.reason }
$analyses = @($summary.items | Where-Object { $_.statistics.engine -eq 'rust4pm' })
if ($analyses.Count -eq 0) { throw '本页没有 Rust4PM 分析；使用 next_after 翻页或先登记已有分析' }
$analysisId = $analyses[0].result_id

$patterns = uv run --locked --extra trace engineering-ci trace query pattern --analysis-result $analysisId --activity command_execution --limit 5 --max-bytes 8192 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw $patterns.reason }
if ($patterns.items.Count -eq 0) { throw '没有匹配模式；调整过滤条件' }
$candidateId = $patterns.items[0].candidate_id

uv run --locked --extra trace engineering-ci trace query candidate --analysis-result $analysisId --candidate $candidateId
uv run --locked --extra trace engineering-ci trace query evidence --analysis-result $analysisId --candidate $candidateId --limit 3
```

要看该分析全部统计和候选概览，使用
`trace query summary --analysis-result $analysisId`。
需要失败见证时，在 evidence 命令加 `--outcome failed`；未匹配返回空集合，
不能由此认定原执行全部成功。

保留同一查询的过滤条件，再使用它返回的书签。例如：

```powershell
if ($patterns.page.next_after) {
    $patterns = uv run --locked --extra trace engineering-ci trace query pattern --analysis-result $analysisId --activity command_execution --limit 5 --max-bytes 8192 --after $patterns.page.next_after | ConvertFrom-Json
}
```

目录 Summary 也支持 `--snapshot $snapshotId`。没有指定分析时只返回登记统计和引用，
不默认选择最新记录冒充当前候选。

## 已有文件，不连接数据库

已有分析文件及当时实际使用的投影文件可直接查询。文件不需要 trace/mining extra：

```powershell
uv run --locked engineering-ci trace query summary --analysis '.test-tools/mining-result.json' --projection '.test-tools/otel-projection.json'
uv run --locked engineering-ci trace query pattern --analysis '.test-tools/mining-result.json' --projection '.test-tools/otel-projection.json' --kind adjacent_activity_repeat --min-cases 2
```

替换为实际存在的文件；少原投影不会推测关联。重新排版文件改变实际字节时，
必须仍与分析的原输入摘要相符，不能只按文件名认定同一输入。

## 按授权读取原记录

先取得上面返回的 `scope.snapshot_id`；只有已经获准读取该快照原文时才执行：

```powershell
$snapshotId = $patterns.scope.snapshot_id
uv run --locked --extra trace engineering-ci trace query raw --snapshot $snapshotId --allow-raw --limit 1 --max-bytes 16384
```

返回 `payload_base64` 保留原字节，可能包含私密内容。需要下一条时使用本页的
`page.next_after`，每次继续仍需 `--allow-raw`。这不是导出完整历史的默认路径。

## 参数与退出状态

查询默认输出紧凑 JSON；`--json` 可兼容使用。`--config` 采用既有 tracing TOML 路径规则。
默认预算 16 KiB；budget_exceeded 返回 3，增加 `--max-bytes` 后执行同一页，
或保持已有书签继续。不要截断 JSON 或把未返回数据理解为不存在。

`returned` / exit 0 是查询完成，不是候选执行或优化通过。
非法查询/配置/文件/损坏返回 2；证据不可用或预算不足返回 3。
详情见[检索契约](https://github.com/dymzz/vesryn/blob/7ac74b83c4a206446b9c8b52cc88c906a13ad2f5/docs/architecture/harness/retrieval.md)。
