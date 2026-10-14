# 用户脚本入口

`scripts/` 只保留两个用户 CLI；业务实现位于 `src/engineering_ci/`。

| 脚本 | 职责 |
| --- | --- |
| `release.py` | 贯穿本地交付、审查合并与人工请求的正式发布；统一查询停点 |
| `download.py` | 查询、恢复和下载已有 Run，不能 Dispatch |

## 导航

- [入口维护约定](rules.md) — 用户 CLI 与 Workflow 内部调用的维护边界。
- [项目入口](../README.md) — 用户指南与技术文档入口。
