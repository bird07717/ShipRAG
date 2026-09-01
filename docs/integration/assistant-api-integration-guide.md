# 智能回答助手 API 对接文档

## 1. 文档说明

本文档面向业务后台接口开发、Android/iOS 联调和测试人员，定义业务系统调用智能回答助手时必须遵守的 HTTP、SSE、身份、会话、错误和资源访问约定。

移动端不直接调用 AI 平台。标准调用关系为：

```text
Android / iOS
        ↓
业务后台
        ↓
智能回答助手 API
```

业务后台负责验证业务用户身份、代理请求和流式响应，并保护服务访问凭证。

## 2. AI 配置透明性

智能回答所使用的模型、Prompt、检索配置、知识版本和回答策略由 AI 平台统一管理。

业务后台、Android、iOS 和最终用户：

- 不获取模型列表。
- 不选择模型。
- 不提交模型名称或模型 ID。
- 不选择回答策略。
- 不指定实际回答链路。
- 不判断或展示平台内部执行链路。
- 不根据模型或内部策略编写业务分支。

接口请求只包含知识库、Conversation、用户问题和必要的交互选项。平台内部配置变化不改变本接口的请求和响应结构。

调用方不得提交未在请求结构中定义的字段。未定义字段会导致 `422 VALIDATION_ERROR`。

`response_type` 表示回答产生后的业务结果分类，不是调用方可选择的回答模式。

## 3. 基础约定

| 项目 | 约定 |
|---|---|
| Base URL | 由部署环境提供 |
| API Prefix | `/api/v1/assistant` |
| 协议 | HTTP 或 HTTPS |
| 字符集 | UTF-8 |
| 时间格式 | ISO-8601 UTC |
| 流式协议 | Server-Sent Events（SSE） |
| Content-Type | JSON 请求使用 `application/json` |

示例中的 `{AI_BASE_URL}` 表示 AI 平台服务地址。

## 4. 服务认证和用户标识

### 4.1 通用 Header

所有 API 请求必须包含：

```http
Authorization: Bearer <assistant-service-token>
X-External-User-ID: <stable-business-user-id>
X-Request-ID: <request-correlation-id>
```

| Header | 必填 | 说明 |
|---|---:|---|
| Authorization | 是 | AI 平台分配给业务后台的服务凭证 |
| X-External-User-ID | 是 | 业务用户稳定、不透明的唯一标识，最长 100 字符 |
| X-Request-ID | 否 | 业务后台调用链 ID；未提供时由 AI 平台生成 |

### 4.2 用户标识要求

业务后台必须：

- 根据已经通过身份认证的登录用户生成 `X-External-User-ID`。
- 使用稳定、不透明且不会重复分配的用户主键。
- 保证 Android/iOS 不能自行指定或覆盖该 Header。

不得使用：

- 用户昵称。
- 手机号。
- 邮箱地址。
- 会发生变化的显示账号。

### 4.3 凭证安全

业务后台必须：

- 将 Assistant Token 保存在服务端 Secret 或安全配置中。
- 不把 Token 返回给 Android/iOS。
- 不在 URL、业务日志或错误正文中输出 Token。
- 不把 AI 平台内网地址返回给移动端。

### 4.4 AI 平台部署配置

AI 平台运维人员在**部署环境的 Secret 或环境变量**中配置：

```env
ASSISTANT_SERVICE_TOKEN=<至少 32 字符的随机值>
ASSISTANT_SERVICE_TOKEN_PREVIOUS=
```

可以在受控运维终端生成值：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

生产环境启动时会拒绝缺失、示例值或短于 32 字符的 Token。使用
`compose.production.yaml` 部署时，在项目根目录供 Compose 读取的 `.env` 文件，或由
CI/CD 注入的同名环境变量中设置它；修改后重新部署 API 与 worker 容器。

内网部署仍应在防火墙、反向代理或服务网格中限制为“业务后台 →
AI 平台 `/api/v1/assistant/*`”。Token 不提供管理台编辑或明文查看能力。

## 5. 通用错误格式

非流式 HTTP 错误统一返回：

```json
{
  "error": {
    "code": "CONVERSATION_NOT_FOUND",
    "message": "会话不存在",
    "details": {},
    "request_id": "req-20260901-001"
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| code | string | 稳定的机器可读错误码 |
| message | string | 可展示或转换的用户提示 |
| details | object | 可选错误详情，不应未经检查直接展示 |
| request_id | string | 排障调用链 ID |

## 6. 查询知识库

用户在移动端选择知识库，页面展示知识库名称，后续聊天和历史查询使用对应 `knowledge_id`。

### 请求

```http
GET {AI_BASE_URL}/api/v1/assistant/knowledge-bases
```

### 响应

```json
{
  "items": [
    {
      "knowledge_id": "4a4c40f1-04f9-4ae6-af4c-52392f302dfa",
      "name": "VDR600 知识库",
      "description": "VDR600 产品知识与故障处理",
      "ready": true
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| knowledge_id | UUID | 知识库稳定标识 |
| name | string | 向用户展示的知识库名称 |
| description | string/null | 知识库说明 |
| ready | boolean | 是否可接受问答请求 |

业务后台不得硬编码知识库 UUID。`ready=false` 的知识库不能发起聊天。

## 7. 查询欢迎信息

欢迎信息属于页面引导，不写入 Conversation 历史。

### 请求

```http
GET {AI_BASE_URL}/api/v1/assistant/knowledge-bases/{knowledge_id}/welcome
```

### 响应

```json
{
  "knowledge_id": "4a4c40f1-04f9-4ae6-af4c-52392f302dfa",
  "message": "你好，请描述需要解决的问题。",
  "suggestions": [
    "如何检查设备？",
    "如何更换保险丝？"
  ]
}
```

欢迎接口失败不应阻止聊天页面使用。客户端可以显示固定兜底欢迎语。

## 8. 查询最近 7 天会话

Conversation 按用户和知识库隔离。用户只能访问自己在指定知识库下的会话。

### 请求

```http
GET {AI_BASE_URL}/api/v1/assistant/conversations?knowledge_id={knowledge_id}&limit=20&cursor={cursor}
```

### Query 参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| knowledge_id | UUID | 是 | 用户选择的知识库 |
| limit | integer | 否 | 默认 20，最大 100 |
| cursor | string | 否 | 上一页返回的不透明游标 |

### 响应

```json
{
  "items": [
    {
      "conversation_id": "be55b152-2cfb-43a1-91f6-bd227bc9289b",
      "knowledge": {
        "knowledge_id": "4a4c40f1-04f9-4ae6-af4c-52392f302dfa",
        "name": "VDR600 知识库"
      },
      "title": "如何更换 PDC",
      "message_count": 6,
      "created_at": "2026-09-01T02:10:00Z",
      "last_activity_at": "2026-09-01T02:20:00Z",
      "expires_at": "2026-09-08T02:20:00Z",
      "status": "ACTIVE"
    }
  ],
  "next_cursor": null
}
```

`cursor` 是不透明字符串，调用方不得解析、修改或自行构造。

## 9. 查询会话详情

### 请求

```http
GET {AI_BASE_URL}/api/v1/assistant/conversations/{conversation_id}
```

### 响应

响应字段与会话列表项一致。

不存在、已经过期或不属于该用户的 Conversation 统一返回：

```http
404 CONVERSATION_NOT_FOUND
```

## 10. 查询历史消息

### 请求

```http
GET {AI_BASE_URL}/api/v1/assistant/conversations/{conversation_id}/messages?limit=50&before={before}
```

### Query 参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| limit | integer | 否 | 默认 50，最大 100 |
| before | string | 否 | 加载更早消息时使用的不透明游标 |

### 响应

```json
{
  "items": [
    {
      "message_id": "0138e735-d91f-4199-864b-013b4c86aff2",
      "conversation_id": "be55b152-2cfb-43a1-91f6-bd227bc9289b",
      "role": "USER",
      "status": "COMPLETED",
      "content": "如何检查保险丝？",
      "created_at": "2026-09-01T02:10:00Z",
      "updated_at": "2026-09-01T02:10:00Z"
    },
    {
      "message_id": "627ac474-8860-45a2-9ee8-731159a03f67",
      "conversation_id": "be55b152-2cfb-43a1-91f6-bd227bc9289b",
      "role": "ASSISTANT",
      "status": "COMPLETED",
      "trace_id": "b31f8104-f378-4c12-9a96-e49143bc5947",
      "response_type": "ANSWERED",
      "content": "请先断开设备电源……",
      "content_blocks": [],
      "sections": [],
      "document": null,
      "references": [],
      "clarification": null,
      "escalation": null,
      "disclaimer": null,
      "error": null,
      "created_at": "2026-09-01T02:10:10Z",
      "updated_at": "2026-09-01T02:10:10Z"
    }
  ],
  "next_before": null
}
```

消息状态：

| 状态 | 说明 |
|---|---|
| GENERATING | 回答仍在生成或等待状态收敛 |
| COMPLETED | 回答完成 |
| FAILED | 回答失败，可能包含部分内容 |
| CANCELLED | 用户停止或连接取消，可能包含部分内容 |

响应数组始终按时间正序排列。第一次查询返回最近一页；加载更早消息时传入 `next_before`。

## 11. 发送消息

### 请求

```http
POST {AI_BASE_URL}/api/v1/assistant/chat/stream
Authorization: Bearer <assistant-service-token>
X-External-User-ID: <stable-business-user-id>
X-Request-ID: <request-correlation-id>
Idempotency-Key: <uuid>
Content-Type: application/json
Accept: text/event-stream
```

### Request Body

```json
{
  "knowledge_id": "4a4c40f1-04f9-4ae6-af4c-52392f302dfa",
  "conversation_id": null,
  "question": "如何更换保险丝？",
  "choice_id": null,
  "include_images": true
}
```

### 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| knowledge_id | UUID | 是 | 用户选择的知识库 |
| conversation_id | UUID/null | 是 | 新聊天传 null，继续聊天传已有 ID |
| question | string | 是 | 用户问题，1～4000 字符 |
| choice_id | string/null | 否 | 澄清选项 ID |
| include_images | boolean | 否 | 是否返回回答相关图片，默认 true |

请求中不能包含模型、Prompt、回答策略、执行链路或 Index 字段。

业务后台不能把历史消息拼接到 `question`。Conversation 历史由平台自动加载。

## 12. 新聊天

新聊天通过首条消息创建，不需要单独创建空 Conversation。

首条消息：

```json
{
  "knowledge_id": "4a4c40f1-04f9-4ae6-af4c-52392f302dfa",
  "conversation_id": null,
  "question": "如何检查设备？",
  "choice_id": null,
  "include_images": true
}
```

Conversation ID 会通过 SSE `context` 和 `complete` 事件返回。业务后台必须把该 ID 返回给移动端，后续消息继续使用。

## 13. 继续历史聊天

```json
{
  "knowledge_id": "4a4c40f1-04f9-4ae6-af4c-52392f302dfa",
  "conversation_id": "be55b152-2cfb-43a1-91f6-bd227bc9289b",
  "question": "下一步应该做什么？",
  "choice_id": null,
  "include_images": true
}
```

平台负责加载历史上下文和 Conversation 状态。调用方只提交新的用户问题。

## 14. SSE 协议

### 14.1 事件清单

| 事件 | 说明 |
|---|---|
| accepted | 请求通过认证和基础校验 |
| progress | 处理阶段变化 |
| context | Conversation、Message 和 Trace 标识已经确定 |
| delta | Markdown 文本增量 |
| complete | 回答成功完成 |
| error | 流式终态错误 |
| `: ping` | 心跳注释 |

每个流只能出现一个终态事件：`complete` 或 `error`。

### 14.2 accepted

```text
event: accepted
data: {"request_id":"req-1","knowledge_id":"4a4c40f1-04f9-4ae6-af4c-52392f302dfa","conversation_id":null}
```

新聊天在该阶段可能还没有 Conversation ID。

### 14.3 progress

```text
event: progress
data: {"stage":"RETRIEVING"}
```

可用阶段：

```text
PREPARING
RETRIEVING
GENERATING
FINALIZING
```

这些值仅用于用户界面提示，调用方不应据此执行重要业务逻辑。

### 14.4 context

```text
event: context
data: {"conversation_id":"be55b152-2cfb-43a1-91f6-bd227bc9289b","user_message_id":"0138e735-d91f-4199-864b-013b4c86aff2","assistant_message_id":"627ac474-8860-45a2-9ee8-731159a03f67","trace_id":"b31f8104-f378-4c12-9a96-e49143bc5947"}
```

### 14.5 delta

```text
event: delta
data: {"message_id":"627ac474-8860-45a2-9ee8-731159a03f67","text":"请先断开设备电源。"}
```

调用方按顺序拼接 `text`。

### 14.6 complete

```text
event: complete
data: {"request_id":"req-1","conversation_id":"be55b152-2cfb-43a1-91f6-bd227bc9289b","message_id":"627ac474-8860-45a2-9ee8-731159a03f67","trace_id":"b31f8104-f378-4c12-9a96-e49143bc5947","response_type":"ANSWERED","answer":"请先断开设备电源。","content":[],"sections":[],"document":null,"references":[],"clarification":null,"escalation":null,"disclaimer":null,"next_suggestions":[],"created_at":"2026-09-01T02:10:10Z"}
```

`complete.answer` 是完整且权威的最终文本。调用方必须使用它校正已经累计的 delta。

### 14.7 error

```text
event: error
data: {"request_id":"req-1","conversation_id":"be55b152-2cfb-43a1-91f6-bd227bc9289b","message_id":"627ac474-8860-45a2-9ee8-731159a03f67","trace_id":"b31f8104-f378-4c12-9a96-e49143bc5947","code":"TURN_DEADLINE_EXCEEDED","message":"请求处理超时，请稍后重试","retryable":true,"partial_content":"已经生成的部分内容"}
```

### 14.8 心跳

```text
: ping
```

心跳不包含业务数据。业务后台应原样转发或转换为等价心跳。

## 15. SSE 解析要求

业务后台必须：

- 按 SSE 标准解析 `event` 和 `data`。
- 支持 TCP 拆包和粘包。
- 支持 `LF`、`CRLF` 和多行 `data`。
- 忽略未知事件，保证向前兼容。
- 保持事件顺序。
- 收到 `complete` 或 `error` 后关闭处理流程。
- 收到 AI 事件后立即向下游 flush。
- 禁用反向代理响应缓冲。
- 不把整个回答读取完成后再一次性返回。

业务后台可以将 AI SSE 转换成自己的流式协议，但必须完整保留事件语义、顺序、取消和错误信息。

## 16. Content Block

`content` 数组支持：

### 16.1 文本

```json
{
  "type": "text",
  "sequence_no": 1,
  "text": "回答正文"
}
```

### 16.2 表格

```json
{
  "type": "table",
  "sequence_no": 2,
  "text": "| 项目 | 说明 |\n|---|---|\n| A | B |"
}
```

### 16.3 图片

```json
{
  "type": "image",
  "sequence_no": 3,
  "image_asset_id": "uuid",
  "caption": "设备接线示意图",
  "mime_type": "image/png",
  "width": 1200,
  "height": 800
}
```

图片必须通过资源接口加载，不能把 `image_asset_id` 当作公开 URL。

## 17. 结构化回答字段

### 17.1 sections

```json
{
  "type": "DOCUMENT_FACT",
  "title": "检查电源",
  "content": "确认设备已经断电。",
  "risk_level": "HIGH",
  "disclaimer": "请由具备资质的人员操作。"
}
```

### 17.2 references

```json
{
  "document_id": "uuid",
  "title": "VDR600 保险丝检查说明",
  "section_paths": [["电源系统", "保险丝"]],
  "source_ids": ["S1"]
}
```

### 17.3 document

```json
{
  "document_id": "uuid",
  "title": "VDR600 保险丝检查说明"
}
```

### 17.4 clarification

```json
{
  "type": "DOCUMENT_CHOICE",
  "message": "请选择需要处理的问题。",
  "choices": [
    {
      "choice_id": "choice-1",
      "title": "如何更换 PDC"
    }
  ]
}
```

### 17.5 escalation

```json
{
  "required": true,
  "reason": "需要人工进一步确认"
}
```

## 18. 澄清选项

用户选择澄清项后，继续使用同一 Conversation：

```json
{
  "knowledge_id": "4a4c40f1-04f9-4ae6-af4c-52392f302dfa",
  "conversation_id": "be55b152-2cfb-43a1-91f6-bd227bc9289b",
  "question": "确认选择：如何更换 PDC",
  "choice_id": "choice-1",
  "include_images": true
}
```

`choice_id` 必须原样传递，不能由业务后台重新生成。

## 19. 停止生成

用户点击停止时：

1. Android/iOS 取消与业务后台的流。
2. 业务后台立即取消对应的 AI 平台 HTTP 请求。
3. 业务后台不得继续在后台读取并丢弃 AI 响应。
4. 平台保存已经生成的部分内容，并将消息标记为 `CANCELLED`。
5. 历史消息展示部分内容和“已停止生成”状态。

停止生成通过取消 HTTP 流实现，不调用单独的 Cancel API。

如果业务后台使用的客户端协议不能通过下游断开表达用户停止，可以在业务后台自己的 App API 中提供停止操作，但最终必须取消对应的 AI 上游 HTTP 请求。

## 20. 幂等和重试

每次实际生成必须提供：

```http
Idempotency-Key: <UUID>
```

### 20.1 使用原 Key

以下情况使用原 Idempotency-Key：

- 业务后台已经发出请求，但无法确认 AI 平台是否收到。
- 网络中断，无法确认回答是否完成。
- 需要恢复状态未知的请求。

平台会重放已经确定的结果，不生成重复 Message。

### 20.2 使用新 Key

以下情况使用新 Idempotency-Key：

- 平台已经明确返回 FAILED。
- 用户主动点击“重试”。
- 用户重新发送同样文本并希望产生新的聊天轮次。

### 20.3 禁止行为

- 收到 delta 后自动重新请求。
- 使用同一个 Key 提交不同参数。
- 超时后立即使用新 Key 静默重发。
- 同一 Conversation 并发发送多个问题。

## 21. 文档下载

### 请求

```http
GET {AI_BASE_URL}/api/v1/assistant/knowledge-bases/{knowledge_id}/documents/{document_id}/content
```

业务后台必须代理二进制响应，并向移动端提供业务后台自己的受认证下载地址或下载响应。

要求：

- 不把 Assistant Token 放入下载 URL。
- 不把 AI 平台内网地址返回给移动端。
- 保留 Content-Type、文件名和长度。
- 下载前验证业务用户仍然登录。

## 22. 图片加载

### 请求

```http
GET {AI_BASE_URL}/api/v1/assistant/knowledge-bases/{knowledge_id}/image-assets/{image_asset_id}/content
```

业务后台向移动端代理图片内容。缓存策略由业务后台依据安全要求决定。

## 23. Markdown 和内容安全

`answer` 和文本 Content Block 使用 Markdown。

Android/iOS 必须：

- 禁止执行 Markdown 中的原生 HTML。
- 禁止脚本和危险 URL Scheme。
- 对链接执行白名单处理。
- 支持段落、列表、标题、代码块、引用和表格。
- 不把 Markdown 作为未经消毒的 HTML 直接插入页面。
- 通过结构化 `image_asset_id` 加载图片。

移动端统一显示“智能回答助手”，不显示模型名称或平台内部执行方式。

## 24. 超时设置

建议配置：

| 项目 | 建议值 |
|---|---:|
| 连接超时 | 5 秒 |
| 上游总超时 | 不低于 60 秒 |
| 上游空闲超时 | 不低于 20 秒 |
| SSE 心跳周期 | 15 秒 |

业务后台和反向代理的空闲超时必须大于心跳周期。

收到 delta 后发生超时或断开时，不能自动使用新 Idempotency-Key 重发。应使用原 Key对账或由用户明确发起重试。

## 25. 错误处理

| HTTP/事件 | 错误码 | 业务后台处理 |
|---|---|---|
| 401 | AUTHENTICATION_REQUIRED | 检查服务凭证并告警，不向移动端暴露凭证信息 |
| 404 | CONVERSATION_NOT_FOUND | 提示会话失效，引导返回历史或新建聊天 |
| 409 | REQUEST_IN_PROGRESS | 保持原请求状态，不创建重复消息 |
| 409 | CONVERSATION_BUSY | 阻止重复发送，提示回答仍在生成 |
| 409 | IDEMPOTENCY_KEY_REUSED | 记录接入错误，检查 Key 使用逻辑 |
| 409 | WORKFLOW_CONFLICT | 提示用户重试，不静默并发重发 |
| 409/503 | KNOWLEDGE_BASE_NOT_READY | 知识库不可用，刷新知识库状态 |
| 422 | VALIDATION_ERROR | 检查请求字段、类型和长度 |
| 504 | TURN_DEADLINE_EXCEEDED | 展示可重试提示 |
| SSE error | 事件中的 code | 展示 message，记录 request_id 和 trace_id |

流在没有 `complete/error` 的情况下断开时，业务后台使用原 Idempotency-Key 对账，不直接创建新请求。

## 26. 日志和排障

业务后台每次 AI 调用应记录：

```text
业务用户内部ID
knowledge_id
X-Request-ID
Idempotency-Key
conversation_id
assistant_message_id
trace_id
HTTP status
SSE terminal event
error code
请求耗时
```

禁止记录：

- Assistant Token。
- 完整 Authorization Header。
- 无必要的完整问题和回答正文。
- 未脱敏的用户敏感信息。

移动端错误反馈至少提供可复制的 `request_id`。AI 平台管理人员可以使用 `request_id` 或 `trace_id` 定位完整执行记录。

## 27. 接入验收清单

### 27.1 身份和安全

- [ ] Android/iOS 不包含 Assistant Token。
- [ ] Android/iOS 不直接访问 AI 平台地址。
- [ ] external user ID 由业务后台生成，移动端不能覆盖。
- [ ] 不同用户不能访问彼此 Conversation。
- [ ] 资源下载经过业务后台认证代理。

### 27.2 AI 配置透明性

- [ ] 移动端没有模型选择入口。
- [ ] 移动端没有回答策略选择入口。
- [ ] 请求中不包含模型、Prompt 或策略字段。
- [ ] 回答页面不显示模型名称。
- [ ] 回答页面不显示平台内部执行方式。
- [ ] AI 平台调整模型或回答策略时，业务后台不需要修改接口代码。

### 27.3 会话和历史

- [ ] 用户可以选择知识库。
- [ ] 不同知识库的历史互相隔离。
- [ ] 首条消息可以取得 Conversation ID。
- [ ] 历史消息可以继续聊天。
- [ ] 最近 7 天列表和分页正确。
- [ ] FAILED/CANCELLED 可以恢复并展示。

### 27.4 Streaming

- [ ] delta 实时到达移动端。
- [ ] 代理不会缓冲完整回答。
- [ ] 15 秒心跳可以通过代理。
- [ ] SSE 拆包、粘包、CRLF 和多行 data 解析正确。
- [ ] complete 可以校正累计 delta。
- [ ] 每个流只有一个终态事件。

### 27.5 Stop、幂等和异常

- [ ] 用户 Stop 会取消 AI 上游请求。
- [ ] App 切后台和网络断开不会产生重复回答。
- [ ] 相同 Idempotency-Key 不生成重复 Message。
- [ ] 状态未知时使用原 Key 对账。
- [ ] 用户明确重试时使用新 Key。
- [ ] request_id 可以在 AI 平台定位对应 Trace。
