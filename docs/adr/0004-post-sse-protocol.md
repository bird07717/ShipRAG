# ADR-0004：Chat 使用 POST + SSE 流式响应

- 状态：接受
- 日期：2026-08-17

## 背景

Chat 请求包含 `knowledge_id`、可选 `conversation_id` 和问题正文，使用 JSON POST 更符合请求语义，也避免把企业问题放入 URL、代理日志或长度受限的查询参数。

浏览器原生 `EventSource` 只适合 GET，不能按本项目协议发送 JSON POST 和自定义认证请求。

## 决策

- 保留 `POST /api/v1/chat/stream`。
- 响应媒体类型为 `text/event-stream`。
- Vue 客户端使用 `fetch` + `ReadableStream` + SSE 帧解析器。
- 事件固定为 `trace`、`source`、`message`、`heartbeat`、`done`、`error`。
- `done` 与 `error` 为终止事件。
- 响应开始前的错误使用标准 HTTP 状态和 JSON 错误；开始后的错误使用 SSE `error`。
- 代理必须关闭响应缓冲和内容转换。
- 客户端断开时取消上游 LLM 流。

完整事件结构见 `docs/api-contract.md`。

## 客户端解析要求

解析器必须正确处理：

- 一个网络 Chunk 包含多个 SSE 事件；
- 一个 SSE 事件跨多个网络 Chunk；
- CRLF 与 LF；
- 多行 `data:`；
- UTF-8 中文字符跨字节边界；
- Heartbeat 与未知事件；
- 终止事件后的连接关闭。

不得按单次 `reader.read()` 结果直接当作一个 JSON 事件解析。

## 后果

### 正面

- 请求体结构清晰，适合较长问题和未来可选参数。
- 问题不出现在 URL。
- 服务端仍可使用标准 SSE 帧和代理配置。

### 代价

- 前端需要可靠的 SSE Parser，不能直接用 `EventSource`。
- OpenAPI 无法完整描述流内事件，需要共享 TypeScript 类型和契约测试。
- 反向代理、压缩和超时需要专门配置。

## 不采用的方案

- GET EventSource：问题和上下文进入 URL，认证与长度处理不理想。
- WebSocket：V1 只有服务端单向 Token 流，增加连接状态管理没有必要。
- 两步 POST 创建 + GET 订阅：适合需要断线恢复的长任务，但 V1 Chat 不要求恢复同一生成流。
