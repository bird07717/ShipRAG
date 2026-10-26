# 数据库认知

## 类型与迁移

数据库是 PostgreSQL，开发和生产编排使用 ParadeDB PostgreSQL 17 镜像，并要求 `vector` 与 `pg_search` 扩展。Alembic 配置在 `backend/alembic.ini`，迁移位于 `backend/alembic/versions/`；2026-10-09 当前仓库 head 为 `0048`（索引实际构建模型身份），本地已从 `0047` 升级；生产未操作。实际环境是否已升级到 head，必须通过 `alembic current` 或数据库检查确认；仓库迁移图不等于当前生产数据库版本。

数据库访问使用 SQLAlchemy async/asyncpg，但业务 SQL 大多由 repository 层直接管理；迁移而不是本文是表结构和约束的权威来源。

## 核心数据模型

`0048` 新增 `knowledge_index.build_models jsonb NOT NULL DEFAULT '{}'` 并限制为 JSON 对象。新构建写入版本号及实际 EMBEDDING/OCR/VISION 非密钥身份，READY/ACTIVE 后不随模型配置保存变动；文档 `metadata.model_fingerprint` 约束缓存复用。迁移保留旧索引空对象，不用今天的配置伪造历史；不更新向量、文档或卡片。关联 `embedding_model_id` 仍供读取可轮换凭据，新索引的身份校验以 `build_models` 为准，不依赖该可修改行；旧索引（空对象）则依赖该行的当前模型名及 `model_config_history` 中无晚于索引创建的 provider/model_name/base_url 变更（该历史只存字段名、不存新旧值，`0048` 之前的旧行无法追溯判定，宁可失败关闭）。降级只删除新增列，已在独立测试 Schema 验证升级/降级，不在开发业务表执行降级。

| 领域 | 主要表 | 关系与作用 |
| --- | --- | --- |
| 模型与运行配置 | `model_config`、`model_config_history`、`rag_config`、`engine_retrieval_config`、`engine_runtime_policy`、`prompt_template` | 模型档案/加密密钥审计、全局及按引擎检索配置、知识库运行策略和 Prompt 版本。 |
| 知识与索引 | `knowledge_base`、`knowledge_index`、`document_source`、`index_document` | 知识库拥有版本化索引与原始文档；索引记录构建状态，知识库指向活动索引。 |
| 内容与检索 | `document_element`、`image_asset`、`document_chunk`、`chunk_element`、`document_parent_chunk`、`parent_chunk_element`、`rag_document_card` | 文档解析为元素与图像，再关联到子/父分块；子分块和 RAG 文档卡片均有 1024 维向量及 `pg_search` 检索索引。 |
| 异步与存储清理 | `task_record`、`idempotency_record`、`storage_cleanup_object` | 任务进度、管理操作幂等、异步对象删除补偿。 |
| 会话与观测 | `conversation`、`message`、`rag_trace`、`request_error_log` | 会话、消息、问答 Trace、请求错误；Trace 还记录运行快照、来源和可选父 Trace。 |
| Support/发布 | `document_problem_profile`、`support_idempotency`、`knowledge_release`、`problem_profile_draft`、`support_release_profile`、`document_profile`、`document_profile_match_term`、`profile_publish_event` | Support 问题画像、幂等和 Profile 草稿/审核/发布快照。 |
| 对外 Assistant | `assistant_request` | 以外部用户、知识库和幂等键隔离请求，关联会话、消息与终态响应。 |

## 重要关系与约束

- `knowledge_base` → `knowledge_index`、`document_source`；索引与文档/内容表通过外键关联。`knowledge_index` 在单一知识库内版本唯一，构建中和活动索引各有局部唯一约束。
- `document_source.display_name` 是可修改的展示元数据；`filename`、文档 UUID 和 MinIO 对象键是稳定源文件标识。改名不修改分块、向量和历史回答快照，应用服务对同一知识库的有效文档名称执行不区分大小写的重复检查。
- `knowledge_base.assistant_online` 是独立的 Assistant 对外发布边界：迁移时原有 `ENABLED` 知识库回填为上线，后续新建行默认下线。该字段不替代知识库状态或 RAG/Support 活动版本。
- `knowledge_base.assistant_suggestions` 为最多三个字符串的 JSONB 配置；`assistant_config_revision` 为非负整数，保存问题及上下线均递增，用于拒绝旧页面覆盖。迁移 `0043` 仅为迁移前已上线知识库回填原全局问题，原未上线库和新建库为空，不改变上线状态。长度、文本及重复校验由应用层执行，数据库约束 JSONB 数组及最多三项。
- `knowledge_base.description` 继续作为 Assistant 可见的公开知识库说明和 RAG 专业范围，不新增列。管理更新以 `updated_at` 做乐观并发条件，在同一事务内返回更新后的完整知识库快照；空白由应用层归一化为 `null`。修改说明不创建 `task_record`，也不改变索引、Chunk 或向量。
- `document_source` 的活跃文件哈希在同一知识库内唯一；其原始对象键、图像对象键也唯一。内容索引可随 `knowledge_index` 版本重建。
- `document_chunk.embedding` 固定为 `vector(1024)`，使用 HNSW cosine 索引；BM25 通过 `pg_search` 索引支持混合检索。
- `rag_document_card` 以 `(index_id, document_id)` 唯一并同时保存 `knowledge_id`，卡片 JSON、稳定路由文本、`vector(1024)`、内容指纹、生成/审核状态、模型/Prompt 快照和乐观并发 `revision`。知识库或索引删除会级联卡片；文档为软删除，应用事务先显式删除关联卡片。显示名称不复制为卡片事实，改名查询时立即可见且不重做卡片向量。
- `conversation` 关联知识库，并可冻结 RAG/Support 索引或发布版本；`message` 有会话内顺序约束，`rag_trace` 关联会话/消息并支持回退父子链路。
- `message.rag_user_message_id` 将新 RAG 助手消息显式关联到本轮用户消息，删除用户消息时置空；旧 Assistant 轮次可在读取时复用 `assistant_request.user_message_id`。V2 只接受这两种显式关联且用户、助手及外部请求状态均完成的轮次，不猜测相邻消息。`rag_history_context` 与消息同生命周期：保留 legacy 文档切换确认信息，并持久化不应进入技术历史的输入门禁分类；历史选择优先使用该字段，Trace 只作为旧数据回退。`ix_message_rag_user` 和 `ix_assistant_request_message` 支持两种关联读取。
- `assistant_request` 的 `(external_user_id, knowledge_id, idempotency_key)` 唯一，同一非空 Conversation 只允许一条 `RUNNING` 记录；`replay_count >= 0`，终态重放会更新计数、`last_replayed_at` 和 `updated_at`。`support_idempotency` 仅记录 Support 内部重放，不作为 Assistant 门面指标。不要绕过这些约束手工重复执行对外写操作。
- `assistant_request.conversation_id` 使用 `ON DELETE CASCADE`。未关联 Conversation 且为 `COMPLETED`、`FAILED` 或 `CANCELLED` 的记录由独立的部分索引支持按 `updated_at` 清理；保留期任务明确不删除 `RUNNING`。
- 会话按 `last_activity_at` 计算空闲保留期，默认 30 天（2026-10-26 本地调整，待部署；`CONVERSATION_RETENTION_DAYS=0` 禁用过期删除）。过期会话由 GC 物理删除，`message` 和关联 `assistant_request` 级联删除，Trace 的会话外键置空；Trace 细节/摘要仍按独立 7/30 天策略清理。没有新增迁移，不恢复已删除聊天；延长保留期也会延长会话冻结索引的 GC 保护时间。
- 新 Assistant 失败/取消的公开错误同步保存到 `message.response_payload.error`，并写入显式空 `content`，避免读取旧内部成功块；该写入与请求终态收口同事务。失败关联 Trace 同步状态和错误，已终态请求不二次覆盖。历史消息优先读取错误快照，旧记录兼容 Trace，不新增列或迁移。
- 历史 RAG 分层技术助手曾使用 `conversation.chat_context.rag_issue` 保存问题和条件；该运行时已随 V5 删除，当前 V2 不读取或更新这一命名空间。旧 JSON 与共享字段保留用于兼容，不能将旧状态描述为当前多轮任务记忆。
- ADR-0019 的 V0～V2 已实施：V2 不恢复 `rag_issue`、不处理待选文档且回答完成不写 RAG 焦点；`begin_turn` 的 V2 分支只读取会话归属和冻结索引，不读取共享焦点或 `chat_context`，新消息兼容列 `focus_epoch` 固定写 `0`。`conversation.focus_document_id/focus_index_id/focus_epoch/chat_context/support_context_version` 与 `message.focus_epoch` 同时被 Support、Gateway 和清理逻辑使用，不能批量清空、修改既有迁移或直接删列。共享字段只有在 Support 独立迁移、数据核对、应用回滚验证和至少两个发布周期零旧列读取后才可评估删除；没有收益时允许保留兼容列。

## 数据流

上传时，原文件先进入 MinIO，`document_source` 保存元数据；异步构建在版本化索引中产生元素、图像、分块和向量。读取时，RAG/Support 根据会话冻结或当前发布的索引检索，再将消息、Trace 和可选幂等终态写回 PostgreSQL。过期的会话/Trace/错误日志、Assistant 终态孤儿请求和无引用索引由维护任务清理；对象删除通过清理记录实现可恢复的后台处理。

## 操作注意

- RAG V2 管理诊断没有新增迁移：`rag_trace.runtime_snapshot.generation` 保存当前生成诊断；`selected_context` 在 V2 表示隐藏全文候选的文档标识与哈希，不是实际采用文档。实际采用取 `generation.answer.used_document_ids`，历史记录不回填。旧 Trace 的 Chunk 上下文与焦点文档二次检索只属于 legacy 历史。
- RAG 显式消息配对需要迁移 `0045`，V2 继续使用其消息关联字段。2026-09-12 的 `0045 (head)` 是历史验证，不是当前 head；生产当前版本须另行确认。该迁移不要求重建知识索引，也不改写历史回答或 Trace。
- 本次知识库范围编辑、范围快照和 `rag_issue` 复用既有 `description`、`updated_at`、JSONB 与上下文版本字段，没有新增 Alembic 迁移；部署仍必须先执行当前仓库的 `upgrade head`，不能据此跳过既有 `0045`。
- RAG 文档卡片由迁移 `0046` 引入；曾验证 `0046 -> 0045 -> 0046`，该记录不代表当前 head。迁移只创建结构和索引，不调用模型、不回填业务卡片。部署执行当前 `upgrade head`，再生成/审核活动索引卡片；2026-09-29 的生产 `0047` 快照见 `current-state.md`，本次未重新检查生产。
- 旧 P2 Shadow 编排已删除；当前 V2 卡片向量/BM25 读取仍限定 `knowledge_id`、会话冻结 `index_id`、`index_document=READY`、`document_source=STORED`、当前卡片 Schema、`generation_status=READY` 且审核状态非 `REJECTED`；显示名称动态联接源文档。该读取不写 Support 表。
- RAG V2 的 V1～V5 计划不新增数据库结构，继续复用 `0046` 卡片、元素、图片、消息、Trace 和 Assistant 请求。若未来执行共享焦点字段清理，必须作为独立 V6 迁移任务，不得与 V2 首次开发切换同批执行。详见 [RAG V2 执行计划](../technical/rag-full-document-generation-v2-execution-plan.md)。
- V2 实施时发现 unfocused 向量/BM25/Chunk ID 查询可能读取软删除文档；Repository 已统一增加 `document_source.status='STORED'` 条件。该修复不改表结构、不需迁移或重建索引，legacy 与 V2 同时受益。

- 新字段、表、索引、枚举约束必须使用 Alembic 迁移，不直接修改线上 schema。
- 更改嵌入维度、索引结构或保留策略会影响已有数据和任务，需要同时评估迁移、重建和回滚。
- 数据库凭据、模型 API 密钥、对象存储密钥只出现在安全配置中；不得写入认知文档。


## 回答标记与复测（0044）

- `answer_review` 按 `request_id` 唯一保存人工标记；关联 `knowledge_id`，持久化原始响应/历史/诊断 JSONB、分类、状态、预期文档 UUID 与保存时名称、备注和修订号。请求/会话/Trace/文档标识有意不设删除级联外键，避免日志保留或文档删除清掉用例；写入服务强制校验归属，知识库删除仍级联清理。
- `answer_retest` 关联问题标记，主键为客户端生成的幂等运行 UUID；保存标记修订、期望快照、执行状态、逐轮结果与起止时间。同一标记 `RUNNING` 部分唯一索引防止并发复测。
- `assistant_request.input_payload` 保存新请求的业务输入 JSONB，默认空对象兼容旧记录；`(knowledge_id, created_at DESC, id DESC)` 索引服务管理分页查询。
- 人工问题与复测记录不走短期 Trace 保留清理；只保留图片资产 ID，不复制 MinIO 对象或阻止原索引 GC。导出与保留范围以管理用途为限。
