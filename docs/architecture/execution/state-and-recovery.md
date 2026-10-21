# ghwf 执行与恢复组件

`scripts/download.py` 是已有 Run 的下载入口；发布请求使用 `scripts/release.py`。普通 Workflow Dispatch 参数保留在 `engineering_ci.execution.cli`，本文的 `--new-run` 仅属于该底层接口。当前命令及旧 PowerShell 函数迁移见 [使用说明](../../../guides/README.md)。
实现放在 `src/engineering_ci/execution/`，用于一次性执行与恢复；没有后台服务、数据库或任务队列。
执行 FSM、下载实现和消费者契约保持不变；Workflow 内部入口迁移不改变原有门禁。

## 目录与接口

```text
scripts/
  release.py                   请求现有 Release Workflow
  download.py                  只下载已有 Run
src/engineering_ci/tools/
  workflow_e2e.py              人工选择的真实验收模块
src/engineering_ci/execution/
  cli.py                        参数、命令输出、缓存选择
  coordinator.py                ExecutionCoordinator：执行顺序及恢复策略
  fsm.py                        ExecutionFSM：transitions 合法事件
  models.py                     不可变身份、状态、类型化结果与错误
  store.py                      StateStore：JSON 原子替换及 filelock
  github.py                     GitHubActionsClient：gh、官方 API、临时地址
  transport.py                  TransportAdapter、资源身份及下载进度契约
  download.py                   DownloadAdapter：可选 aria2 / native HTTP 后端
  config.py                     CLI / 环境变量 / 本地 TOML 配置
  archive.py                    ZIP 校验、受限暂存解压
  collector.py                  ArtifactCollector：日志及 Artifact 完成条件
  repository.py                 原有 Git origin 识别及仓库缓存规则
docs/architecture/execution/state.schema.json      状态 Schema v2
tests/test_execution_state.py    P0-1
tests/test_http_download.py      P0-2，本地 HTTP + 真实 aria2c
tests/test_archive.py            P0-2
tests/test_execution_integration.py  P0-3
tests/test_ghwf_cli.py            CLI / 旧缓存回归
```

Coordinator 注入 Client、Collector 和 StateStore；Client 注入 `ProcessRunner` 和
官方 API opener；DownloadAdapter 注入原生进程 runner 和 HTTP probe。
这些是普通 Python 接口，测试替身不需要依赖注入框架。

TransportAdapter 调用成熟传输工具或标准库 HTTP，返回 `TransportResult` / `DownloadProgress`，
通过类型化异常报告故障。`DownloadInterrupted.progress` 提供总大小、是否尝试续传等信息；
aria2 中断时的精确已完成字节数为 `None`，进度交给原生 `.aria2` 文件，不用稀疏文件大小冒充进度。
native 报告实际流式写入的字节数，但未完成文件下次从头下载；已校验文件不重复下载。
Git、Git LFS、Docker / Dagger 将来可按此边界扩展；本轮没有空壳适配器。

## Python 组件消费

普通 Python >=3.14 环境可以安装固定版本包，直接导入 `engineering_ci.execution`
公开的四个组件；安装方式见[消费者接入](../../../guides/new-repository.md#环境与固定引用)。
模型位于 `engineering_ci.execution.models`，不依赖工作区配置、Vesryn 或 PostgreSQL。

```python
from pathlib import Path
from engineering_ci.execution import ExecutionFSM, StateStore
from engineering_ci.execution.models import (
    CollectionIdentity,
    DispatchIdentity,
    TaskRecord,
)

record = TaskRecord(
    DispatchIdentity.create("example/consumer", "ci.yml", "main", {}),
    CollectionIdentity.create(Path("outputs"), no_artifacts=True),
)
record = ExecutionFSM().advance(record, "attach_run", run_id=42)
store = StateStore(Path(".artifacts/execution.json"))
with store.lock():
    store.save(record)
    restored = store.load()
assert restored.run_id == 42
```

这里 42 是离线状态示例，不执行 API 或声称真实 Run 存在。
`advance(record, event, **updates)` 返回新 TaskRecord，非法事件或字段抛 `InvalidTransition`。
`StateStore(path, lock_timeout=0)` 的 load/save 读写 JSON，调用方须用 lock 包住整个读改写；
锁冲突或无效文件抛 `ExecutionError`，不会自动另建任务或覆盖损坏状态。

`ExecutionCoordinator(store, client, collector, identity, collection, inputs, *, timeout=2700, interval=10)`
协调既有协议；`execute(resume=None, new_run=False, restart_finished=False, legacy_paths=())`
返回 `ExecutionResult`（record、exit_code），有真实 Dispatch/查询/文件收集副作用。
client 提供校验、单次 Dispatch、Run 查询/等待，collector 提供 intact/collect；
使用现有 GitHubActionsClient/ArtifactCollector 时需要 gh 认证、HTTP 网络及明确输出目录。
自定义协议替身须保持同一失败与恢复契约，不由 Coordinator 再实现传输。
UNKNOWN 不自动 POST；远端 failure 即使收集完成仍返回非零。
这些 API 的文件格式和合法状态见下文；未列出的 CLI/Release 内部助手不是安装包稳定 API。

## FSM 转换与不变量

`transitions.Machine(auto_transitions=False)` 禁止 `to_STATE` 和未定义事件。
业务模块只提交事件；Coordinator 先计算合法的新记录，再原子持久化，成功后才更新内存记录。

| 当前状态 | 事件 → 目标 | 恢复行为 |
| --- | --- | --- |
| INIT | validate → VALIDATED；attach_run → RUNNING | 校验目标；显式恢复 ID 必须匹配所属仓库 / Workflow |
| VALIDATED | dispatch → DISPATCHING | 校验当前目标并保存 Dispatch 意图，才允许 POST |
| DISPATCHING | attach_run → RUNNING；reject → REJECTED；uncertain → UNKNOWN | 重启时进入 UNKNOWN，不自动再 POST |
| UNKNOWN | attach_run → RUNNING | 人工核验后指定 `--resume RUN_ID` |
| REJECTED | 无自动状态跳转 | 修正配置后新建 INIT 请求；不会当成 UNKNOWN |
| RUNNING | remote_completed → COLLECTING；pause → PAUSED | 查询同一 Run，立即保存已确定的远端结果 |
| COLLECTING | finish → FINISHED；pause → PAUSED | 只恢复未完成或校验不通过的收集项 |
| PAUSED | resume_running → RUNNING 或 resume_collecting → COLLECTING | 必须与 `paused_from` 相符 |
| FINISHED | recollect → COLLECTING | 输出选项改变或本地文件失效时，只重新收集 |

另有明确的自转换：`configure` 用于 INIT / VALIDATED 的本地收集配置；
`checkpoint` 用于 RUNNING / COLLECTING / PAUSED / FINISHED 的文件检查点。
每个事件都限制可修改字段，不能用 checkpoint 偷换 Run ID、远端结果或 Dispatch 身份。

关键不变量：

- 本地和远端校验失败，不创建 Dispatch 意图。HTTP 422 等明确拒绝进入 REJECTED。
- POST 只发一次；响应丢失、无有效 Run ID、进程在意图保存后终止，都不能自动再次 POST。
- 收到 Run ID 后立即保存。即使这次保存失败，也先输出 Run ID 供人工恢复。
- PAUSED 保存原阶段与 Run ID；恢复不能回到 Dispatch。
- `remote_status`、`remote_conclusion`、`collection_complete` 分别描述远端结束、远端结果、本地完成。
- FINISHED 要求远端结果确定，并且必需输出的哈希校验通过。
- 远端失败也必需保存 `run.log`，即使未传 `--save-log`；缺失的失败 Run Artifact 记录为可选缺失。
  收集完成后进入 FINISHED，但返回码仍为 1。

## 身份、缓存与迁移

Dispatch 身份只含 `repo / workflow / ref / inputs_sha256`；不保存 Workflow Input 的原值。
Collection 身份含绝对输出目录、Artifact 选择、日志选项和是否跳过 Artifact；
文件计划同时关联 Run ID、Run attempt 与资源 ID。
改变 `--artifact`、`--out`、`--save-log`、`--no-artifacts` 会继续同一 Run。

缓存目录保持原规则：Windows 为 `%LOCALAPPDATA%\gh-workflow-artifacts`；
其他系统为 `$XDG_CACHE_HOME/gh-workflow-artifacts` 或 `~/.cache/gh-workflow-artifacts`。
仓库缓存仍用 `repo-cache.json` v1，按当前 Git 根目录和 origin SHA256 失效；
`--refresh-repo` 保留原含义。

任务状态位于 `runs/<dispatch-hash>.json`，同名 `.lock` 覆盖读取、Dispatch、等待和收集全过程。
第二个进程默认立即报错退出。输出目录另有收集锁，避免两个不同调用同时写同一 Run 的目录。
JSON 在同目录临时文件中写完并 flush/fsync，再 `os.replace`，失败保留上一版状态。
文件锁由操作系统管理，进程崩溃后释放。

完整字段约束见 [状态 Schema](state.schema.json)。示例：

```json
{
  "schema_version": 2,
  "dispatch": {
    "repo": "owner/repo",
    "workflow": "build.yml",
    "ref": "main",
    "inputs_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
  },
  "collection": {
    "out": "D:\\work\\outputs",
    "artifact": "build-{run_id}",
    "save_log": true,
    "no_artifacts": false
  },
  "phase": "PAUSED",
  "run_id": 123,
  "paused_from": "COLLECTING",
  "remote_status": "completed",
  "remote_conclusion": "failure",
  "collection_complete": false,
  "files": {},
  "last_error": "RecoverableError",
  "http_status": null,
  "updated_at": "2026-10-09T00:00:00+00:00",
  "dispatch_started_at": "2026-10-09T00:00:00+00:00"
}
```

`files.plans` 记录当前收集选项所需的文件键、Artifact 清单完成标志及日志 attempt。
`files.outputs` 记录 `kind / required / status / resource / manifest`；
manifest 是相对路径到 SHA256 的映射。状态分为 `pending / complete / optional_missing`。
每个收集项成功后立即提交检查点。

v1 迁移规则：

| v1 stage | v2 解释 |
| --- | --- |
| dispatch_pending | UNKNOWN，禁止自动 POST |
| dispatch_rejected | REJECTED，可修正后重新发起 |
| running / dispatched | RUNNING，恢复相同 Run ID |
| completed / finished | COLLECTING，重新校验收集内容；没有确定 conclusion 时回到 RUNNING |

重现原参数 scope 哈希寻找 v1 文件，包括旧版只有 Run ID、未保存 repo 字段的显式恢复记录。
原文件保留，迁移写入新的 v2 路径。v1 的保存标志不直接升级为完成：
声明的旧输出可被接管为已知文件，再通过新下载的 ZIP 校验 / 提取；其他用户文件禁止覆盖。
旧版未记录原始文件哈希，迁移无法辨别迁移前对这些声明输出的人工修改。

如果 collection 参数已经改变，v1 缺少 inputs 和路径信息，不能安全推断完整身份。
发现匹配的未完成旧记录时阻止盲目 Dispatch，要求指定 `--resume RUN_ID`。
无法识别的版本、损坏状态或多个旧 Run 不会自动清空状态。

## HTTP、日志与 Artifact

用 `gh` 完成认证、读取和单次 Dispatch；官方下载 API 的 302 地址通过禁止自动跳转的
HTTP opener 获取。认证 Token 只保留在进程内，不传给 aria2 或重定向后的下载主机。
签名 URL 通过 aria2 stdin 输入，不放入 argv、JSON 或输出日志。

下载放在 `<out>/<run_id>/.ghwf-cache/`：ZIP、`.aria2` 原生进度、
`.resource.json` 身份记录各自独立。每次恢复确认 Run attempt / Artifact ID、size、Digest
及可用的强 ETag。临时地址失效最多重新获取一次，不做短间隔网络重试。
资源改变时保留数据并报错，不拼接来自不同资源的字节。

HTTP probe 用一字节 Range GET 检测实际 206 行为。没有 Range、缺少原生进度，
或缺少强 validator / immutable resource / digest 时，默认只删除组件拥有的部分文件，
明确打印原因并从头下载；`--download-recovery pause` 可禁用这种降级。
无 Range 时只用一个连接，不声称已续传。正常网络故障立即 PAUSED，下一次命令恢复。

Windows aria2 1.37.0 在本机出现原生快照最后一次重命名失败。
适配器仅在原生进程正常退出、明确报该重命名位置错误时，用 Python 完成同一文件的原子替换；
下一次仍由 aria2 解析和校验原生进度。强杀进程留下的临时快照不直接接管，缺少有效快照时安全降级。
原生退出码 0 后校验下载大小和可用 Digest，再清理遗留进度文件。

日志使用指定 attempt 的官方 ZIP，保留完整原文并加文件名分隔，原子生成 `run.log`。
Artifact 清单分页读取，校验可用的 `sha256:` Digest；没有 Digest 时仍校验 ZIP CRC 和结构。
默认下载全部 Artifact 到各自名称目录；指定 `--artifact` 时保持 gh 原习惯，内容直接放 Run 目录。

解压前拒绝绝对路径、`..`、反斜杠、Windows 设备名、重复或大小写冲突路径、
链接、特殊文件、加密 ZIP、保留元数据路径及异常大小 / 文件数 / 压缩比。
通过暂存目录验证后提交；不覆盖与已知内容或已保存 manifest 不符的文件。
进程在提交后、状态保存前崩溃时，内容哈希相同的文件可安全重新接管。
多个文件不是单个事务，但只有全部完成并验证后才标记 FINISHED。

## 依赖与 Windows 用法

| 依赖 | 版本 / 说明 |
| --- | --- |
| Python | 仓库 `requires-python >=3.14`；独立入口实现兼容 Python 3.10+ |
| transitions | 0.9.3，FSM |
| filelock | 3.20.3，进程间互斥 |
| pytest | 9.0.3，dev 依赖 |
| six | 1.17.0，transitions 的锁定传递依赖 |
| aria2c | 可选后端；使用官方 Windows 1.37.0 便携版验证 |
| gh / Git | 外部原生命令；gh 需认证及 Actions 权限 |

未引入 platformdirs，避免改变已验证的缓存目录。Python 版本与依赖详见
`pyproject.toml` / `uv.lock`。安装：

```powershell
Set-Location D:\workspace\engineering-ci
uv sync --locked --group dev
$env:PATH = "D:\workspace\engineering-ci\.venv\Scripts;" + $env:PATH
function ghwf {
    uv run --project "D:\workspace\engineering-ci" --locked python "D:\workspace\engineering-ci\scripts\download.py" @args
}
function ghdispatch {
    uv run --project "D:\workspace\engineering-ci" --locked python -m engineering_ci.execution.cli @args
}
```

没有 uv 时，在选定的 Python 环境安装：

```powershell
python -m pip install transitions==0.9.3 filelock==3.20.3 pytest==9.0.3
```

GitHub CLI 从[官方安装入口](https://cli.github.com/)安装，然后 `gh auth login`。
默认 `auto` 检测 aria2c；未安装时使用 Python 标准库 HTTP 下载。
后端选择、恢复限制及 Windows/Linux 环境变量示例见[下载配置](../../configuration/downloads.md)。
可选 aria2 Windows 便携包为
[官方 1.37.0 ZIP](https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip)，
解压后把目录加入 PATH，或用 `--aria2c D:\tools\aria2\aria2c.exe`。

以下 Workflow 名称及参数是占位示例，需替换为消费者仓库的实际值：

```powershell
# 从当前 Git origin 自动识别 repo，首次执行可能产生真实 Run。
ghdispatch --workflow build.yml --ref main --input target=release --save-log `
     --artifact 'build-{run_id}' --out D:\work\outputs

# 检查状态，不访问 GitHub；显式 --repo 时不依赖 gh 安装。
ghdispatch --repo owner/repo --workflow build.yml --show-state

# 中断后用相同命令，或显式恢复。
ghdispatch --repo owner/repo --workflow build.yml --resume --save-log
ghdispatch --repo owner/repo --workflow build.yml --resume 123 --save-log --no-artifacts

# 改收集选项仍复用已保存的 Run，不发送新 Dispatch。
ghdispatch --repo owner/repo --workflow build.yml --out D:\other\outputs --save-log

# 自定义传输和降级策略。
ghdispatch --repo owner/repo --workflow build.yml --resume 123 --connections 2 `
     --download-timeout 60 --connect-timeout 20 --download-recovery pause

# 只有人工核验没有重复 Run，才使用该覆盖选项。
ghdispatch --repo owner/repo --workflow build.yml --new-run
```

## 兼容性对照

| 原行为 / 参数 | 本轮行为 |
| --- | --- |
| PowerShell 函数及脚本路径 | 旧脚本移除；ghwf 迁移到 download.py，Dispatch 用独立模块 |
| 底层 --repo / --workflow / --ref / --input | execution.cli 保留；Inputs 仅参与哈希，不持久化原值 |
| 自动识别 origin / --refresh-repo | 保留原解析及缓存失效规则 |
| --artifact / `{run_id}` | 保留名称选择和替换；指定名称时平铺输出 |
| --out / run.log | 保留 `<out>/<run_id>` 和 run.log；日志来源改为官方 ZIP |
| --save-log / --no-artifacts | 保留；失败 Run 自动补充必需诊断日志 |
| 底层 --resume / --resume RUN_ID | execution.cli 保留；download.py 要求显式 ID；核对 Run 所属仓库 / Workflow 后恢复，不 Dispatch |
| --new-run | 保留明确覆盖语义；未完成状态会输出警告 |
| --show-state | 保留并支持离线读取 / 必要迁移 |
| --interval / --timeout | 保留等待间隔及等待上限 |
| --retry-attempts / --retry-delay | 仍接受和校验；不再控制短间隔网络重试，请用恢复命令 |
| 未完成调用再次启动 | 优先恢复相同 Run，Collection 参数变化不影响 Dispatch 身份 |
| 完全成功后相同参数再次调用 | 保留旧版有意启动新 Run 的行为；`--resume` 只读取 / 补收集 |
| 完成但远端失败后再次调用 | 保留相同 Run，非零退出码；需要新 Run 时用 --new-run |

## 测试和人工验收

默认测试全部使用外部进程 / GitHub 请求替身，或 loopback HTTP Server。
不会真实 Dispatch，不需要 GitHub Token。

```powershell
# 分阶段运行
uv run --locked pytest tests/test_execution_state.py tests/test_sync_consumer.py -q
uv run --locked pytest tests/test_http_download.py tests/test_archive.py -q
uv run --locked pytest tests/test_execution_integration.py tests/test_ghwf_cli.py -q

# 完整回归
uv run --locked pytest -q
```

HTTP 集成测试使用 PATH 中的 aria2c；也可指定 `$env:ARIA2C = 'D:\tools\aria2\aria2c.exe'`。
没有 aria2c 时两项真实 HTTP 测试会 skip；验收要求它们实际运行。
本轮的阶段记录及最终锁定环境结果见 [执行验证记录](../../verification/execution-validation.md)。

| 要求的故障场景 | 测试位置 |
| --- | --- |
| 1 正常 Dispatch / 完成 / 日志 / Artifact | state + integration |
| 2 错 repo / ref 校验前拒绝 | state + integration |
| 3 HTTP 422 | state |
| 4 POST 响应丢失 / 禁止重发 | state |
| 5 Run ID 保存后崩溃 | state；另测 Run ID 保存失败的人工恢复输出 |
| 6 TLS 超时等待 | state |
| 7 日志中断仅恢复收集 | integration |
| 8 Artifact 分段中断 / 原生进度续传 | integration + 本地 HTTP |
| 9 临时 URL 失效 / 资源一致性 | integration + HTTP；另测 ETag / attempt 变化 |
| 10 无 Range 降级 | 本地 HTTP |
| 11 损坏文件 / ZIP / Digest | integration + archive |
| 12 远端失败仍保存日志 | integration |
| 13 两个进程仅一个 Dispatch | state，真实 multiprocessing spawn |
| 14 日志 / 输出选项变化不 Dispatch | integration + state |
| 15 v1 状态、原输出、旧参数 | CLI + state + integration |
| 16 非法 FSM 事件 | state |

初次实现时未运行真实 E2E。现有入口支持指定已有 Run：

```powershell
uv run --locked python -m engineering_ci.tools.workflow_e2e --repo owner/repo --workflow build.yml `
       --resume 123 --save-log --artifact 'build-{run_id}'

# 人工决定确实要创建 Run 后，才允许以下操作。
uv run --locked python -m engineering_ci.tools.workflow_e2e --allow-dispatch --repo owner/repo `
       --workflow build.yml --ref main --new-run --save-log
```

真实验收须核对 gh 认证、仓库 Actions 权限、官方 Dispatch 返回 Run ID、日志 ZIP
的 attempt 地址、实际 Artifact Digest 及消费者输出目录。独立安装包下载已有失败 Run
的验证只证明该查询与收集路径，不能替代 Dispatch、企业代理 / TLS 等其他路径验收。
当前候选的实际消费证据与未验证范围保存在对应 PR 交付记录；初次实现的历史结果见上文。

## 限制与后续事项

- 初次实现时未运行真实 GitHub E2E，该历史结果不代表当前环境。模拟测试仍不能替代真实认证和 API 行为验收。
- 仅支持 github.com，与原 origin 规则一致；GHES 主机 / 自定义 gh host 尚未实现。
- Dispatch 本身没有可依赖的幂等键。UNKNOWN 必须人工核验；--new-run 的明确覆盖可能产生重复 Run。
- `--show-state` 可以创建锁和迁移文件，仍不产生远端请求。
- v1 身份不完整时选择保守恢复，不猜测 Inputs 或新输出选项；未知 Schema 不自动降级。
- 下载源不提供 Digest 时，ZIP CRC 和本地 SHA256 不能代替发布者提供的密码学真实性证明。
- 已有输出与 manifest 不符时暂停，避免覆盖人工改动。人工检查并移走损坏文件后再恢复。
  缓存 ZIP 本身结构损坏时，检查资源并移走对应 ZIP 和 `.resource.json` 后可重新获取。
- 同一 Run 被重新运行后，attempt 不同的部分日志不能合并；请核验后使用独立输出目录 / 显式恢复。
- 多文件提交不是事务；哈希和检查点保证重启后可复核。锁和原子替换以本地文件系统为验收范围，
  网络共享盘的锁 / 断电持久性未测试。
- 突然强杀 aria2 尚未写出有效进度时，必须从头下载；不会解析或伪造原生进度。
- Git / LFS / OCI 等未来实际使用时接入 TransportAdapter，不增加常驻调度设施。
