# 数据库认知

## 类型与迁移

数据库是 PostgreSQL，开发和生产编排使用 ParadeDB PostgreSQL 17 镜像，并要求 `vector` 与 `pg_search` 扩展。Alembic 配置在 `backend/alembic.ini`，迁移位于 `backend/alembic/versions/`；当前仓库可见的最新修订是 `0042_assistant_request_observability_retention`。实际环境是否已升级到 head，必须通过 `alembic current` 或数据库检查确认。

数据库访问使用 SQLAlchemy async/asyncpg，但业务 SQL 大多由 repository 层直接管理；迁移而不是本文是表结构和约束的权威来源。

## 核心数据模型

| 领域 | 主要表 | 关系与作用 |
| --- | --- | --- |
| 模型与运行配置 | `model_config`、`model_config_history`、`rag_config`、`engine_retrieval_config`、`engine_runtime_policy`、`prompt_template` | 模型档案/加密密钥审计、全局及按引擎检索配置、知识库运行策略和 Prompt 版本。 |
| 知识与索引 | `knowledge_base`、`knowledge_index`、`document_source`、`index_document` | 知识库拥有版本化索引与原始文档；索引记录构建状态，知识库指向活动索引。 |
| 内容与检索 | `document_element`、`image_asset`、`document_chunk`、`chunk_element`、`document_parent_chunk`、`parent_chunk_element` | 文档解析为元素与图像，再关联到子/父分块；子分块带 1024 维向量和 `pg_search` 检索索引。 |
| 异步与存储清理 | `task_record`、`idempotency_record`、`storage_cleanup_object` | 任务进度、管理操作幂等、异步对象删除补偿。 |
| 会话与观测 | `conversation`、`message`、`rag_trace`、`request_error_log` | 会话、消息、问答 Trace、请求错误；Trace 还记录运行快照、来源和可选父 Trace。 |
| Support/发布 | `document_problem_profile`、`support_idempotency`、`knowledge_release`、`problem_profile_draft`、`support_release_profile`、`document_profile`、`document_profile_match_term`、`profile_publish_event` | Support 问题画像、幂等和 Profile 草稿/审核/发布快照。 |
| 对外 Assistant | `assistant_request` | 以外部用户、知识库和幂等键隔离请求，关联会话、消息与终态响应。 |

## 重要关系与约束

- `knowledge_base` → `knowledge_index`、`document_source`；索引与文档/内容表通过外键关联。`knowledge_index` 在单一知识库内版本唯一，构建中和活动索引各有局部唯一约束。
- `document_source.display_name` 是可修改的展示元数据；`filename`、文档 UUID 和 MinIO 对象键是稳定源文件标识。改名不修改分块、向量和历史回答快照，应用服务对同一知识库的有效文档名称执行不区分大小写的重复检查。
- `knowledge_base.assistant_online` 是独立的 Assistant 对外发布边界：迁移时原有 `ENABLED` 知识库回填为上线，后续新建行默认下线。该字段不替代知识库状态或 RAG/Support 活动版本。
- `document_source` 的活跃文件哈希在同一知识库内唯一；其原始对象键、图像对象键也唯一。内容索引可随 `knowledge_index` 版本重建。
- `document_chunk.embedding` 固定为 `vector(1024)`，使用 HNSW cosine 索引；BM25 通过 `pg_search` 索引支持混合检索。
- `conversation` 关联知识库，并可冻结 RAG/Support 索引或发布版本；`message` 有会话内顺序约束，`rag_trace` 关联会话/消息并支持回退父子链路。
- `assistant_request` 的 `(external_user_id, knowledge_id, idempotency_key)` 唯一，同一非空 Conversation 只允许一条 `RUNNING` 记录；`replay_count >= 0`，终态重放会更新计数、`last_replayed_at` 和 `updated_at`。`support_idempotency` 仅记录 Support 内部重放，不作为 Assistant 门面指标。不要绕过这些约束手工重复执行对外写操作。
- `assistant_request.conversation_id` 使用 `ON DELETE CASCADE`。未关联 Conversation 且为 `COMPLETED`、`FAILED` 或 `CANCELLED` 的记录由独立的部分索引支持按 `updated_at` 清理；保留期任务明确不删除 `RUNNING`。

## 数据流

上传时，原文件先进入 MinIO，`document_source` 保存元数据；异步构建在版本化索引中产生元素、图像、分块和向量。读取时，RAG/Support 根据会话冻结或当前发布的索引检索，再将消息、Trace 和可选幂等终态写回 PostgreSQL。过期的会话/Trace/错误日志、Assistant 终态孤儿请求和无引用索引由维护任务清理；对象删除通过清理记录实现可恢复的后台处理。

## 操作注意

- 新字段、表、索引、枚举约束必须使用 Alembic 迁移，不直接修改线上 schema。
- 更改嵌入维度、索引结构或保留策略会影响已有数据和任务，需要同时评估迁移、重建和回滚。
- 数据库凭据、模型 API 密钥、对象存储密钥只出现在安全配置中；不得写入认知文档。
