# 上游库使用观察

## Psycopg Binary：Windows free-threaded Python 3.14 分发限制

- 状态：已确认的兼容性限制；不是已确认的上游 Bug，也不是数据库配置或镜像错误。
- 正常使用场景：v0.6.0 PostgreSQL 归档预检，引入 `psycopg[binary]==3.3.6` 后安装可选依赖。
- 实际环境：Windows AMD64；现有 `.venv` 为 CPython 3.14.8 free-threading build。
- 复现：在该环境执行 `uv sync --locked --extra trace --group dev`。
- 可观察结果：`psycopg-binary==3.3.6` 没有适合当前平台的 source distribution 或 wheel，安装失败。
- 核对依据：官方 PyPI 的该版本 Windows Python 3.14 分发为
  `psycopg_binary-3.3.6-cp314-cp314-win_amd64.whl`，未提供 Windows `cp314t` wheel。
- 当前处理：用户授权使用已安装的普通 CPython 3.14.3，原 checkout 的 .venv 更新后安装和真实 PostgreSQL 预检通过；未改数据库。
- 范围：仅影响采用 binary 实现的此运行时组合；不据此推断普通 CPython 3.14、纯 Python 实现或其他平台均不可用。
- 未开展额外上游调查，未搜索或提交上游 Issue/PR，未修改第三方库。
