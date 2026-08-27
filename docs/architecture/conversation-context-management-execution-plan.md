# 双模式用户对话上下文管理轻量执行计划

状态：代码实施完成，待数据库迁移部署与 PostgreSQL 环境验收  
计划日期：2026-08-27  
前置版本：Engine Routing 收敛（数据库版本 `0033`）  
目标版本：Conversation Context V1  
风险等级：中低  
批准记录：2026-08-27 用户确认“开始执行”；本次仅提交可审阅的代码、迁移和测试，不直接变更共享数据库。

## 1. 决策摘要

本计划采用轻量改造，不建设独立的“对话记忆平台”。目标是在不改变两种回答模式和现有降级流程的前提下，解决当前最现实的上下文稳定性问题：

1. 文档切换后不能继承旧文档的步骤、观察和缺失信息。
2. Focused Support 只能读取当前焦点周期的历史。
3. Support→RAG 单轮降级不能重复注入当前问题或内部 `NO_MATCH`。
4. 待确认文档候选不能跨焦点周期继续生效。
5. 焦点、工作流和候选更新必须受到现有版本锁保护。

V1 只做以下结构性变更：

- 为 `message` 增加一个可空的 `focus_epoch` 字段。
- 固定并校验 `conversation.chat_context` 的 V1 Schema。
- 文档首次聚焦或确认切换时递增 `focus_epoch`，并重置 Support 工作流。
- 扩展现有 `support_context_version` 的保护范围，不重命名、不新增第二套版本号。
- 按回答模式和 `focus_epoch` 选择有限历史窗口。
- 为 `pending_switch_options` 增加来源 epoch 和必要来源信息。

V1 明确不做：

- 不新增 `conversation_support_state` 表。
- 不新增 `conversation_summary` 表。
- 不增加 LLM 历史摘要调用。
- 不进行新旧状态双写。
- 不重构所有 Gateway/RAG 事务。
- 不新增跨会话长期记忆、用户画像或向量记忆。

## 2. 为什么采用轻量方案

当前项目已经拥有可用的消息表、会话焦点、焦点 epoch、候选字段和 Support 工作流 JSON。继续增加状态表、摘要模型和全链路事务，会带来双写分歧、额外模型失败、迁移回滚困难和大范围回归。

轻量 V1 的稳定性原则是：

- 优先复用现有字段和事务。
- 只新增一个不可替代的消息维度：`focus_epoch`。
- 用固定窗口控制 Prompt，不引入摘要模型。
- 只在会改变业务状态的操作上使用乐观锁。
- 保持 RAG-only 主流程不变。
- 保留未来升级空间，但不提前实现未被真实负载证明需要的能力。

预计影响：一个小型数据库迁移，主要修改 Support Repository/Service、少量 RAG 焦点写入和相关测试；前端原则上无业务改动。

## 3. 当前双模式和降级边界

### 3.1 `SUPPORT_WITH_RAG_FALLBACK`

无焦点时处于 `SUPPORT_DISCOVERY`：

```text
当前问题
↓
Profile 路由
├─ 唯一命中 → 交付文档 → FOCUSED_SUPPORT
└─ 未命中/冲突
   ↓
Support 文档级检索
├─ 高置信单文档 → 交付文档 → FOCUSED_SUPPORT
├─ 多候选 → CLARIFICATION_REQUIRED
└─ 无可靠文档 → NO_MATCH → 完整 RAG 单轮兜底
```

有焦点时处于 `FOCUSED_SUPPORT`：

- 当前文档相关问题：围绕完整文档回答并更新进度。
- 明显属于另一篇文档：只建议切换并等待确认。
- 其他文档也无可靠匹配：询问更多信息或说明无法确认。
- 与知识库完全无关：拒绝回答。
- 高风险操作：建议人工升级。
- 不执行完整 RAG 回答。

### 3.2 `RAG_ONLY`

继续执行现有完整流程：

```text
历史读取
→ Query Rewrite
→ 向量/BM25 召回
→ RRF
→ Rerank
→ 上下文构建
→ 生成
→ 引用校验
```

V1 不修改 RAG-only 的 Prompt、历史窗口、文档路由和回答生成行为。

### 3.3 单轮降级不变量

- 只有 Hybrid Discovery 返回 `NO_MATCH` 才允许完整 RAG。
- `CLARIFICATION_REQUIRED`、人工升级、模型错误和 Focused Support 偏题不触发完整 RAG。
- RAG 只负责本轮兜底，`last_execution_engine=RAG` 不决定下一轮路由。
- RAG 交付单文档且该文档满足 Support 可用条件时，下一轮进入 Focused Support。
- RAG 跨文档回答或无法确认时，下一轮重新进入 Support Discovery。
- Support 和 RAG 使用同一组业务消息，内部 Support 探测只保留 Trace。

## 4. 当前问题与V1处理方式

| 当前问题 | V1处理 |
|---|---|
| `chat_context` 内容由不同路径自由拼装 | 固定 V1 Schema，所有读写通过统一模型校验 |
| 切换文档后可能残留旧步骤 | 建立新焦点时原子重置 `support_workflow` |
| 消息无法按焦点周期筛选 | 给 `message` 增加 `focus_epoch` |
| 待切换候选可能在焦点变化后继续有效 | 候选记录 `origin_focus_epoch`，确认时校验 |
| RAG 设置焦点未完全受 Support 版本锁保护 | 焦点晋升使用现有 `support_context_version` CAS |
| 长历史可能污染当前回答 | 按模式使用固定消息窗口，不增加模型摘要 |
| RAG fallback 可能重复携带本轮问题 | Context Builder 明确排除复用消息对和内部探测 |

## 5. 数据模型调整

### 5.1 唯一新增字段

为 `message` 增加：

```text
focus_epoch integer null
```

含义：该消息最终所属的焦点周期。

写入规则：

- 新会话无焦点时，消息初始使用当前会话 epoch，通常为 `0`。
- 本轮没有改变焦点时，用户消息和助手消息保留本轮开始时的 epoch。
- 本轮首次交付文档时，焦点从无到有，`focus_epoch + 1`；本轮用户消息和最终助手消息一起更新为新 epoch。
- 用户确认 A→B 时，`focus_epoch + 1`；确认消息和文档交付消息一起属于新 epoch。
- Hybrid RAG 兜底交付并成功晋升单文档时，本轮消息属于新 epoch。
- RAG 跨文档回答或无法确认时，不递增 epoch。
- RAG-only 继续现有行为；V1 可以记录当前 epoch，但不改变其历史选择逻辑。

历史数据回填：

- 无法从现有数据准确恢复每条旧消息的焦点周期，因此不伪造精确历史。
- 已有消息保持 `NULL`，兼容读取时将 `NULL` 视为 legacy history。
- 新版本产生的消息开始精确记录 epoch。

### 5.2 保留现有会话字段

继续使用：

```text
execution_policy_snapshot
conversation_mode
last_execution_engine
focus_document_id
focus_index_id
focus_epoch
pending_switch_options
support_context_version
chat_context
```

本轮不重命名 `support_context_version`。虽然名称偏向 Support，但直接重命名会扩大迁移和回归范围。代码注释明确其 V1 同时保护焦点、候选和 Support 工作流即可。

## 6. 固定 `chat_context` Schema

V1 将 `chat_context` 固定为：

```json
{
  "schema_version": 2,
  "support_workflow": {
    "focus_epoch": 1,
    "document_id": "...",
    "confirmed_completed_steps": [],
    "current_step": null,
    "user_observations": [],
    "collected_information": {},
    "missing_information": [],
    "unverified_hypotheses": [],
    "risk_flags": [],
    "escalation_status": null
  },
  "discovery": {
    "last_clarification_question": null,
    "collected_information": {}
  }
}
```

### 6.1 字段职责

`support_workflow`：

- 只在 `FOCUSED_SUPPORT` 中读取和更新。
- 必须与当前 `focus_epoch` 和 `focus_document_id` 一致。
- 保存已确认步骤、用户观察、已收集信息和缺失信息。
- 未验证推断始终放在 `unverified_hypotheses`，不得提升为文档事实。

`discovery`：

- 只保存无焦点阶段为文档路由收集的少量用户信息。
- 例如上一轮要求补充设备型号，下一轮用户只回答“X200”。
- 不保存完整 RAG 回答、模型推断或文档内容。

`pending_switch_options`：

- 继续使用 `conversation` 独立字段。
- 不再镜像到 `chat_context.pending_options`。

### 6.2 兼容读取

读取旧 `chat_context` 时：

1. 如果 `schema_version=2`，严格按 V1 模型校验。
2. 如果是现有旧格式，将合法 workflow 字段映射进 `support_workflow`。
3. 旧 `pending_options` 只在独立字段为空时读取一次，并在下一次成功状态写入时迁移。
4. 无法识别的字段忽略并记录 Trace 诊断，不让整轮回答失败。
5. 不做双写；新版本成功写入后只写 V1 Schema 和独立候选字段。

## 7. 焦点与工作流生命周期

### 7.1 首次建立焦点

在一个现有事务中完成：

```text
next_epoch = focus_epoch + 1
focus_document_id = 命中文档
focus_index_id = 当前固定 Support Index
focus_epoch = next_epoch
conversation_mode = FOCUSED_SUPPORT
pending_switch_options = []
chat_context.support_workflow = 新空工作流
本轮消息 focus_epoch = next_epoch
support_context_version += 1
```

### 7.2 当前文档连续问答

- 不改变 `focus_epoch`。
- 只更新当前 `support_workflow`。
- 本轮消息使用当前 epoch。
- 使用 `support_context_version` 进行乐观锁更新。

### 7.3 建议切换文档

- 当前焦点保持不变。
- 当前 epoch 不变。
- 将候选写入 `pending_switch_options`。
- 不清空当前 workflow。
- 返回 `CLARIFICATION_REQUIRED`。
- 不执行完整 RAG。

### 7.4 确认切换文档

在一个事务中完成：

```text
校验 choice_id
校验 origin_focus_epoch == conversation.focus_epoch
校验候选文档属于当前知识库且 Support 可用
next_epoch = focus_epoch + 1
更新 focus_document_id / focus_index_id
focus_epoch = next_epoch
pending_switch_options = []
support_workflow = 新文档空工作流
本轮消息 focus_epoch = next_epoch
support_context_version += 1
```

旧文档的 workflow 不迁移到新文档。旧消息仍保留，可供审计和用户界面展示，但不进入新文档的默认 Prompt 历史。

### 7.5 RAG 单文档晋升

Hybrid RAG 兜底只有满足以下条件时才能建立 Support 焦点：

- RAG 结果明确交付单一文档。
- 文档属于当前知识库。
- 文档存在于会话可使用的 Support Index。
- 文档满足 Support-ready 条件。

满足条件时按“首次建立焦点”处理；否则保持 `SUPPORT_DISCOVERY`，不创建一个下一轮无法加载的焦点。

## 8. 待确认文档候选

候选 V1 Schema：

```json
[
  {
    "choice_id": "switch_1",
    "document_id": "...",
    "title": "...",
    "problem_code": "...",
    "routing_method": "DOCUMENT_ROUTER",
    "confidence": 0.91,
    "origin_question": "...",
    "origin_focus_epoch": 2,
    "created_at": "2026-08-27T10:00:00Z"
  }
]
```

V1 不引入定时过期任务。确认时只做必要校验：

- `choice_id` 属于当前候选集合。
- `origin_focus_epoch` 等于当前 epoch。
- 目标文档当前仍然存在、属于该知识库且 Support-ready。

校验失败时：

- 不切换焦点。
- 清理无效候选。
- 返回 `DOCUMENT_SWITCH_EXPIRED`，提示用户重新描述当前问题。

## 9. 不同模式的历史选择

### 9.1 Support Discovery

目标是补全路由问题，不进行全文对话记忆。

使用：

- 当前用户问题。
- `discovery.last_clarification_question`。
- `discovery.collected_information`。
- 最近最多 2 条用户业务消息。
- 当前有效文档候选。

不使用：

- 旧助手的完整回答。
- 旧焦点文档 workflow。
- RAG 未验证推断。
- 内部 Support `NO_MATCH`。

对于“型号是 X200”这类省略回答，由确定性代码拼接上一轮澄清问题，不新增 LLM 路由调用。

### 9.2 Focused Support

Prompt 上下文按以下顺序组装：

1. 当前 Focused Support 系统与安全规则。
2. 当前焦点文档。
3. 当前 `support_workflow`。
4. 当前 epoch 最近最多 6 条已完成业务消息。
5. 当前用户问题。

历史查询条件：

```text
conversation_id = 当前会话
status = COMPLETED
focus_epoch = 当前 epoch
最近最多 6 条
```

Legacy 消息的 `focus_epoch=NULL` 默认不进入新 epoch Prompt。为了兼容升级时已经聚焦的旧会话，可以在“尚未产生任何带 epoch 的消息”时最多带入最近 2 条 legacy 消息；一旦产生新格式消息即停止该兼容行为。

### 9.3 Hybrid RAG 单轮兜底

保持现有 RAG 生成流程，只修正历史来源：

- 使用最近最多 8 条已完成业务消息。
- 排除本轮复用的用户消息和助手占位，当前问题由单独参数传入一次。
- 排除 Support 内部 `NO_MATCH` 探测。
- 不读取 Focused Support workflow，因为完整 RAG 只发生在 Discovery。

### 9.4 RAG-only

V1 不修改现有历史窗口和上下文组装。新增的消息 epoch 只作为元数据写入，不参与 RAG-only 的历史过滤，避免改变现有完整 RAG 行为。

## 10. Prompt预算策略

V1 不增加历史摘要，采用固定窗口和现有预算裁剪：

| 场景 | 最大历史 |
|---|---:|
| Support Discovery | 最近2条用户消息 |
| Focused Support | 当前epoch最近6条业务消息 |
| Hybrid RAG fallback | 最近8条业务消息 |
| RAG-only | 保持当前配置 |

Focused Support 的裁剪顺序保持：

1. 删除旧历史。
2. 保留当前工作流中的已确认进度、用户观察和缺失信息。
3. 最后才按现有策略选择相关文档元素。
4. 文档发生裁剪时继续向用户返回不完整上下文提示。

是否需要 LLM 摘要必须依据真实数据决定。只有出现大量超过20轮的有效会话，并且固定窗口与工作流仍无法保持必要信息时，才单独提出 Context V2 摘要计划。

## 11. 乐观锁和事务边界

### 11.1 继续使用现有版本号

不新增 `conversation_state_version`，继续使用：

```text
support_context_version
```

V1 将其解释为“会影响 Hybrid Support 上下文的状态版本”。

### 11.2 必须执行CAS的操作

- 创建或更新 Support workflow。
- 首次设置 Support 焦点。
- 写入或清理待切换候选。
- 用户确认切换焦点。
- Hybrid RAG 单文档晋升为 Support 焦点。

更新形式：

```sql
UPDATE conversation
SET ...,
    support_context_version = support_context_version + 1
WHERE id = :conversation_id
  AND support_context_version = :expected_version
```

失败返回现有 `WORKFLOW_CONFLICT`，或在保持兼容的前提下统一为 `CONVERSATION_STATE_CONFLICT`。具体错误码在执行阶段先检查现有客户端和测试依赖后决定。

### 11.3 不需要CAS的派生展示字段

`last_execution_engine` 只是本轮最终执行结果，不决定下一轮路由。它可以继续由 Gateway 在回答完成后更新，不与 workflow 共用 CAS，避免把普通展示更新扩大成业务冲突。

## 12. 数据库迁移

计划新增迁移：

```text
0034_message_focus_epoch
```

迁移内容：

```text
ALTER TABLE message ADD COLUMN focus_epoch integer;
CREATE INDEX ix_message_conversation_focus_epoch_sequence
ON message (conversation_id, focus_epoch, sequence_no);
```

约束：

- 字段允许为空，确保历史数据和应用回滚兼容。
- 不批量猜测旧消息 epoch。
- 不增加新表。
- 不删除或重命名现有字段。
- downgrade 只在确认不存在依赖后删除索引和新增列；生产是否执行 downgrade 仍按项目迁移规范处理。

`chat_context` Schema 升级属于应用层惰性迁移：旧会话在下一次成功完成 Support 状态写入时转为 V1 Schema，不执行大规模 JSON 数据重写。

## 13. 分阶段执行计划

### L0：冻结现有行为和测试基线

状态：`DONE`（2026-08-27）

- 固定 Hybrid 首轮、Focused Support、RAG fallback 和 RAG-only 的现有契约。
- 确认当前数据库版本和工作树状态。
- 补充“RAG 后下一轮回到 Support”和“Focused Support 不完整降级”的回归测试。
- 记录完整测试中与本计划无关的既有失败，避免错误归因。

验收：未改变运行行为，相关基线测试稳定。

### L1：消息 epoch 迁移与领域模型

状态：`DONE`（迁移待部署）

- 新增 `0034_message_focus_epoch`。
- Turn 模型携带当前 `focus_epoch`。
- 创建消息时写入当前 epoch。
- 建立或切换焦点时更新本轮消息为新 epoch。
- 增加 PostgreSQL 迁移和消息持久化测试。

验收：新消息准确记录 epoch，旧消息为空时仍可读取。

### L2：固定 Context Schema和切换重置

状态：`DONE`

- 为 `chat_context` 增加严格领域模型与兼容解析器。
- 将 workflow 和 discovery 分区。
- 停止把 pending options 镜像到 `chat_context`。
- 首次聚焦和确认切换时生成新空 workflow。
- 当前文档连续问答只更新当前 epoch workflow。

验收：文档A切到文档B后，B的Prompt不含A的步骤、观察和文档证据。

### L3：按模式选择历史

状态：`DONE`

- Support Repository 增加按 epoch 查询历史。
- Discovery 使用最多2条用户消息和结构化补充信息。
- Focused Support 使用当前 epoch 最多6条业务消息。
- Hybrid RAG fallback 明确排除本轮复用消息和内部探测。
- RAG-only 保持现有行为。

验收：同一会话在四种路径中选择的历史符合本计划规则，没有当前问题重复注入。

### L4：候选校验和有限CAS

状态：`DONE`（并发 PostgreSQL 验收待环境）

- 扩展候选 V1 Schema。
- 确认选择时校验 origin epoch 和文档可用性。
- 将焦点、候选和 workflow 更新纳入现有版本锁。
- Hybrid RAG 晋升焦点时使用同一版本保护。
- 增加并发和重复确认测试。

验收：旧候选不能切换新焦点；并发请求不能把旧 workflow 写回。

### L5：回归、观测和文档更新

状态：`DONE`（数据库集成项待环境）

- Trace 继续复用现有 routing/workflow 字段；新增专门的历史诊断字段延后到出现线上排障需求时再评估。
- 不增加新指标系统和摘要观测。
- 执行后端静态检查、相关测试和 PostgreSQL 集成测试。
- 执行前端现有回归，确认 DemoChat 无需业务改动。
- 更新本文档实际结果和遗留风险。

验收：两种回答模式和降级机制不变，上下文隔离测试全部通过。

## 14. 预计代码影响范围

主要影响：

```text
backend/alembic/versions/0034_message_focus_epoch.py
backend/app/support/models.py
backend/app/support/repository.py
backend/app/support/service.py
backend/app/support/workflow_state.py
backend/app/rag/models.py
backend/app/rag/repository.py
backend/app/services/rag.py
backend/tests/
```

可能的小范围影响：

```text
backend/app/gateway/repository.py
backend/app/gateway/service.py
backend/app/api/schemas.py
```

原则上不修改：

```text
RAG检索、Rerank、生成和引用校验算法
Engine Routing管理策略
DemoChat正式入口
Support安全和风险边界
前端路由控制面
```

执行前必须通过 `rg` 确认实际依赖，避免为了符合预估清单修改无关文件。

## 15. 测试矩阵

### 15.1 基础历史与焦点

| 编号 | 场景 | 预期 |
|---|---|---|
| V01 | Hybrid 首轮 Profile 命中 | 本轮消息写入新 epoch，创建空 workflow |
| V02 | Support 文档级检索命中 | 不调用完整 RAG，建立焦点和新 epoch |
| V03 | Support 多候选 | 保持当前 epoch，不触发完整 RAG |
| V04 | Support `NO_MATCH` | 单轮 RAG，当前问题只进入模型一次 |
| V05 | RAG 交付 Support-ready 单文档 | 晋升焦点，本轮消息进入新 epoch |
| V06 | RAG 跨文档回答 | 不建立焦点，下一轮重新 Support Discovery |
| V07 | RAG-only 连续对话 | 行为和历史窗口保持现状 |

### 15.2 Focused Support隔离

| 编号 | 场景 | 预期 |
|---|---|---|
| V08 | 当前文档连续追问 | 只使用当前 epoch 最近6条消息 |
| V09 | 文档A完成两步后切到B | B workflow为空，不包含A步骤 |
| V10 | A→B后询问“刚才第三步” | 不把A第三步当成B上下文 |
| V11 | Focused Support明显转题 | 只返回切换候选，不执行完整 RAG |
| V12 | Focused Support知识库外问题 | 拒绝且不污染 workflow |

### 15.3 候选与并发

| 编号 | 场景 | 预期 |
|---|---|---|
| V13 | 当前 epoch 确认候选 | 原子切换并递增一次 epoch |
| V14 | 点击旧 epoch 候选 | 返回候选失效，不切换 |
| V15 | 候选文档已不可用 | 清理候选，不建立无效焦点 |
| V16 | 重复提交同一确认 | epoch 只增加一次 |
| V17 | 两个并发 workflow 更新 | 一个成功，一个冲突，不覆盖新状态 |
| V18 | RAG晋升期间焦点已变化 | 旧RAG结果不能覆盖新焦点 |

### 15.4 兼容和迁移

| 编号 | 场景 | 预期 |
|---|---|---|
| V19 | 旧消息 `focus_epoch=NULL` | 会话可继续回答，不发生验证错误 |
| V20 | 旧格式 `chat_context` | 能解析并在下一次成功写入时转换 |
| V21 | 异常旧 JSON | 安全回退到空字段并记录诊断 |
| V22 | 迁移 downgrade演练 | 只移除新增列和索引，不损坏旧数据 |

## 16. 验证要求

实施后至少执行：

```text
后端 Ruff
后端严格 Mypy
相关后端 Pytest
PostgreSQL迁移测试
Support PostgreSQL集成测试
并发和幂等专项测试
前端 ESLint
前端 Vue Type Check
前端 Vitest
git diff --check
```

如果完整后端测试仍被本计划之外的既有测试收集问题阻塞，需要明确记录；不能把相关专项测试通过描述成“完整测试全部通过”。

## 17. 风险与控制

| 风险 | 控制措施 |
|---|---|
| 旧消息没有准确 epoch | 允许NULL并采用有限 legacy兼容，不猜测历史 |
| `chat_context` 旧格式多样 | 集中兼容解析，未知字段忽略并记录诊断 |
| 固定窗口遗漏长期信息 | 依靠结构化 workflow 保留进度；观察真实长会话后再决定摘要 |
| RAG错误晋升不可用于Support的文档 | 晋升前执行 Support-ready 和固定Index门禁 |
| 扩展版本锁增加冲突 | 只保护业务状态写入，不保护展示字段；返回明确可重试错误 |
| 切换时消息epoch写入不一致 | 消息、焦点、workflow和版本号在同一事务更新 |
| 修改影响RAG-only | 为RAG-only建立不变性测试，V1不替换其Context Builder |

## 18. 延后项目

以下内容只有在真实使用数据证明必要时才重新立项：

- 独立 `conversation_support_state` 表。
- 独立 `conversation_summary` 表。
- LLM自动摘要。
- 事实级来源追踪和消息级引用图。
- 全局统一 `conversation_state_version`。
- 所有Gateway/RAG状态的统一事务编排。
- 向量化长期记忆和跨会话用户画像。
- 候选定时过期任务和后台清理任务。

触发重新评估的建议条件：

- 大量有效会话超过20轮，并且结构化 workflow 仍不足以保持必要信息。
- `chat_context` 大小持续接近数据库或 Prompt 预算上限。
- 需要跨文档恢复旧工作流，而不是切换后重新开始。
- 当前有限 CAS 无法控制已观测到的真实并发冲突。

## 19. 验收标准

全部满足后才可标记 V1 完成：

1. Hybrid 每轮继续优先 Support，RAG 降级仍然是单轮行为。
2. RAG-only 的 Rewrite、检索、Rerank、生成、引用和历史行为不变。
3. Focused Support 不触发完整 RAG。
4. 首次聚焦和确认切换会建立新 epoch 并重置 workflow。
5. Focused Support 默认只读取当前 epoch 历史。
6. 旧 epoch 的步骤、观察、推断和文档证据不会进入新文档 Prompt。
7. Support→RAG 只产生一组业务消息，当前问题不会重复进入 RAG Prompt。
8. 旧候选不能在新 epoch 中切换焦点。
9. 并发旧请求不能覆盖新焦点和新 workflow。
10. 旧会话和 `focus_epoch=NULL` 消息可以兼容继续使用。
11. 仅新增一个消息字段和一个索引，不新增状态表或模型调用。
12. 相关静态检查和单元测试通过；目标 PostgreSQL 上的迁移、集成与并发测试在部署前通过。

## 20. 执行与批准记录

代码实施结果如下：

| 阶段 | 状态 | 批准人/时间 | 完成结果 |
|---|---|---|---|
| L0 行为与测试基线 | DONE | 用户 / 2026-08-27 | 相关回归通过；全量后端收集被既有 `test_api.py` 导入错误阻塞。 |
| L1 消息epoch迁移 | DONE | 用户 / 2026-08-27 | 新增 `0034`，已通过 Alembic head 校验；待目标库执行升级。 |
| L2 Context Schema与切换重置 | DONE | 用户 / 2026-08-27 | Schema V2、旧格式兼容、独立候选字段已落地。 |
| L3 按模式选择历史 | DONE | 用户 / 2026-08-27 | Discovery 最多 2 条、Focused Support 当前 epoch 最多 6 条；RAG-only 未改。 |
| L4 候选校验与有限CAS | DONE | 用户 / 2026-08-27 | 候选绑定 origin epoch；Support/RAG 焦点与候选写入使用共享 CAS。 |
| L5 回归、观测和文档 | DONE | 用户 / 2026-08-27 | Ruff、Mypy、后端专项和前端检查通过；PostgreSQL 集成项因未配置连接跳过。 |

实际执行未新增状态表、摘要模型调用、跨会话记忆或 RAG-only 的检索/生成改造。部署前仍需在目标 PostgreSQL 上执行迁移与并发专项验收。
