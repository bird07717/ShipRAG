# 后端认知

## 技术栈和启动入口

后端位于 `backend/`，使用 Python（项目声明 `>=3.11,<3.15`）、FastAPI、Pydantic Settings、SQLAlchemy async + asyncpg、Alembic、Redis/RQ、MinIO、structlog。`app.main:create_app` 创建应用；`app.worker:main` 是 `rag-worker` 命令入口。

`AppResources` 在 FastAPI lifespan 内创建异步数据库引擎、Redis 客户端和 MinIO 客户端，关闭时释放 Redis 与数据库连接。应用在 `/health/live` 和 `/health/ready` 暴露健康端点。

## API 组织与认证

应用统一前缀默认为 `/api/v1`。`app/api/router.py` 分离两条认证边界：

| 边界 | 认证依赖 | 主要范围 |
| --- | --- | --- |
| 管理 API | `require_service_token` | Chat、会话、文档摄取、索引、模型、RAG 配置、Profile 发布、日志、指标、Support。 |
| Assistant API | `require_assistant_service_token`，且要求 `X-External-User-ID` | `/assistant` 下知识库、欢迎语、会话/消息、流式问答与受限图片读取。 |

路由模块位于 `app/api/`；请求/响应模型集中在 `schemas.py`。应用统一输出携带 `request_id` 的错误体，且会收敛用户聊天路径上的服务端错误信息。流式聊天使用 SSE，而非 WebSocket。

管理指标 `/metrics/overview` 与 `/metrics/timeseries` 支持按 `engine`、`source`、知识库及时间范围过滤。RAG 回答结果口径为：直接回答率=`(ANSWERED + 遗留 DOC_DELIVERED) / 产品范围内已分类轮次`，待补充信息率=`UNCONFIRMED / 产品范围内已分类轮次`，范围外请求率=`OUT_OF_SCOPE / 全部已分类轮次`；`OUT_OF_SCOPE` 不再计作无答案，失败率独立计算。Overview 另按 `answer_mode` 聚合 `PRODUCT_KNOWLEDGE`、`PRODUCT_EXPLAINED`、`PRODUCT_GENERAL`，三者之和是回答依据分布的独立分母，不混入欢迎语、遗留文档交付或未分类历史。Dashboard 固定使用外部 `ASSISTANT_API`、`RAG` 和同一个近 7 天窗口，趋势、待补充列表和趋势柱形下钻使用相同过滤条件；失败待办将已显示 Overview 的准确 from/to 和来源/引擎传给 `LogsView` 的 Trace 列表请求（2026-10-05 本地修复，未改后端契约）。失败列表最多 200 条，上方失败概览仍是独立汇总，不随列表筛选变化。`UNCONFIRMED` 只说明需要更多条件或输入信息不足，不能单独证明知识库覆盖缺口。

## 模块关系

2026-10-03 管理台诊断与上线清单补充：管理 `GET /knowledge-bases/{id}/assistant-config` 增加 `checks`（code/title/status/messages/action）。Gateway 在既有 blocker 产生处同时记录分组，`assistant_runtime_blockers` 可选输出分组，不增加数据库读取或改变既有上线/持续可用判断；管理清单与实际门禁共用同一来源，不由前端解析中文错误字符串。公开 Assistant 配置/欢迎契约不增加此字段。

管理 `/system/info` 增加 `build={commit,built_at,dirty}`：`create_app` 读取镜像内 `core/build-info.json` 并冻结，缺失/非法全部返回 `null`，不从运行时环境或服务器当前 Git 目录推断版本。Worker 启动记录同源构建标识，可用 `python -m app.core.build_info` 只读核对。既有 `version=1.0.0` 为兼容字段，不能用作提交身份。构建与安装包均携带标记，参见 ADR-0031。

| 模块 | 职责 |
| --- | --- |
| `core/` | Settings、日志、连接资源、模型密钥加密。 |
| `common/` | 错误模型、中间件、文本与资产工具。 |
| `gateway/` | 读取运行策略及会话模式，调度 RAG/Support 和记录回退。 |
| `rag/` 与 `services/rag.py` | 唯一 RAG V2：双 LLM、文档级检索、隐藏全文上下文、答案/图片校验、Trace 和 RAG 文档卡片。 |
| `support/` | 已冻结的兼容链路：保留技术支持 Profile、聚焦工作流、文档交付、风险校验和幂等。 |
| `ingestion/` 与 `services/ingestion.py` | DOCX/PDF 安全校验、按格式解析、图像理解、分块、嵌入、索引构建。 |
| `document_profiles/`、`releases/` | Profile 生成、审核、发布与回滚。 |
| `assistant/` | 对外 Assistant API 的门面、公开响应过滤、游标及遗留运行请求恢复。 |
| `tasks/` | RQ 任务、任务记录、GC、保留期清理、恢复和密钥轮换。 |
| `observability/` | Trace 与未处理请求错误记录。 |

## 后台任务和外部依赖

RQ 队列逻辑名为 `ingestion`、`index_build`、`maintenance`。开发/生产均将索引 Worker 和维护 Worker 分开：索引 Worker 负责索引构建、启动时的索引恢复/GC；维护 Worker 负责 Profile/维护任务、过期错误日志和周期 GC 调度。`request_error_log` 按 `REQUEST_ERROR_LOG_RETENTION_DAYS`（默认 30 天，0 禁用）在维护 Worker 启动时清理一次；随后维护 Worker 复用 RQ 约每 10 分钟的维护检查，以 Redis 24 小时门禁向 `maintenance` 队列投递一次性清理任务。投递失败会尝试释放门禁供后续检查重试，任务失败也不会切断后续周期；任务并非固定钟点执行，仍受维护队列等待影响。旧版已注册的延迟清理任务只执行一次、不再续排。不要因方便把两个 Worker 合并，长索引任务会阻塞维护工作。

索引构建的文档级熔断（ADR-0022）：文档处理失败时按（同 KB + 同 `document_id` + 同 `error_code`）统计历史 FAILED 的 `index_document` 行数，达到 2 次后该文档在当轮构建中标为 `SKIPPED`（错误信息带文件名），其余文档继续、构建可成功；跳过清单写入任务 `metadata.skipped_documents`。`_finalize_index` 容忍 SKIPPED 但要求至少 1 份 READY，`knowledge_index.document_count` 只计 READY。`list_documents` 以 LATERAL join 附带每份文档最近一次构建的 `last_build_status/last_build_error/last_build_version` 供前端告警。注意 `_mark_failed` 仍会把未轮到的文档批量标为同一错误——判断"哪份文档失败"必须看任务 `metadata.current_document` 或 SKIPPED/首个 FAILED 行，不能读批量标记的 error_message。

构建重试与中止的人工决策（ADR-0024，2026-09-17 实施）：构建失败、worker 崩溃回收（`mark_abandoned_index_build_failed`，含取消后 worker 死亡的 CANCELLED 分支——按取消语义结算索引，任务终态不改写）和协作取消都不再自动排 followup——`create_followup_build_if_needed` 已删除，`rebuild_required` 在失败/取消时重新置位并由管理台"待重建"徽标提示，重试由管理员经 `POST /indexes/{id}/retry` 触发（白名单 FAILED/CANCELLED），熔断计数由手动重试推进。INDEX_BUILD 接入协作取消：`POST /tasks/{id}/cancel` 白名单含 INDEX_BUILD，取消是任务行终态转换，构建循环在文档边界轮询（`_update_task` 带 `status IN ('QUEUED','RUNNING')` 守卫防进度心跳覆盖终态），排队期取消在管线入口短路零模型消耗，激活事务开始后不中断；索引与未处理文档落 `CANCELLED` 终态（迁移 `0047` 同时补上 `index_document` 的 `SKIPPED` 约束值），CANCELLED 与 FAILED 同权可删可重试，且不进失败计数。单索引删除与 GC 在数据库行物理删除成功之后按 `indexes/{index_id}/` 前缀整清 MinIO 对象（指针恢复路径的对象完整性优先，孤儿留给未来桶级对账）；文档原件与 `document_source` 不受索引删除影响。任务记录保留：`task_record_retention_days=90`（0=永久）由周期 GC 修剪终态超期记录，每库保底最新 10 条。RAG 理解卡片跨版本复用（ADR-0025，2026-09-18 实施）：构建成功后把 REUSE 文档的卡片行（含向量与 review_status）从 `IndexBuildResult.prev_index_id` 复制到新索引，生成任务的指纹跳过逻辑只为新增/变更文档调 LLM；`content_fingerprint` 已去除 `index_id`（内容指纹不含索引身份，存量卡自愈重生成）；一键确认端点 `POST .../rag-document-cards/confirm-batch` 单事务把 READY/AUTO 全部置 CONFIRMED。

维护 Worker 启动时及每 15 分钟还会执行陈旧轮次自动恢复（`app/tasks/assistant_recovery.py`）：超过 `ASSISTANT_REQUEST_STALE_SECONDS`（默认 600，必须大于轮次时限，0 禁用）仍 `RUNNING` 的 `assistant_request` 及关联消息/Trace 用与手动 `app.assistant.recovery` 相同的单语句收口为失败，被 `CONVERSATION_BUSY` 锁死的会话因此自愈；额外扫描的 `ASSISTANT_API` Trace 只允许回收没有 RUNNING 请求引用的孤儿，或请求本身已进入同批 stale 集合的关联 Trace，不能仅因 Trace 创建时间较早而回收仍被活跃请求持有的 Message/Trace。同一语句还回收没有 `assistant_request` 行的内部轮次（`rag_trace.source` 为 `INTERNAL_CHAT`/`PLAYGROUND`），它们以 `ABANDONED_CHAT_TURN` 单独计数和标记，避免终态冲突回滚后 Message 永远停在 `STREAMING`；内部 Trace 不并入 `stale_trace_ids`，因为同一语句里两个 CTE 更新同一行时只有第一个生效。Redis 哨兵键防止多 Worker 重复注册周期链。保留期任务仍不删除 `RUNNING`。

问答轮次准入闸门位于 `app/common/turn_gate.py`：管理 `/api/v1/chat`、`/chat/stream` 和对外 Assistant 流在预留请求/调用模型前获取每进程信号量（按事件循环隔离），超过 `CHAT_TURN_CONCURRENCY_LIMIT`（默认 64，0 禁用）的轮次在等待 `CHAT_TURN_WAIT_SECONDS` 后快速失败 503 `SERVICE_BUSY`；Assistant 路径在预留幂等请求前判定，容量拒绝不消耗幂等键，信号量随流收口释放。管理端复测（answer retest）不走该闸门，由其每标记单运行的唯一索引约束。

`RAG_DOCUMENT_CARD_GENERATE` 运行在 `maintenance` 队列，可对活动/指定就绪索引全量回填或生成单文档卡片。同一索引只允许一个活动任务，支持合作取消和孤儿任务恢复；索引构建成功后会尽力提交该任务，但卡片或模型失败不改变索引成功状态。卡片 Prompt 携带实际 JSON Schema；仅对大小写及语义明确的类型别名做确定性归一化，其他非法结构仍失败。结构错误可使用离线重试预算；整批 0 成功必须把任务标为失败，部分失败保留成功卡片并报告计数。Embedding 按 `knowledge_index.embedding_model_id` 查询关联模型行；该行可被管理员原地修改，并非不可变的历史配置快照；每份保存必须使用该 `document_id` 对应 revision，失败卡片不可标为 `READY`。

管理 API 在 `/knowledge-bases/{knowledge_id}/rag-document-cards` 下提供列表、就绪检查和生成，在同一知识库的文档路径下提供单卡查看、编辑与审核。编辑/审核携带 `expected_revision`，冲突返回 409；自动生成是 `AUTO`，人工编辑后重新回到 `AUTO`，只有显式审核成为 `CONFIRMED/REJECTED`。已确认卡片默认不被自动生成覆盖，显式替换成功后也回到待确认状态。以上端点只属于管理认证，不加入外部 Assistant 契约。

外部服务包括 PostgreSQL、Redis、MinIO，以及可配置的模型服务。代码支持 SiliconFlow 嵌入/重排/OCR、智谱 LLM/视觉，以及阿里云百炼 DashScope；2026-09-15 起 DashScope 注册为全部五类模型类型（LLM/EMBEDDING/RERANK/OCR/VISION）的 Provider：LLM、Embedding、OCR、Vision 走 OpenAI 兼容 `chat/completions` / `embeddings`，Rerank 使用 DashScope 专有 `/api/v1/services/rerank/text-rerank/text-rerank` 端点（`model_providers.dashscope_rerank_base_url` 把管理台保存的 compatible-mode 地址映射到原生 API 根），Embedding 仅文本输入且沿用 1024 维契约。`auto` 模式会按环境或数据库模型快照中的凭据选择。Embedding、LLM、Rerank 在 staging/production 中禁止显式或隐式回退到 fake，缺少凭据时失败关闭；fake 仅用于开发/测试的受控场景。实际模型、超时和阈值来自 Settings、模型快照及环境变量，不能仅根据本文推断部署值。

## 管理模型配置与 Assistant 生效边界（2026-10-05 源码核对）

两类凭据区分 API 权限，不区分模型配置。管理台 `PATCH /models/{id}` 保存到共享 `model_config`；Assistant 经 `AssistantFacade → ChatGatewayService → RagService` 使用同一套问答实现。前提是管理台和业务 Assistant 请求连接的是同一套后端及数据库，不是不同部署。

| 类型 | 保存后的消费方式 | 索引与会话影响 |
| --- | --- | --- |
| LLM | `RagRepository.begin_turn()` 每轮重新读取启用配置；LLM 1、技术 LLM 2、非文档 LLM 2 共用该轮快照 | 下一轮生效，包括既有会话的新问题；不重建索引、不重启进程，已准备的轮次不被中途替换。 |
| Rerank | 同样每轮读取；仅文档路径实际调用 | 下一次文档检索生效；寒暄/非文档路径不调用，不重建索引。 |
| Embedding | 查询配置每轮读取，但索引向量不能原地变成新模型向量；查询选择器优先使用与会话冻结索引模型名匹配的数据库配置 | 改 Provider、模型名或 Base URL 必须确认全库重建并通过 1024 维探测；保存后向有有效文档的启用库提交重建，默认成功自动激活。模型名与活动索引不匹配期间 Assistant 可能 `ready=false`；新索引还须完成卡片生成/确认。旧会话不自动迁移，切换后应用新会话验收。 |
| OCR / Vision | 索引 Worker 开始任务时读取数据库快照，影响实际重新解析文档时的识别/图片描述 | 保存标记各库 `rebuild_required`，不自动重跑原文；普通构建可能 `REUSE` 旧元素、图片和向量，不能保证改完模型点击重建就刷新旧图片描述。 |

- Provider/模型/Base URL 的身份变更以及新 API Key 在写入前探测；保存成功不代表回答质量回归通过。`parameters`/`enabled` 的单独修改不执行这一身份探测。
- “模型配置共用”不代表参数 JSON 中任意字段都被 V2 使用：V2 的结构化调用明确传入各阶段 `temperature/max_tokens/thinking`，`ZhipuLlmProvider._complete_response()` 不合并 `snapshot.parameters`；数据库同名字段不能覆盖这些阶段值。2026-10-05 管理台已移除这些编辑控件，改为只读来源说明，保存其他字段不覆盖历史参数 JSON；后端策略未改。模型名、Provider、Base URL 和密钥仍按轮次快照消费，阶段预算/思考开关以代码和 Settings 为准。
- DB 密钥优先于对应 Provider 环境密钥；DB 无密钥时才用环境兜底。网页模型配置不要求容器重启；修改 `.env`/Settings 则须重建相应容器。历史消息与幂等重放读取保存的终态，不按新模型重生成。
- 当前索引复用和就绪一致性主要按 `embedding_model_name` 判断，不是完整 Provider/Base URL/模型指纹；同名模型换供应商或地址不能仅凭重建成功证明向量空间一致。查询选择器在旧会话模型名不匹配时回退环境 Provider，并未读取旧索引的完整历史模型凭据，因此“会话冻结索引”不等于“旧 Embedding 配置完整保留”。这些限制见 `known-issues.md`。
- 证据入口：`api/rag.py:update_model`、`rag/repository.py:begin_turn`、`services/rag.py:_select_query_embedding_provider`、`ingestion/model_config.py:load_build_models`、`ingestion/repository.py:_create_build_locked`、`gateway/repository.py:get_readiness`。

## 已删除的 RAG legacy 约束（历史记录）

本节到“通用实现约束”之前仅解释历史 Trace 和旧验收资料，V5 后不再对应运行时代码；当前 RAG 以“RAG V2 边界与当前实现”为准。

- RAG 上下文先按相关性决定保留集合，再按文档阅读顺序渲染；预算裁剪不按阅读顺序删除末尾步骤。邻接块继承种子的重排分数，仍保留块完整性提示，不截断块内正文。
- DOC_QA、CHUNK_QA 与 ROUTING 的最终提示词都检查字符和估算 Token 上限。焦点全文超预算时，复用本轮改写查询及查询向量，在同知识库、会话冻结索引和已确认文档内重新混合检索/重排；无相关片段、重排降级或超时不使用其他文档代答，保留原焦点。查询模型不新增调用，文档内二次重排仅在全文超预算时发生，受本轮剩余时限限制。
- 生成诊断 `runtime_snapshot.generation` 记录实际计划类型、最终提示词 SHA256、估算 Token 与实际上下文来源；原 `selected_context` 保留检索阶段语义，不能混作实际生成来源。`runtime_snapshot.retrieval` 保存 Gateway 覆盖后的有效参数；原 `runtime_snapshot.prompt` 仍为准备阶段 Active Prompt 的模板哈希。
- 运行时 DOC_QA、CHUNK_QA、ROUTING 模板为版本 3。条件不满足不能反推无需更换或维修成功；目录标题不构成步骤、参数或维修判断的事实依据。查询改写识别“之前/之后/排查后/是否/如果/文档说”等承接信号，保持原 3 秒默认上限。改写失败或无效时，仅“还是失败怎么办/然后呢”等明确省略对象的短追问可补同知识库、仍有效的焦点文档标题，并记录 `query_rewrite.fallback=FOCUS_TITLE`；这只补检索查询，不强制 STAY、不改变原问题或路由阈值。
- `M3_HISTORY_STRATEGY=focused` 时，RAG 历史只选取显式关联、用户和助手均为 `COMPLETED`、且属于当前 `focus_epoch` 的完整问答轮次；关联优先使用 `message.rag_user_message_id`，旧 Assistant 记录可使用 `assistant_request` 的既有消息关联。旧内部会话若没有可靠关联或焦点代次，不猜测相邻消息。选择从最新轮开始，按完整轮次同时受消息数和 Token 预算限制；最新有效轮次超预算时停止，不跳过它拼接更旧上下文。总 Prompt 超预算时先移除最旧完整轮次，再减少本轮文档证据。
- RAG 历史 Prompt 将用户内容标为未独立核实的陈述，将旧助手回答标为非事实证据；原文保留否定、条件、假设和较新纠正，不做 LLM 摘要。文档切换确认只把触发确认的原问题作为新焦点轮次的用户陈述桥接，不带入旧焦点回答。Trace 的 `runtime_snapshot.history` 和 `generation.history` 只记录策略、边界及消息 ID，不复制历史正文。
- 模型问答上下文使用文档管理中的最新显示名称（`_generation_candidates` 只重命名候选的文档标签，不改写候选正文）；后续正文中的同名文字不做全局替换。ADR-0011 的“交付时删除首条非空文本行”约定已于 2026-09-15 退役：RAG/Support 交付、路由内容块和文档问答 Prompt 均保留首个 TEXT 元素原文，不再做标题删除或前缀匹配。
- 提示词区分产品文档事实、基于证据的推断和通用知识，允许计算、条件分析、原理解释与明确标注的待验证假设；不从现象改善反推维修成功或无需更换。混合回答使用 `PRODUCT_EXPLAINED`，末尾通用建议不借用文档引用/图片；纯通用回答不返回文档引用。
- 对“文档说/提到某原文，如何操作/理解”这类无新对象的明确追问，仅在引述逐字命中同库、冻结索引内仍有效的焦点文档正文时进入 DOC_QA，Trace 标记 `FOCUS_EXCERPT`。原重排结果保留；既有确认、输入门禁及重排降级不被覆盖。引述不匹配或另提产品时沿用原路由。
- `M3_RAG_EXPLANATION_MODE` 默认 `enabled`，只对首轮可靠选中的文档及明确解释类问法启用 DOC_QA；完整全文/流程请求与确认交互保持原路径，焦点仍通过同一版本检查保存。`shadow/off` 可回退原文交付；与事实短答开关独立。
- `rag/presentation.py` 流式缓冲并隐藏跨增量的 MODE/IMG/来源标记及控制字符，保留普通 Markdown；最终答案和文本块同样清理。图片仍通过结构化块交付。修复图片标点重排中的控制字符替换问题。超时、空回答、上游 EOF 未终止或 length 截断均失败收口，保留干净的部分文本和可重试状态，不保存为成功回答。Assistant 继续保存公开终态，历史/重放不会重新生成旧答案。
- `M3_RAG_FACT_QA_MODE` 默认 `shadow`，只记录明确事实问法；`enabled` 才在首轮可靠选中文档后复用文档问答。完整步骤/流程请求、已有焦点和澄清确认不进入该实验，选文档规则不因此放宽。使用相同乐观版本检查建立焦点；对外 Assistant 契约和 Support 路由不变。
- `M3_RAG_TECH_ASSIST_MODE` 默认 `off`。`enabled` 时，RAG 用确定性策略统一区分完整文档、局部短答、解释和排查；可靠文档仍是专有事实依据，无可靠正文且知识库公开说明非空时才进入 `DOMAIN_ASSIST`。`shadow` 只记录回答形式及领域扩展可用性，不额外调用模型、不写 `rag_issue`、不改变公开回答。
- `DOMAIN_ASSIST` 沿用现有 LLM 和总时限，以内部模式前缀区分 `PRODUCT_GENERAL`/`UNCONFIRMED`/`OUT_OF_SCOPE`。流式层只实时放行专业分析；待确认、范围外或非法前缀在服务端校验后收口，不返回文档引用或图片。RAG 的有据回答、文档交付、路由辅助、专业分析和待确认响应统一携带一行短提醒；`OUT_OF_SCOPE` 不重复附加该提醒。字段仍为可选 `disclaimer`，Support 不因本规则改变。
- `RagRepository.begin_turn` 在知识库与 Active Index 锁定快照中同时读取名称、公开 `description` 及 `updated_at`。范围数据移除控制字符、折叠空白、转义标记边界，并受 `M3_RAG_SCOPE_MAX_TOKENS`（默认 800）限制；Trace 只记范围版本、长度、截断和 SHA256，不额外复制描述正文。直入 RAG 和 Support→RAG 回退共用该快照。
- 新策略的待确认状态使用 `conversation.chat_context.rag_issue`，只保留原问题、回答形式和一个条件。读取时验证最初用户消息、当前条件来源用户消息及追问助手消息的会话、角色、完成状态和配对关系；新状态只与成功回答终态在同一事务内 CAS 提交，失败、取消和噪声轮不推进状态。Support 不消费该命名空间。
- 以下 P2/P3 条目是 legacy 的历史行为，不是当前 V2 运行时。`rag_document_card` 仍是 RAG 专属派生数据，不读取或写入 Support Profile；旧 P2 只检索同库、会话冻结索引、当前 Schema、`READY` 且未拒绝的卡片，并与多查询 Chunk 候选融合。
- P2 查询规划器使用严格 `QueryPlanV1` 区分新任务、追问、纠正、条件回复、意图和回答范围，最多生成配置上限内的检索句。当前问题、历史、范围及焦点被标记为不可信 JSON；旧助手回答不作为事实，模型新增的精确型号/参数会触发降级。补充查询 Embedding 失败时保留已经取得的原问题 Chunk/卡片通道和候选，并标记 `DEGRADED`；初始问题 Embedding 失败仍沿用现有请求失败语义。
- P3 使用严格 `ResponsePlanV1`，在最多三份文档及其有限 Source 白名单内一次完成证据关系、六种回答策略和必要生成。`shadow` 在 P2 后继续后台生成并保存脱敏响应计划，但不执行策略、不改变答案/焦点；`enabled` 才执行计划。局部参数问题禁止全文交付；`DOCUMENT_DELIVERY` 只允许一份已确认的流程卡片，并由后端读取冻结索引原文；文档问答的 Source、文档归属和引用均再次校验。模型返回的 `case_state_patch` 在 P3 被清空且不写会话，结构化排障状态仍属于 P4。
- P3 六种内部策略映射既有公开契约：`SHORT_ANSWER`/`DOCUMENT_ASSISTED` 为 `ANSWERED`，`DOCUMENT_DELIVERY` 为 `DOC_DELIVERED`，其余为通用回答、单条件 `UNCONFIRMED` 或 `OUT_OF_SCOPE`。响应规划失败时只在本轮剩余总时限内回到旧路由；规划 Trace 在执行或旧路由失败前独立合并持久化。Support 不调用该 Planner。
- `runtime_snapshot.semantic_orchestration` 只保存 P2 版本、配置/有效模式、Planner Prompt 与问题/查询哈希、分类计数、查询来源、文档/Chunk 候选摘要及耗时；`runtime_snapshot.response_plan` 只保存策略、白名单 ID、答案哈希/长度、状态和降级原因，不复制答案正文。`runtime_snapshot.semantic_orchestration_config` 保存本轮配置快照。`shadow` 下原 `retrieval_result`、`selected_context` 和 Prompt 仍是公开旧路径的权威记录。

## 通用实现约束

- 嵌入维度在 Settings 与数据库契约中固定为 1024，变更需要迁移与重建计划。
- 查询嵌入优先使用与 Active Index 模型一致的数据库模型快照，无法使用时才尝试环境配置；Trace 的 `retrieval_result` 同时记录索引模型及实际查询 Embedding provider/model，便于识别索引与查询向量不一致。
- 明显低信息输入（纯符号、单字符、同字符重复、连续键盘字符）在检索和模型调用前返回 `UNCONFIRMED`；待确认选项存在时，`1` 等合法选择回复不受该门禁影响。
- **已删除的 legacy 行为：**旧 RAG 在 Rerank 降级后不选文档并保留焦点；V2 的文档级 Rerank 降级会保留 RRF 顺序，由 LLM 2 在隐藏候选上下文内回答。
- 运行策略有 `RAG_ONLY` 和 `SUPPORT_WITH_RAG_FALLBACK`；会话模式可覆盖引擎选择。Support 优先模式已冻结、不再使用（ADR-0021）：实际运行均为 RAG_ONLY，轮次被 `M3_TURN_DEADLINE_SECONDS` 严格包络；重新启用 Support 前必须重评 `ASSISTANT_REQUEST_STALE_SECONDS` 边界。
- **已删除的 legacy 行为：**首轮冲突保护、标题补召回和单强直达已在 V5 连同相关开关与测试删除；V2 每轮使用相同 Query Interpreter、Chunk/卡片融合和文档级 Rerank。
- DOCX 解析支持旧式 `v:imagedata`，保留重复引用的实际出现次数，兼容分支只取一份；图片未完整提取时失败，正文稀疏时告警。少于 80 字符正文且至少十个小型 VML 图片时进入文字碎片恢复；仅顺序内联版式允许真实 OCR、预处理和异常输出校验。绝对定位的浮动文字图片以 `LEGACY_FLOATING_TEXT_LAYOUT` 中止构建，不按 XML 锚点顺序拼接步骤；普通小图标仍走原图片处理流程。正式 OOXML 页眉/页脚是独立部件，解析器从不读取；只有拖入正文的图片会入库。
- PDF 摄取（`ingestion/parser_pdf.py`，pdfplumber，MIT）产出与 DOCX 相同的 TEXT/IMAGE 元素契约，按（页码, 纵坐标, 横坐标）保证阅读顺序，图文自然交错；字号聚类（相对正文 ≥1.15 倍的三个最大字号）推断 h1–h3 生成 `section_path`。页眉/页脚剔除在一切输出之前：顶部/底部 10% 区域内跨页重复文本（数字归一化判重）、页码模式行、跨页重复的带内图片（按解码字节摘要判重，正文区同图保留）；区域内一次性内容保留。栅格图片解码 DCT（原样 JPEG）/Flate（按 colorspace/bits 重组，SMask 合成后转 PNG）/JPX（PIL 转换）；滤镜缩写名（`/Fl`、`/DCT`、`/AHx` 等，WPS 导出/扫描仪固件常见）先归一化为全名再匹配白名单；**单张图片解码失败（含不支持编码如 CCITTFax/LZW、损坏数据）跳过该图并记录 `PDF_IMAGE_SKIPPED` parser_warning，不再中止构建（ADR-0022）**；小于 16px 的装饰性小图跳过并告警。加密 PDF 在上传校验即拒绝；限制为 `m2_pdf_max_bytes`（默认 100 MiB）与 `m2_pdf_max_pages`（默认 200）。PDF 不产 TABLE 元素；若未来文档出现表格，其文字仍按行提取，届时以新的 PDF 解析版本号触发重解析。上传链路按扩展名分发校验与存储：对象键 `source.pdf`、MIME `application/pdf` 写入 `document_source.mime_type`，管线按该列分发解析器；DOCX 上传行为不变。
- 摄取结果按格式记录 `content_revision`：DOCX 为 `ooxml-vml-1`，PDF 为 `pdf-1`（`models.content_revision_for_mime` 按文档 MIME 分派）。构建计划及执行 REUSE 时都按各自格式的期望版本检查，执行时同时检查源哈希与 READY 状态；升级一种格式的解析版本不会强制另一种格式重解析。`index_document.metadata` 记录 `parser`（`mammoth+ooxml` / `pdfplumber-pdf`）与 `parser_warnings`（原 `mammoth_warnings` 键已泛化）。无当前版本标记的旧结果须重新解析，不能冒用新版本；旧活动索引不因此被修改。该摄取规则为公共基础能力，RAG/Support 都受影响，但两条问答路由没有混用。DC24V 浮动版式正文仍需转换核验，不能宣称已恢复其检索质量。
- 对外 Assistant 请求使用幂等记录与外部用户隔离；终态幂等重放会原子累计 `replay_count` 并刷新最后重放时间，其响应通过 `public_payload` 过滤内部字段。
- Assistant 消息分页从数据库正序返回的最近 `limit+1` 条中取末尾 `limit` 条，以本页最早序号作为 `next_before`；不丢弃最新消息。游标仅接受正的 ASCII 十进制 int32 序号；会话列表仍按倒序取前 `limit` 条，两种分页不可共用截取方向。
- 失败幂等重放使用公开错误字段白名单，保留保存的 `retryable`、请求/会话/消息/Trace 标识与 `partial_content`；新 HTTP 请求的标识在响应头和 `accepted` 中，旧错误快照的 `request_id` 不被覆盖。旧记录缺少布尔重试标志时按已知错误码补默认值，未知错误不建议重试。
- 管理指标的延迟口径与质量口径分离：所有输入门禁短路（`GREETING`、`META_ACK`、低信息）都把门禁码写入 `citation_result.shortcut`，LLM 对话分支把 `response_path=CONVERSATION` 写入同一 JSON；`overview`/`timeseries`/`latency`/`assistant` 的技术延迟分位、均值和阶段耗时排除零模型短路及不经过 RAG 的 Conversation 轮次，并返回可用处的 `latency.samples` 说明分母。请求总量、完成/失败数、失败率、会话数和用户数仍把这些轮次计为真实请求。技术回答、范围和引用/重排质量比率排除 `GREETING`、会话控制类 `META_ACK` 及 `CONVERSATION`，低信息澄清仍是真实技术信号并保留在质量分母中。
- Message、Trace 与 `assistant_request` 的终态写入视为关联不变量：`complete_turn()` 与 `complete_assistant_request()` 校验每条目标行的会话、角色、配对 ID 与原状态，任何一行未按预期更新即整事务回滚并返回 409（`TURN_STATE_CONFLICT`、`ASSISTANT_REQUEST_STATE_CONFLICT`、`ASSISTANT_MESSAGE_STATE_CONFLICT`）。关联绑定（`set_assistant_request_context()`，`ASSISTANT_CONTEXT_CONFLICT`）是观测性写入：尚未解析出的 `user_message_id`/`assistant_message_id`/`trace_id` 按未绑定放行并保留列中已经验证的非空值；后续事件只提供部分标识时，新标识仍须与已保留标识交叉验证，列已绑定到其它 Message/Trace 或组合关系不一致才算冲突。这样已经生成成功的回答不会仅因关联元数据缺失被 Assistant 门面转成 `INTERNAL_ERROR`，同时不会放过跨事件冲突。`assistant_turn_context()` 解析 `user_message_id` 供该次绑定使用时，`message.rag_user_message_id` 已显式设置但连接校验（同会话、角色为 USER、序号早于助手消息）失败，返回未知（`None`）而不是回退到"最近一条 USER 消息"的顺序猜测——顺序猜测只用于该字段本身为空的旧数据；一条已存在但校验不通过的显式关联视为数据异常，不能被静默替换成另一条消息（2026-09-29 PG 回归验证）。
- 新失败/取消请求在收口事务中同步关联 Message 的公开错误快照；失败同时同步关联 Trace，终态请求不被重复收口覆盖。历史错误优先使用 Message 快照，再兼容旧 Trace；Trace 详情过期不影响新消息已保存的错误语义。旧历史不批量回填。
- RAG V2 不再公开交付原文；完整候选文档只作为 LLM 2 的隐藏上下文。冻结的 Support 仍保留其完整文档交付实现。
- `include_images` 默认 `true`。Gateway 在同步/流式直入 RAG 及 Support 回退 RAG 时传递该参数；RAG 在结果保存前过滤图片块及交付来源中的图片 ID，不改变用于检索、重排和文档问答的图片证据。Support 原有图片控制保持不变。新 Assistant 请求保存过滤后的公开终态，历史与幂等重放沿用该快照。
- Assistant 历史消息优先采用 `message.response_payload` 的公开答案和内容块；显式空 `content` 不得回退到内部 `content_blocks`。没有公开内容快照的旧记录才读取内部块，并递归移除 `url`/`download_url`；历史读取和旧终态重放均执行同一地址过滤，不回写原记录、不重新调用模型。修复前已经保存的错误带图结果不会按新规则追溯重算，重新验证需使用新幂等键。
- 对外 Assistant 只列出 `assistant_online=true` 且知识库本身启用的项目；欢迎语、问答、会话和图片入口都执行同一上线边界及知识库归属检查。新知识库默认不对外，迁移前已经启用的知识库保持原可见性。
- Assistant 列表的 `ready` 按知识库当前执行策略和 RAG 文档理解审核状态计算；活动索引中全部 `READY/STORED` 文档必须具有当前结构版本、生成成功且 `CONFIRMED` 的卡片。Chat 在预留幂等请求前再次执行同一检查。已上线后若切换索引、增加文档或显式重生成卡片造成缺失、失败、过期、拒绝或待确认，列表保留该知识库但返回 `ready=false`，欢迎语和问答返回 409，直至全部重新确认。
- 对外 Assistant 与管理端 DemoChat 的就绪态欢迎语使用同一简洁用户侧文案，不暴露知识库名称、描述或文档数量。Assistant 的三条快捷问题读取当前知识库的 `assistant_suggestions`，与 Profile 标准问题独立维护；DemoChat 现有快捷问题仍从文档目录生成。未就绪时 Assistant 只返回通用准备中文案，DemoChat 保留管理员侧索引/上传指引。欢迎语、列表、管理上线和 Chat 共享 Gateway 的策略就绪判断。
- Assistant Chat 会在创建 `StreamingResponse` 前完成会话权限、知识库及 Active Index 可用性、策略、幂等和同会话并发检查；这些前置失败保持 HTTP JSON 错误，`accepted` 后的生成期失败才使用 SSE `error`。
- Assistant Chat 的已预留请求由专用流响应持有；ASGI 发送失败或客户端断连时会在响应收口阶段幂等地将仍为 `RUNNING` 的请求及其关联消息/Trace 标记为取消，正常终态与幂等重放不会被改写。进程强制终止等无法执行收口的情况仍由遗留请求恢复命令处理。
- Assistant SSE `context` 只允许输出会话、用户消息、Assistant 消息和 Trace 标识，不透传 Index 等内部 Trace 字段；已知短暂上游/超时错误保留可重试语义。
- Assistant 不提供原文下载接口；公开回答中的文档 ID 和标题只用于来源展示。图片接口只允许读取状态为 `STORED` 的所属文档图片，按 64 KiB 分块代理 MinIO，并转发可信的内容类型和可用的对象长度。
- 管理 API 允许修改 `document_source.display_name`，但不修改原始文件名、对象存储键、文档 ID 或索引内容，也不触发索引重建；同一知识库内新增或重命名后的有效显示名称不得重复。后续 RAG/Support 回答从文档源读取最新显示名称，既有 Message、Assistant 幂等终态和 Trace 保持原快照。
- 回答正文中出现文档标题时使用 Markdown `**《文档标题》**`；文档直达回答的 `answer` 只包含该标题，不添加“已找到”或“以下是文档内容”等引导语。ADR-0011 已于 2026-09-15 废弃：RAG/Support 文档交付不再删除第一条非空 TEXT 元素的首条非空行，元素按原文逐块交付；图片、表格及正文顺序保持原样，源文档和索引不修改。结构化 `document.title` 和 `references[].title` 保持无 Markdown 的纯文本契约。
- 超过指定时间仍为 `RUNNING` 的 Assistant 请求可通过 `app.assistant.recovery` 预览并事务恢复；恢复会保留请求行，并同步将关联的流式消息和 Trace 标为失败。
- 维护 Worker 启动时及之后每天清理超过 `ASSISTANT_REQUEST_RETENTION_DAYS`、未关联 Conversation 且处于终态的 Assistant 请求；关联会话的请求随 Conversation 级联清理，`RUNNING` 请求只能经正常收口或遗留恢复处理。
- API 密钥可存储为加密密文，密钥轮换由 `app.tasks.key_rotation` 支持；文档只可记录变量名，不能记录值。

## 测试

`backend/tests/` 覆盖 API、网关、RAG、Support、摄取、任务和 PostgreSQL 集成。常规全套校验由根目录 `scripts/check.sh` 编排；以当前配置和实际结果为准，详见 [`environment.md`](environment.md) 与 [`known-issues.md`](known-issues.md)。

## 并发观测

- `/api/v1/metrics/assistant` 响应新增 `concurrency` 段：当前 `RUNNING` 请求数、最老 RUNNING 年龄、超过陈旧窗口的计数，以及窗口期内 `CONVERSATION_BUSY`/`REQUEST_IN_PROGRESS`/`SERVICE_BUSY` 拒绝计数（来自 `request_error_log`，仅管理认证）。
- `/api/v1/metrics/runtime`（管理认证）返回本进程快照：准入闸门 `limit`/`in_flight` 与数据库连接池 `size/checked_in/checked_out/overflow`；多 uvicorn Worker 时每个实例只报告自身，需按实例抓取。

## RAG V2 边界与当前实现

- 历史默认窗口为最近最多 4 个完整技术轮次、10 条消息、估算 2,000 Token；来自 `core/config.py`，部署可覆盖。选择器从最新往前按整对消息选取，最新有效轮次超预算时直接停止，不截断答案或跳过去拼更旧历史；因此可能得到空窗口。只在同一 `conversation_id` 读取，不使用长期记忆、LLM 摘要或 30 分钟自动分段。
- 按 ADR-0030，技术 `build_answer_prompt()` 只接收标准化问题 `resolved_question`、`relation/active_task`、最近所选历史和本轮隐藏文档；本轮原话仍参与检索和服务端参数来源检查，不直接进入技术 LLM 2 Prompt。非文档 `build_conversation_prompt()` 则仍接收本轮原话及最近一个所选技术轮次，不能将“LLM 2 不看原话”推广到所有路径。改写丢失语言、否定或条件时，下游缺少直接纠正本轮改写的输入；已有英文作答失败记录见 ADR-0030。
- ADR-0026 的保守二路由由 ADR-0027 修订：`KNOWLEDGE` 完整保留检索与技术 LLM 2；`CONVERSATION` 是无需文档依据的非检索 LLM 2，跳过 Embedding、检索、Rerank 和文档加载。是否需要作出新判断不再自动决定检索；一般性人工协助建议可不检索，但产品专有事实、操作/诊断、官方服务事实及明确询问文档规定必须检索。非检索 LLM 2 不得把统一免责声明或历史回答冒充文档依据，不得编造服务承诺。路由字段缺失/无效时保守降级 Knowledge；对话 LLM 2 失败使用固定兜底，不回跳 RAG；对外 Assistant/SSE 结构不变。
- ADR-0019 已停止原 P3 六策略和 P4 `rag_case` 的继续实施。V0～V5 已交付 `app.rag.v2`：最小 Query Interpreter 输出独立请求、范围、会话关系、当前任务和二值 `response_path`，不输出检索补充查询或用户约束，不生成答案、不决定回答篇幅。知识库名称和公开说明是可信产品档案；询问产品身份、用途、组成、原理、操作、维护或故障不因缺少直接文档变成范围外。技术 LLM 2 的范围与证据收口仍沿用既有规则。LLM 1 只接收按轮次组织的最近成功技术历史；`FOLLOW_UP/CORRECTION` 任务从最近完整轮次的用户请求继承，旧助手回答仅用于理解指代。LLM 1 单次时限 25 秒，完整超时不重试，429/5xx/连接类即时失败最多快速重试一次；失败时只对有完整历史的省略对象短追问确定性继承任务，其余保守走 Knowledge。Trace 记录超时、历史兜底及检索结果。V2 不调用旧 Query Rewrite、首轮标题召回、焦点路由或 P2/P3 Planner。
- Query Interpreter 对“产品技术任务 + 无关创作/娱乐/闲聊”的混合输入只保留产品技术子任务用于独立问题和检索；Answer Generator 再次明确只回答该技术部分，不执行、复述或评价无关要求。纯无关任务仍由 LLM 1 判为范围外；这里不维护诗句或问题文本词表。
- V2 复用文档卡片、Chunk/卡片文档级融合、Rerank 和 `get_document_blocks()`。`RagService.prepare()` 只创建冻结轮次，不运行 legacy 预检索；原问题、独立请求和可选任务锚点去重后批量 Embedding 一次，再以 RRF 聚合及文档级 Rerank 排序。`FOLLOW_UP/CORRECTION` 的最近实际使用文档可作为零初始分软候选，但不保证进入最终上下文；`NEW_TASK` 不继承。前 1～3 份有序文档送入技术 LLM 2。技术 LLM 2 应给最小充分回答，不能把任务特定条件泛化为全产品规则；询问文档有无规定时，未见规定不能推断可自行操作、无需授权或安全。文档是产品专有事实的优先证据但不是回答许可证；零候选时范围内问题仍可用通用知识有界推进。后端继续校验文档/图片白名单并映射公开结构，`references=[]`，不交付完整原文。
- `M3_RAG_ANSWER_THINKING_ENABLED=false` 是上线默认值：LLM 1、LLM 2 及合规修复均不请求内部思考。需要定向对照实验时可显式开启，此时仅 LLM 2 初次生成及其一次合规修复请求思考；DashScope 发送 `enable_thinking=true` 及默认 2048 的 `thinking_budget`，智谱发送 `thinking.enabled` 但当前不发送跨供应商预算字段。不支持的 Provider 保持关闭，始终思考模型不发送控制字段。Qwen 思考调用不同时发送不稳定的 JSON Mode，仍由 Prompt Schema、本地结构校验和既有一次修复保护；关闭思考的结构化调用继续使用 JSON Mode。推理正文不会进入最终答案或 Trace；Trace 只记录请求状态、配置预算和供应商用量。
- LLM 2 在回答操作前后、下一步或失败追问时，必须检查所有与当前任务直接相关的候选；主任务文档定义必需动作，补充文档只能展开某一步，不能替代主任务文档中的其他并列动作。实际采用事实的文档全部写入内部证据键。图片输出兼容单个或成组 `[IMG:...]` 标记，只按正文标记顺序映射白名单图片；模型仅在 `used_image_keys` 声明但未在正文定位的图片不会追加到回答末尾，公开 `image_count` 以实际图片块为准。句末标点必须位于关联文字末尾、图片之前，模型误把标点放到标记后时由公开组装层确定性前移。未声明、无效或异常图片标记只被清除，不产生图片，也不会泄漏到公开文本。该兼容不增加模型调用。
- `document_source.status='STORED'` 已统一应用于向量、BM25 和按 Chunk ID 读取，避免已软删除文档成为 V2 全文候选；全文加载还会再次校验知识库归属。V2 准备 Trace 不保存 legacy Chunk Prompt，`selected_context` 只记录候选标识与内容 SHA-256；生成 Trace 记录全文包/答案 SHA-256、数量、预算和耗时，不复制正文。
- 管理试问和 Assistant 的有效 V2 路径均进入 `_generate_v2()`；管理端额外信息只从 Trace 取得。最终答案发布前只检测 Prompt 标记、内部服务主机、未经本轮证据支持的相对管理路径和对象存储字段，证据 D/I key 仍做归属白名单校验；不因标题、文件名、技术编号、产品私网 URL 或普通产品 API 直接失败。IP、版本、带单位数值和端口只写入 `runtime_snapshot.generation.answer.parameter_observations`，按文档、用户输入或无来源分类，不参与回答拒绝或修复。Assistant Facade 以公开字段白名单移除模型用量、检索/重排诊断、候选分数、管理 URL 和 MinIO 位置；欢迎语和外部响应不公开文档总数。
- RAG V2 在既有 `runtime_snapshot.generation` 中写入内部 `diagnostic_reason`：确定性低信息门禁为 `LOW_INFORMATION`，LLM 1 与 LLM 2 范围结果被服务端归一化为 `SCOPE_CONFLICT`，无范围冲突且最终 `ASK_ONE` 为 `NEEDS_KEY_CONDITION`。原始 `input_guard` 继续保留；Prompt 提取拦截不会误标为低信息。完整消息精确匹配“你好、您好、Hello、在吗”等纯寒暄时，在 LLM 1、Embedding 和 Rerank 前确定性返回固定欢迎语，Trace 以 `input_guard=GREETING` 和零模型调用正常完成；含技术内容的组合输入不做子串匹配，仍进入 V2。寒暄 Trace 继续出现在近期记录，Assistant 请求运营总量也仍把它视为一次真实请求；RAG 技术回答率、趋势和延迟聚合通过不会被详细 Trace 脱敏清除的 `citation_result.shortcut` 排除它。该字段不改变外部 Assistant/SSE 结构，不回填历史记录，也不能自动等同于知识库缺口或检索误判。
- V2 不恢复 `rag_issue`、不消费待选文档，也不读写 RAG 焦点。`begin_turn` 使用独立的完整轮次选择器：只接受 `message.rag_user_message_id` 或 `assistant_request.user_message_id` 显式关联且双方成功完成的轮次，按最近连续轮次、消息数和 Token 预算裁剪；失败、取消、运行中、无法配对、当前复用轮次以及 `input_guard=GREETING/META_ACK`、`response_path=CONVERSATION` 的非技术轮次排除，遇到这些轮次时继续向前选取仍在预算内的技术轮次。低信息澄清轮次保留，以便下一轮补充条件。输入门禁和回答路径同时写入 `message.rag_history_context`，历史优先读取这一与消息同生命周期的标记，并仅对旧数据回退 Trace，因此 Trace 脱敏或删除不会让非技术轮次重新进入技术历史。Conversation LLM 2 为改写最近技术回答只临时接收该轮，不会把自身产物作为后续技术事实。最近成功技术轮次还从其已完成 RAG Trace 读取实际 `used_document_ids`，仅供上述追问软候选；这不是会话焦点，也不授权跨库/跨索引加载。历史 Prompt 不携带消息 ID；任务正文和检索查询在 Trace 中只保存长度、来源与 SHA-256，完整上下文只保存文档 ID/内部别名和内容哈希。共享焦点字段仍被 Support/Gateway 使用，不能删列。
- V5 后代码和所有环境默认只有 V2，不存在 `auto/legacy/v2` 模式分派。旧首轮、焦点、Query Rewrite、Query/Response Planner、六策略与 RAG 原文交付已物理删除；回滚只能部署旧 Git 提交或旧镜像。数据库 Prompt 行只供管理和历史查看，RAG 就绪不再要求 Active Prompt；两个运行时 Prompt 由代码只读展示。具体删除边界见 [V2 执行计划](../technical/rag-full-document-generation-v2-execution-plan.md)。

## 知识库 Assistant 管理

- 管理凭据下新增 `PATCH /api/v1/knowledge-bases/{id}`，请求只含公开 `description` 和必填 `expected_updated_at`；空白说明归一化为 `null`，过期版本返回 409 `KNOWLEDGE_BASE_UPDATE_CONFLICT` 及当前更新时间，删除中拒绝修改。成功返回完整 `KnowledgeBaseResponse`。该操作不创建任务、不改 Active Index/Chunk、不触发重建；外部 Assistant API 没有新增管理入口。
- 管理凭据下提供 `GET /api/v1/knowledge-bases/{id}/assistant-config`（配置及 `ready`/`blockers`）、`PUT` 同路径（`revision`、`suggestions`）和 `PUT /api/v1/knowledge-bases/{id}/assistant-publication`（`revision`、`assistant_online`）。成功写入返回 `knowledge_id`、`assistant_online`、`suggestions` 和新 `revision`；外部 `/assistant/*` 不增加管理接口，公开欢迎响应结构不变。
- 每条推荐问题去除首尾空白后为 4 至 200 字，最多三条，NFKC、忽略大小写/空白及末尾问号后不得重复。下线库可保存 0～3 条，线上库必须保留三条；上线检查完整性、当前执行策略，以及活动索引全部 RAG 文档理解已确认。缺失问题或任一运行/审核条件未就绪返回 409，文本校验失败返回 422。
- `AssistantConfiguration` 在同一事务内锁定知识库、检查修订和执行写入；上线就绪检查复用该事务连接。冲突返回 409 `ASSISTANT_CONFIG_CONFLICT`，需重新加载；下线不查询运行就绪，问题保存不隐式切换上线状态。删除中的知识库拒绝配置写入。
- 不建立审批状态机或冻结运行版本；管理员通过管理 Chat 试问后确认上线，修改线上问题立即生效。索引、模型和 Profile 调整后由管理员重新试问；需要检查期间停止对外时先下线。下线阻止后续入口访问，不承诺撤回已经发送或缓存的内容，也不主动中断已建立的流。
- 使用方法：管理台“文档”→ 对活动索引生成/补齐 RAG 理解并逐份确认 → 管理 Chat 试问 → “知识库”→ “Assistant 上线与推荐问题”→ 填写并保存三条问题 → “检查就绪”→ “确认上线”。正式部署前执行 Alembic `upgrade head`（当前仓库 head 为 `0047`），再更新后端和前端。


## Assistant 回答优化（管理侧）

- `api/answer_reviews.py` 仅注册到管理认证边界。按知识库提供 `GET /knowledge-bases/{id}/answer-records`（`query`、`marked`、`status`、`limit`、`offset`）、`GET /answer-records/{request_id}`、`PUT /answer-records/{request_id}/review` 和 `POST /answer-reviews/{review_id}/retests`，后三者同样带知识库前缀。对外 Assistant 路径、事件和响应结构不变。
- 列表以 `assistant_request.id` 为单位，不因幂等重放或引擎回退重复计数。详情展示已保存的公开终态响应、前置消息及 Trace 诊断；不读取当前索引来重建历史回答。无问题内容或快照的旧请求明确显示缺失。
- `assistant/reviews.py` 使用独立 `answer_review` 表保存原始快照，问题分类、预期文档、预期表现、备注和三种处理状态 `OPEN/VERIFY/CLOSED`；`ACCEPTABLE` 表示合理回答无需修改。首次保存要求 `revision=0`，后续写入需携带当前修订；并发首次创建与后续修改均串行校验。预期文档必须为同库 `STORED` 文档。
- `assistant_request.input_payload` 仅在新预留请求时保存 `question`、`choice_id`、`include_images`，幂等重放不覆盖；旧数据不猜测补写。问题标记保存该输入及前置消息，不保存管理凭据。
- `assistant/retest.py` 用新内部会话逐轮重问前置问题和当前问题，复用 Gateway 当前配置与真实模型，Trace 来源为 `INTERNAL_CHAT`；不创建外部 Assistant 请求、不复用原会话。每次复测保存期望与标记修订，以及各轮实际响应、索引、运行快照和耗时；人工判断正文，不自动把执行成功当作质量通过或关闭问题。
- 复测请求携带 `run_id` 和 `revision`；相同标识重放已有记录，不重复调用模型，每条问题同时最多一次复测。总时限180秒、最多20轮；上下文缺失拒绝执行，前置澄清目标变化停止续问。中断/失败保存已完成轮次；进程强制终止留下的运行记录在超过5分钟后再次发起复测时收敛为失败。
- 人工保存的问题及复测不随会话、Trace 或源文档删除而丢失；知识库删除会级联清理。仅保存图片资产引用，不延长图片/索引保留期，资源过期时前端明确显示不可用。JSON 用例导出是保存快照，本期尚未接入 Golden 批量执行或自动调参。
