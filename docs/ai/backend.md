# 后端认知

## 技术栈和启动入口

后端位于 `backend/`，使用 Python（项目声明 `>=3.11,<3.15`）、FastAPI、Pydantic Settings、SQLAlchemy async + asyncpg、Alembic、Redis/RQ、MinIO、structlog。`app.main:create_app` 创建应用；`app.worker:main` 是 `rag-worker` 命令入口。

`AppResources` 在 FastAPI lifespan 内创建异步数据库引擎、Redis 客户端和 MinIO 客户端，关闭时释放 Redis 与数据库连接。应用在 `/health/live` 和 `/health/ready` 暴露健康端点。

## API 组织与认证

应用统一前缀默认为 `/api/v1`。`app/api/router.py` 分离两条认证边界：

| 边界 | 认证依赖 | 主要范围 |
| --- | --- | --- |
| 管理 API | `require_service_token` | Chat、会话、文档摄取、索引、模型、RAG 配置、Profile 发布、日志、指标、Support。 |
| Assistant API | `require_assistant_service_token`，且要求 `X-External-User-ID` | `/assistant` 下知识库、欢迎语、会话/消息、流式问答与受限资产读取。 |

路由模块位于 `app/api/`；请求/响应模型集中在 `schemas.py`。应用统一输出携带 `request_id` 的错误体，且会收敛用户聊天路径上的服务端错误信息。流式聊天使用 SSE，而非 WebSocket。

## 模块关系

| 模块 | 职责 |
| --- | --- |
| `core/` | Settings、日志、连接资源、模型密钥加密。 |
| `common/` | 错误模型、中间件、文本与资产工具。 |
| `gateway/` | 读取运行策略及会话模式，调度 RAG/Support 和记录回退。 |
| `rag/` 与 `services/rag.py` | 查询重写、嵌入、BM25/向量检索、重排、文档路由、Prompt、LLM、Trace。 |
| `support/` | 技术支持 Profile 解析、聚焦工作流、文档交付、风险校验、幂等。 |
| `ingestion/` 与 `services/ingestion.py` | DOCX 安全校验、解析、图像理解、分块、嵌入、索引构建。 |
| `document_profiles/`、`releases/` | Profile 生成、审核、发布与回滚。 |
| `assistant/` | 对外 Assistant API 的门面、公开响应过滤、游标及遗留运行请求恢复。 |
| `tasks/` | RQ 任务、任务记录、GC、保留期清理、恢复和密钥轮换。 |
| `observability/` | Trace 与未处理请求错误记录。 |

## 后台任务和外部依赖

RQ 队列逻辑名为 `ingestion`、`index_build`、`maintenance`。开发/生产均将索引 Worker 和维护 Worker 分开：索引 Worker 负责索引构建、启动时的索引恢复/GC；维护 Worker 负责 Profile/维护任务、过期错误日志和周期 GC 调度。不要因方便把它们合并，长索引任务会阻塞维护工作。

外部服务包括 PostgreSQL、Redis、MinIO，以及可配置的模型服务。代码支持 SiliconFlow 嵌入/重排/OCR、智谱 LLM/视觉等提供者；`auto` 模式会按密钥可用性选择，开发测试可使用受控 fake provider。实际模型、超时和阈值来自 Settings/环境变量，不能仅根据本文推断部署值。

## 关键实现约束

- 嵌入维度在 Settings 与数据库契约中固定为 1024，变更需要迁移与重建计划。
- 运行策略有 `RAG_ONLY` 和 `SUPPORT_WITH_RAG_FALLBACK`；会话模式可覆盖引擎选择。
- 对外 Assistant 请求使用幂等记录与外部用户隔离；其响应通过 `public_payload` 过滤内部字段。
- 对外 Assistant 就绪态欢迎语使用简洁的用户侧文案，不暴露知识库名称、描述或文档数量；三条快捷问题固定取自已通过的 Assistant Golden Set `standard + EXACT` 用例。管理端 Chat 欢迎语仍使用知识库目录生成，不受此规则影响。
- Assistant Chat 会在创建 `StreamingResponse` 前完成会话权限、知识库及 Active Index 可用性、策略、幂等和同会话并发检查；这些前置失败保持 HTTP JSON 错误，`accepted` 后的生成期失败才使用 SSE `error`。
- Assistant Chat 的已预留请求由专用流响应持有；ASGI 发送失败或客户端断连时会在响应收口阶段幂等地将仍为 `RUNNING` 的请求及其关联消息/Trace 标记为取消，正常终态与幂等重放不会被改写。进程强制终止等无法执行收口的情况仍由遗留请求恢复命令处理。
- Assistant SSE `context` 只允许输出会话、用户消息、Assistant 消息和 Trace 标识，不透传 Index 等内部 Trace 字段；已知短暂上游/超时错误保留可重试语义。
- Assistant 文档/图片接口只允许读取状态为 `STORED` 的所属文档资源，按 64 KiB 分块代理 MinIO，并转发可信的内容类型、可用的对象长度和文档文件名；调用方无需为取得长度缓冲完整对象。
- 超过指定时间仍为 `RUNNING` 的 Assistant 请求可通过 `app.assistant.recovery` 预览并事务恢复；恢复会保留请求行，并同步将关联的流式消息和 Trace 标为失败。
- API 密钥可存储为加密密文，密钥轮换由 `app.tasks.key_rotation` 支持；文档只可记录变量名，不能记录值。

## 测试

`backend/tests/` 覆盖 API、网关、RAG、Support、摄取、任务和 PostgreSQL 集成。常规全套校验由根目录 `scripts/check.sh` 编排；以当前配置和实际结果为准，详见 [`environment.md`](environment.md) 与 [`known-issues.md`](known-issues.md)。
