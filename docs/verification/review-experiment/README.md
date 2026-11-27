# 结构化审阅试验

本目录仅保存显式试验消费入口与验证记录，规范见 [ECI 审阅要求](../../architecture/eci-review-requirements.md)，机器合同见 [结果 Schema](../../architecture/eci-review-result.schema.json)。

- [validate.py](validate.py) 使用固定 jsonschema 4.26.0（PEP 723），校验实际结果与宿主取得的请求、基线、规范摘要及局部引用。命令见规范 §5.2。
- 2026-11-27：以合成实例对 Draft 2020-12 元 Schema 及16项正反例进行验证。完整、明确单中心、合法分批通过；全未审 accept、全量缩成单中心、分批 accept、缺规则、错 HEAD、错规范摘要、悬空证据、重复证据 ID、未知 accept、未解决项 accept、错任务类型、部分发现 accept、不适用无证据均拒绝。使用 Python 环境的 jsonschema 4.26.0；未运行 ECI 全量工程验证。合成记录不证明实际审阅完成。
- 原 #165 试验报告只有 Markdown，不能当作符合新合同的真实 JSON；后续实际试验须独立固定规范版本及受审 HEAD，保留结果与校验输出。

脚本不验证真实证据内容、未知关系完整性或授权，不接入默认 Hook、CI 或正式发布。宿主自动消费尚待实际验收。


2026-11-27 两次 #165 Chat 试验未交付 JSON 或实际校验记录；第二次把旧 PR base 当作 main。规范已合入 main，但普通 Chat 不保证自动加载 AGENTS.md。修订后直接读取审阅要求，独立查询 main，合并重复规则表；本次只调整文档入口与组织，不修改 2.0 Schema 或 Validator，不声称真实审阅试验通过。

## 实施与验收要求

| 编号 | ECI 实际验收场景 |
|---|---|
| ECI-AC01 规范与范围 | 实际 PR/Issue 绑定当前基线、历史裁决及适用工程合同，仅以普通审阅请求启动，自动获取基线及事实，输出四中心各自义务，不依赖用户预制输入或 Agent 自报分母 |
| ECI-AC02 关系发现 | 用 #165 历史案例检出 Fixture→测试消费者、Dagger helper→测试映射，以及 Source 物化/递归收集关系；区分原生探针、源码推断、真实生产验收 |
| ECI-AC03 跨入口与依赖 | 用 #168 历史根 manifest→Dagger lock、xdist→报告、#169 生产消费/多 Ref 与身份案例检查未修改对象及实际入口；不把相同 SHA 当同次 Invocation |
| ECI-AC04 公共合同复用 | 在实际新变更中定位已有 Check/Source/NativeResult/证据/报告等公共接口，发现有证据的重复实现并说明修复位置；成熟原生能力不重造 |
| ECI-AC05 输出真实强制 | 实际消费 Knowledge 固定 Schema/Validator；故意遗漏必审义务、错版本、缺引用、非法状态的产物不能作为完整审阅交付；合法部分可作为诊断 |
| ECI-AC06 阶段、增量与汇聚 | 全量请求必须有 A0–A5 的实际 MD 接续记录，四视点分别交付 partial_batch 且保留原请求范围；中断后继续未完成子批，缺口回对应中心补审；不因复杂度/严重缺陷/CI/轮数省略阶段或诱导停止。多视点保留原缺陷身份及共享证据，无关变更不重跑；新增关系触发对应义务。实际汇聚及校验须独立验证，不将单批冒充全量 |
| ECI-AC07 工程授权边界 | 审阅符合性、实际原生验证、Trusted/Required/授权分别核对；未完成不得冒充已验收；不引入新调度、状态、门禁或图谱系统 |
| ECI-AC08 实际收益及演进 | 在未参与规则设计的真实新变更上评估四视点是否发现新对象、关系、职责、合同、不变量和验证义务及迁移影响；不得因不匹配历史技术栈或任务列表而排除。比较遗漏、错误建议、重复核查、证据复用及成本；历史回放只验证特定检查能力，不证明未知缺陷发现率 |

历史案例来源：[PR #165 Review 6084403085](https://github.com/dymzz/engineering-ci/pull/165#pullrequestreview-6084403085)、[PR #168 Review 6075021820](https://github.com/dymzz/engineering-ci/pull/168#pullrequestreview-6075021820)、[PR #169](https://github.com/dymzz/engineering-ci/pull/169)。上述历史案例是验收设计输入，不是当前 PR 状态判断或本轮实测。

限制：本次修订保留原32类规则与全部工程关系；结构及试验消费合同为2.0。规则族不是数学完备性证明，未知关系发现率和实际审阅收益需通过真实试验评估。

上级：[验证记录](../README.md)。
