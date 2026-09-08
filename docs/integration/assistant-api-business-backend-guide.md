# Assistant API 业务后台对接指南

## 1. 生产入口与配置

| 项目 | 值 |
| --- | --- |
| Shipdemo 内网 IP | `172.17.2.12` |
| Base URL | `http://172.17.2.12:18080` |
| Assistant API | `http://172.17.2.12:18080/api/v1/assistant` |
| 就绪检查 | `http://172.17.2.12:18080/health/ready` |

以上是迁移完成后的目标地址；`/health/ready` 验收通过前不能视为已上线。Token 不由接口自动生成，必须由运维在 Shipdemo 与业务后台的服务端 Secret 中配置同一个值。

Shipdemo 生产环境：

```env
APP_HTTP_BIND_ADDRESS=0.0.0.0
APP_HTTP_PORT=18080
ASSISTANT_SERVICE_TOKEN=<由部署 Secret 注入的生产值>
ASSISTANT_SERVICE_TOKEN_PREVIOUS=
```

业务后台：

```env
SHIPDEMO_BASE_URL=http://172.17.2.12:18080
SHIPDEMO_ASSISTANT_TOKEN=<通过安全渠道取得的生产值>
SHIPDEMO_CONNECT_TIMEOUT_SECONDS=5
SHIPDEMO_SSE_IDLE_TIMEOUT_SECONDS=25
```

- 生产 Token 只存放在双方服务端的 Secret/环境变量中，不写入代码、文档、URL 或日志，不下发给 App/Web。
- 业务后台不使用 Shipdemo 管理端的 `SERVICE_TOKEN`。
- 防火墙仅允许业务后台服务器访问 `172.17.2.12:18080`；具备内网 TLS 网关时改用 HTTPS 地址。
- Token 轮换时，Shipdemo 临时将旧值配置到 `ASSISTANT_SERVICE_TOKEN_PREVIOUS`，业务后台切换完成后清空旧值并重新部署。

连通性检查：

```bash
curl --fail http://172.17.2.12:18080/health/ready

curl --fail \
  -H "Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}" \
  -H "X-External-User-ID: integration-check" \
  http://172.17.2.12:18080/api/v1/assistant/knowledge-bases
```

## 2. 调用契约

除健康检查外，所有 Assistant 接口都必须携带：

```http
Authorization: Bearer <SHIPDEMO_ASSISTANT_TOKEN>
X-External-User-ID: <业务后台生成的稳定用户 ID>
X-Request-ID: <业务调用链 ID，可选但建议提供>
```

`X-External-User-ID` 长度为 1～100 字符，必须由业务后台从已登录用户生成；客户端不得提交或覆盖。聊天接口额外要求：

```http
Idempotency-Key: <本次业务请求的稳定唯一键，1～200 字符>
Content-Type: application/json
Accept: text/event-stream
```

### 2.1 接口清单

| 用途 | 方法与路径 |
| --- | --- |
| 知识库列表 | `GET /api/v1/assistant/knowledge-bases` |
| 欢迎语 | `GET /api/v1/assistant/knowledge-bases/{knowledge_id}/welcome` |
| 会话列表 | `GET /api/v1/assistant/conversations?knowledge_id=...&limit=20&cursor=...` |
| 会话详情 | `GET /api/v1/assistant/conversations/{conversation_id}` |
| 历史消息 | `GET /api/v1/assistant/conversations/{conversation_id}/messages?limit=50&before=...` |
| 流式问答 | `POST /api/v1/assistant/chat/stream` |
| 下载文档 | `GET /api/v1/assistant/knowledge-bases/{knowledge_id}/documents/{document_id}/content` |
| 加载图片 | `GET /api/v1/assistant/knowledge-bases/{knowledge_id}/image-assets/{image_asset_id}/content` |

完整字段结构见[《Assistant API 完整接口契约》](assistant-api-integration-guide.md)。

### 2.2 首次提问

无需预创建会话，`conversation_id` 传 `null`：

```bash
curl --no-buffer \
  -X POST "${SHIPDEMO_BASE_URL}/api/v1/assistant/chat/stream" \
  -H "Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}" \
  -H "X-External-User-ID: business-user-123" \
  -H "X-Request-ID: req-7f2c" \
  -H "Idempotency-Key: 914f6886-8070-45fe-a9c3-bdc28f430beb" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  --data '{
    "knowledge_id": "4a4c40f1-04f9-4ae6-af4c-52392f302dfa",
    "conversation_id": null,
    "question": "如何更换保险丝？",
    "choice_id": null,
    "include_images": true
  }'
```

请求约束：

- `knowledge_id` 必须来自知识库列表，且 `ready=true`；不要硬编码 UUID。
- `question` 必须为非空字符串，最长 4,000 字符。
- `choice_id` 仅在回答澄清选项时传入，最长 100 字符。
- 请求体禁止额外字段，业务后台不能选择内部执行引擎。

### 2.3 继续会话

只传本次新问题，不拼接历史消息：

```json
{
  "knowledge_id": "4a4c40f1-04f9-4ae6-af4c-52392f302dfa",
  "conversation_id": "be55b152-2cfb-43a1-91f6-bd227bc9289b",
  "question": "下一步应该做什么？",
  "choice_id": null,
  "include_images": true
}
```

Shipdemo 会加载该会话的历史和冻结版本。会话必须同时属于当前 `X-External-User-ID` 和 `knowledge_id`，否则返回 `CONVERSATION_NOT_FOUND`。

### 2.4 SSE 处理

常规顺序：

```text
accepted → progress* → context → delta* → complete
                                  └──────────→ error
```

| 事件 | 业务后台动作 |
| --- | --- |
| `accepted` | 标记上游已接收；不是成功终态 |
| `progress` | 可转发进度提示，不参与成功判断 |
| `context` | 保存 `conversation_id`、用户/助手消息 ID、`trace_id` |
| `delta` | 按到达顺序追加 `text`，立即 flush 给客户端 |
| `complete` | 唯一成功终态；用完整 `answer` 校正增量文本并幂等落库 |
| `error` | 失败终态；保存 `code`、关联 ID 和 `partial_content` |
| `: ping` | 15 秒无上游帧时的 SSE 注释心跳，不是业务事件 |

必须满足：

- `accepted` 表示认证、参数、会话权限、知识库状态、幂等和并发前置检查均已通过；在它之前失败时，接口返回对应 HTTP `4xx/5xx` JSON 错误，不建立 SSE。
- 使用标准 SSE 解析器，兼容 TCP 拆包/粘包、`LF`/`CRLF`、多行 `data` 和注释帧。
- HTTP `200` 只表示流已建立；必须等到 `complete` 或 `error`。
- 幂等重放可能只有 `accepted → complete` 或 `accepted → error`，不能依赖一定出现 `context`/`delta`。
- 忽略未知事件并记录事件名，不能因此中断当前流。
- App 断开或用户停止生成时，立即取消业务后台到 Shipdemo 的上游请求。
- 无终态断流时标记 `UNKNOWN`，使用原请求参数和原 `Idempotency-Key` 对账。

### 2.5 历史与资源

- 会话列表默认 20、最大 100；`next_cursor` 原样作为下一页 `cursor`。
- 消息列表默认 50、最大 100；`next_before` 原样作为下一页 `before`，单页消息按时间正序返回。
- 会话默认保留 7 天，以 `last_activity_at` 计算；生产环境可通过 `CONVERSATION_RETENTION_DAYS` 覆盖。
- 文档和图片由业务后台完成用户权限校验后流式代理；保留上游 `Content-Type`、可用的 `Content-Length` 和文档文件名，不得把 Shipdemo 地址或 Token 返回客户端，不得整文件读入内存。

## 3. 业务后台内部实现约束

### 3.1 权限边界

Shipdemo 只按 `X-External-User-ID` 隔离会话，不掌握业务租户、角色或产品权限。业务后台必须校验用户能否：使用问答、访问知识库、查看会话、读取引用文档/图片。资源接口按知识库与资源 ID 校验，不替代业务权限校验。

### 3.2 幂等与状态

建议保存最小请求台账：

```text
client_request_id (unique) | client_chat_id | idempotency_key (unique) | business_user_id
knowledge_id | conversation_id | request_id | user_message_id
assistant_message_id | trace_id | request_payload_hash
status: REQUESTED/ACCEPTED/STREAMING/COMPLETED/FAILED/CANCELLED/UNKNOWN
error_code | started_at | first_delta_at | completed_at
```

- App 每次点击发送生成稳定 `client_request_id`；业务后台首次受理时生成一个 `Idempotency-Key`。
- `client_chat_id` 是业务后台为一个聊天窗口/草稿会话生成的稳定 ID；首次取得 Shipdemo `conversation_id` 前，同一聊天窗口的所有发送操作必须复用它。它不是每次发送都变化的 `client_request_id`。
- 相同 `client_request_id` 的重复提交必须复用原 Key 和原参数。
- 相同 Key 修改问题、会话、`choice_id` 或 `include_images` 会返回 `IDEMPOTENCY_KEY_REUSED`。
- 收到部分增量或结果未知后，不得换新 Key 静默重试；用原 Key 对账。
- 只有明确失败后由用户主动重试，才创建新的业务请求和新 Key。
- Shipdemo 是会话和消息正文的事实源；业务后台保存副本时，按 `conversation_id`、`assistant_message_id` 幂等 upsert。

### 3.3 并发锁

同一 Conversation 同时只能有一个生成请求：

- 已有会话锁：`assistant:conversation:{conversation_id}`。
- 新会话在取得 `context` 前的锁：`assistant:new:{user_id}:{knowledge_id}:{client_chat_id}`。
- 收到 `context` 后保存 `client_chat_id → conversation_id` 映射，后续改用已有会话锁；同一 `client_chat_id` 不得同时创建多个 Shipdemo Conversation。
- 多实例业务后台使用 Redis 锁或数据库唯一约束，不使用进程内锁。
- 锁 TTL 覆盖请求总超时；收到终态或取消后主动释放。

Shipdemo 数据库会以 `CONVERSATION_BUSY` 最终拒绝同会话并发，但业务后台应提前拦截，避免占用连接和模型资源。

### 3.4 超时、取消和重试

- 连接超时建议 3～5 秒；SSE 空闲超时不得低于 25 秒。
- 端到端总超时按真实链路 p99 设置，并小于 Nginx 当前 180 秒读写超时。
- HTTP 客户端不得自动使用新 Key 重试 POST。
- 进程异常后将本地 `ACCEPTED/STREAMING` 改为 `UNKNOWN`，使用原 Key 与原参数对账。
- 下游断开必须传播取消；Shipdemo 会将请求记为 `CANCELLED`。
- 非流式 `4xx/5xx` 按 JSON 错误体处理；流建立后的失败按 SSE `error` 处理。

### 3.5 核心处理流程

```text
认证用户 → 校验知识库/会话权限 → 获取或创建请求台账 → 获取会话锁
  → 打开 Shipdemo SSE → accepted/context/delta 实时更新并转发
  → complete 幂等落库，或 error/断流记录失败状态
  → 关闭上游连接 → 释放锁
```

主要错误处理；“HTTP”表示在 `accepted` 前返回 JSON，“SSE”表示流建立后的 `error` 事件：

| 错误码 | 载体 | 处理 |
| --- | --- | --- |
| `AUTHENTICATION_REQUIRED` | HTTP 401 | 配置告警，不自动重试 |
| `VALIDATION_ERROR` | HTTP 422 | 接入参数错误，记录告警 |
| `CONVERSATION_NOT_FOUND` | HTTP 404 | 会话过期、越权或知识库不匹配；引导新建会话 |
| `REQUEST_IN_PROGRESS` | HTTP 409 | 原 Key 仍在执行，不创建新请求 |
| `ABANDONED_ASSISTANT_REQUEST` | SSE | 原请求已被运维恢复为失败终态；用户主动重试时使用新 Key |
| `CONVERSATION_BUSY` | HTTP 409 | 阻止同会话重复发送 |
| `IDEMPOTENCY_KEY_REUSED` | HTTP 409 | 幂等实现错误，立即告警 |
| `KNOWLEDGE_BASE_NOT_READY` | HTTP 409/503 | 停止发送并刷新知识库状态 |
| `TURN_DEADLINE_EXCEEDED` | SSE | 本轮失败；用户主动重试时使用新 Key |

## 4. 性能压力与容量边界

### 4.1 当前实现事实

- Production Compose 默认启动 2 个 Uvicorn worker。
- Assistant 当前没有独立 QPS、单用户或全局并发限制。
- 每条 Chat 占用一条“客户端→业务后台”和一条“业务后台→Shipdemo”长连接。
- Assistant 空闲心跳为 15 秒；RAG 单轮默认时限为 45 秒；Nginx `/api/` 读写超时为 180 秒且关闭响应缓冲。
- 数据库引擎未显式配置连接池容量；增加 Uvicorn worker 会同步放大数据库连接数和模型调用并发。
- 文档/图片以 64 KiB 分块从 MinIO 输出；高并发资源下载主要消耗 MinIO 连接与内网带宽。
- `assistant_request` 当前没有独立清理任务，需要监控表增长。

### 4.2 业务后台资源设计

业务后台必须使用非阻塞流式 IO，并拆分连接池：

| 连接池 | 用途 | 约束 |
| --- | --- | --- |
| Chat SSE | 长时间问答 | 容量不低于允许的最大 Chat 并发并保留突发余量 |
| Metadata | 知识库、会话、历史 | 独立小池，不能被长流占满 |
| Asset | 文档、图片 | 独立并发和带宽限制，全程流式转发 |

并发限制至少覆盖：同会话 1、单用户、单租户、全局四级。超过已验证容量时快速返回业务侧 `429/503`，不要无限排队。

### 4.3 压力估算与瓶颈

```text
平均活跃 Chat SSE ≈ 峰值发起速率（次/秒）× 平均完整回答时间（秒）
```

例如峰值 2 次/秒、平均 20 秒，平均约 40 条活跃 Chat 流；连接池、文件描述符、内存和网关连接上限还需预留突发余量。该公式不是容量承诺。

压力优先级：

1. 模型 Provider 的并发额度、429、首字延迟和生成速度。
2. PostgreSQL 连接池等待、混合检索慢查询和锁等待。
3. Shipdemo Uvicorn worker 的活跃 SSE、CPU、内存和事件循环延迟。
4. 业务后台上下游连接池、文件描述符和断连回收。
5. 文档/图片下载对 MinIO 和网络带宽的挤占。

### 4.4 当前结论与压测门槛

当前仓库只有 fake provider 负载验收，未覆盖“业务后台代理 + Assistant SSE + 真实模型”完整链路，因此不能承诺生产 QPS 或最大并发。

上线前使用真实模型、代表性知识库和生产同规格环境验证：持续 Chat、2 倍短时突发、同 Key 重放、同会话并发、随机取消、Chat/历史/资源混合流量、Provider 429/超时及单实例退出。记录成功率、活跃 SSE、首字/完整耗时 p50/p95/p99、错误码、Provider 限流、CPU、内存、文件描述符、数据库连接池等待和慢查询；据此确定单用户、单租户和全局并发阈值。

## 5. 上线检查

- [ ] 业务后台可访问 `http://172.17.2.12:18080/health/ready`。
- [ ] TCP 18080 仅对白名单业务后台开放。
- [ ] 两端通过 Secret 配置相同 Assistant Token，客户端无 Token。
- [ ] `X-External-User-ID` 由服务端生成并完成业务权限校验。
- [ ] 首次提问、续聊、历史分页、资源代理和 Token 轮换联调通过。
- [ ] SSE 实时转发，且只以 `complete/error` 判断终态。
- [ ] 断连/停止会取消上游；原 Key 对账、同会话锁已实现。
- [ ] Chat、Metadata、Asset 使用独立连接池和并发额度。
- [ ] 完成真实链路压测后再设置和承诺容量指标。
