# ghwf 下载后端与配置

`DownloadAdapter` 选择后端，两个实现继续使用 `ResourceIdentity` 和 `TransportResult`。
FSM、Coordinator、StateStore、Collector 和脚本入口的执行语义不变。

| 模式 | 行为 | 字节续传 |
| --- | --- | --- |
| `auto`（默认） | 检测 aria2c，找不到时自动使用 native | 取决于实际选用的后端 |
| `aria2` | 强制 aria2c；工具缺失报告配置错误 | 要求 HTTP Range、匹配的资源身份及原生 `.aria2` 进度 |
| `native` | Python 标准库，单连接流式 HTTP，保持 TLS 校验 | 不支持；重新下载未完成文件 |

日志显示实际后端及字节续传条件。网络中断后 FSM 暂停，保留收集进度；
`--resume` 恢复同一 Run。已通过完整校验的文件不重复下载。
native 先写 `.native.part` 临时文件，校验后原子替换为目标文件。
两种后端都保留资源身份及可用的 SHA256 校验；ZIP 校验和安全解压仍由现有 Collector 完成。

后端切换不复用另一后端的部分文件。`recovery="restart"` 打印原因并重下当前文件；
`recovery="pause"` 保留原文件并暂停，需恢复原后端或显式允许重下。
历史未标注后端的 `.aria2` 检查点继续视为 aria2 数据。
完整文件跨后端复用前仍检查身份和本地 SHA256。
临时 URL 失效时最多重新获取一次；ETag、大小或资源身份变化会拒绝复用。
下载地址不会写入状态、检查点或命令行。

## 可选 TOML

复制 `config/ghwf.example.toml` 为 `config/ghwf.local.toml` 后按需修改。
本地文件已加入 `.gitignore`；不需要安装新的下载依赖。
TOML 使用 Python 3.11+ 的标准库 `tomllib`；仓库支持的 Python 3.14 已包含该模块。
不使用 TOML 时仍可用原有独立入口。

```toml
[download]
backend = "auto"
aria2c_path = ""
connections = 4
timeout = 30
connect_timeout = 15
recovery = "restart"
```

配置固定从 engineering-ci 自身的 `config/ghwf.local.toml` 读取，
从消费者仓库运行 ghwf 时不会读取消费者目录下的同名文件。
可执行文件的相对路径也相对于 engineering-ci 根目录。
绝对 Windows/Linux 路径保持原样；不要把 Windows 工具路径复制到 Linux 配置。
`aria2c_path=""` 使用 PATH 查找 `aria2c`。

优先级：**CLI > 环境变量 > 本地 TOML > 内置默认值**。

| TOML 属性 | 环境变量 | CLI | 默认 |
| --- | --- | --- | --- |
| `backend` | `GHWF_DOWNLOAD_BACKEND` | `--download-backend` | `auto` |
| `aria2c_path` | `GHWF_ARIA2C_PATH` | `--aria2c` | `aria2c` |
| `connections` | `GHWF_DOWNLOAD_CONNECTIONS` | `--connections` | `4`，仅 aria2，范围 1..16 |
| `timeout` | `GHWF_DOWNLOAD_TIMEOUT` | `--download-timeout` | `30` 秒，读取 / 空闲超时 |
| `connect_timeout` | `GHWF_CONNECT_TIMEOUT` | `--connect-timeout` | `15` 秒，连接 / TLS 超时 |
| `process_timeout` | `GHWF_DOWNLOAD_PROCESS_TIMEOUT` | `--download-process-timeout` | `1800` 秒 |
| `recovery` | `GHWF_DOWNLOAD_RECOVERY` | `--download-recovery` | `restart`；可选 `pause` |

`--aria2c` 保留兼容性，覆盖其他来源的可执行文件配置；
若需要缺失工具时立即报错，同时指定 `--download-backend aria2`。
aria2 保留多连接、原生检查点和每次传输一次尝试的策略。
native 无 Range 请求，不模拟分段下载，也不会报告已经字节续传。

## Windows PowerShell

可参考或点入 `config/ghwf.windows.env.example.ps1`。
环境变量只对当前 shell 及其子进程生效。旧 ghwf 函数需按 [入口迁移说明](../scripts/README.md#旧-powershell-函数迁移) 更新为 download.py。

```powershell
$env:GHWF_DOWNLOAD_BACKEND = "auto"
$env:GHWF_DOWNLOAD_TIMEOUT = "60"
# 按需启用 aria2；路径可包含空格。
$env:GHWF_ARIA2C_PATH = "D:/tools/aria2/aria2c.exe"
ghwf --run RUN_ID --save-log --download-backend native
```

移除覆盖值：`Remove-Item Env:GHWF_DOWNLOAD_BACKEND`。

## Linux / POSIX shell

可参考或 source `config/ghwf.linux.env.example.sh`。

```sh
export GHWF_DOWNLOAD_BACKEND="auto"
export GHWF_DOWNLOAD_TIMEOUT="60"
# 未安装 aria2 时可省略该变量，auto 自动使用 native。
export GHWF_ARIA2C_PATH="/usr/bin/aria2c"
python /path/to/engineering-ci/scripts/download.py \
  --run RUN_ID --save-log --download-backend native
```

移除覆盖值：`unset GHWF_DOWNLOAD_BACKEND`。

## 故障分类

| 异常 | 含义 |
| --- | --- |
| `DownloadInterrupted` | 网络 / 进程中断，保留检查点，恢复同一收集阶段 |
| `DownloadConfigurationError` | 工具缺失或配置无效；检查后端、路径及参数 |
| `ResourceChanged` | 远端身份、ETag 或大小变化，拒绝复用缓存 |
| `DownloadIntegrityError` | 下载体积或 Digest 错误，不标记完成 |
| `DownloadAddressExpired` | 刷新一次后地址仍不可用，不进行密集重试 |

pytest 使用本地 HTTP、注入的进程和 HTTP 替身验证回退、恢复、URL 刷新及路径兼容。
测试不访问真实 GitHub Workflow。
