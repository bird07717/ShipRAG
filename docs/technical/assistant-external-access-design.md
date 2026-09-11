# 智能回答助手对外接入技术设计

## 1. 文档目的

本文面向 AI 平台研发、测试、运维和管理人员，说明如何在复用项目既有能力的基础上，为业务后台提供稳定的智能回答服务。

本文负责定义：

- 项目现状和复用边界。
- 外部 Assistant API 的平台侧实现。
- 用户、知识库、Conversation 和 Message 的隔离规则。
- 流式输出、幂等、取消和失败恢复。
- 7 天会话和 Trace 生命周期。
- 模型、Prompt、知识版本和回答策略的管理规则。
- 管理端、排障、测试和上线要求。

业务后台的调用方法见《智能回答助手 API 对接文档》。

## 2. 已有实现分析

### 2.1 技术架构

项目后端使用 FastAPI、SQLAlchemy、PostgreSQL、pgvector、pg_search、Redis/RQ 和 MinIO；管理前端使用 Vue 3、TypeScript 和 Element Plus。

项目已经具备：

- Conversation、Message 和 RAG Trace 持久化。
- 统一 Chat Gateway。
- RAG 和 Technical Support 两条回答链路。
- 模型、Prompt、检索参数、知识库、文档和发布管理。
- SSE 输出、Markdown、图片和文档来源展示；不提供原文下载。
- Trace、请求错误日志和管理端会话回放。
- Conversation 定时清理和旧 Index 回收。

核心实现位置：

- `backend/app/gateway/service.py`：统一路由、双链路和 fallback。
- `backend/app/rag/repository.py`：RAG 会话、消息、Trace、历史上下文和版本冻结。
- `backend/app/support/service.py`：Profile Support、文档交付、澄清和升级人工。
- `frontend/src/components/DemoChat.vue`：DemoChat 的实际交互基准。

### 2.2 默认 RAG 链路

配置 `chat_execution_mode=legacy_rag` 时，Gateway 默认解析为 `RAG_ONLY`。实际流程为：

1. 校验知识库和 Conversation。
2. 为 Conversation 绑定 RAG Index 版本。
3. 读取最近最多 10 条、约 2,000 token 的历史消息。
4. 读取生效的 LLM、Embedding、Rerank、Prompt 和检索参数。
5. 必要时进行 Query Rewrite。
6. 并行执行向量检索、BM25 检索和同义词扩展。
7. 使用 RRF 融合并执行 Rerank；Rerank 超时或可降级异常时退回融合排序。
8. 扩展相邻上下文，执行文档路由、文档交付、文档问答或 Chunk RAG。
9. 调用 LLM、校验引用并保存 Message 和 Trace。
10. RAG 整轮默认受 45 秒时限约束。

### 2.3 Technical Support 链路

Technical Support 使用已发布的文档 Profile、问题编码、澄清选项和 Conversation 文档焦点，支持：

- 高置信 Profile 命中。
- 文档完整交付。
- 聚焦文档问答。
- 澄清和文档切换。
- 结构化处理步骤。
- 风险提示和人工升级建议。

在 `SUPPORT_WITH_RAG_FALLBACK` 策略下，Support 返回 `NO_MATCH` 时，Gateway 自动转入 RAG。fallback 使用父子 Trace，并复用同一组用户和助手 Message。

Support 的流式接口目前先完成整个 `handle()`，再按 24 个字符切块输出，因此属于协议层流式回放，不是真实的模型 token 流。

### 2.4 DemoChat 能力

DemoChat 当前具备：

- 知识库选择。
- 欢迎词和推荐问题。
- 新会话和多轮上下文。
- SSE 增量回答。
- 检索/生成状态。
- Markdown 渲染，禁用原生 HTML 并使用 DOMPurify 消毒。
- 文本、表格、图片和引用。
- 澄清选项。
- Stop、Loading、Error 和自动滚动。
- 内部引擎、模式和 fallback 标签。

DemoChat 不具备移动端所需的历史列表、历史恢复和失败重试入口。移动端应复用其回答展示和交互语义，但不展示内部引擎、模型或 fallback。

### 2.5 直接对外使用的风险

现有接口不能直接作为业务后台 Contract，原因包括：

- 一个 `SERVICE_TOKEN` 同时保护聊天和管理 API，权限过大。
- `user_id` 可由请求体或 Query 提交，并且可以省略。
- 继续已有 Conversation 时，RAG 和 Support 的持久化路径没有强制校验 owner。
- Conversation 历史接口未提供游标、`expires_at` 和消息级错误信息。
- 历史消息过滤 `CANCELLED`，Stop 时看到的部分回答无法完整恢复。
- 幂等能力主要位于 Support，RAG 没有统一的 Gateway 幂等记录。
- Support 未显式捕获 `asyncio.CancelledError`，可能遗留 `STREAMING` 状态。
- `conversation.updated_at` 同时受内部焦点和路由状态更新影响，不能作为严格的业务保留时钟。
- Trace 中保存问题、回答、Prompt 和上下文，其生命周期与 Index GC 耦合。

## 3. 设计原则

### 3.1 调用方零 AI 配置

最终用户、Android、iOS 和业务后台永远不参与以下选择：

- LLM、Embedding 或 Rerank 模型。
- 模型供应商。
- Prompt。
- RAG 检索参数。
- 回答策略。
- RAG 或 Support 链路。
- fallback 条件。
- Index 或知识发布版本。

所有配置由 AI 平台管理后台控制。

外部请求不得接受以下字段：

```text
model
model_id
model_name
provider
engine
execution_engine
execution_policy
answer_mode
support_mode
prompt
prompt_version
index_id
release_id
rag_config
fallback
```

外部 Pydantic Schema 必须使用 `ConfigDict(extra="forbid")`。调用方提交任何未定义字段时返回 `422 VALIDATION_ERROR`。

`response_type` 表示回答产生后的业务结果类型，例如正常回答、文档交付或需要澄清，不是调用方可选择的回答模式。

### 3.2 对外 Contract 与内部实现隔离

外部 Contract 不返回：

- 实际执行引擎。
- 是否发生 fallback。
- 模型名称。
- Prompt 或 Prompt 版本。
- Index、Release 和内部配置版本。
- 内部路由分数和检索详情。

上述信息只记录在 Trace 中。

### 3.3 Conversation 稳定性

- Conversation 保存创建时的执行策略快照。
- 管理后台调整回答策略只影响之后创建的 Conversation。
- 已有 Conversation 继续使用原策略，防止同一历史会话行为突变。
- 模型、Prompt 和检索参数按每轮请求读取生效配置。
- 每一轮将精确配置记录到 Trace `runtime_snapshot`。

## 4. 目标架构

```text
Android / iOS
  ├─ 使用业务系统用户身份
  ├─ 选择知识库
  └─ 展示聊天和历史
          ↓
业务后台
  ├─ 校验登录用户
  ├─ 注入 external_user_id
  ├─ 持有 Assistant Token
  ├─ 代理 SSE 和取消
  └─ 代理图片和文档
          ↓
/api/v1/assistant
  ├─ 服务认证
  ├─ 用户和知识库隔离
  ├─ Conversation / Message
  ├─ 幂等与并发控制
  ├─ 外部 SSE 适配
  └─ Chat Gateway
       ├─ RAG
       └─ Technical Support → 可选内部 fallback
```

## 5. 外部路由和认证

### 5.1 路由

外部能力使用独立前缀：

```text
/api/v1/assistant
```

现有 `/chat`、`/rag/chat`、`/support/chat` 和管理接口继续用于管理端、Demo 和调试，不作为业务后台 Contract。

### 5.2 Assistant Token

增加配置：

```text
ASSISTANT_SERVICE_TOKEN
ASSISTANT_SERVICE_TOKEN_PREVIOUS
```

路由依赖拆分为：

- 管理 Router：`require_service_token`。
- Assistant Router：`require_assistant_service_token`。

Assistant Token 不能访问模型、Prompt、配置、Trace、文档管理或其他管理 API。Previous Token 用于凭证滚动轮换；管理页面只显示配置状态和凭证版本，不回显明文。

### 5.3 用户上下文

业务用户由 Header 注入：

```http
X-External-User-ID: <稳定业务用户ID>
```

要求：

- 最长 100 字符。
- 使用业务系统稳定、不透明的用户主键。
- 不使用昵称、手机号等可变或敏感字段。
- 外部 body/query 不接受 `user_id`。
- 所有 Conversation、Message 和资源访问使用服务端解析的用户上下文。

## 6. 外部 API

平台提供：

```text
GET  /api/v1/assistant/knowledge-bases
GET  /api/v1/assistant/knowledge-bases/{id}/welcome
GET  /api/v1/assistant/conversations
GET  /api/v1/assistant/conversations/{id}
GET  /api/v1/assistant/conversations/{id}/messages
POST /api/v1/assistant/chat/stream
GET  /api/v1/assistant/knowledge-bases/{kb_id}/image-assets/{id}/content
```

平台不提供：

- 模型或回答策略选择接口。
- 空 Conversation 创建接口。
- 用户侧 Conversation 删除接口。
- 已完成回答重新生成接口。
- 独立生成任务 Cancel 接口。

新聊天通过 `conversation_id=null` 的首条消息原子创建 Conversation。

## 7. 用户和知识库隔离

所有 Conversation 操作必须同时满足：

```text
conversation.user_id == X-External-User-ID
conversation.knowledge_id == 请求 knowledge_id
conversation.last_activity_at >= now() - 7 days
```

校验覆盖：

- 会话列表和详情。
- 历史消息。
- 继续提问。
- 澄清选择。
- 幂等重放。
- 图片资源；文档 ID 和标题仅作为回答来源元数据。

不存在、过期、越权和知识库不匹配统一返回：

```text
404 CONVERSATION_NOT_FOUND
```

该处理避免暴露其他用户 Conversation 是否存在。

## 8. 数据模型

### 8.1 Conversation

在现有表增加：

```sql
last_activity_at timestamptz NOT NULL
```

迁移时使用 `updated_at` 回填。之后：

- 创建 Conversation 时写入当前时间。
- 回答成功到达业务终态时刷新。
- FAILED、CANCELLED 和内部焦点更新不刷新。
- `updated_at` 继续表示数据库行最后修改时间。
- `expires_at` 不入库，API 统一计算为 `last_activity_at + 7 days`。

外部 Conversation 结构：

```text
conversation_id
knowledge { knowledge_id, name }
title
message_count
created_at
last_activity_at
expires_at
status = ACTIVE
```

### 8.2 Message

增加：

```sql
response_payload jsonb NOT NULL DEFAULT '{}'::jsonb
```

`response_payload` 保存历史恢复所需的稳定业务字段：

```text
response_type
content
sections
document
references
clarification
escalation
disclaimer
next_suggestions
error
```

外部状态映射：

```text
STREAMING  → GENERATING
COMPLETED  → COMPLETED
FAILED     → FAILED
CANCELLED  → CANCELLED
```

历史接口必须返回 FAILED 和 CANCELLED，以及已保存的部分回答。

### 8.3 AssistantRequest

增加统一幂等表：

```text
id uuid primary key
external_user_id varchar(100) not null
knowledge_id uuid not null
idempotency_key varchar(200) not null
request_hash varchar(64) not null
conversation_id uuid null
user_message_id uuid null
assistant_message_id uuid null
trace_id uuid null
status varchar(20) not null
terminal_response jsonb not null default '{}'
terminal_error jsonb not null default '{}'
created_at timestamptz not null
updated_at timestamptz not null
```

约束：

```text
UNIQUE(external_user_id, knowledge_id, idempotency_key)
```

同一已有 Conversation 同时只能有一个 RUNNING 请求，应使用部分唯一索引或等效原子锁实现。

## 9. 幂等和并发

同一 Idempotency-Key 的行为：

- `COMPLETED`：重放原 SSE 终态。
- `RUNNING`：`409 REQUEST_IN_PROGRESS`。
- 请求 hash 不同：`409 IDEMPOTENCY_KEY_REUSED`。
- `FAILED/CANCELLED`：重放原错误终态，不再次调用模型。

用户明确重试失败请求时必须使用新 Key。

同一 Conversation 已有生成请求时，第二个请求返回：

```text
409 CONVERSATION_BUSY
```

增加 stale RUNNING 回收任务，把超过执行时限仍为 RUNNING 的 AssistantRequest、Message 和 Trace 统一收敛为 FAILED。

## 10. 外部 SSE

### 10.1 事件

外部事件固定为：

```text
accepted
progress
context
delta
complete
error
: ping
```

`progress.stage` 只允许：

```text
PREPARING
RETRIEVING
GENERATING
FINALIZING
```

### 10.2 accepted

认证、参数和已有 Conversation 预检通过后立即发送：

```json
{
  "request_id": "平台请求ID",
  "knowledge_id": "uuid",
  "conversation_id": null
}
```

新 Conversation 的 ID 此时允许为 null。

### 10.3 context

引擎建立真实执行上下文后发送：

```json
{
  "conversation_id": "uuid",
  "user_message_id": "uuid",
  "assistant_message_id": "uuid",
  "trace_id": "uuid"
}
```

该事件必须早于第一个 `delta`。

### 10.4 complete

两条内部链路统一输出：

```text
request_id
conversation_id
message_id
trace_id
response_type
answer
content
sections
document
references
clarification
escalation
disclaimer
next_suggestions
created_at
```

不得出现实际引擎、fallback、模型、Prompt、Index 或内部配置。

### 10.5 心跳

Support 在 `handle()` 中存在长时间阻塞，仅在 Async Generator 外简单加入 `yield` 无法产生心跳。外部适配器必须：

1. 将内部 Generator 的下一次 `anext()` 创建为 Task。
2. 最多等待 15 秒。
3. 未完成则发送 `: ping`。
4. 继续等待同一个 Task。
5. 得到内部事件后进行转换。
6. 下游断开时取消 Task 并等待内部清理。

该包装同时覆盖 RAG 检索准备阶段和 Support 完整处理阶段。

## 11. Stop 和取消

Stop 使用端到端 HTTP 取消：

```text
App 关闭业务流
→ 业务后台取消 AI 上游请求
→ 外部适配器取消内部 Task
→ Message / Trace / AssistantRequest = CANCELLED
```

平台需要补充：

- `TechnicalSupportService.handle()` 显式捕获 `asyncio.CancelledError`。
- Support Repository 增加 `cancel_turn()`。
- 使用 shield 等待取消状态落库。
- RAG 保留现有部分回答和 CANCELLED 逻辑。
- FAILED/CANCELLED 消息进入历史。

首个版本不建设独立分布式生成任务和 Cancel API。

## 12. 双链路统一

管理后台可为知识库选择：

```text
RAG_ONLY
SUPPORT_WITH_RAG_FALLBACK
```

平台内部继续记录：

- 实际执行引擎。
- 策略版本。
- fallback 原因。
- 父子 Trace。
- RAG/Support Index 和 Release。

外部 Contract 不返回这些字段。调用方不根据平台内部链路编写任何业务逻辑。

## 13. 会话生命周期

会话可见条件：

```sql
last_activity_at >= now() - interval '7 days'
```

规则：

- 成功回答刷新 `last_activity_at`。
- 失败、取消和内部状态更新不刷新。
- API 查询立即隐藏过期 Conversation，不等待定时 GC。
- 每日 RQ Maintenance 物理删除过期 Conversation。
- Message 和 AssistantRequest 随 Conversation 清理。
- 旧 Index 引用释放后进入现有 GC。

必须修复 `conversation_retention_days=0`：值为 0 表示跳过 Conversation 清理，不能按 0 天执行删除。

## 14. Trace 和排障

### 14.1 关联链

```text
Idempotency-Key
      ↓
request_id
      ↓
AssistantRequest
      ↓
conversation_id
      ↓
user_message_id / assistant_message_id
      ↓
最终 trace_id
      ↓
父子 fallback Trace
```

### 14.2 Runtime Snapshot

Trace 增加 `runtime_snapshot jsonb`，记录：

```text
execution_policy 和 config_version
actual_engine
rag_index_id/version
support_index_id/release_id
LLM provider/model/config revision
Embedding model
Rerank model
Prompt id/version/hash
RAG config revision
```

### 14.3 生命周期

采用：

```text
完整 Trace：7 天
脱敏摘要：30 天
```

7 天后：

- `user_id` 置空。
- `question` 替换为固定脱敏文本。
- `answer`、`prompt` 置空。
- Context、Sources 和内容型检索结果清空。
- error 只保留 code、stage 和 retryable。
- 保留 request_id、trace_id、状态、耗时、Token 数和 runtime snapshot。

30 天后物理删除 Trace。

数据库调整：

- `rag_trace.index_id` 改为可空。
- Index FK 使用 `ON DELETE SET NULL`。
- Trace Response Schema 的 `index_id` 改为可选。
- Index GC 不再无条件删除未满 30 天的 Trace。

## 15. 管理端适配

管理后台继续作为模型和回答策略的唯一控制入口。

需要增加：

- Conversation 列表和详情。
- 知识库、external user ID、活跃时间筛选。
- FAILED/CANCELLED 和部分回答回放。
- Trace 按 request_id 精确查询。
- 父子 Trace 链和最终 Message 跳转。
- runtime snapshot 展示。
- Trace 脱敏状态。
- Assistant Token 配置状态和版本。

引擎路由页面应明确：

- 回答策略由管理人员控制。
- 调用方无权指定。
- 策略修改只影响新 Conversation。
- fallback 是平台内部行为。

## 16. 错误契约

| 场景 | HTTP / Code |
|---|---|
| Assistant Token 无效 | 401 `AUTHENTICATION_REQUIRED` |
| Conversation 不存在、过期或越权 | 404 `CONVERSATION_NOT_FOUND` |
| 相同幂等请求仍在执行 | 409 `REQUEST_IN_PROGRESS` |
| 同一 Conversation 正在生成 | 409 `CONVERSATION_BUSY` |
| 幂等键用于不同请求 | 409 `IDEMPOTENCY_KEY_REUSED` |
| 会话工作流并发冲突 | 409 `WORKFLOW_CONFLICT` |
| 知识库未就绪 | 409/503 `KNOWLEDGE_BASE_NOT_READY` |
| 请求字段非法或包含额外字段 | 422 `VALIDATION_ERROR` |
| 整轮超时 | 504 / `TURN_DEADLINE_EXCEEDED` |
| 模型或平台异常 | 流内 `error`，用户文案统一 |

Rerank 可降级异常和内部 fallback 不作为外部错误。

## 17. 实施顺序

1. 拆分管理和 Assistant 认证依赖。
2. 增加外部 Schema、Router 和用户上下文。
3. 执行 Conversation、Message、AssistantRequest 和 Trace 数据迁移。
4. 修复 RAG/Support owner 校验。
5. 实现知识库、会话、消息和资源查询。
6. 实现 Gateway 幂等和 Conversation 并发锁。
7. 实现外部 SSE 转换、心跳和终态重放。
8. 修复 Support 取消和 stale RUNNING 回收。
9. 实现 7 天会话清理、Trace 脱敏和 30 天删除。
10. 完成管理端 Conversation 和 Trace 适配。
11. 编写 OpenAPI、对接示例和联调测试。

## 18. 测试和验收

### 18.1 权限

- Assistant Token 不能访问管理 API。
- 用户 A 不能查询或继续用户 B 的 Conversation。
- 同一用户不同知识库完全隔离。
- 外部请求提交模型或回答策略字段时返回 422。

### 18.2 会话

- 首问原子创建 Conversation。
- 不产生空 Conversation。
- 历史消息可以继续聊天。
- 同一 Conversation 并发请求返回 `CONVERSATION_BUSY`。
- FAILED/CANCELLED 和部分回答可以恢复。

### 18.3 双链路和 SSE

- 两种管理策略产生相同外部 Contract。
- Support→RAG fallback 只产生一个外部 Assistant Message。
- 外部事件不包含模型、引擎和 fallback。
- RAG 和 Support 阻塞阶段每 15 秒存在心跳。
- SSE 拆包、粘包、CRLF、多行 data 和唯一终态正确。

### 18.4 幂等和取消

- COMPLETED、FAILED、CANCELLED 正确重放。
- 请求 hash 冲突返回 409。
- Stop 后 Message、Trace 和 AssistantRequest 均为 CANCELLED。
- 进程异常后的 stale RUNNING 能够回收。

### 18.5 生命周期

- 7 天边界正确。
- 成功续聊刷新活动时间。
- 失败和取消不刷新。
- `conversation_retention_days=0` 不删除会话。
- Trace 7 天脱敏、30 天删除。
- Index 删除后脱敏 Trace 摘要仍可查询。
