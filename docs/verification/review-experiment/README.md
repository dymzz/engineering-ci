# 结构化审阅试验

本目录仅保存显式试验消费入口与验证记录，规范见 [ECI 审阅要求](../../architecture/eci-review-requirements.md)，机器合同见 [结果 Schema](../../architecture/eci-review-result.schema.json)。

- [validate.py](validate.py) 使用固定 jsonschema 4.26.0（PEP 723），校验实际结果与宿主取得的请求、基线、规范摘要及局部引用。命令见规范 §5.2。
- 2026-11-27：以合成实例对 Draft 2020-12 元 Schema 及16项正反例进行验证。完整、明确单中心、合法分批通过；全未审 accept、全量缩成单中心、分批 accept、缺规则、错 HEAD、错规范摘要、悬空证据、重复证据 ID、未知 accept、未解决项 accept、错任务类型、部分发现 accept、不适用无证据均拒绝。使用 Python 环境的 jsonschema 4.26.0；未运行 ECI 全量工程验证。合成记录不证明实际审阅完成。
- 原 #165 试验报告只有 Markdown，不能当作符合新合同的真实 JSON；后续实际试验须独立固定规范版本及受审 HEAD，保留结果与校验输出。

脚本不验证真实证据内容、未知关系完整性或授权，不接入默认 Hook、CI 或正式发布。宿主自动消费尚待实际验收。

上级：[验证记录](../README.md)。
