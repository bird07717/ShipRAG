# 系统架构

## 概览

系统由浏览器管理台、FastAPI 应用、异步任务 Worker 和三项基础设施组成。数据面使用 PostgreSQL（含 `vector` 与 `pg_search` 扩展），Redis 承载 RQ 队列，MinIO 保存原始文档和图片资产。生产环境由 Nginx 同域代理 API 和健康检查。

```text
Vue 管理台 / 业务后端
        │ HTTP、SSE
        ▼
 FastAPI (`backend/app/main.py`)
        │
        ├─ 管理 API（Service Token）
        ├─ Assistant API（独立 Assistant Service Token + 外部用户 ID）
        ├─ Chat Gateway ──┬► RAG 引擎
        │                 └► Support 引擎 ──(策略允许时)► RAG 回退
        └─ 摄取/管理 API ──► Redis RQ ──► Index / Maintenance Workers
                 │                   │
                 ▼                   ▼
        PostgreSQL ◄──────────── MinIO
```

## 核心组件与职责

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| 管理台 | `frontend/src/` | 知识库、文档、索引、配置、链路与问答测试的单页管理界面。 |
| HTTP 应用 | `backend/app/main.py`、`api/` | 生命周期管理、CORS、统一错误体、健康检查、管理与 Assistant 路由。 |
| Gateway | `backend/app/gateway/` | 按知识库运行策略选择 RAG 或 Support；管理 Support→RAG 回退、Trace 关联和 SSE 状态事件。 |
| RAG | `backend/app/rag/`、`services/rag.py` | 查询改写、混合检索、重排、文档路由、Prompt/LLM 生成、会话和 Trace 持久化。 |
| Support | `backend/app/support/`、`document_profiles/` | Profile 驱动的技术支持、聚焦文档工作流、风险/证据校验、文档交付与幂等。 |
| 文档摄取 | `backend/app/ingestion/` | DOCX 校验/解析、图片理解、分块、嵌入及索引版本构建。 |
| 发布 | `backend/app/releases/` | Support Profile 草稿审核、发布、回滚与与 RAG 索引分离的生命周期。 |
| Worker | `backend/app/worker.py`、`tasks/` | RQ 消费、索引构建、维护、回收、孤儿任务恢复和保留期清理。 |
| 可观测性 | `observability/`、`rag/metrics.py` | 请求错误、问答 Trace、性能与质量指标。 |

## 关键调用链

### 文档入库与索引

1. 管理 API 接收知识库、文档和构建请求；原始对象存入 MinIO，元数据写入 PostgreSQL。
2. 请求创建 `task_record` 并投递 Redis 队列。
3. `index_build` Worker 使用摄取管线校验/解析 DOCX、处理图像、分块并生成 1024 维嵌入。
4. 结果写入按版本隔离的索引表；成功构建可激活 RAG 索引。Support Profile 的审核发布是独立生命周期。

### 问答

1. `/api/v1/chat*` 或 `/api/v1/assistant/chat/stream` 接收请求；后者使用独立服务凭据并要求 `X-External-User-ID`。
2. 对外 Assistant 先检查知识库独立的 `assistant_online` 发布状态、知识库/会话归属及当前策略完整就绪态；未上线知识库不进入对外列表，也不能通过已知资源 ID 绕过。随后 Gateway 读取知识库的 `engine_runtime_policy` 和会话模式：`RAG_ONLY` 直入 RAG；`SUPPORT_WITH_RAG_FALLBACK` 先入 Support。
3. RAG 使用已冻结的索引、向量/BM25 混合检索与重排，按路由结果生成回答或澄清；Support 处理已聚焦文档、Profile/文档路由和工作流。
4. 策略允许且 Support 返回指定回退类型时，Gateway 复用消息上下文调用 RAG，并建立父子 Trace 关联。
5. 内部管理聊天流（经 Gateway）按 SSE 发送 `status`、`trace`、`message`、可选 `fallback` 和终态 `done`/`error`。对外 Assistant 流不是原样透传：它先发送 `accepted`，随后按需要发送 `progress`、`context`、`delta`，并以 `complete` 或 `error` 结束；上游长时间无帧时每 15 秒发送 SSE 注释心跳 `: ping`。两套接口的字段和事件名不能混用。

### 可观测性与保留

请求失败可写入 `request_error_log`，问答写入 `rag_trace`；定时维护清理过期会话、Trace 细节、错误日志，以及没有关联 Conversation 的 Assistant 终态请求。默认保留期由环境配置决定，当前代码默认会话 7 天、Assistant 终态孤儿请求 7 天、完整 Trace 7 天、Trace 摘要 30 天、错误日志 30 天；部署配置可覆盖，`RUNNING` Assistant 请求不由保留期任务删除。

## 架构约束

- 管理 API 与对外 Assistant API 不能共用凭据边界；后者只暴露窄化契约。
- 知识库后台可维护状态、RAG/Support 发布状态和 Assistant 对外上线状态彼此独立；新知识库默认不对 Assistant 上线。管理台按库保存推荐问题并确认上下线，使用管理凭据和配置修订检查；Assistant 欢迎接口只消费已上线库的保存值，保持外部契约。
- 聊天会话会冻结使用的索引/发布版本，避免在线版本切换改变既有会话语义。
- RAG 索引自动激活与 Support Profile 人工审核发布彼此独立。
- 生产运行时容器采用只读根文件系统与 `/tmp` tmpfs；部署细节见 [`environment.md`](environment.md)。
