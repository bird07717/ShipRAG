# V1 API 与 SSE 契约

## 1. 通用约定

- Base path：`/api/v1`。
- JSON 字段使用 `snake_case`。
- ID 使用 UUID 字符串。
- 时间使用 RFC 3339 UTC，例如 `2026-08-17T08:00:00Z`。
- 普通响应 `Content-Type: application/json`。
- Chat 流响应 `Content-Type: text/event-stream; charset=utf-8`。
- 客户端可以发送 `X-Request-ID`；缺失时服务端生成并在响应头返回。
- 除健康检查外，生产环境接口必须由服务级 Bearer Token 或可信 API 网关保护。
- RAG Studio 在生产环境通过反向代理获得访问能力，浏览器不得保存长期服务密钥。

运行中的 OpenAPI 是当前 REST 实现的机器可读来源；本文补充 OpenAPI 不能完整表达的 SSE 事件和行为边界。下文只列出当前已实现的接口；尚未提供的管理写接口不会因为出现在早期设计稿中而成为可调用契约。

## 2. 错误格式

在响应头尚未开始发送前，所有错误使用：

```json
{
  "error": {
    "code": "KNOWLEDGE_BASE_NOT_FOUND",
    "message": "知识库不存在",
    "details": {},
    "request_id": "0198..."
  }
}
```

规则：

- `code` 是稳定的机器可读枚举。
- `message` 可以面向用户显示，但不得包含栈、SQL、密钥或厂商原始鉴权错误。
- 字段级校验信息放在 `details.fields`。
- SSE 响应开始后的错误使用 `event: error`，不再尝试修改 HTTP 状态码。

基础错误码：

```text
VALIDATION_ERROR             400/422
AUTHENTICATION_REQUIRED      401
FORBIDDEN                    403
KNOWLEDGE_BASE_NOT_FOUND     404
DOCUMENT_NOT_FOUND           404
INDEX_NOT_FOUND              404
CONVERSATION_NOT_FOUND       404
CONVERSATION_KB_MISMATCH     409
TASK_NOT_FOUND               404
BUILD_ALREADY_RUNNING        409
INDEX_NOT_READY              409
KNOWLEDGE_BASE_NOT_READY     409
DUPLICATE_DOCUMENT           409
MODEL_NOT_CONFIGURED         503
UPSTREAM_TIMEOUT             504
UPSTREAM_UNAVAILABLE         503
INTERNAL_ERROR               500
```

`FORBIDDEN` 在鉴权或知识库状态不允许时通常为 `403`；删除 `ACTIVE` 或 `BUILDING` 索引时，当前实现使用 `409` 表示状态冲突。

## 3. 列表与过滤

当前知识库、文档、索引、任务、模型、Prompt 和会话消息列表直接返回 JSON 数组，尚未实现通用游标分页、`sort` 参数或 `next_cursor` 响应包装。调用方不应依赖早期设计中的 Cursor 协议。

Trace 列表是例外，支持以下查询参数：

```text
GET /api/v1/traces?knowledge_id=<uuid>&status=<RUNNING|COMPLETED|FAILED|CANCELLED>&mode=<CHAT|PLAYGROUND>&limit=50
```

`limit` 默认 `50`，取值范围为 `1`–`200`。Trace 当前按服务端既定顺序返回，不提供 Cursor。

## 4. 知识库 API

### 创建知识库

```http
POST /api/v1/knowledge-bases
```

```json
{
  "name": "产品A",
  "description": "产品A部署与使用文档"
}
```

返回 `201 Created`：

```json
{
  "id": "uuid",
  "name": "产品A",
  "description": "产品A部署与使用文档",
  "status": "ENABLED",
  "runtime_state": "EMPTY",
  "active_index_id": null,
  "rebuild_required": false,
  "document_count": 0,
  "active_chunk_count": 0,
  "building_index_id": null,
  "created_at": "...",
  "updated_at": "..."
}
```

### 列表与详情

```http
GET /api/v1/knowledge-bases
GET /api/v1/knowledge-bases/{knowledge_id}
```

列表和详情均使用同一 `KnowledgeBaseResponse`；其中 `document_count`、`active_chunk_count` 和 `building_index_id` 为服务端计算的当前统计。当前响应不包含 `last_activated_at`。

### 当前管理边界

当前实现提供知识库创建、列表和详情查询；尚未提供 `PATCH /knowledge-bases/{knowledge_id}` 或 `DELETE /knowledge-bases/{knowledge_id}`。因此，`DISABLED` 状态和受控删除属于数据模型/后续管理能力，不是当前公开 API 的可调用操作。

## 5. 文档 API

### 上传 DOCX

```http
POST /api/v1/knowledge-bases/{knowledge_id}/documents
Content-Type: multipart/form-data
Idempotency-Key: <client-generated-key>
```

表单字段：

```text
file: required, one .docx
display_name: optional
request_build: optional boolean, default true
```

成功返回 `202 Accepted`：

```json
{
  "document": {
    "id": "uuid",
    "knowledge_id": "uuid",
    "filename": "部署手册.docx",
    "display_name": "部署手册",
    "file_hash": "sha256...",
    "status": "STORED",
    "created_at": "..."
  },
  "build_request": {
    "requested": true,
    "coalesced": false,
    "index_id": "uuid-or-null",
    "rebuild_required": false
  }
}
```

如果已有构建，上传仍然成功，本次变更不进入已经冻结的构建快照；`coalesced=true`、`rebuild_required=true`，协调器在当前构建结束后自动启动下一次构建。

相同 `Idempotency-Key` 和相同请求重放必须返回相同结果；相同 Key 对应不同文件返回 `409`。

### 文档查询与删除

```http
GET    /api/v1/knowledge-bases/{knowledge_id}/documents
GET    /api/v1/documents/{document_id}
DELETE /api/v1/documents/{document_id}?request_build=true
```

- 删除为软删除，当前 Active Index 在下一索引发布前仍可能返回该文档。
- 删除成功返回 `202 Accepted` 和 `build_request`；源 DOCX 与旧索引快照保留，避免破坏
  当前在线引用，后续由统一保留策略清理。
- 当前索引模型不发布空快照，因此删除知识库最后一份文档返回 `409 VALIDATION_ERROR`。
- 当前没有按单一文档重处理的 `reprocess` 接口。需要重建时，调用知识库的索引构建接口，并可在请求体中使用 `reason=REPROCESS`；任何重建都生成新的完整索引快照，不原地修改 Active Index。

### 文档处理视图

```http
GET /api/v1/documents/{document_id}/index-results?index_id={index_id}
GET /api/v1/documents/{document_id}/elements?index_id={index_id}
GET /api/v1/documents/{document_id}/chunks?index_id={index_id}
GET /api/v1/documents/{document_id}/parent-chunks?index_id={index_id}
GET /api/v1/image-assets/{image_asset_id}
GET /api/v1/image-assets/{image_asset_id}/content
```

未指定 `index_id` 时默认查看 Active Index；没有 Active Index 返回 `409 KNOWLEDGE_BASE_NOT_READY`，而不是静默选择 BUILDING 索引。

## 6. 索引与任务 API

### 请求构建

```http
POST /api/v1/knowledge-bases/{knowledge_id}/indexes/build
Idempotency-Key: <client-generated-key>
```

```json
{
  "reason": "MANUAL",
  "activate_on_success": true
}
```

返回 `202 Accepted`：

```json
{
  "requested": true,
  "index_id": "uuid",
  "task_id": "uuid",
  "coalesced": false,
  "rebuild_required": false
}
```

实际响应没有单独的 `status` 字段；客户端以 `index_id` 查询索引详情获取状态。`requested`、`coalesced` 与 `rebuild_required` 用于区分新建、合并和重放后的构建请求。

已有构建时，不创建第二个 BUILDING 索引；返回当前 `index_id`，设置 `rebuild_required=true`，并将 `coalesced=true`。

### 索引接口

```http
GET  /api/v1/knowledge-bases/{knowledge_id}/indexes
GET  /api/v1/indexes/{index_id}
POST /api/v1/indexes/{index_id}/activate
POST /api/v1/indexes/{index_id}/retry
DELETE /api/v1/indexes/{index_id}
POST /api/v1/knowledge-bases/{knowledge_id}/indexes/gc
```

- 只有 `READY` 可激活。
- 默认自动激活；`activate_on_success=false` 时供 RAG Studio 验证后手动激活。
- `ACTIVE` 和 `BUILDING` 禁止删除；`DEPRECATED`、`FAILED` 或遗留 `DELETING` 索引可被删除。
- `retry` 创建新索引，不复用或清空失败索引 ID。
- `gc` 按保留配置清理可删除的旧索引；显式 `DELETE` 忽略保留策略。两者都会先标记 `DELETING`，再物理删除索引子表、关联 Trace 与索引专属 MinIO 图片。

### 任务接口

```http
GET /api/v1/tasks/{task_id}
GET /api/v1/indexes/{index_id}/tasks
```

任务响应必须包含阶段、进度、尝试次数、脱敏错误和关联资源 ID。

## 7. RAG Playground API

```http
POST /api/v1/rag/playground
```

请求：

```json
{
  "knowledge_id": "uuid",
  "question": "如何配置数据库？",
  "conversation_id": null,
  "options": {
    "vector_top_k": 50,
    "bm25_top_k": 50,
    "fusion_top_k": 30,
    "rerank_top_n": 10,
    "include_prompt": true
  }
}
```

响应包含：

```text
trace_id
index_id
vector_candidates
bm25_candidates
fusion_candidates
rerank_candidates
selected_context
prompt（权限和配置允许时）
answer
sources
timings
```

Playground 只能覆盖白名单内的调试参数，不允许覆盖模型 API Key、Base URL 或任意 SQL 过滤条件。

## 8. Chat SSE API

### 请求

```http
POST /api/v1/chat/stream
Accept: text/event-stream
Content-Type: application/json
```

```json
{
  "knowledge_id": "uuid",
  "conversation_id": "uuid-or-null",
  "question": "如何配置数据库？"
}
```

约束：

- `question` 去除首尾空白后不能为空，最大长度在 M1 冻结。
- `conversation_id=null` 时服务端创建 Conversation。
- 已有 Conversation 的 `knowledge_id` 必须与请求一致，否则返回 `409 CONVERSATION_KB_MISMATCH`。
- 知识库必须 `ENABLED` 且存在 Active Index。

### 响应头

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
Connection: keep-alive
X-Accel-Buffering: no
X-Request-ID: ...
```

### 事件顺序

```text
trace
source
message *
done
```

任意阶段可以以 `error` 结束。当前服务不会发送 `heartbeat`；`done` 或 `error` 是终止事件，之后不得再发送业务事件。

#### `trace`

```text
event: trace
data: {"trace_id":"uuid","conversation_id":"uuid","index_id":"uuid"}
```

#### `source`

在 LLM 生成前发送本次 Context 中允许引用的来源：

```text
event: source
data: {"sources":[{"source_id":"S1","document_id":"uuid","document":"部署手册.docx","section_path":["数据库配置"],"page":null,"element_ids":["uuid"],"chunk_id":"uuid"}]}
```

这里的来源是“允许引用集合”，不等于模型最终实际引用集合。

#### `message`

```text
event: message
data: {"delta":"数据库默认端口为"}

event: message
data: {"delta":"3306。[S1]"}
```

`delta` 按原样拼接，不保证单个事件对应完整字或词。

#### `done`

```text
event: done
data: {"conversation_id":"uuid","message_id":"uuid","answer":"数据库默认端口为3306。[S1]","sources":[{"source_id":"S1","document_id":"uuid","document":"部署手册.docx","section_path":["数据库配置"],"page":null,"element_ids":["uuid"],"chunk_id":"uuid"}],"usage":{},"latency_ms":1234}
```

`done.sources` 只包含答案中出现且通过服务端校验的来源编号。

文档路由是 Chat 的固定行为（无开关），`done` 额外携带
`response_type` / `answer_mode` / `disclaimer` / `content` / `references`：

- `response_type` 取值 `ANSWERED` / `UNCONFIRMED` / `OUT_OF_SCOPE` / `DOC_DELIVERED`。
- `DOC_DELIVERED` 表示按文档路由命中并整份投递了文档：没有 `message` 事件，
  `content` 为按阅读顺序排列的 text/image 内容块，`references` 含原文档下载链接，
  `usage` 为空对象（零 LLM 路径）。
- 文档投递消息落库为摘要文本，不包含完整内容块，避免经会话历史回灌。

#### `error`

```text
event: error
data: {"code":"UPSTREAM_TIMEOUT","message":"模型服务暂时不可用","trace_id":"uuid","retryable":true}
```

流式错误不得包含厂商原始响应、密钥、内部 URL 或栈。

### 断连与持久化

- 客户端断开后，服务端应取消上游 LLM 流并将 Assistant Message 标为 `CANCELLED`。
- User Message 在开始检索前持久化。
- Assistant Message 在流开始前创建为 `STREAMING`，完成后写入完整答案和最终 sources。
- 失败时可保存已生成的部分文本用于 Trace，但普通会话 API 不把失败的部分答案当作已完成回答。

`POST` + JSON 不适用于原生 `EventSource`。任何实现 Chat 的浏览器客户端都必须使用 `fetch`、`ReadableStream` 和能处理跨网络分片 SSE 帧的解析器。RAG Studio 的 DemoChat 页面是面向用户的 Chat SSE 客户端；Playground 由服务端消费同一 RAG 流并返回完整 Trace。

### 非流式 Chat 与文档路由状态机

```http
POST /api/v1/chat
Content-Type: application/json
```

请求体与 `/chat/stream` 相同；响应为单个 JSON（`ChatResponse`），字段与 `done` 事件对齐
（不含 `sources`，新增 `trace_id` 与 `answer`）。

会话遵循文档路由状态机（详见 [MVP1 Chat 执行计划](mvp1-chat-execution-plan.md)）：

- Conversation 通过 `focus_document_id` 持久化当前聚焦文档；为空即对齐阶段。
- 对齐阶段按文档聚合分数决策：锁定投递（`DOC_DELIVERED`，零 LLM）、澄清
  （候选文档写入会话 `chat_context.pending_options`，用户回复序号/标题/确认词可直接锁定）、
  或基于文档目录回答（含"你可以帮我做什么"）。
- 聚焦阶段以文档全文为上下文回答；强证据指向其他文档时切换并投递新文档，
  中等证据时先向用户确认。
- 路由决策完整记录在 `rag_trace.retrieval_result.doc_routing`。

## 9. Prompt、模型与 Trace API

当前已实现的管理与诊断接口：

```http
GET  /api/v1/prompts
GET  /api/v1/models
GET /api/v1/traces
GET /api/v1/traces/{trace_id}
GET /api/v1/conversations/{conversation_id}/messages
```

Prompt 和模型页面在当前版本为只读安全快照。尚未提供 Prompt 创建/启用、模型创建/编辑/测试/启用或 API Key 轮换端点；这些操作不应由调用方假定可用。

模型响应只返回：

```json
{
  "api_key_configured": true
}
```

模型响应还包含非敏感的 `id`、名称、类型、Provider、模型名称、启用状态与参数。绝不返回 `api_key_ciphertext`、Nonce、密钥版本、掩码以外的密钥材料或任何可还原密钥。Trace 查询可能含问题、Prompt、答案和检索上下文，必须置于可信代理与服务 Token 边界之后。

## 10. 兼容性规则

- V1 中只允许向响应增加可选字段；删除、改名或改变字段含义需要新 API 版本或兼容期。
- SSE 新增事件时客户端应忽略未知事件；不得改变既有终止事件含义。
- 错误码一旦公开，不得复用为不同语义。
- API 和数据库迁移必须在同一发布说明中标记兼容性要求。
