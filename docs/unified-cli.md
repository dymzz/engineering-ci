# 统一 CLI 与 Workflow 输出

在 engineering-ci 源码仓库中使用安装后的命令：

```sh
uv run --locked engineering-ci --help
uv run --locked engineering-ci checks pr
uv run --locked engineering-ci version check
```

GitHub Actions 设置 PYTHONPATH 为当前 checkout 的 src，并直接使用
`python -m engineering_ci.cli`。所有实际调用位于同一个 Python 调用层；
scripts/manual_release.py、release_manifest.py、release_version.py 和 checks.py
保留为兼容入口。Release 策略和原生证据验证位于 release/，本地检查位于 checks.py。
工具版本集中维护；Release 检查注册表引用核心检查的实际 step 名称。

## 交互式选择

```sh
uv run --locked engineering-ci workflow --interactive --repo dymzz/engineering-ci --out workflow-artifacts
```

依次选择 Workflow、最近 30 个 Run 之一和未过期 Artifact 名称；0 表示所有名称，q
取消。菜单显示 GitHub 原生身份。已结束 Run 没有未过期 Artifact 时只收集日志；
尚未结束的 Run 会等待结束后收集输出，不提前关闭 Artifact 收集。选定的 Run ID
和实际分支传给原有 ghwf 恢复入口，保存日志并复用原有缓存、资源校验、安全解压、
auto/aria2/native 后端。菜单只查询和恢复已有 Run，不触发 Dispatch。
相同名称的 Artifact 按原有名称过滤语义收集，不按最大 ID 猜测最新 attempt。

首次使用应安装 gh 并运行 gh auth login。省略 repo 时按既有 Git origin 与缓存规则
识别当前消费者仓库。交互要求终端；非交互输入不会自动选择默认 Workflow 或 Run。

## Codex / 非交互调用

```sh
uv run --locked engineering-ci workflow --repo dymzz/engineering-ci --workflow release.yml --resume RUN_ID --save-log --out workflow-artifacts
uv run --locked engineering-ci workflow --repo dymzz/engineering-ci --workflow release.yml --ref main --show-state
```

RUN_ID 必须替换为实际 ID，分支不同于 main 时提供实际 --ref。所有既有 ghwf 参数
继续可用，包括 --input、--artifact、--no-artifacts、--new-run、--aria2c 和下载配置。
显式新 Dispatch 仍遵守既有单次 POST、UNKNOWN 与人工 Run ID 恢复约束。
只读菜单、下载与正式发布是不同操作；正式发布继续由人手动启动 GitHub Workflow。

更短的脚本名 scripts/ghwf.py 可替代 gh_workflow_artifacts.py；原路径继续可用：

```powershell
function ghwf {
    python 'D:\workspace\engineering-ci\scripts\ghwf.py' @args
}
```

此函数仍直接接受原有非交互参数，需 Python 环境已安装项目依赖。也可以使用
`uv run --project D:/workspace/engineering-ci --locked engineering-ci workflow`，
通过锁定的项目环境调用；--project 不改变消费者当前目录。交互选择使用上面的命令。
本轮不引入新的远程调度、工作流 FSM、发布数据库或消费者业务改动。
