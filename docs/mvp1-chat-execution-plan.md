# MVP1 Chat 执行计划：文档路由状态机与文档聚合

状态：已实现（后端迁移 `0009`、`app/rag/doc_router.py`、Chat 服务层与前端 DemoChat 支持；测试见 `tests/test_doc_router.py`、`tests/test_chat_doc_routing_flow.py`、`tests/test_chat_repository_state.py`）。本文档是 Chat 文档路由（Doc Routing）的设计与验收基准；涉及当前行为时，以代码和 Alembic 迁移头为准。

## 1. 背景与动机

golden-set 中 case_01（"如何U盘升级PDC"）的 Trace 归因结论：

1. **检索层成功**：目标文档（故障解决文档11）的 4 个关键 chunk 全部进入上下文，引用与配图正确。
2. **答案被截断**：`completion_tokens=2049` 撞上 `max_tokens=2048`，其中 reasoning 消耗 1512，正文在第 7 步中断（运维项：上调 `model_config.parameters.max_tokens` 或关闭 thinking）。
3. **50% 上下文为噪音**：8 个选中 chunk 中 S5–S8 来自不相关的文档10。
4. **语料无标题结构**：故障解决文档是平铺流程文，`section_path` 全空，章节级 Parent/Child 展开无从着力。

核心认知：**该知识库的文档定位是"问题解决流程文档"——一份文档 = 一个故障 = 一套完整方案**，整份文档才是价值单元。chunk 级检索对此类语料是过度工程；且整份文档仅约 3k token，全文进上下文绰绰有余。

由此确定 MVP1 架构：**文档 = 检索单元 = 回答单元**（retrieve small, answer big 的文档粒度版本）。

## 2. 架构设计

### 2.1 会话状态机

两个相位，从数据推导，不单独存枚举：

```
ALIGNING   (conversation.focus_document_id IS NULL)   对齐：理解需求、锁定文档
DOC_FOCUS  (conversation.focus_document_id NOT NULL)  聚焦：围绕锁定文档问答
```

转移图：

```
ALIGNING
  ├─ 聚合明确赢家 ────────────→ 锁定 + 投递全文（DELIVER，零 LLM）→ DOC_FOCUS
  ├─ 聚合多文档接近 ──────────→ LLM 生成澄清问题（CLARIFY），存 pending_options
  ├─ 澄清后回复"第二个"等 ────→ 选项解析命中 → 锁定 + 投递（DELIVER）
  ├─ 聚合全部低分 ────────────→ LLM 基于文档目录追问/回答（NO_MATCH）
  └─ "你可以帮我做什么" ──────→ 归入 NO_MATCH 路径：目录 + LLM 回答

DOC_FOCUS
  ├─ 命中当前文档（STAY）────→ 全文为上下文的文档问答（DOC_QA）
  ├─ 强证据指向其他文档 ──────→ 切换：解焦 → 锁定新文档 → 投递（SWITCH=DELIVER）
  ├─ 中等证据指向其他文档 ────→ 确认："这属于《X》，要切换吗？"（OFFER_SWITCH）
  └─ 当前文档零命中且无替代 ──→ 文档内回答"未涉及"，可主动提供目录
```

关键规则：

- **澄清回复解析优先于重新检索**：`pending_options` 非空时先做选项解析（规则法），命中直接锁定；未命中才走聚合。显式否定（"不用/算了"）清空 pending 后按正常决策处理。
- **去留判定是确定性的**：LLM 只用于澄清问题生成、选项辅助与文档问答；STAY/SWITCH 判定完全基于检索分数，可测试、可回放。
- 投递消息落库为摘要文本（`【已提供文档】《标题》`），**全文 blocks 不进 message.content**，防止经 history 回灌污染后续 prompt。

### 2.2 文档聚合（`app/rag/doc_router.py`，纯函数）

输入为 Rerank 后的 chunk 候选（管线不变：BM25 + 向量 + RRF + Rerank）。Rerank 降级时用 `1/rank` 作为分数回退（语义见 §2.4 注意事项）。

```
doc_hits(d)  = 该文档进入候选的 chunk 数
doc_score(d) = Σ 该文档 chunk 分数（按分数降序取前 m3_doc_agg_max_hits 个封顶）
doc_best(d)  = 该文档最高单 chunk 分数
```

ALIGNING 决策（三档）：

| 条件 | 动作 |
|---|---|
| `winner.hits ≥ m3_doc_agg_min_hits 且 winner.score ≥ m3_doc_agg_t_high` 且 `winner.score ≥ m3_doc_agg_ratio × runner.score` | LOCK → DELIVER |
| ≥2 个文档 `hits ≥ 1 且 score ≥ m3_doc_agg_t_low` | CLARIFY（取前 3 个） |
| 无文档达标 | NO_MATCH |

DOC_FOCUS 决策（对候选按 document_id 分区，**强切换证据优先于续留**——关键词重叠可能让聚焦文档的绝对分高于续留线，但问题实际已转移）：

| 条件 | 动作 |
|---|---|
| 其他文档满足 LOCK 条件 | SWITCH → DELIVER（优先判定） |
| 当前文档 `doc_best ≥ m3_doc_stay_score` | STAY → DOC_QA |
| 其他文档 `hits ≥ 1 且 score ≥ t_low` | OFFER_SWITCH（pending_options=[目标文档]） |
| 其余 | STAY（文档问答由 Prompt 引导说明"文档未涉及"） |

### 2.3 文档投递（DELIVER，零 LLM）

- 数据源：`document_element` 按 `sequence_no` 排序（保真度最高，无 chunk 前缀/重叠）；IMAGE 元素经 `image_asset.element_id` 关联资产。
- TEXT/TABLE → text block；IMAGE → image block（`image_asset_id`）。
- 响应 `response_type = "DOC_DELIVERED"`（新枚举），`answer` 为确定性摘要文本，`content` 为完整 blocks，`references` 含下载链接。
- SSE：`trace` → `source` → `done`，无 `message` delta 事件。
- **大文档守门**：文档估算 token 超过 `m3_doc_delivery_max_tokens` 时按 element 顺序截断投递，末尾追加截断说明并保留下载链接。

### 2.4 文档问答（DOC_QA）

- 上下文 = 当前文档全文（element 序列）；每个 IMAGE 元素注册为来源 `S1..Sk`（带 caption），文本事实不要求引用。
- 回答仍带 `[MODE:xxx]` 标记（沿用五模式后验），但 `post_validate` 以 `require_citations=False` 调用：无引用不降级 UNCONFIRMED；`[IMG:Sn]` 图片定位复用现有 content block 机制。
- 引用列表固定包含当前文档（文档级 reference + 下载链接），不依赖模型是否引用。

### 2.5 路由阶段 Prompt（CLARIFY / OFFER_SWITCH / NO_MATCH）

- 统一 ROUTING 模板：候选文档（编号）/文档目录 + 历史 + 问题；LLM 生成澄清或范围说明，直接输出内容（无模式标记）。
- 知识库无文档时跳过 LLM，直接返回 UNCONFIRMED 话术（零 LLM 路径）。

### 2.6 上下文管理

```
DOC_QA prompt = 文档全文（常量锚点，位置固定）
             + 最近 K 轮历史（复用 m3_history_max_messages / m3_history_token_budget 滑动截断）
             + 当前问题
```

不累加全程对话；切换文档后旧焦点只剩落库摘要。

## 3. 数据模型（迁移 0009）

```sql
ALTER TABLE conversation
  ADD COLUMN focus_document_id uuid
    REFERENCES document_source(id) ON DELETE SET NULL,  -- 文档删除自动回 ALIGNING
  ADD COLUMN chat_context jsonb NOT NULL DEFAULT '{}';
-- chat_context: {"pending_options": [{"document_id","title"}], "pending_query": str}
```

`Turn` 扩展 `focus_document_id` / `chat_context`（默认值，向后兼容）；`begin_turn` 读取会话状态；`PreparedRag` 扩展 `rerank_candidates`。决策结果写入 `rag_trace.retrieval_result.doc_routing`（零迁移，jsonb）。

## 4. 配置项（Settings）

| 配置 | 默认 | 说明 |
|---|---|---|
| `m3_doc_agg_t_high` | `0.9` | 锁定档分数线 |
| `m3_doc_agg_t_low` | `0.5` | 澄清档分数线 |
| `m3_doc_agg_ratio` | `1.8` | 锁定要求的领先倍数 |
| `m3_doc_agg_min_hits` | `2` | 锁定要求的命中 chunk 数 |
| `m3_doc_agg_max_hits` | `3` | 单文档计分封顶 chunk 数 |
| `m3_doc_stay_score` | `0.35` | DOC_FOCUS 续留分数线 |
| `m3_doc_delivery_max_tokens` | `8000` | 全文投递 token 上限 |

阈值默认值是起点，**需用 golden-set 标定**。case_01 实测：文档11 rerank 前 2 分 0.80/0.77（score 1.57, hits 4）远超锁定线；文档10 无 chunk 进入 rerank 前列，将正确判为 LOCK。

原 `m3_doc_routing_enabled` 回退开关及五模式 chunk-RAG Chat 路径已在实机验证后删除（2026-08-20）；文档路由是 Chat 的唯一行为，历史回退能力由版本控制承担。

## 5. 影响范围

| 模块 | 改动 |
|---|---|
| `rag/doc_router.py` | 新增：聚合、决策、澄清选项解析（纯函数） |
| `services/rag.py` | Chat 层（`chat_stream`/`generate`）集成状态机；`prepare` 补充 `rerank_candidates` 透出 |
| `rag/repository.py` | `begin_turn` 读取会话状态；新增 `set_conversation_focus`/`set_conversation_pending`/`clear_conversation_pending`/`get_document_blocks`/`list_kb_documents`；`complete_turn` 支持覆写 prompt 与 doc_routing |
| `rag/routing.py` | `ResponseType.DOC_DELIVERED`；`build_assist_result`；`post_validate(require_citations)` |
| `rag/prompt.py` | 新增 DOC_QA / ROUTING 模板与构建函数 |
| `api/schemas.py` | `ChatResponse.response_type` 扩 `DOC_DELIVERED` |
| 前端 | `chat.ts` 类型扩枚举；`DemoChat.vue` 对 `DOC_DELIVERED` 整块渲染（无 delta 流） |
| 不动 | ingestion 管线、chunk 索引、双索引发布、检索/Rerank 管线、Playground（`mode="PLAYGROUND"` 不走路由）、trace 表结构 |

## 6. 验收标准

1. **单测**：doc_router 三档决策边界、STAY/SWITCH/OFFER 分区、澄清选项解析（序号/肯定/标题/否定）、封顶计分、RRF 回退。
2. **集成**：ALIGNING→投递→DOC_QA→切换全链路（fake providers）；投递消息不回灌 history；投递路径零 LLM token；文档删除自动回 ALIGNING；大文档截断投递。
3. **golden-set 回归**：case_01 问答应产出 `DOC_DELIVERED` + 完整 element 序列（含 telnet 截图），全程零 LLM token。
4. Chat 测试全部覆盖文档路由路径（`test_chat_doc_routing_flow.py` 含 LLM 失败路径；五模式后验行为由 DOC_QA 测试覆盖）。

## 7. 已知风险与运维事项

- 阈值未标定前，CLARIFY 可能偏多或偏少；观察 `doc_routing` trace 后调整。
- Rerank 降级时 `1/rank` 回退分数与 rerank_score 语义不同，阈值含义弱化；降级期间路由质量下降属预期。
- ~~截断 bug~~：已由迁移 `0010` 与 `ZhipuLlmProvider._payload` 修正修复——
  default-llm `max_tokens` 上调至 4096、thinking 显式 `disabled`
  （守卫条件仅更新仍是 0003 种子值的行）。
  注意：GLM-5.2 对省略 `thinking` 键的默认行为是**开启**，因此 payload 必须显式携带
  DB `model_config` 中的 thinking 配置；`m3_llm_thinking_enabled` 双开关已删除，
  模型参数以 DB 为唯一事实来源。管理侧配置界面已上线：`PATCH /api/v1/models/{id}`
  直接写 `model_config`（LLM/RERANK 的 model_name、base_url、thinking、温度等），
  `GET/PATCH /api/v1/rag-config` 写 `rag_config`（检索 top-k），下一轮对话即生效。
- 跨文档对比（"对比升级和更换"）暂不支持，留待挂载双文档的后续版本。
