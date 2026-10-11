# Dagger 版本与本地环境

本轮使用 **v1.0.0-beta.16**：`dagger.json` 固定模块 Engine 版本，
共享 Workflow 的 `DAGGER_VERSION` 同时固定 CLI 和 Engine 镜像。
版本一致性测试防止两处出现偏差。不使用浮动 `latest`。
安装方式来自 [Dagger 官方文档](https://docs.dagger.io/getting-started/install/)。

Windows PowerShell 7：

```powershell
Invoke-WebRequest https://dl.dagger.io/dagger/install.ps1 -OutFile "$env:TEMP/install-dagger.ps1"
& "$env:TEMP/install-dagger.ps1" -DaggerVersion 1.0.0-beta.16 -AddToPath
# 新开终端使用户 PATH 生效。
dagger version
```

Linux：

```sh
curl -fsSL https://dl.dagger.io/dagger/install.sh -o /tmp/install-dagger.sh
DAGGER_VERSION=1.0.0-beta.16 BIN_DIR="$HOME/.local/bin" sh /tmp/install-dagger.sh
export PATH="$HOME/.local/bin:$PATH"
dagger version
```

需要可用的 Docker Engine。默认由 CLI 选择 Engine；使用已有测试容器时，
显式设置 `DAGGER_ENGINE=container://容器名`，并确认容器镜像是相同版本。
升级 CLI 不会升级另一个正在运行的旧 Engine。

## 现有模块的兼容调用

1.0 仍支持 `dagger.json`。本轮保留 `.dagger/src` 和消费者已有模块引用；
不运行会移动源码的 workspace 自动迁移。参见[官方迁移说明](https://docs.dagger.io/config/migrate-dagger-json/)。

```sh
dagger check
dagger check -l --format cli
dagger api call fixture-pytest-artifacts --scenario pass export --path .test-tools/pytest-report
```

`@check` fixture 返回 `None`，通过异常保留失败判定；诊断写到执行输出。
普通 `python_test` 和报告函数的返回契约保持不变。
报告模式通过真实检查列表生成原生选择器，只替代明确指定的 pytest 检查。
`remaining-checks.txt` 保留原有模块限定检查身份用于发布证据，原生选择器用于执行。

生成现有目录中的 SDK，并检查模块锁文件：

```sh
dagger api call --no-load-module module-source --ref-string . \
  generated-context-directory directory --path .dagger export --path .dagger
uv lock --project .dagger --check
```

SDK 必须由实际 Engine 生成，不能只检查旧 SDK 的锁文件而声称完成升级。
版本输出读取兼容 0.x 单行和 1.0 多行格式，Manifest 只保存校验后的版本值。
发布门禁和证据关联仍由[发布机制](../architecture/release/workflow.md)定义。
