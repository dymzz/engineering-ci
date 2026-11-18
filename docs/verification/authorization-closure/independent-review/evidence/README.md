# D 的保留证据与定向重放

本目录是一次性审阅记录，脚本以 `.txt` 保存，不接入项目 CLI、Dagger、Hook 或每次 CI 的强制检查。`SHA256SUMS` 的相对根目录为上一级 `independent-review/`，包括报告、派生清单及全部原始压缩证据；校验文件本身与 `validation.json` 不进入其自身摘要闭环。

| 材料 | 证明范围 |
| --- | --- |
| `snapshot-manifest.json.gz` | 两个固定 Git blob 导出的源码/测试/配置/Workflow 文件 SHA-256；所有快照留在本 worktree 的忽略目录中 |
| `main-semantic-results.json.gz` / `pr124-semantic-results.json.gz` | 每次实际 API 的完整输入、输入摘要/是否被修改、返回对象或异常类型/原生诊断、case 预期与受控权威边界；主分区 65 例、候选 17 例 |
| `candidate-builder-plan-inputs.json.gz` | 在旧测试第一次 Binder 调用前截获的原 Producer 输入/Plan；旧 Binder 断言没有作为 D 的预期判定 |
| `mechanical-review.json.gz` / `mechanical-summary.json.gz` | 独立 AST 符号、函数与模块入口、调用表达式/解析方式、候选出口/反向调用及 Workflow 边界 |
| `runtime-source-comparison.json.gz` | 原生 Git argv、exit、stdout/stderr；A/B→D runtime 无变化、本轮 runtime 无改动 |
| `review-context.json.gz` | 当前 Issue 原文及 #124/#127 元数据，保留各自更新时间和固定 SHA |
| `source-*.json.gz` / `live-server-observation.json.gz` | 真实 GET 的 transport、UTC、完整归一化/raw JSON 或原生安全异常；没有真实 POST、PATCH、保护修改或 Release |

Native Result 的 `stdout` 内容在语义探针中明确为受控模型；实际工具没有运行。所有 issuer/verifier/provider 回调的 True 都是替身。源码快照模块加载路径及摘要写入各分区原始报告，原测试 helper 摘要包括在快照 manifest 中；没有跨 worktree 混用导入。

实际检查使用现成 Python **3.14.3**、pytest/PyYAML 依赖，不安装工具。禁用 pytest 自动插件；语义 worker 禁止真实 GitHub transport 与非 Git 子进程，fixture Git hooks 设置为 `NUL`，并清除继承的 Git 工作区/索引环境。Git fixture 的实体目录、输入摘要、受控执行时间和真实服务端查询时间各有独立身份；重放 fixture 的目录/提交 ID 可以变化，不能逐字比较新旧模拟输入摘要。

在仓库根目录，先检查已保留材料，不发起网络请求：

```powershell
python docs/verification/authorization-closure/independent-review/evidence/check-review.txt
```

如需重放语义探针，两个固定 commit 必须已在本地 Git 对象库，且既有 Python 依赖可导入。以下通过只读 `git cat-file --batch` 直接导出固定 blob，创建本仓内快照并运行本次范围内的 API 模型，不执行 Native 工具/Hook。本机 Git archive 的 EOL 转换不参与源码身份比较：

```powershell
python docs/verification/authorization-closure/independent-review/evidence/prepare-authorization-review.txt
python docs/verification/authorization-closure/independent-review/evidence/authorization-semantics-review.txt main
python docs/verification/authorization-closure/independent-review/evidence/authorization-semantics-review.txt pr124
python docs/verification/authorization-closure/independent-review/evidence/authorization-mechanical-review.txt
```

输出写入忽略目录 `.artifacts/authorization-review-d/`；候选 fixture 每次创建独立目录。原归档证据不被覆盖。`package-authorization-review.txt` 保存本次打包步骤，重新打包需把各 `.txt` 对应回其记录的 `.test-tools/*.py` 输入；通常无需重打包已签摘要的历史材料。

实时服务端观察是独立的可选步骤，会读取当前事实，不能逐字重现 2026-11-18 的服务器响应：

```powershell
python docs/verification/authorization-closure/independent-review/evidence/authorization-source-review.txt
```

该脚本只调用 G3 的三个 GET 端点/必要分页，默认原生 `gh` 凭据；它不发布 Check、不执行 enforcement probe、不修改服务器。其他 Run/Check/Artifact 记录由 GitHub connector 对明确 SHA/Run GET 获得，没有下载归档或日志。

`validation.json` 只汇总本批文件完整性、固定源码引用、93/86 对账、82 个案例的确切预期分类及 C 原证据未变；不是新的授权结果。真实生产验收项继续留原 Issue。
