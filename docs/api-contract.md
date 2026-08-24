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
- Support Studio 在生产环境通过反向代理获得访问能力，浏览器不得保存长期服务密钥。

运行中的 OpenAPI 是当前 REST 实现的机器可读来源；本文补充 OpenAPI 不能完整表达的 SSE 事件和行为边界。下文只列出当前已实现的接口；尚未提供的管理写接口不会因为出现在早期设计稿中而成为可调用契约。

## Technical Support Chat V2

企业软件正式接入使用 `POST /api/v1/support/chat` 或
`POST /api/v1/support/chat/stream`，这两个接口只进入 Technical Support 链路，不依赖
Generic RAG。旧 `/api/v1/chat` 暂时作为手动回退兼容入口，`/api/v1/rag/playground` 保留通用
RAG 调试能力。运行模式只有 `technical_support` 和 `legacy_rag`：前者让兼容入口进入正式
Support，后者显式回退旧链路。当前规模不建设 Shadow、百分比 Rollout 或对应管理平台。

```json
{
  "knowledge_id": "uuid",
  "question": "PDC怎么用U盘刷版本？",
  "conversation_id": null,
  "problem_code": null,
  "document_id": null,
  "choice_id": null,
  "user_id": "enterprise-user-123",
  "request_id": "enterprise-request-456",
  "include_images": true
}
```

`document_id`、`problem_code`、会话待确认项、标准问题和 Alias 按此顺序解析。
明确命中时返回 `DOCUMENT_DELIVERED`、原始顺序的 `content`（`text`、`table`、`image`）
和全量 `images`；该路径不调用回答 LLM 或 Generic RAG。后续聚焦对话返回
`ANSWERED`、`sections`、`workflow` 和经过服务端验证的图片引用。可能转题会返回
`CLARIFICATION_REQUIRED`，只能在后续请求传入当前会话的 `choice_id` 后切换。
技术文档不做默认脱敏；只有可复用密码、令牌等凭据不得进入回答和日志。出现这类内容时，
服务端返回 `ESCALATION_REQUIRED`，由授权人员决定安全的访问方式。

企业调用应带 `Idempotency-Key`；同一个键用于不同请求返回
`409 IDEMPOTENCY_KEY_REUSED`，处理中返回 `409 REQUEST_IN_PROGRESS`。图片 URL 指向
服务端受保护的 Image Asset 内容接口，企业后台应代理该请求，不能把长期 Bearer Token
交给终端用户。若同一 Conversation 的两个 Support 回合基于相同状态同时提交，后到请求
返回 `409 WORKFLOW_CONFLICT`，调用方应使用新的幂等键重新读取并提交。

问题配置由以下接口维护：

```text
GET /api/v1/support/problem-profiles?knowledge_id=<uuid>
PUT /api/v1/support/problem-profiles?knowledge_id=<uuid>
```

`PUT` 请求包含 `document_id`、`problem_code`、`canonical_question`、`aliases`、
`keywords`、`model_names`、`task_type` 和 `enabled`。Alias、关键词、产品、型号和任务类型
均为可选优化项；启用的 Alias 在同一知识库内仍保持唯一，Profile 必须指向该知识库尚未删除的文档。
Profile 响应同时返回 `support_ready` 和 `readiness_reasons`。只有启用、文档仍
存在、文档属于当前发布版本且图片资源完整的 Profile 才进入正式问题目录；显式请求未就绪
文档返回 `409 DOCUMENT_NOT_SUPPORT_READY`。

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
MODEL_NOT_FOUND              404
MODEL_UPDATE_FORBIDDEN       409
MODEL_UPDATE_CONFLICT        409
MODEL_SECRET_NOT_CONFIGURED  409
MODEL_PROBE_FAILED           409
EMBEDDING_DIMENSION_MISMATCH 422
API_KEY_DECRYPT_FAILED       500
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
  "question": "如何配置数据库？",
  "user_id": "mobile-user-42"
}
```

约束：

- `question` 去除首尾空白后不能为空，最大长度在 M1 冻结。
- `conversation_id=null` 时服务端创建 Conversation。
- `user_id` 可选（1–100 字符）：调用方（如移动端 BFF）透传其登录体系中的用户标识，仅用于 Trace 归因、质量统计与按用户过滤，服务端不校验其真实性与权限。
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

`POST` + JSON 不适用于原生 `EventSource`。任何实现 Chat 的浏览器客户端都必须使用 `fetch`、`ReadableStream` 和能处理跨网络分片 SSE 帧的解析器。Support Studio 的问答测试页调用正式 Support SSE；Generic RAG Playground 由服务端消费旧 RAG 流并返回完整 Trace。

### 非流式 Chat 与文档路由状态机

```http
POST /api/v1/chat
Content-Type: application/json
```

请求体与 `/chat/stream` 相同；响应为单个 JSON（`ChatResponse`），字段与 `done` 事件对齐
（不含 `sources`，新增 `trace_id` 与 `answer`）。

当前会话遵循以下文档路由状态机；后续目标架构与兼容迁移见[对话式技术支持系统重构计划](technical-support-refactor-plan.md)：

- Conversation 通过 `focus_document_id` 持久化当前聚焦文档；为空即对齐阶段。
- 对齐阶段按文档聚合分数决策：锁定投递（`DOC_DELIVERED`，零 LLM）、澄清
  （候选文档写入会话 `chat_context.pending_options`，用户回复序号/标题/确认词可直接锁定）、
  或基于文档目录回答（含"你可以帮我做什么"）。
- 聚焦阶段以文档全文为上下文回答；强证据指向其他文档时切换并投递新文档，
  中等证据时先向用户确认。
- 路由决策完整记录在 `rag_trace.retrieval_result.doc_routing`。

## 9. Prompt、模型、RAG 配置与 Trace API

当前已实现的管理与诊断接口：

```http
GET   /api/v1/prompts
GET   /api/v1/models
PATCH /api/v1/models/{model_id}
PUT   /api/v1/models/{model_id}/api-key
DELETE /api/v1/models/{model_id}/api-key
POST  /api/v1/models/{model_id}/probe
GET   /api/v1/rag-config
PATCH /api/v1/rag-config
GET   /api/v1/traces
GET   /api/v1/traces/{trace_id}
GET   /api/v1/conversations/{conversation_id}/messages
```

### 模型查询与在线调整

`GET /api/v1/models` 返回全部五种类型（LLM / EMBEDDING / RERANK / OCR / VISION）的安全快照。模型响应只返回：

```json
{
  "api_key_configured": true,
  "api_key_source": "DATABASE"
}
```

模型响应还包含非敏感的 `id`、名称、类型、Provider、Base URL、模型名称、启用状态与参数。绝不返回 `api_key_ciphertext`、Nonce、密钥版本、掩码以外的密钥材料或任何可还原密钥。

`PATCH /api/v1/models/{model_id}` 的可编辑类型按消费时机划分：

| 类型 | 生效语义 |
|---|---|
| `LLM` / `RERANK` | `begin_turn` 每轮快照，写入后下一轮对话即生效、无需重启 |
| `OCR` / `VISION` | 索引构建启动时快照，写入后**下一次索引构建**生效；已有 Active Index 不受影响。变更后所有知识库自动标记 `rebuild_required=true` |
| `EMBEDDING` | 同上构建时生效，但变更 `model_name` / `base_url` 时必须先通过**维度契约门禁**（见下）；`parameters` / `enabled` 变更无需门禁 |

请求体（对所有可编辑类型一致）：

```json
{
  "model_name": "glm-5.2",
  "base_url": "https://open.bigmodel.cn/api/paas/v4/",
  "enabled": true,
  "parameters": {
    "temperature": 0.1,
    "max_tokens": 4096,
    "thinking": { "type": "enabled" }
  }
}
```

约束与错误：

- 所有字段可选，但至少提供一个，否则返回 `422 VALIDATION_ERROR`。
- `base_url` 必须以 `http(s)://` 开头并以 `/` 结尾。
- `parameters.temperature` 取值 0–2；`parameters.max_tokens` 取值 100–100000；
  `parameters.thinking` 必须为 `{"type": "enabled" | "disabled"}`。未知键原样透传。
- 模型不存在返回 `404 MODEL_NOT_FOUND`。
- 每个 `model_type` 最多一个 `enabled=true` 配置，违反时返回 `409 MODEL_UPDATE_CONFLICT`。
- 尚未提供模型创建、删除端点；这些操作不应由调用方假定可用。

### 构建时模型消费与维度契约门禁（Embedding / OCR / Vision）

索引构建启动时一次性快照启用的 `EMBEDDING` / `OCR` / `VISION` 行（含解密密钥），
构建期间的管理端写入不会进入本次构建；行缺失或 Provider 不受支持时回退
`Settings` 环境配置并在任务 `metadata.build_models` 记录来源
（`DATABASE` / `ENV` / `ENV_FALLBACK`）。`PUT/DELETE /api/v1/models/{id}/api-key`
对这三类写入或清除数据库密钥同样只在下一次构建生效，并标记
`rebuild_required=true`。

**维度契约门禁（ADR-0008）**：`PATCH` 变更 EMBEDDING 行的 `model_name` 或
`base_url`（实际值发生变化）时，服务端先用候选值 + 生效密钥发起一次最小
Embedding 探测，实测输出维度必须等于冻结维度 1024：

- 维度不符 → `422 EMBEDDING_DIMENSION_MISMATCH`；
- 探测不可达 / Provider 不受支持 → `409 MODEL_PROBE_FAILED`；
- 值未变化或仅改 `parameters` / `enabled` 不触发探测。

构建期兜底：索引构建对每批返回向量校验长度等于冻结维度，不符则以
`EMBEDDING_DIMENSION_MISMATCH` 终止构建——即使门禁被直改库绕过，
`vector(N)` 列也不会收到脏数据。

### API Key 管理（write-only，ADR-0005）

`PUT /api/v1/models/{model_id}/api-key` 请求体 `{"api_key": "..."}`（1–4096 字符），
`DELETE /api/v1/models/{model_id}/api-key` 清除数据库密钥。两者返回更新后的
`ModelConfigResponse`。规则：

- **密钥只写不读**：API 永远只返回 `api_key_configured` 与 `api_key_source`
  （`DATABASE` / `ENV` / `null`），绝不返回密文、Nonce、密钥版本或任何可还原材料。
- **优先级链**：数据库密钥（AES-GCM 加密，`MODEL_SECRET_KEY` 信封）> 环境变量
  （`SILICONFLOW_API_KEY` / `ZHIPU_API_KEY`）。清除数据库密钥即回退环境变量。
- 未配置 `MODEL_SECRET_KEY` 时写入返回 `409 MODEL_SECRET_NOT_CONFIGURED`。
- `begin_turn` 每轮解密并快照 LLM/Rerank 密钥，写入下一轮对话即生效、无需重启。
- 所有写入（含 `PATCH /models`）在 `model_config_history` 表记录
  操作类型、变更字段名与 actor（request id），不记录任何值。
- `MODEL_SECRET_KEY` 轮换：设置 `MODEL_SECRET_KEY_PREVIOUS` 后运行
  `python -m app.tasks.key_rotation`（密钥只经环境变量，不经 Redis）。

### 连通性探测

`POST /api/v1/models/{model_id}/probe` 用当前生效配置（DB 行 + 生效密钥）向上游
发起一次最小请求，返回 `{ok, model_type, provider, model_name, latency_ms, detail,
dimension?}`。`dimension` 仅 EMBEDDING 返回，若与冻结维度 1024 不一致则
`ok=false` 并在 `detail` 说明。探测结果不含密钥、内部 URL 或上游原始响应体。

### RAG 运行时检索配置

`GET /api/v1/rag-config` 返回全局单行检索参数（`rag_config` 表，迁移 `0011`）；行缺失时回退 `Settings` 环境默认值：

```json
{
  "vector_top_k": 10,
  "bm25_top_k": 10,
  "fusion_top_k": 20,
  "rerank_top_n": 10,
  "context_max_chunks": 8,
  "updated_at": "..."
}
```

`PATCH /api/v1/rag-config` 更新部分或全部字段，每个字段取值范围 `1`–`100`，至少提供一个否则返回 `422 VALIDATION_ERROR`。`begin_turn` 每轮读取快照，保存后下一轮对话即生效；Playground 的手动参数仍可在同一层临时覆盖（见第 7 节）。

### Prompt 与 Trace

`GET /api/v1/prompts` 返回两类只读条目，运行时内置模板在前：

- `source=RUNTIME`：Chat 主路径（文档问答、澄清与切换）实际使用的三个代码管理模板（`runtime-doc-qa` / `runtime-chunk-qa` / `runtime-routing`），与解析逻辑强耦合（`[MODE:*]` 标记、`[IMG:S*]` 引用），由代码版本化管理（`backend/app/rag/prompt.py`）；`description` 字段说明各自作用，`active` 恒为 `false`，`created_at` / `updated_at` 为空。
- `source=DATABASE`：`prompt_template` 表的版本化模板（当前仅 `default-rag` v2 一行，active），只在文档路由的片段检索降级路径（CHUNK_RAG 兜底与降级）生效。迁移 `0012` 保证该行内容与代码常量 `DEFAULT_RAG_PROMPT` 逐字节一致。

两类条目均不可写：尚未提供 Prompt 创建、版本切换或启用端点，这些操作不应由调用方假定可用。Trace 查询可能含问题、Prompt、答案和检索上下文，必须置于可信代理与服务 Token 边界之后。

### Trace 查询

`GET /api/v1/traces` 支持过滤参数：`knowledge_id`、`status`、`mode`、`response_type`（`ANSWERED` / `UNCONFIRMED` / `OUT_OF_SCOPE` / `DOC_DELIVERED`）、`user_id`、`from` / `to`（ISO 时间戳，按 `created_at` 过滤，`to` 为开区间）与 `limit`（默认 50，最大 200）。摘要行自迁移 `0014` 起包含 `user_id`、`response_type`、`answer_mode`。

失败 trace 的 `error` 载荷为 `{"code", "message"}` 并按可用性附加运维字段：`stage`（失败阶段：`prepare` / `query_embedding` / `routing` / `rerank` / `llm`）、`retryable`（是否可安全重试）、`detail`（非敏感的上游诊断信息，如 `HTTP 429`，用于区分限流与宕机）。这些字段面向运营排障，调用方不应依赖其存在性做逻辑分支。

### 服务端质量统计（Metrics）

基于 `rag_trace` 实时聚合的只读统计（迁移 `0014` 提供持久化的 `user_id` / `response_type` / `answer_mode`，并回填历史 `DOC_DELIVERED`）：

- `GET /api/v1/metrics/overview`：总量、`response_type` 分布、无答案率（`UNCONFIRMED` + `OUT_OF_SCOPE` / 已分类 trace 数）、失败率、引用缺失率、rerank 降级率、独立用户数、延迟分位数（P50/P95/P99/均值，取 `latency.total_ms`）与 token 消耗（LLM 与 rerank 分列）。
- `GET /api/v1/metrics/timeseries`：按 `granularity=day|hour` 分桶的趋势点，含查询量、各 `response_type` 计数、无答案率、P95 延迟与 token 汇总。
- `GET /api/v1/metrics/latency`：端到端分位数与各阶段平均耗时（`prepare_turn_ms`、`query_rewrite_ms`、`query_embedding_ms`、`bm25_retrieval_ms`、`vector_retrieval_ms`、`fusion_ms`、`rerank_ms`、`context_expansion_ms`、`prompt_ms`、`llm_ms`）。
- `GET /api/v1/metrics/support`：仅聚合 `retrieval_result.support_version=1` 的正式 Support Trace，提供首轮/后续轮数、文档交付、澄清及澄清后成功、无匹配、人工升级、确定性/LLM Catalog 路由、证据失败、结构化输出重试、工作流冲突、模型调用、首轮与后续 P50/P95/P99 延迟及 LLM token。文档命中和内容完整性由少量开发验收案例及人工测试确认，不建设独立评测平台，也不从生产 Trace 中虚构。

公共参数：`knowledge_id`（可选）、`mode`（默认 `CHAT`，排除 Playground 实验）、`from` / `to`（可选，默认最近 7 天，最长 92 天，`to` 为开区间；`from >= to` 返回 `422`）。

比率分母说明：无答案率与文档投递率的分母是 `response_type` 非空的 trace 数（`classified_traces`）。迁移 `0014` 之前的历史行除可回填的 `DOC_DELIVERED` 外 `response_type` 为空，不计入分母；失败回合也不计入。

## 10. 兼容性规则

- V1 中只允许向响应增加可选字段；删除、改名或改变字段含义需要新 API 版本或兼容期。
- SSE 新增事件时客户端应忽略未知事件；不得改变既有终止事件含义。
- 错误码一旦公开，不得复用为不同语义。
- API 和数据库迁移必须在同一发布说明中标记兼容性要求。
