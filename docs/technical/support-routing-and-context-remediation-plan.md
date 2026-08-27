# Support 文档命中、对话上下文与降级机制修复执行计划

## 目标

在不增加新服务、数据表或检索链路的前提下，修复当前 Support 双链路中已确认的状态传递、上下文继承、索引一致性、错误降级与模型输出校验问题。目标是降低误切换、误检索和被掩盖的运行故障，同时保留：

```text
Profile 快速路由 → Support 文档级检索 → 聚焦文档辅助回答
```

## 已确认问题与处理原则

| 问题 | 根因 | 修复原则 |
| --- | --- | --- |
| 聚焦会话误提示切换 | SupportDocumentRouter 将所有问题按无焦点首轮决策 | 将当前焦点传入既有文档决策器，复用其 STAY/OFFER_SWITCH 规则 |
| 短新话题被历史污染 | 任意短问题都拼接上一条用户消息 | 只对明确的省略/承接式追问补充上一轮上下文 |
| 切换后短追问缺失原因 | 触发切换的问题留在旧 focus epoch，交付时又重置上下文 | 将已确认的切换原因以受控的用户事实保留在新焦点工作流中 |
| 冻结索引与 Profile 查询不一致 | 显式 problem_code/document_id 查询未传入 turn.index_id | 所有 Support Profile 读取统一使用会话冻结 index_id |
| 永久 Rerank 故障被伪装为澄清 | Support 未区分 degradable 与 non-degradable RerankError | 仅临时上游故障允许受控降级；配置、协议和内部错误显式失败并记录 Trace |
| 顶层 answer 绕过安全校验 | 校验器只检查 sections | 为 answer 增加内部证据字段；校验 IP 与高风险操作，高风险无证据时改为安全升级提示 |

## 实施范围

### 1. 文档级路由

- 为 `SupportDocumentRouter.resolve` 增加可选焦点文档参数；
- 聚焦态传入当前文档 ID，沿用已有的停留和切换阈值；
- 路由器显式返回 STAY/OFFER_SWITCH，Service 仅将真正的跨文档候选呈现为确认操作；
- 首轮 Discovery 保持现有 DELIVER / CLARIFY / NO_MATCH 行为。

### 2. 对话历史与切换上下文

- 短追问只在包含承接语义时追加最近用户问题；
- 对包含产品、型号或明确任务的新问题不追加历史；
- 用户确认切换后，将上一轮“需要切换的原始问题”写入新焦点 workflow 的用户观察，以便下一轮短追问可用；
- 不把旧文档的整段聊天记录带入新焦点，继续保持文档隔离。

### 3. 发布版本一致性

- `get_profile_by_code` 与 `get_profile_by_document` 接收可选 `index_id`；
- Support Service 的所有文档/Profile 读取都传入 `turn.index_id`；
- 显式 document_id、problem_code、候选确认和聚焦加载使用同一个冻结索引视图。

### 4. 受控降级与可观测性

- Rerank 的临时上游错误：保留候选但禁止自动交付，返回澄清；
- Rerank 的配置/协议错误：返回 API 错误，Trace 标记失败；
- Embedding 或未知内部错误：显式失败，禁止伪装为 NO_MATCH 后再次调用同一失效依赖；
- 保留已有 Gateway 的业务 NO_MATCH → Generic RAG 兜底，不把运行故障视为业务 NO_MATCH。

### 5. 模型回答校验

- `SupportDraft` 增加仅供服务端校验的 `answer_evidence_element_ids`；
- Prompt 要求 answer 使用文档专有事实或高风险操作时携带证据；
- 顶层 answer 中的 IP 必须在当前文档中出现；
- 高风险 answer 若无有效文档证据，替换为安全暂停提示并设置 escalation；
- 不改变前端返回结构，不向用户展示内部证据 ID。

## 不在本次范围内

- 不新增第三条检索链路，不改写 Embedding、BM25、RRF 或 Rerank 基础流程；
- 不引入人工审核作为运行时前置条件；
- 不迁移或重写已有 Profile 数据；
- 不改动 Generic RAG 已有的独立多轮查询改写机制；
- 不引入备用 LLM/Rerank Provider 自动切换策略。

## 验收标准

1. 聚焦文档 A 的普通追问不会因为文档 B 的低分候选而提示切换。
2. 只有 B 明显优于 A 时才展示切换确认，且不自动切换。
3. “还是失败怎么办”继承上一轮；“FFC怎么更换”不会因上一轮 PDC 问题被拼接污染。
4. 切换后“下一步呢”可从 workflow 中看到触发切换的原始用户问题。
5. 显式文档、问题编码和候选确认均使用会话冻结索引。
6. Rerank 暂时不可用时不自动交付；Rerank 配置/协议错误时 Trace 失败而非伪装 NO_MATCH。
7. 无证据的高风险顶层回答不能直接交付给用户。
8. 新增测试覆盖上述行为，现有后端和前端回归通过。
