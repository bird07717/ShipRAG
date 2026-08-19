# M3 文本 RAG 验收记录

验收日期：2026-08-17。

## 结论

M3 状态：**PASS**。

指定知识库的 Active Index 冻结、查询文本 Embedding、pgvector 余弦检索、Context 构建、全局 Prompt、多轮历史、GLM-5.2 流式生成、SSE、引用校验、Conversation/Message 和 RAG Trace 已形成端到端闭环。

## 已实现范围

### 数据库与默认配置

Alembic `0003_m3_text_rag_schema.py` 创建：

- `prompt_template`：全局唯一 Active Prompt，模板必须包含 `{{context}}`、`{{question}}`、`{{history}}`。
- `conversation`：创建后固定 `knowledge_id`。
- `message`：显式 `sequence_no` 保证同事务内 User/Assistant 的稳定顺序，支持 `STREAMING/COMPLETED/FAILED/CANCELLED`。
- `rag_trace`：保存冻结索引、检索候选、Context、Prompt（可配置关闭）、答案、引用、用量、阶段耗时和脱敏错误。
- 默认 LLM 配置：智谱 `glm-5.2`，密钥来源为环境变量。
- 默认全局 Prompt：要求只依据 Context 回答、证据不足时明确说明、只引用注册的 `[S<number>]`。

### 文本向量检索

每次请求开始时在事务内校验并冻结：

1. Knowledge Base 存在且为 `ENABLED`。
2. `active_index_id` 存在。
3. Active Index 状态为 `ACTIVE`，并属于请求知识库。
4. 已有 Conversation 必须属于相同知识库。

查询使用 Active Index 的 Embedding 模型快照名称，生成固定 1024 维查询向量。pgvector SQL 强制过滤：

```text
document_chunk.kb_id = request.knowledge_id
document_chunk.index_id = frozen_active_index_id
```

按 cosine distance 排序，并以 Chunk ID 作为稳定并列排序。Context 只取完整 Chunk，同时服从最大 Chunk 数和 Token Budget，不在 Chunk 中间截断。

### Prompt 与多轮上下文

- 只读取最近的 `COMPLETED` 消息，失败、取消和流式中的 Assistant 不进入历史。
- 历史同时受消息数和 Token Budget 限制，当前问题始终完整保留。
- Prompt 渲染使用变量白名单，不执行表达式或任意模板代码。
- 来源编号按最终 Context 顺序由服务端分配为 `S1/S2/...`，文档、章节、Element 和 Chunk 元数据只来自数据库。

### LLM 与 SSE

Chat 接口：

```http
POST /api/v1/chat/stream
Accept: text/event-stream
Content-Type: application/json
```

事件顺序：

```text
trace → source → message* → done
```

流开始后的模型异常以 `error` 终止；`done` 或 `error` 后不再发送业务事件。响应关闭代理缓冲和内容转换。智谱适配器只转发 `delta.content`，不返回或持久化 thinking/reasoning 内容。

回答完成后，服务端从答案识别 `[S<number>]`：未知编号和重复编号不会进入 `done.sources`，最终结构化来源只指向本次注册的真实 Chunk。客户端取消会将 Assistant Message 和 Trace 标记为 `CANCELLED`。

### 查询接口

```text
POST /api/v1/chat/stream
GET  /api/v1/prompts
GET  /api/v1/traces/{trace_id}
GET  /api/v1/conversations/{conversation_id}/messages
```

## 实机证据

真实云服务验收命令：

```bash
M3_SMOKE_EMBEDDING_PROVIDER=siliconflow \
M3_SMOKE_LLM_PROVIDER=zhipu \
./scripts/check-m3.sh
```

2026-08-17 结果：

```text
SiliconFlow 查询 Embedding：HTTP 200，1024 维
智谱 glm-5.2 流式回答：HTTP 200
向量候选：1
知识库隔离：PASS（两个知识库含相同查询向量，仅返回指定知识库）
SSE：trace → source → message* → done
有效引用：1
RAG Trace：COMPLETED
Assistant Message：COMPLETED
```

烟测只创建随机 UUID 临时数据，结束后精确清理两个测试知识库。

## 范围边界

M3 是文本向量 RAG 基线，以下能力不在本里程碑内：

- BM25、Hybrid Fusion 和 Rerank。
- 图片 OCR/Vision Caption 在线检索增强。
- RAG Playground 和 Prompt/模型写管理页面。
- 自动评测、Query Rewrite、长期记忆和 Agent 工作流。

这些边界不会影响 M3 的文本向量检索、Prompt、LLM 和 SSE 闭环。
