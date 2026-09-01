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

### 1.1 内容性质说明

为避免把当前项目事实与外部系统推测混在一起，本文档按以下规则表达：

| 类型 | 含义 | 文档中的表达方式 |
|---|---|---|
| 已确认事实 | 已能从当前 AI 平台代码、配置或数据模型直接确认 | 使用“平台会”“接口返回”“当前项目使用”等确定性描述 |
| 对接契约 | 业务后台和移动端为正确接入必须遵守的要求 | 使用“必须”“不得” |
| 接入建议 | 不由 AI 平台代码强制，业务团队可结合自身架构调整 | 明确使用“建议”“推荐” |
| 部署待定值 | 只有部署或联调时才能确定，不属于源代码事实 | 明确标记为“由部署环境提供”或“联调前确认” |

本文档不会假设业务后台、Android 或 iOS 的开发语言、框架、数据库、内部鉴权方案和页面
组件。文中关于这些系统的“必须”表示接口边界要求，不表示其内部实现已经存在。

### 1.2 当前 AI 平台已确认能力

以下内容已经在当前项目中实现，不是方案推测：

- `/api/v1/assistant/*` 使用独立 Assistant Token 认证，与管理端 `SERVICE_TOKEN` 分离。
- 所有 Assistant API 都要求 `X-External-User-ID`。
- 已提供知识库、欢迎信息、会话列表、会话详情、历史消息、SSE 问答、文档和图片资源接口。
- 问答请求严格拒绝模型、Prompt、回答策略和执行链路等未定义字段。
- 新会话随首条消息创建；会话按 `X-External-User-ID` 和知识库隔离。
- 已实现 SSE `accepted/progress/context/delta/complete/error` 事件和 15 秒空闲心跳。
- 已实现 Idempotency-Key 持久化、结果重放、同会话并发保护和 HTTP 断开取消。
- 会话保留期默认 7 天，并按 `last_activity_at` 判断是否仍可见。
- Assistant Trace 会记录 `request_id`、Conversation、Message 和 Trace 的关联，管理端可据此排障。

### 1.3 不属于当前项目已确认事实的内容

以下事项必须由部署或业务团队确定，本文档不将其描述为既有实现：

| 事项 | 状态与影响 |
|---|---|
| AI 平台实际内网 Base URL | 部署时确定，业务后台联调前获取 |
| Assistant Token 明文 | 部署时生成并通过安全渠道交付，不能写入本文档 |
| 生产环境可用知识库及 `knowledge_id` | 属于运行数据，通过知识库列表接口查询 |
| 业务用户到 `X-External-User-ID` 的映射 | 由业务后台根据自身用户体系实现 |
| 业务后台对移动端暴露 SSE 还是自定义流协议 | 由业务后台决定，但必须保持本文规定的事件语义 |
| Android/iOS 页面组件和网络框架 | 当前无法确认，只规定功能和安全要求 |
| 内网 DNS、TLS、网关、防火墙和超时最终值 | 由实际部署网络确认，本文只提供约束和建议 |

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
| Base URL | 部署待定值，由部署环境提供 |
| API Prefix | `/api/v1/assistant` |
| 协议 | HTTP 或 HTTPS |
| 字符集 | UTF-8 |
| 时间格式 | ISO-8601 UTC |
| 流式协议 | Server-Sent Events（SSE） |
| Content-Type | JSON 请求使用 `application/json` |

示例中的 `{AI_BASE_URL}` 表示 AI 平台服务地址。

## 4. 服务认证和用户标识

### 4.1 通用 Header

所有 Assistant API 请求必须包含服务凭证和外部用户标识；`X-Request-ID` 是可选但强烈建议
传入的调用链关联标识：

```http
Authorization: Bearer <assistant-service-token>
X-External-User-ID: <stable-business-user-id>
X-Request-ID: <request-correlation-id>
```

| Header | 必填 | 说明 |
|---|---:|---|
| Authorization | 是 | 固定格式 `Bearer <assistant-service-token>`；Token 由 AI 平台运维人员生成并分配给业务后台 |
| X-External-User-ID | 是 | 业务用户稳定、不透明的唯一标识，最长 100 字符 |
| X-Request-ID | 否，强烈建议 | 业务后台调用链 ID，最长 128 字符；未提供、为空或仅空白时由 AI 平台生成 |

AI 平台会在 HTTP 响应头 `X-Request-ID` 中返回最终采用的值，错误正文和 SSE
终态事件也会携带该值。业务后台应将其贯穿自身调用链。

### 4.2 Assistant Token 定义与权限边界

Assistant Token 是 AI 平台与业务后台之间的**固定服务凭证**，用于识别调用服务，
不是业务用户登录 Token，也不是一次性 Token：

- 正常运行期间，同一业务后台的请求使用同一个当前 Token。
- Token 不随用户、Conversation 或请求变化。
- AI 平台不会在启动时自动生成 Token。
- Token 不由管理页面生成、编辑、查看或下发。
- Android/iOS 永远不能持有或提交该 Token。
- `X-External-User-ID` 才用于标识 Token 背后的具体业务用户。

当前项目使用两套相互隔离的服务凭证：

| 凭证 | AI 平台环境变量 | 持有方 | 可访问范围 |
|---|---|---|---|
| Assistant Token | `ASSISTANT_SERVICE_TOKEN` | AI 平台、业务后台 | 仅 `/api/v1/assistant/*` |
| 管理服务凭证 | `SERVICE_TOKEN` | AI 平台、管理页面使用者 | 模型、文档、配置、Trace 等管理 API |

部署时必须为两者配置不同的随机值，两者不能互换。在正确隔离配置下，使用
`SERVICE_TOKEN` 调用 Assistant API，或使用 Assistant Token 调用管理 API，均返回
`401 AUTHENTICATION_REQUIRED`。业务后台不需要、也不应获得 `SERVICE_TOKEN`。

事实边界：当前代码使用两个独立配置项和两套认证依赖，但不会比较
`ASSISTANT_SERVICE_TOKEN` 与 `SERVICE_TOKEN` 的明文是否相同。因此“两个值必须不同”是
部署时必须执行的安全契约，不应误写成平台已经自动阻止相同值。

缺少 Token、Bearer 格式错误或 Token 不匹配时，认证在建立业务流之前失败：平台返回
HTTP `401`、JSON 错误正文和 `WWW-Authenticate: Bearer`，不会先返回 SSE `accepted`
事件。

### 4.3 用户标识要求

业务后台必须：

- 根据已经通过身份认证的登录用户生成 `X-External-User-ID`。
- 使用稳定、不透明且不会重复分配的用户主键。
- 保证 Android/iOS 不能自行指定或覆盖该 Header。

不得使用：

- 用户昵称。
- 手机号。
- 邮箱地址。
- 会发生变化的显示账号。

### 4.4 凭证安全

业务后台必须：

- 将 Assistant Token 保存在服务端 Secret 或安全配置中。
- 不把 Token 返回给 Android/iOS。
- 不在 URL、业务日志或错误正文中输出 Token。
- 不把 AI 平台内网地址返回给移动端。
- 不将 Token 写入代码仓库、前端构建产物或客户端配置。
- 不把完整 `Authorization` Header 记录到接入日志。

### 4.5 AI 平台部署配置

首次部署时，AI 平台运维人员生成一个随机值，并在**部署环境的 Secret 或环境变量**中
配置：

```env
ASSISTANT_SERVICE_TOKEN=<至少 32 字符的随机值>
ASSISTANT_SERVICE_TOKEN_PREVIOUS=
```

可以在受控运维终端生成值：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

生成后的同一个当前 Token 需要分别保存到：

1. AI 平台的 `ASSISTANT_SERVICE_TOKEN`。
2. 业务后台的服务端 Secret 配置。

`ASSISTANT_SERVICE_TOKEN_PREVIOUS` 是可选旧凭证。首次部署和不处于轮换期时必须保持
为空，不需要同时生成两个 Token。

生产环境启动时会拒绝以下配置：

- 缺少 `ASSISTANT_SERVICE_TOKEN`。
- 使用 `change-me-for-assistant-integration` 等示例值。
- Token 少于 32 字符或包含首尾空格。
- 当前 Token 与 Previous Token 相同。

部署人员还必须确保 `ASSISTANT_SERVICE_TOKEN` 与管理端 `SERVICE_TOKEN` 使用不同随机值，
维持 Assistant API 与管理 API 的权限隔离。

使用 `compose.production.yaml` 部署时，在项目根目录供 Compose 读取的 `.env` 文件，或由
CI/CD 注入的同名环境变量中设置它；变更后重新部署使用共享应用环境的容器。开发和测试
环境允许未配置 Token，但业务联调环境必须显式配置，不能依赖该开发便利行为。

### 4.6 Token 轮换

只有更换 Token 且需要保持调用不中断时，才使用 Previous Token：

1. 生成新 Token。
2. AI 平台配置“新 Token 为 `ASSISTANT_SERVICE_TOKEN`、旧 Token 为
   `ASSISTANT_SERVICE_TOKEN_PREVIOUS`”，然后重新部署。
3. 业务后台切换为新 Token。
4. 确认旧 Token 已无调用后，清空 `ASSISTANT_SERVICE_TOKEN_PREVIOUS` 并再次部署。

轮换窗口内，AI 平台同时接受当前值和 Previous 值。业务后台不得同时随机使用两个值；
Previous 仅用于兼容尚未完成切换的实例。

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

## 8. 查询保留期内会话（当前默认最近 7 天）

Conversation 按用户和知识库隔离。用户只能访问自己在指定知识库下的会话。
当前代码的 `CONVERSATION_RETENTION_DAYS` 默认值为 7，可见性按
`last_activity_at >= 当前时间 - 保留天数` 计算。若生产部署修改该值，AI 平台必须在联调前
通知业务团队；本次对接验收基线为 7 天。

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
| before | string | 否 | 上一页 `next_before` 返回的消息序号游标；必须原样回传 |

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

`items` 数组始终按时间正序排列。第一次查询返回最近一页；加载更早消息时传入
`next_before`。当前游标值是字符串形式的消息序号，调用方不应自行生成或修改。

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
| conversation_id | UUID/null | 否 | 新聊天省略或传 null，继续聊天传已有 ID |
| question | string | 是 | 用户问题，1～4000 字符 |
| choice_id | string/null | 否 | 澄清选项 ID，最长 100 字符 |
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

对于新的正常回答，当前外部事件顺序为：

```text
accepted → progress（0 次或多次）→ context → delta（0 次或多次）→ complete
```

`progress` 只用于展示处理状态；调用方不得依赖其次数或把它当作 Conversation 已创建的信号。
幂等重放已完成请求时，平台可以直接返回 `accepted → complete`；发生流式错误时，以
`error` 作为唯一终态。调用方应按事件名处理，而不是假设每一种非终态事件都会出现。

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
```

这些值仅用于用户界面提示，调用方不应据此执行重要业务逻辑。

### 14.4 context

```text
event: context
data: {"conversation_id":"be55b152-2cfb-43a1-91f6-bd227bc9289b","user_message_id":"0138e735-d91f-4199-864b-013b4c86aff2","assistant_message_id":"627ac474-8860-45a2-9ee8-731159a03f67","trace_id":"b31f8104-f378-4c12-9a96-e49143bc5947"}
```

`context` 是 Conversation 和消息标识可用于后续请求及排障的时点；在收到它之前，
调用方不得假设新 Conversation 已有可复用 ID。

### 14.5 delta

```text
event: delta
data: {"message_id":"627ac474-8860-45a2-9ee8-731159a03f67","text":"请先断开设备电源。"}
```

调用方按顺序拼接 `text`。

### 14.6 complete

```text
event: complete
data: {"request_id":"req-1","conversation_id":"be55b152-2cfb-43a1-91f6-bd227bc9289b","message_id":"627ac474-8860-45a2-9ee8-731159a03f67","trace_id":"b31f8104-f378-4c12-9a96-e49143bc5947","response_type":"ANSWERED","answer":"请先断开设备电源。","content":[],"sections":[],"references":[],"next_suggestions":[],"created_at":"2026-09-01T02:10:10Z"}
```

`complete.answer` 是完整且权威的最终文本。调用方必须使用它校正已经累计的 delta。
`document`、`clarification`、`escalation` 和 `disclaimer` 等没有值的可选字段会被省略，
调用方必须同时兼容“字段不存在”和 `null`，不能假设所有可选字段固定出现。

### 14.7 error

```text
event: error
data: {"request_id":"req-1","conversation_id":"be55b152-2cfb-43a1-91f6-bd227bc9289b","message_id":"627ac474-8860-45a2-9ee8-731159a03f67","trace_id":"b31f8104-f378-4c12-9a96-e49143bc5947","code":"TURN_DEADLINE_EXCEEDED","message":"请求处理超时，请稍后重试","retryable":true,"partial_content":"已经生成的部分内容"}
```

`partial_content` 仅在已产生文本增量时出现；`conversation_id`、`message_id` 和 `trace_id`
在错误发生得很早时也可能为空或缺失。调用方应保留 `code`、`request_id` 和可用的关联 ID
用于排障，并仅向用户展示适合当前产品的 `message`。

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

当前接口接受 1～200 字符的非空值，推荐业务后台统一使用 UUID。该值的作用域包含业务
用户和知识库；业务后台仍应保证所有实际生成请求使用全局唯一值。

### 20.1 使用原 Key

以下情况使用原 Idempotency-Key：

- 业务后台已经发出请求，但无法确认 AI 平台是否收到。
- 网络中断，无法确认回答是否完成。
- 需要恢复状态未知的请求。

平台会重放已经确定的结果，不生成重复 Message。

同一逻辑请求使用原 Key 重试时，建议同时复用原 `X-Request-ID`。如果重试时使用新的
`X-Request-ID`，HTTP 响应头和 `accepted` 表示本次 HTTP 尝试，而重放的 `complete`
可能保留首次生成时的 `request_id`；业务后台应以 Idempotency-Key 关联这两次尝试。

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

## 24. 接入建议：超时设置

本节数值是业务后台和代理层的接入建议，不是当前 AI 平台对调用方技术栈的已确认配置。
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
- [ ] 业务后台仅保存 `ASSISTANT_SERVICE_TOKEN`，不持有管理端 `SERVICE_TOKEN`。
- [ ] Assistant Token 与管理端 `SERVICE_TOKEN` 使用不同随机值。
- [ ] 非轮换期的 `ASSISTANT_SERVICE_TOKEN_PREVIOUS` 保持为空。
- [ ] 缺失、错误以及误用管理 Token 调用 Assistant API 均返回 HTTP 401。
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

## 28. 当前代码依据与交接确认

### 28.1 代码依据

| 契约内容 | 当前项目代码依据 |
|---|---|
| Assistant 路由及全部外部接口 | `backend/app/api/assistant.py` |
| Assistant 与管理端凭证隔离 | `backend/app/api/router.py`、`backend/app/api/dependencies.py` |
| 请求、会话和消息结构 | `backend/app/api/schemas.py` |
| SSE 转换、心跳、幂等重放和错误终态 | `backend/app/assistant/service.py` |
| 用户会话隔离、分页、请求持久化和资源查询 | `backend/app/gateway/repository.py` |
| 模型和回答策略不对外暴露 | `backend/app/assistant/service.py`、`backend/app/gateway/service.py` |
| Token 强度与生产环境校验 | `backend/app/core/config.py`、`compose.production.yaml` |
| Nginx SSE 代理和缓冲设置 | `infra/nginx/default.conf` |
| 7 天会话及 Trace 生命周期 | `backend/app/core/config.py`、`backend/app/tasks/gc.py` |

### 28.2 联调前必须交付的值

AI 平台团队在联调前只需向业务后台安全交付或开放以下内容：

1. AI 平台内网 `{AI_BASE_URL}`。
2. 当前 `ASSISTANT_SERVICE_TOKEN`；不得交付管理端 `SERVICE_TOKEN`。
3. 到 `/api/v1/assistant/*` 的内网连通性。

`knowledge_id` 不作为静态秘密交付。业务后台使用知识库列表接口获取当前可用值。

### 28.3 交接结论

当前 AI 平台端接口能力已实现，业务后台可以按照本文档开始开发和联调。对业务后台、
Android 和 iOS 内部技术实现的描述仅属于接口要求或建议；除第 1.2 节列出的平台能力外，
本文档不声称外部系统已经完成相应功能。
