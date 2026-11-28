# 用户脚本入口

`scripts/` 保留两个业务 CLI；薄本地入口负责宿主环境前置后分派原生操作，业务实现位于 `src/engineering_ci/`。

| 脚本 | 职责 |
| --- | --- |
| `engineering-ci.sh` / `engineering-ci.ps1` | 本地薄入口：解析共享环境/候选源码、执行只读就绪检查后分派 verify / cli / pytest / ruff；Windows PowerShell 调用 Git Bash |
| `eci-env.sh` | 上述入口与 Hook 共用的环境解析、候选源码绑定和就绪合同；被 source，不单独调用 |
| `lefthook-arguments.sh` | Git Hook 原生参数交接与环境绑定（由 Lefthook 调用） |
| `release.py` | 贯穿本地交付、审查合并与人工请求的正式发布；统一查询停点 |
| `download.py` | 查询、恢复和下载已有 Run，不能 Dispatch |

## 导航

- [入口维护约定](rules.md) — 用户 CLI、薄入口与 Workflow 内部调用的维护边界。
- [项目入口](../README.md) — 用户指南与技术文档入口。
