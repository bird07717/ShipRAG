# 数据库认知

## 类型与迁移

数据库是 PostgreSQL，开发和生产编排使用 ParadeDB PostgreSQL 17 镜像，并要求 `vector` 与 `pg_search` 扩展。Alembic 配置在 `backend/alembic.ini`，迁移位于 `backend/alembic/versions/`；当前仓库可见的最新修订是 `0045_rag_history_pairs`。实际环境是否已升级到 head，必须通过 `alembic current` 或数据库检查确认。

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
- `knowledge_base.assistant_suggestions` 为最多三个字符串的 JSONB 配置；`assistant_config_revision` 为非负整数，保存问题及上下线均递增，用于拒绝旧页面覆盖。迁移 `0043` 仅为迁移前已上线知识库回填原全局问题，原未上线库和新建库为空，不改变上线状态。长度、文本及重复校验由应用层执行，数据库约束 JSONB 数组及最多三项。
- `document_source` 的活跃文件哈希在同一知识库内唯一；其原始对象键、图像对象键也唯一。内容索引可随 `knowledge_index` 版本重建。
- `document_chunk.embedding` 固定为 `vector(1024)`，使用 HNSW cosine 索引；BM25 通过 `pg_search` 索引支持混合检索。
- `conversation` 关联知识库，并可冻结 RAG/Support 索引或发布版本；`message` 有会话内顺序约束，`rag_trace` 关联会话/消息并支持回退父子链路。
- `message.rag_user_message_id` 将新 RAG 助手消息显式关联到本轮用户消息，删除用户消息时置空；`rag_history_context` 仅保存历史选择所需的结构化元数据，目前用于文档切换确认的原问题桥接。迁移 `0045` 不猜测或回填旧内部消息；旧 Assistant 轮次可在读取时复用 `assistant_request.user_message_id`。`ix_message_rag_user` 和 `ix_assistant_request_message` 支持两种关联读取。
- `assistant_request` 的 `(external_user_id, knowledge_id, idempotency_key)` 唯一，同一非空 Conversation 只允许一条 `RUNNING` 记录；`replay_count >= 0`，终态重放会更新计数、`last_replayed_at` 和 `updated_at`。`support_idempotency` 仅记录 Support 内部重放，不作为 Assistant 门面指标。不要绕过这些约束手工重复执行对外写操作。
- `assistant_request.conversation_id` 使用 `ON DELETE CASCADE`。未关联 Conversation 且为 `COMPLETED`、`FAILED` 或 `CANCELLED` 的记录由独立的部分索引支持按 `updated_at` 清理；保留期任务明确不删除 `RUNNING`。
- 新 Assistant 失败/取消的公开错误同步保存到 `message.response_payload.error`，并写入显式空 `content`，避免读取旧内部成功块；该写入与请求终态收口同事务。失败关联 Trace 同步状态和错误，已终态请求不二次覆盖。历史消息优先读取错误快照，旧记录兼容 Trace，不新增列或迁移。

## 数据流

上传时，原文件先进入 MinIO，`document_source` 保存元数据；异步构建在版本化索引中产生元素、图像、分块和向量。读取时，RAG/Support 根据会话冻结或当前发布的索引检索，再将消息、Trace 和可选幂等终态写回 PostgreSQL。过期的会话/Trace/错误日志、Assistant 终态孤儿请求和无引用索引由维护任务清理；对象删除通过清理记录实现可恢复的后台处理。

## 操作注意

- RAG 质量优化没有新增迁移：`rag_trace.runtime_snapshot` 的 JSON 新增 `generation`，保存实际生成计划/提示词哈希/来源；已有 `retrieval` 改为记录本轮有效覆盖值。`selected_context` 继续表示准备阶段检索上下文，历史记录不回填。文档内二次检索同时在 SQL 限定知识库、索引、文档与有效源文件状态，再应用 Top K。
- RAG 聚焦历史需要迁移 `0045`。本地开发库已在 2026-09-12 验证升级到 `0045 (head)`；生产状态仍须在目标服务器单独执行 `alembic current` 确认。该迁移不要求重建知识索引，也不改写历史回答或 Trace。

- 新字段、表、索引、枚举约束必须使用 Alembic 迁移，不直接修改线上 schema。
- 更改嵌入维度、索引结构或保留策略会影响已有数据和任务，需要同时评估迁移、重建和回滚。
- 数据库凭据、模型 API 密钥、对象存储密钥只出现在安全配置中；不得写入认知文档。


## 回答标记与复测（0044）

- `answer_review` 按 `request_id` 唯一保存人工标记；关联 `knowledge_id`，持久化原始响应/历史/诊断 JSONB、分类、状态、预期文档 UUID 与保存时名称、备注和修订号。请求/会话/Trace/文档标识有意不设删除级联外键，避免日志保留或文档删除清掉用例；写入服务强制校验归属，知识库删除仍级联清理。
- `answer_retest` 关联问题标记，主键为客户端生成的幂等运行 UUID；保存标记修订、期望快照、执行状态、逐轮结果与起止时间。同一标记 `RUNNING` 部分唯一索引防止并发复测。
- `assistant_request.input_payload` 保存新请求的业务输入 JSONB，默认空对象兼容旧记录；`(knowledge_id, created_at DESC, id DESC)` 索引服务管理分页查询。
- 人工问题与复测记录不走短期 Trace 保留清理；只保留图片资产 ID，不复制 MinIO 对象或阻止原索引 GC。导出与保留范围以管理用途为限。
