# Agent 指令的作用域与职责

AGENTS.md 按适用范围组织开发指令。技术契约仍由既有设计文档和代码定义，执行配置仍由
TOML / YAML 等配置定义，当前任务进度与证据保留在任务或 PR 交付记录中。

## 分层

| 位置 | 负责 | 内容边界 |
| --- | --- | --- |
| 用户全局 `~/.codex/AGENTS.md` | 跨项目开发约定 | 授权范围、Git/验证纪律、大阶段备份和上游观察；不写项目版本、临时状态或业务架构 |
| [仓库根 AGENTS.md](../../AGENTS.md) | 本项目目标、依赖边界和开发入口 | CI/CD 职责与外部边界、当前仓库操作授权、验证/发布边界和专项指令导航 |
| `.github/AGENTS.md`（如存在） | Actions 文件的专项约束 | 远端 DAG、权限、复用接口、证据关联与 Secret 传递；不复写全部 Job/Step 定义 |
| [.dagger/AGENTS.md](../../.dagger/AGENTS.md) | Dagger 文件的专项约束 | 容器执行、SDK/Engine、原始失败与单次 pytest、Secret 生命周期；不负责开发生命周期 |

## 加载与冲突

Codex CLI 官方说明按全局、仓库根到当前工作目录逐层发现并合并指令，后出现的目录更具体。
因此不能假定所有后代目录都存在或已加载专项文件；按当前根指令及实际文件确定适用范围，
最新用户指示具有明确优先级。来源：[OpenAI 官方指令加载说明](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide#using-agentsmd)。

子指令补充范围内的规则。层级解析并不代表可以无声扩大人的授权、降低质量门禁或更改项目位置；
发现实质冲突时报告具体条目，按当前用户指示处理，不用历史轨迹或生成配置替代授权。

全局共享规则维护一处；仓库根保留必要的项目边界和明确授权，专项文件引用现有技术契约。
上游观察只使用全局定义，本仓库日志位置为 `.agent/upstream-observations.md`，不再维护中英文两份规则。

## 操作与执行的区分

Agent 获得当前任务的 PR 自动合并授权后，可以通过 Git/gh 执行实际操作；
`release.py` 自身仍停在 REVIEW.waiting，不新增合并调度器。正式 Release 保持人工主动请求，
未覆盖的私有 Git 来源仍由安全门禁阻断，不因修改 AGENTS.md 自动获得豁免。
备份操作见[阶段分支备份](../../guides/common-tasks.md#大阶段分支备份)。

文档中的规划不能被记录为已实现能力。
