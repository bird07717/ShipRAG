# Assistant API 业务后台对接契约

## 目录

- [1. 服务信息](#1-服务信息)
- [2. 环境与鉴权](#2-环境与鉴权)
- [3. 公共调用契约](#3-公共调用契约)
- [4. 整体接入流程](#4-整体接入流程)
- [5. 接口调用关系](#5-接口调用关系)
- [6. 接口清单](#6-接口清单)
- [7. 核心对接规则](#7-核心对接规则)
- [8. 性能压力与容量规则](#8-性能压力与容量规则)
- [9. 错误处理规则](#9-错误处理规则)
- [10. 对接验收清单](#10-对接验收清单)

## 1. 服务信息

### 1.1 服务名称

Shipdemo Assistant API

### 1.2 服务说明

向业务后台提供知识库查询、欢迎语、会话管理、流式问答、历史消息查询，以及回答所引用图片和文档的受控下载能力。

业务 App、Web 页面不得直接调用本服务；所有请求应由业务后台发起或代理。

## 2. 环境与鉴权

### 2.1 基础地址

- 当前内网联调地址：`http://172.17.2.12:18080`
- Assistant API 前缀：`/api/v1/assistant`
- 完整示例：`http://172.17.2.12:18080/api/v1/assistant/knowledge-bases`
- 项目已迁移至目标服务器；2026-09-10 已确认 `/health/ready`、Assistant 鉴权和知识库列表接口可用，可进行内网联调。正式上线前仍须完成凭据轮换、防火墙来源限制和真实流式问答验收。

### 2.2 鉴权方式

除健康检查外，所有接口均使用 Bearer Token：

该凭据对应 Shipdemo 的 `ASSISTANT_SERVICE_TOKEN`，仅用于业务后台调用 Assistant API，不得与管理接口使用的 `SERVICE_TOKEN` 混用。

当前处于测试联调阶段，已配置临时 Assistant Token，并通过安全渠道提供给对接方。该 Token 仅允许保存在双方服务端的 Secret 或环境变量中；正式上线前必须重新生成生产 Token、完成双方配置切换，并撤销测试 Token。文档不记录任何真实 Token 值。

```http
Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}
X-External-User-ID: user-12345
X-Request-ID: 550e8400-e29b-41d4-a716-446655440000
```

| Header | 必填 | 规则 |
| --- | --- | --- |
| `Authorization` | 是 | 格式固定为 `Bearer <token>`；Token 只保存在业务后台服务端密钥配置中，不得下发到浏览器、App 或日志。 |
| `X-External-User-ID` | 是 | 业务系统内稳定且唯一的用户标识，长度 1～100；不得使用昵称等可变化字段。 |
| `X-Request-ID` | 否 | 单次 HTTP 请求追踪标识，最长 128 字符；建议由业务后台生成 UUID。 |

业务后台建议配置：

```env
SHIPDEMO_BASE_URL=http://172.17.2.12:18080
SHIPDEMO_ASSISTANT_TOKEN=replace-with-secret-from-secure-channel
SHIPDEMO_CONNECT_TIMEOUT_SECONDS=5
SHIPDEMO_READ_TIMEOUT_SECONDS=25
```

流式问答不能使用普通 25 秒整体读取超时，应分别配置连接超时、首事件超时和业务总时限。

## 3. 公共调用契约

- JSON 请求和响应统一使用 UTF-8，时间统一为 ISO 8601 UTC 字符串，资源 ID 使用 UUID 字符串。
- 请求字段按文档发送；未声明的额外字段可能返回 HTTP 422。
- 响应中的可选字段可能缺失或为 `null`；调用方必须兼容新增字段和未知 SSE 事件。
- 除 `/health/ready` 外，HTTP 非 2xx 错误统一按下列结构解析：

```json
{
  "error": {
    "code": "CONVERSATION_NOT_FOUND",
    "message": "会话不存在",
    "details": {},
    "request_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

HTTP 错误字段：

| 字段 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `error` | object | 是 | 错误对象。 | 所有 HTTP 失败统一从该对象读取。 |
| `error.code` | string | 是 | 稳定的机器可读错误码。 | 业务逻辑以该字段分支，不依赖 `message` 文案。 |
| `error.message` | string | 是 | 用户可读错误说明。 | 可展示或映射为业务侧文案。 |
| `error.details` | object | 是 | 补充信息；没有详情时为空对象。 | 仅辅助排查，调用方必须兼容新增字段。 |
| `error.request_id` | string | 是 | 服务端请求追踪 ID。 | 记录到日志，联调或报障时提供。 |

常见 `response_type`：

| 值 | 含义 | 调用方处理 |
| --- | --- | --- |
| `ANSWERED` | 已生成正常回答。 | 展示 `answer` 及相关内容。 |
| `UNCONFIRMED` | 当前资料不足以确认。 | 展示回答并提示用户补充信息。 |
| `OUT_OF_SCOPE` | 问题不在服务范围内。 | 展示范围提示。 |
| `DOC_DELIVERED` / `DOCUMENT_DELIVERED` | 已匹配并交付文档。 | 展示 `document`、`content` 和下载入口。 |
| `CLARIFICATION_REQUIRED` | 需要用户进一步选择或补充。 | 根据 `clarification` 构建交互。 |
| `ESCALATION_REQUIRED` | 建议转人工或升级处理。 | 根据 `escalation` 进入业务侧流程。 |
| 其他值 | 后续扩展类型。 | 按普通文本回答兼容，不直接报错。 |

- `UNCONFIRMED`、`OUT_OF_SCOPE` 及不引用产品资料的通用解释不会返回可下载文档引用；调用方仅在 `references` 或 `document` 实际包含文档 ID 时展示下载入口。
- 流式问答在服务接受请求前发生的鉴权、参数、会话归属、知识库/索引可用性、幂等或并发错误，以 HTTP 4xx/5xx JSON 返回；接受请求后的生成错误，以 SSE `error` 事件返回。
- `cursor`、`before` 均为不透明游标，只能原样回传，不得拆解、拼接或自行生成。

## 4. 整体接入流程

### 4.1 标准流程

```text
1. 服务启动或定时探测：调用就绪检查
2. 用户进入问答功能：查询后台已上线知识库，并根据 `ready` 判断当前是否可用
3. 用户选择知识库：查询欢迎语（可选）
4. 业务后台创建 client_chat_id，并为首次提问生成 Idempotency-Key
5. 首次流式问答：conversation_id 省略或传 null
6. 收到 context/complete：保存 client_chat_id → conversation_id 映射
7. 后续追问：携带同一 conversation_id，并为每次新提问生成新的 Idempotency-Key
8. 页面恢复或历史查询：调用会话列表、会话详情和历史消息接口
9. 展示引用资源：根据 document_id/image_asset_id，由业务后台代理下载
```

### 4.2 流式问答状态处理

```text
提交请求
  ├─ HTTP 4xx/5xx：请求未被接受，按 error.code 处理
  └─ HTTP 200：开始解析 SSE
       ├─ accepted：前置检查通过
       ├─ progress/context/delta：更新进度、会话映射和临时回答
       ├─ complete：本轮成功，保存最终回答和资源 ID
       ├─ error：本轮失败，按 retryable 决定是否允许重试
       └─ 网络中断且无终态：结果未知，使用原 Key 和原请求体重试
```

### 4.3 页面恢复流程

1. 使用接口 4 查询当前用户在目标知识库下的会话列表。
2. 用户选择会话后，使用接口 5 确认会话仍然存在。
3. 使用接口 6 加载最近一页消息；存在 `next_before` 时按需加载更早消息。
4. 历史回答包含资源 ID 时，通过接口 8 或接口 9 重新获取资源。
5. 会话返回 404 时，删除业务后台中的旧映射，并按首次问答流程创建新会话。

## 5. 接口调用关系

| 顺序 | 当前接口 | 关键输入来源 | 关键输出 | 后续用途 | 是否必调 |
| --- | --- | --- | --- | --- | --- |
| 1 | 接口 1：就绪检查 | 固定基础地址 | `status` | 判断是否允许发起业务请求 | 上线验收必调，运行期建议定时调用 |
| 2 | 接口 2：知识库列表 | 当前用户身份 | 已上线知识库的 `knowledge_id`、`ready` | 欢迎语、问答、资源下载 | 必调 |
| 3 | 接口 3：欢迎语 | 接口 2 的 `knowledge_id` | `message`、`suggestions` | 页面展示或快捷提问 | 可选 |
| 4 | 接口 7：首次问答 | `knowledge_id`、新 `Idempotency-Key` | `conversation_id`、最终回答 | 建立会话映射并继续追问 | 必调 |
| 5 | 接口 7：继续问答 | 已保存的 `conversation_id`、新 `Idempotency-Key` | 新消息、引用和资源 ID | 更新当前对话 | 按需重复调用 |
| 6 | 接口 4：会话列表 | `knowledge_id` | `conversation_id`、`next_cursor` | 选择历史会话、继续分页 | 有历史页面时调用 |
| 7 | 接口 5：会话详情 | 接口 4 或接口 7 的 `conversation_id` | 会话元数据 | 校验和展示会话 | 可选 |
| 8 | 接口 6：历史消息 | `conversation_id` | 消息、`next_before`、资源 ID | 恢复对话、继续分页或下载资源 | 有历史页面时调用 |
| 9 | 接口 8/9：资源下载 | 回答或历史消息中的资源 ID | 文档/图片二进制 | 由业务后台代理给前端 | 有资源时调用 |

关键传递关系：

- `knowledge_id` 只能来自接口 2，后续传给接口 3、7、8、9。
- `conversation_id` 来自接口 7 的 `context`/`complete` 或接口 4，后续传给接口 5、6、7。
- `document_id` 来自 `complete.references[].document_id`、`complete.document.id` 或历史消息中的对应字段，传给接口 8。
- `image_asset_id` 来自 `complete.content` 或历史消息的 `content_blocks`，传给接口 9。
- `next_cursor` 只回传给接口 4；`next_before` 只回传给接口 6。
- `Idempotency-Key` 由业务后台生成，不来自其他接口；一次用户主动发送对应一个 Key。

## 6. 接口清单

### 6.1 接口 1：服务就绪检查

接口名称：服务就绪检查

接口用途：判断 Assistant 服务及其关键依赖是否可接收请求。

method：`GET`

url：`{BASE_URL}/health/ready`

请求参数：无；不需要鉴权 Header。

请求示例：

```bash
curl -i "${SHIPDEMO_BASE_URL}/health/ready"
```

成功响应示例：

```http
HTTP/1.1 200 OK
Content-Type: application/json

{"status":"ready","checks":{"postgres":{"status":"ok","latency_ms":3.2},"redis":{"status":"ok","latency_ms":1.1},"minio":{"status":"ok","latency_ms":2.4}}}
```

响应字段说明：

| 字段 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `status` | string | 是 | `ready` 表示服务可用，`not_ready` 表示至少一项依赖异常。 | 只有 `ready` 才允许继续调用业务接口。 |
| `checks` | object | 是 | 各项就绪检查结果，键名可能扩展。 | 遍历展示或记录，不依赖固定数量。 |
| `checks.<name>.status` | string | 是 | 单项状态：`ok` 或 `error`。 | `error` 时记录对应检查项。 |
| `checks.<name>.latency_ms` | number | 是 | 单项检查耗时，单位毫秒。 | 用于监控，不作为业务成功条件。 |
| `checks.<name>.version` | string | 否 | 依赖版本；并非所有检查项都会返回。 | 可记录，不能设为必填。 |
| `checks.<name>.detail` | string | 否 | 不可用时的简要原因。 | 用于排查，不直接展示给终端用户。 |

失败响应示例：

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json

{"status":"not_ready","checks":{"postgres":{"status":"error","detail":"dependency unavailable","latency_ms":5.1},"redis":{"status":"ok","latency_ms":1.1},"minio":{"status":"ok","latency_ms":2.4}}}
```

特殊规则：只有 HTTP 200 才表示服务可用；响应字段可扩展，调用方不得依赖 `checks` 中固定的检查项数量。

---

### 6.2 接口 2：查询可用知识库

接口名称：查询可用知识库

接口用途：取得后台已对 Assistant 上线、且当前 Token 允许业务用户使用的知识库。

method：`GET`

url：`{BASE_URL}/api/v1/assistant/knowledge-bases`

请求参数：无 Query 参数；必须携带公共鉴权 Header。

请求示例：

```bash
curl -sS "${SHIPDEMO_BASE_URL}/api/v1/assistant/knowledge-bases" \
  -H "Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}" \
  -H "X-External-User-ID: user-12345"
```

成功响应示例：

```json
{
  "items": [
    {
      "knowledge_id": "5ae9881a-a568-436b-9330-2da5e9a2a9c9",
      "name": "船舶业务知识库",
      "description": "船舶业务问答",
      "ready": true
    }
  ]
}
```

响应字段说明：

| 字段 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `items` | array | 是 | 当前已上线并对 Assistant 可见的知识库列表；无上线项时为空数组。 | 遍历展示，不得假定至少存在一项。 |
| `items[].knowledge_id` | UUID string | 是 | 知识库唯一标识。 | 保存为字符串，供欢迎语、问答和资源接口使用。 |
| `items[].name` | string | 是 | 知识库名称。 | 用于页面展示。 |
| `items[].description` | string/null | 否 | 知识库说明。 | 为空时隐藏说明区域。 |
| `items[].ready` | boolean | 是 | 当前运行策略所需索引、模型、Prompt 等依赖是否完整就绪。 | 仅允许选择 `true` 的知识库。 |

失败响应示例：

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{"error":{"code":"AUTHENTICATION_REQUIRED","message":"服务访问凭据无效","details":{},"request_id":"..."}}
```

特殊规则：业务后台只能展示或使用响应中返回且 `ready=true` 的知识库；知识库上线状态可能变化，应在进入问答功能时刷新列表，不得在代码中长期硬编码或永久缓存知识库 ID。后台未上线或已下线的知识库不会出现在列表中。

---

### 6.3 接口 3：查询知识库欢迎语

接口名称：查询知识库欢迎语

接口用途：在用户进入问答页面时展示知识库欢迎信息。

method：`GET`

url：`{BASE_URL}/api/v1/assistant/knowledge-bases/{knowledge_id}/welcome`

请求参数：

| 位置 | 参数 | 必填 | 说明 |
| --- | --- | --- | --- |
| Path | `knowledge_id` | 是 | 接口 2 返回的知识库 UUID。 |

请求示例：

```bash
curl -sS "${SHIPDEMO_BASE_URL}/api/v1/assistant/knowledge-bases/5ae9881a-a568-436b-9330-2da5e9a2a9c9/welcome" \
  -H "Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}" \
  -H "X-External-User-ID: user-12345"
```

成功响应示例：

```json
{
  "knowledge_id": "5ae9881a-a568-436b-9330-2da5e9a2a9c9",
  "message": "您好，我是您的智能技术助手。请描述您遇到的问题或想完成的操作，我会为您提供清晰、可执行的建议。",
  "suggestions": [
    "如何使用U盘升级PDC",
    "如何更换PSU电源板",
    "如何手动自检VDR"
  ]
}
```

响应字段说明：

| 字段 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `knowledge_id` | UUID string | 是 | 当前欢迎语所属知识库。 | 可用于校验响应是否对应当前选择。 |
| `message` | string | 是 | 欢迎文案。 | 作为页面初始提示展示，不写入会话历史。 |
| `suggestions` | string[] | 是 | 服务就绪时返回 3 条经过回归验证的标准问题；服务未就绪时可能为空。 | 可渲染为快捷提问；点击后仍按一次新问答处理。 |

失败响应示例：

```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{"error":{"code":"KNOWLEDGE_BASE_NOT_FOUND","message":"知识库不存在","details":{},"request_id":"..."}}
```

特殊规则：欢迎语只用于展示，不产生会话或历史消息；只能使用接口 2 当前返回的知识库 ID。知识库下线后可能返回 HTTP 404；失败时业务页面可使用本地默认文案，不应阻断其他知识库的问答功能。

---

### 6.4 接口 4：查询会话列表

接口名称：查询会话列表

接口用途：分页查询指定用户在指定知识库下的历史会话。

method：`GET`

url：`{BASE_URL}/api/v1/assistant/conversations`

请求参数：

| 位置 | 参数 | 必填 | 说明 |
| --- | --- | --- | --- |
| Query | `knowledge_id` | 是 | 知识库 UUID。 |
| Query | `limit` | 否 | 1～100，默认 20。 |
| Query | `cursor` | 否 | 上一页响应的 `next_cursor`，最长 500 字符。 |

请求示例：

```bash
curl -sS "${SHIPDEMO_BASE_URL}/api/v1/assistant/conversations?knowledge_id=5ae9881a-a568-436b-9330-2da5e9a2a9c9&limit=20" \
  -H "Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}" \
  -H "X-External-User-ID: user-12345"
```

成功响应示例：

```json
{
  "items": [
    {
      "conversation_id": "b23a568e-5d20-42f3-87e6-d7019b992c70",
      "knowledge": {
        "knowledge_id": "5ae9881a-a568-436b-9330-2da5e9a2a9c9",
        "name": "船舶业务知识库"
      },
      "title": "船舶证书如何办理",
      "message_count": 2,
      "created_at": "2026-09-08T02:00:00Z",
      "last_activity_at": "2026-09-08T02:01:00Z",
      "expires_at": "2026-09-15T02:01:00Z",
      "status": "ACTIVE"
    }
  ],
  "next_cursor": null
}
```

响应字段说明：

| 字段 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `items` | array | 是 | 当前页会话，按最近活动时间倒序排列。 | 依次展示；空数组表示没有历史会话。 |
| `items[].conversation_id` | UUID string | 是 | 会话唯一标识。 | 用于会话详情、历史消息和继续问答。 |
| `items[].knowledge` | object | 是 | 会话所属知识库摘要。 | 展示知识库名称并校验当前页面上下文。 |
| `items[].knowledge.knowledge_id` | UUID string | 是 | 所属知识库 ID。 | 继续问答时作为 `knowledge_id`。 |
| `items[].knowledge.name` | string | 是 | 所属知识库名称。 | 用于展示。 |
| `items[].title` | string/null | 否 | 会话标题。 | 为空时使用本地默认标题。 |
| `items[].message_count` | integer | 是 | 会话内消息总数。 | 用于展示，不用于分页计算。 |
| `items[].created_at` | datetime string | 是 | 会话创建时间。 | 按业务时区格式化展示。 |
| `items[].last_activity_at` | datetime string | 是 | 最近活动时间。 | 用于排序展示。 |
| `items[].expires_at` | datetime string/null | 否 | 会话预计过期时间；未启用保留期时可为 `null`。 | 可用于过期提示，不作为本地删除指令。 |
| `items[].status` | string | 是 | 当前固定为 `ACTIVE`。 | 兼容未来新增状态。 |
| `next_cursor` | string/null | 是 | 下一页游标；`null` 表示没有下一页。 | 非空时原样传给下一次接口 4 请求。 |

失败响应示例：

```http
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/json

{"error":{"code":"INVALID_CURSOR","message":"分页游标无效","details":{},"request_id":"..."}}
```

特殊规则：会话按 Token 权限、`X-External-User-ID` 和 `knowledge_id` 隔离；只使用服务端返回的游标翻页。会话存在保留周期，调用方不得将此接口作为永久业务档案存储。

---

### 6.5 接口 5：查询会话详情

接口名称：查询会话详情

接口用途：根据会话 ID 查询会话基本信息。

method：`GET`

url：`{BASE_URL}/api/v1/assistant/conversations/{conversation_id}`

请求参数：

| 位置 | 参数 | 必填 | 说明 |
| --- | --- | --- | --- |
| Path | `conversation_id` | 是 | 流式问答返回或会话列表返回的 UUID。 |

请求示例：

```bash
curl -sS "${SHIPDEMO_BASE_URL}/api/v1/assistant/conversations/b23a568e-5d20-42f3-87e6-d7019b992c70" \
  -H "Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}" \
  -H "X-External-User-ID: user-12345"
```

成功响应示例：

```json
{
  "conversation_id": "b23a568e-5d20-42f3-87e6-d7019b992c70",
  "knowledge": {
    "knowledge_id": "5ae9881a-a568-436b-9330-2da5e9a2a9c9",
    "name": "船舶业务知识库"
  },
  "title": "船舶证书如何办理",
  "message_count": 2,
  "created_at": "2026-09-08T02:00:00Z",
  "last_activity_at": "2026-09-08T02:01:00Z",
  "expires_at": "2026-09-15T02:01:00Z",
  "status": "ACTIVE"
}
```

响应字段说明：

| 字段 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `conversation_id` | UUID string | 是 | 会话唯一标识。 | 与请求 ID 对比，并用于历史消息或继续问答。 |
| `knowledge` | object | 是 | 所属知识库摘要。 | 展示并校验知识库上下文。 |
| `knowledge.knowledge_id` | UUID string | 是 | 所属知识库 ID。 | 继续问答时作为 `knowledge_id`。 |
| `knowledge.name` | string | 是 | 所属知识库名称。 | 用于展示。 |
| `title` | string/null | 否 | 会话标题。 | 为空时使用本地默认标题。 |
| `message_count` | integer | 是 | 会话消息总数。 | 仅用于展示。 |
| `created_at` | datetime string | 是 | 会话创建时间。 | 按业务时区格式化。 |
| `last_activity_at` | datetime string | 是 | 最近活动时间。 | 用于展示或判断新旧。 |
| `expires_at` | datetime string/null | 否 | 预计过期时间。 | 可用于提醒；实际是否存在仍以接口返回为准。 |
| `status` | string | 是 | 当前固定为 `ACTIVE`。 | 兼容未来新增状态。 |

失败响应示例：

```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{"error":{"code":"CONVERSATION_NOT_FOUND","message":"会话不存在","details":{},"request_id":"..."}}
```

特殊规则：同一 `conversation_id` 只能由创建它的外部用户访问；404 时不得自动改用其他用户身份重试。

---

### 6.6 接口 6：查询会话历史消息

接口名称：查询会话历史消息

接口用途：加载指定会话的用户提问、Assistant 回答及关联资源。

method：`GET`

url：`{BASE_URL}/api/v1/assistant/conversations/{conversation_id}/messages`

请求参数：

| 位置 | 参数 | 必填 | 说明 |
| --- | --- | --- | --- |
| Path | `conversation_id` | 是 | 会话 UUID。 |
| Query | `limit` | 否 | 1～100，默认 50。 |
| Query | `before` | 否 | 上一页响应的 `next_before`，最长 32 字符。 |

请求示例：

```bash
curl -sS "${SHIPDEMO_BASE_URL}/api/v1/assistant/conversations/b23a568e-5d20-42f3-87e6-d7019b992c70/messages?limit=50" \
  -H "Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}" \
  -H "X-External-User-ID: user-12345"
```

成功响应示例：

```json
{
  "items": [
    {
      "message_id": "1ea8f37d-3c89-4730-a933-0c6b3979cf65",
      "conversation_id": "b23a568e-5d20-42f3-87e6-d7019b992c70",
      "role": "USER",
      "status": "COMPLETED",
      "content": "船舶证书如何办理？",
      "trace_id": null,
      "response_type": null,
      "content_blocks": [],
      "sections": [],
      "document": null,
      "references": [],
      "clarification": null,
      "escalation": null,
      "disclaimer": null,
      "error": null,
      "created_at": "2026-09-08T02:00:00Z",
      "updated_at": "2026-09-08T02:00:00Z"
    },
    {
      "message_id": "f115de3b-30dd-462b-b07f-e88ae102a289",
      "conversation_id": "b23a568e-5d20-42f3-87e6-d7019b992c70",
      "role": "ASSISTANT",
      "content": "办理流程如下……",
      "status": "COMPLETED",
      "trace_id": "0a377fb1-a80f-43a6-85ee-189de51d15f9",
      "response_type": "ANSWERED",
      "content_blocks": [],
      "sections": [],
      "document": null,
      "references": [
        {
          "document_id": "79271b06-cf5e-4c6c-aea9-c8b48f803543",
          "title": "船舶证书办理指南"
        }
      ],
      "clarification": null,
      "escalation": null,
      "disclaimer": null,
      "error": null,
      "created_at": "2026-09-08T02:00:10Z",
      "updated_at": "2026-09-08T02:00:10Z"
    }
  ],
  "next_before": null
}
```

响应字段说明：

| 字段 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `items` | array | 是 | 当前页消息，按会话内时间正序排列。 | 按数组顺序渲染。 |
| `items[].message_id` | UUID string | 是 | 消息唯一标识。 | 用于前端列表 Key 和本地幂等更新。 |
| `items[].conversation_id` | UUID string | 是 | 消息所属会话。 | 校验是否属于当前会话。 |
| `items[].role` | string | 是 | `USER` 或 `ASSISTANT`。 | 决定消息展示方向。 |
| `items[].status` | string | 是 | `GENERATING`、`COMPLETED`、`FAILED` 或 `CANCELLED`。 | 只有 `COMPLETED` 表示回答完整成功。 |
| `items[].content` | string | 是 | 用户问题或 Assistant 最终文本。 | 作为历史正文展示。 |
| `items[].trace_id` | UUID string/null | 否 | Assistant 回答的追踪标识。 | 故障反馈时随 `request_id` 一并提供。 |
| `items[].response_type` | string/null | 否 | 回答类型。 | 用于选择展示组件；未知值按普通文本兼容。 |
| `items[].content_blocks` | object[] | 是 | 结构化内容块，常见类型为 `text`、`table`、`image`。 | 按 `type` 渲染；未知类型忽略。 |
| `items[].content_blocks[].type` | string | 条件必有 | 内容块类型。 | `text/table` 展示文本，`image` 使用资源 ID 加载图片。 |
| `items[].content_blocks[].text` | string/null | 否 | 文本或表格内容。 | 存在时按原顺序展示。 |
| `items[].content_blocks[].image_asset_id` | UUID string/null | 否 | 图片资源 ID。 | 存在时调用接口 9。 |
| `items[].sections` | object[] | 是 | 可选的分段结构。 | 支持时分段展示，否则使用 `content`。 |
| `items[].document` | object/null | 否 | 单文档交付信息，常用字段为 `id`、`title`。 | 使用 `id` 调用接口 8，不拼接内部地址。 |
| `items[].references` | object[] | 是 | 回答引用列表，常用字段为 `document_id`、`title`。 | 显示标题；存在 `document_id` 时可调用接口 8。 |
| `items[].clarification` | object/null | 否 | 需要用户补充信息时的结构化内容。 | 根据内容生成补充提问交互。 |
| `items[].escalation` | object/null | 否 | 建议转人工或升级处理的信息。 | 交给业务侧人工流程。 |
| `items[].disclaimer` | string/null | 否 | 回答附加提示。 | 在回答附近展示。 |
| `items[].error` | object/null | 否 | 失败或取消消息的错误信息。 | 非空时显示失败状态，不把不完整内容当作成功答案。 |
| `items[].error.code` | string | 条件必有 | 机器可读错误码。 | 用于业务分支和日志。 |
| `items[].error.message` | string | 条件必有 | 用户可读错误说明。 | 可展示给用户。 |
| `items[].error.retryable` | boolean/null | 否 | 是否建议重试。 | `true` 时提供重试入口。 |
| `items[].created_at` | datetime string | 是 | 消息创建时间。 | 按业务时区展示。 |
| `items[].updated_at` | datetime string | 是 | 消息最后更新时间。 | 用于本地记录更新判断。 |
| `next_before` | string/null | 是 | 更早一页游标；`null` 表示没有更早消息。 | 非空时原样传给接口 6。 |

失败响应示例：

```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{"error":{"code":"CONVERSATION_NOT_FOUND","message":"会话不存在","details":{},"request_id":"..."}}
```

特殊规则：单页 `items` 按时间正序返回；首次请求返回最近一页，更早消息使用 `next_before` 查询。调用方应兼容回答状态不是 `COMPLETED` 的历史记录。

---

### 6.7 接口 7：流式问答

接口名称：流式问答

接口用途：提交用户问题，并通过 SSE 持续返回回答、引用和资源信息。

method：`POST`

url：`{BASE_URL}/api/v1/assistant/chat/stream`

请求参数：

Header：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `Authorization` | 是 | Bearer Token。 |
| `X-External-User-ID` | 是 | 当前业务用户稳定 ID。 |
| `Idempotency-Key` | 是 | 1～200 字符；每次用户主动发送生成一个新值，建议 UUID。 |
| `X-Request-ID` | 否 | HTTP 请求追踪 ID。 |
| `Content-Type` | 是 | `application/json`。 |
| `Accept` | 建议 | `text/event-stream`。 |

Body：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knowledge_id` | UUID string | 是 | 知识库 ID。 |
| `conversation_id` | UUID string / null | 否 | 首次问答省略或传 `null`，追问传服务返回的会话 ID。 |
| `question` | string | 是 | 本次新增问题，长度 1～4000；不要拼接完整历史。 |
| `choice_id` | string / null | 否 | 用户选择建议选项时回传，长度 1～100。 |
| `include_images` | boolean | 否 | 是否返回图片资源，默认 `true`。 |

请求示例：

```bash
curl -N "${SHIPDEMO_BASE_URL}/api/v1/assistant/chat/stream" \
  -H "Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}" \
  -H "X-External-User-ID: user-12345" \
  -H "X-Request-ID: 916752f2-5256-46d3-a186-e62417e3f23a" \
  -H "Idempotency-Key: 2f965322-c441-4ce3-8371-9ae612c46e71" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  --data '{
    "knowledge_id":"5ae9881a-a568-436b-9330-2da5e9a2a9c9",
    "conversation_id":null,
    "question":"船舶证书如何办理？",
    "choice_id":null,
    "include_images":true
  }'
```

成功响应示例：

```text
event: accepted
data: {"request_id":"916752f2-5256-46d3-a186-e62417e3f23a","knowledge_id":"5ae9881a-a568-436b-9330-2da5e9a2a9c9","conversation_id":null}

event: progress
data: {"stage":"PREPARING"}

event: context
data: {"conversation_id":"b23a568e-5d20-42f3-87e6-d7019b992c70","user_message_id":"1ea8f37d-3c89-4730-a933-0c6b3979cf65","assistant_message_id":"f115de3b-30dd-462b-b07f-e88ae102a289","trace_id":"0a377fb1-a80f-43a6-85ee-189de51d15f9"}

event: delta
data: {"message_id":"f115de3b-30dd-462b-b07f-e88ae102a289","text":"办理流程"}

event: delta
data: {"message_id":"f115de3b-30dd-462b-b07f-e88ae102a289","text":"如下……"}

event: complete
data: {"request_id":"916752f2-5256-46d3-a186-e62417e3f23a","conversation_id":"b23a568e-5d20-42f3-87e6-d7019b992c70","message_id":"f115de3b-30dd-462b-b07f-e88ae102a289","trace_id":"0a377fb1-a80f-43a6-85ee-189de51d15f9","response_type":"ANSWERED","answer":"办理流程如下……","content":[{"type":"text","text":"办理流程如下……"},{"type":"image","image_asset_id":"5b594127-f6b8-41b0-8bd8-5df90f47ff26"}],"sections":[],"references":[{"document_id":"79271b06-cf5e-4c6c-aea9-c8b48f803543","title":"船舶证书办理指南"}],"next_suggestions":[],"created_at":"2026-09-08T02:00:10Z"}

```

响应 Header：

| Header | 说明 | 调用方处理 |
| --- | --- | --- |
| `Content-Type` | `text/event-stream`。 | 使用 SSE 流解析，不按普通 JSON 读取。 |
| `X-Request-ID` | 本次请求追踪 ID。 | 保存到日志，故障反馈时提供。 |
| `Cache-Control` | 禁止缓存和转换。 | 代理层保持原值。 |
| `X-Accel-Buffering` | 值为 `no`。 | Nginx 等代理不得缓冲流。 |

SSE 事件关系：

| 事件 | 是否终态 | 主要字段 | 含义 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `accepted` | 否 | `request_id`、`knowledge_id`、`conversation_id` | 请求已通过前置检查。 | 标记本轮已接受，开始等待后续事件。 |
| `progress` | 否 | `stage` | 当前处理阶段。 | 更新进度提示；同一请求可能出现多次。 |
| `context` | 否 | 会话和消息 ID | 返回本轮持久化标识。 | 立即保存 `conversation_id` 和消息映射。 |
| `delta` | 否 | `message_id`、`text` | 回答文本增量。 | 按收到顺序追加到临时显示内容。 |
| `complete` | 是 | 最终回答、引用和资源 | 本轮成功结束。 | 使用该事件覆盖临时内容并保存终态。 |
| `error` | 是 | 错误码、消息、重试信息 | 本轮失败结束。 | 停止读取，按 `retryable` 决定交互。 |

公共及过程事件字段：

| 事件.字段 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `accepted.request_id` | string | 是 | 请求追踪 ID。 | 与请求日志关联。 |
| `accepted.knowledge_id` | UUID string | 是 | 本轮知识库 ID。 | 校验当前页面上下文。 |
| `accepted.conversation_id` | UUID string/null | 是 | 续聊时为已有会话；首次问答通常为 `null`。 | 首次问答等待 `context` 获取新 ID。 |
| `progress.stage` | string | 是 | `PREPARING`、`RETRIEVING` 或 `GENERATING`。 | 映射为业务侧进度文案；未知值使用通用文案。 |
| `context.conversation_id` | UUID string | 是 | 本轮所属会话。 | 建立或更新 `client_chat_id → conversation_id` 映射。 |
| `context.user_message_id` | UUID string/null | 否 | 本轮用户消息 ID。 | 保存为本地消息映射。 |
| `context.assistant_message_id` | UUID string/null | 否 | 本轮 Assistant 消息 ID。 | 后续 `delta` 和终态按此 ID 更新。 |
| `context.trace_id` | UUID string/null | 否 | 本轮追踪 ID。 | 故障反馈时提供。 |
| `delta.message_id` | UUID string/null | 否 | 当前 Assistant 消息 ID。 | 存在时按 ID 合并；为空时归入当前请求。 |
| `delta.text` | string | 是 | 本次新增文本片段，可能很短。 | 原样按顺序追加，不自行插入分隔符。 |

`complete` 字段：

| 字段 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `request_id` | string | 是 | 请求追踪 ID。 | 与当前请求核对并记录。 |
| `conversation_id` | UUID string | 是 | 会话 ID。 | 保存，后续追问必须回传。 |
| `message_id` | UUID string | 是 | 最终 Assistant 消息 ID。 | 按 ID 幂等保存最终回答。 |
| `trace_id` | UUID string/null | 否 | 本轮追踪 ID。 | 故障反馈时提供。 |
| `response_type` | string/null | 否 | 回答类型。 | 选择展示组件；未知值按普通文本处理。 |
| `answer` | string | 是 | 完整最终回答。 | 覆盖由 `delta` 拼接的临时文本。 |
| `content` | object[] | 是 | 结构化内容块，常见类型为 `text`、`table`、`image`。 | 按数组顺序和 `type` 渲染。 |
| `content[].type` | string | 条件必有 | 内容块类型。 | `text/table` 展示文本，`image` 加载图片；未知类型忽略。 |
| `content[].text` | string/null | 否 | 文本或表格内容。 | 存在时原样展示。 |
| `content[].image_asset_id` | UUID string/null | 否 | 图片资源 ID。 | 存在时调用接口 9。 |
| `content[].source_id` | string/null | 否 | 内容块对应的引用标识。 | 可用于关联引用，不用于拼接下载地址。 |
| `sections` | object[] | 是 | 分段回答结构，可能为空。 | 支持时分段展示，否则使用 `answer`。 |
| `document` | object/null | 否 | 单文档交付信息，常用字段为 `id`、`title`。 | 使用 `document.id` 调用接口 8。 |
| `references` | object[] | 是 | 回答引用列表。 | 展示引用；包含 `document_id` 时可下载。 |
| `references[].document_id` | UUID string | 条件必有 | 被引用文档 ID。 | 与当前 `knowledge_id` 一起传给接口 8。 |
| `references[].title` | string | 否 | 被引用文档标题。 | 用于展示下载入口名称。 |
| `clarification` | object/null | 否 | 需要用户补充信息时的内容。 | 转换为补充提问交互。 |
| `escalation` | object/null | 否 | 转人工或升级处理信息。 | 对接业务侧人工流程。 |
| `disclaimer` | string/null | 否 | 附加提示。 | 在回答附近展示。 |
| `next_suggestions` | string[] | 是 | 下一步建议问题，可能为空。 | 可渲染为快捷提问。 |
| `created_at` | datetime string | 是 | 服务端生成终态的时间。 | 记录并按业务时区展示。 |

`error` 字段：

| 字段 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| `request_id` | string | 是 | 请求追踪 ID。 | 记录到错误日志。 |
| `conversation_id` | UUID string/null | 否 | 已创建或已有的会话 ID。 | 非空时保存会话映射。 |
| `message_id` | UUID string/null | 否 | 失败的 Assistant 消息 ID。 | 按 ID 标记失败。 |
| `trace_id` | UUID string/null | 否 | 追踪 ID。 | 故障反馈时提供。 |
| `code` | string | 是 | 机器可读错误码。 | 按错误码执行稳定业务分支。 |
| `message` | string | 是 | 用户可读错误说明。 | 可直接展示或映射为业务文案。 |
| `retryable` | boolean | 是 | 是否属于可重试错误。 | `true` 时提供重试入口；自动重试仍须遵守幂等规则。 |
| `partial_content` | string | 否 | 失败前已经生成的文本，可能缺失或为空。 | 只能作为失败现场展示，不能当作完整答案保存。 |

失败响应示例：

请求接受前返回 HTTP JSON：

```http
HTTP/1.1 409 Conflict
Content-Type: application/json

{"error":{"code":"CONVERSATION_BUSY","message":"会话正在生成回答","details":{},"request_id":"..."}}
```

请求接受后返回 SSE `error`：

```text
event: error
data: {"code":"TURN_DEADLINE_EXCEEDED","message":"服务繁忙，请稍后重试","request_id":"...","retryable":true,"partial_content":""}

```

特殊规则：

- SSE 按空行分帧；同一事件可能包含多行 `data:`，不能按单个网络数据块解析。
- 正常事件顺序为 `accepted → progress/context/delta → complete`；`progress` 和 `delta` 可出现多次，`complete` 与 `error` 均为终止事件。
- HTTP 200 仅表示流已建立；只有收到 `complete` 才算业务成功，最终回答以 `complete.answer` 为准。
- 服务可能发送以冒号开头的心跳注释，调用方应忽略但用于刷新空闲计时。
- 网络中断且结果未知时，使用原 `Idempotency-Key` 和完全相同的请求体重试；用户主动再次发送时必须生成新 Key。
- 同一个 `Idempotency-Key` 不得对应不同请求体；冲突时按失败处理，不可自动换 Key 掩盖问题。
- 同一会话同一时刻只允许一个问答请求。首次问答尚未取得 `conversation_id` 时，业务后台应按自己的稳定聊天窗口标识串行化请求。
- 幂等重放可能直接返回 `accepted → complete` 或 `accepted → error`，调用方不得假定一定会收到 `delta`。
- 客户端断开后，业务后台应取消对上游 SSE 的读取，避免无效连接继续占用资源。

---

### 6.8 接口 8：下载引用文档

接口名称：下载引用文档

接口用途：根据回答或历史消息中的 `document_id` 获取原始文档内容。

method：`GET`

url：`{BASE_URL}/api/v1/assistant/knowledge-bases/{knowledge_id}/documents/{document_id}/content`

请求参数：

| 位置 | 参数 | 必填 | 说明 |
| --- | --- | --- | --- |
| Path | `knowledge_id` | 是 | 当前会话所属知识库 UUID。 |
| Path | `document_id` | 是 | `complete.references[].document_id`、`complete.document.id` 或历史消息返回的文档 UUID。 |

请求示例：

```bash
curl -fS "${SHIPDEMO_BASE_URL}/api/v1/assistant/knowledge-bases/5ae9881a-a568-436b-9330-2da5e9a2a9c9/documents/79271b06-cf5e-4c6c-aea9-c8b48f803543/content" \
  -H "Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}" \
  -H "X-External-User-ID: user-12345" \
  --output document.bin
```

成功响应示例：

```http
HTTP/1.1 200 OK
Content-Type: application/pdf
Content-Length: 245760
Content-Disposition: attachment; filename="document.pdf"; filename*=UTF-8''document.pdf

[binary body]
```

响应字段说明：

| 响应项 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| Body | binary stream | 是 | 原始文档二进制内容。 | 边读边转发，不完整加载到内存。 |
| `Content-Type` | Header | 是 | 文档 MIME 类型；无法识别时为 `application/octet-stream`。 | 原样转发给前端。 |
| `Content-Length` | Header | 否 | 文档字节数；无法取得时可能缺失。 | 有值时原样转发并用于进度展示。 |
| `Content-Disposition` | Header | 是 | `attachment` 及安全编码后的文件名。 | 原样转发，使浏览器按附件下载。 |
| `Cache-Control` | Header | 是 | 私有短时缓存策略。 | 不得改成公共缓存。 |
| `X-Content-Type-Options` | Header | 是 | 固定为 `nosniff`。 | 原样转发。 |

失败响应示例：

```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{"error":{"code":"DOCUMENT_NOT_FOUND","message":"文档不存在","details":{},"request_id":"..."}}
```

特殊规则：业务后台必须校验当前登录用户有权访问该知识库后再代理下载；应流式转发二进制响应及 `Content-Type`、`Content-Disposition`，并在存在时转发 `Content-Length`，不得把 Assistant Token 暴露给终端。

---

### 6.9 接口 9：下载回答图片

接口名称：下载回答图片

接口用途：根据回答内容中的 `image_asset_id` 获取图片二进制内容。

method：`GET`

url：`{BASE_URL}/api/v1/assistant/knowledge-bases/{knowledge_id}/image-assets/{image_asset_id}/content`

请求参数：

| 位置 | 参数 | 必填 | 说明 |
| --- | --- | --- | --- |
| Path | `knowledge_id` | 是 | 当前会话所属知识库 UUID。 |
| Path | `image_asset_id` | 是 | `complete.content` 中返回的图片资源 UUID。 |

请求示例：

```bash
curl -fS "${SHIPDEMO_BASE_URL}/api/v1/assistant/knowledge-bases/5ae9881a-a568-436b-9330-2da5e9a2a9c9/image-assets/5b594127-f6b8-41b0-8bd8-5df90f47ff26/content" \
  -H "Authorization: Bearer ${SHIPDEMO_ASSISTANT_TOKEN}" \
  -H "X-External-User-ID: user-12345" \
  --output image.bin
```

成功响应示例：

```http
HTTP/1.1 200 OK
Content-Type: image/png
Content-Length: 48321

[binary body]
```

响应字段说明：

| 响应项 | 类型 | 是否必有 | 说明 | 调用方处理 |
| --- | --- | --- | --- | --- |
| Body | binary stream | 是 | 图片二进制内容。 | 边读边转发，不完整加载到内存。 |
| `Content-Type` | Header | 是 | 图片 MIME 类型；无法识别时为 `application/octet-stream`。 | 原样转发并用于图片展示。 |
| `Content-Length` | Header | 否 | 图片字节数；上游未提供时可能缺失。 | 存在时原样转发，不得设为业务必填。 |
| `Cache-Control` | Header | 是 | 私有短时缓存策略。 | 不得改成公共缓存。 |
| `X-Content-Type-Options` | Header | 是 | 固定为 `nosniff`。 | 原样转发。 |

失败响应示例：

```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{"error":{"code":"IMAGE_ASSET_NOT_FOUND","message":"图片不存在","details":{},"request_id":"..."}}
```

特殊规则：图片 URL 应由业务后台生成并代理访问；前端不得自行拼接 Assistant 地址或携带 Token。`Content-Length` 在上游未提供时可能缺失。下载失败时保留回答文本并显示资源不可用，不应判定整次问答失败。

## 7. 核心对接规则

1. 用户身份：`X-External-User-ID` 必须来自业务后台当前登录态，禁止接受前端任意传值后直接透传。
2. 权限控制：业务后台应先校验用户是否可以使用目标知识库；Assistant 返回的可见列表仍是最终可调用范围。
3. 幂等控制：业务后台应保存“业务发送记录 ID、`Idempotency-Key`、请求体摘要、`conversation_id`、终态”。结果未知时复用原 Key；明确失败后由用户重新发起时使用新 Key。平台只在幂等记录保留期间保证重放：已关联请求随 Conversation 生命周期保留，未关联 Conversation 的终态请求当前从最后更新或重放起保留 7 天；超过窗口后同一 Key 可能被视为新请求，业务后台不得把平台记录当作永久消息存储。
4. 并发控制：同一会话严格串行。首次请求使用业务后台生成的稳定 `client_chat_id` 串行化，取得 `conversation_id` 后保存 `client_chat_id → conversation_id` 映射。
5. SSE 转发：必须使用支持流式读取和即时刷新的 HTTP 客户端及网关配置；不得缓存完整答案后一次性返回。
6. 资源访问：文档和图片只能通过接口 8、接口 9 受控访问；禁止将对象存储地址或 Assistant Token 返回给前端。
7. 日志脱敏：可以记录 `X-Request-ID`、`Idempotency-Key`、状态码和耗时；不得记录 Token、完整问题、完整回答或文档二进制内容。
8. 兼容性：未知 JSON 字段和未知非终止 SSE 事件应忽略；已声明字段类型变化、终止事件缺失或响应无法解析时应告警。

## 8. 性能压力与容量规则

| 压力来源 | 对接要求 |
| --- | --- |
| Chat SSE 长连接 | Chat 使用独立连接池；连接池容量至少覆盖允许的最大并发，并预留突发余量。 |
| 元数据请求 | 知识库、会话和历史查询使用独立小连接池，避免被 Chat 长连接占满。 |
| 图片和文档下载 | 使用独立并发及带宽限制，全程流式转发，禁止先完整读入内存。 |
| 重试流量 | 仅对短暂故障有限重试；问答结果未知时复用原 Key，避免重复生成放大压力。 |

活跃 Chat 流可按“峰值发起速率 × 平均完整回答时间”估算。例如峰值 2 次/秒、平均 20 秒，平均约有 40 条活跃流；还需为突发流量预留连接、文件描述符和内存余量。该估算不是容量承诺。


## 9. 错误处理规则

| 场景 | 典型状态/错误码 | 调用方处理 |
| --- | --- | --- |
| Token 缺失或无效 | HTTP 401 `AUTHENTICATION_REQUIRED` | 不重试；检查业务后台密钥配置。 |
| 知识库不存在、未上线、已下线、停用或未就绪 | HTTP 404 `KNOWLEDGE_BASE_NOT_FOUND`、HTTP 403 `FORBIDDEN`、HTTP 409 `KNOWLEDGE_BASE_NOT_READY`/`INDEX_NOT_READY` | 刷新知识库列表；未就绪时稍后重试。 |
| 参数或游标错误 | HTTP 422 | 修正请求；游标错误时从第一页重新加载。 |
| 会话不存在 | HTTP 404 `CONVERSATION_NOT_FOUND` | 清理本地会话映射，回到新会话流程。 |
| 会话正在处理 | HTTP 409 `CONVERSATION_BUSY` | 禁止并发发送；等待当前请求结束后再处理。 |
| 幂等请求仍在处理 | HTTP 409 `REQUEST_IN_PROGRESS` | 使用原 Key 延迟重试，不生成新 Key。 |
| 幂等 Key 与请求体冲突 | HTTP 409 `IDEMPOTENCY_KEY_REUSED` | 终止请求并告警，检查调用方幂等实现。 |
| SSE `TURN_DEADLINE_EXCEEDED` | `error` 终止事件 | 标记本次失败；由用户重新发送时生成新 Key。 |
| 资源暂时不可用 | HTTP 503 `ASSET_UNAVAILABLE` | 文本回答继续可用，资源入口提供稍后重试。 |

自动重试仅适用于网络连接失败、HTTP 502/503/504 等短暂故障，建议最多 2 次并使用指数退避；流式问答在结果未知时必须复用原 `Idempotency-Key` 和相同请求体。

## 10. 对接验收清单

- `/health/ready` 返回 HTTP 200。
- 能使用服务端安全保存的 Token 和稳定用户 ID 查询知识库。
- 业务页面不会展示知识库列表中不存在或 `ready=false` 的知识库；列表变化后能够刷新。
- 首次问答传 `conversation_id=null`，能保存返回的会话 ID 并完成追问。
- 能正确处理 HTTP JSON 错误、SSE `complete`、SSE `error`、心跳、断流和幂等重放。
- 同一会话不会并发发送，重复点击不会产生重复问答。
- 会话列表、详情和历史消息均按当前用户隔离。
- 图片和文档经业务后台代理可下载，响应类型和长度正确，前端不可见 Assistant Token。
- 日志中无 Token、完整问答正文和资源内容。
