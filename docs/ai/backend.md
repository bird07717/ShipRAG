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

## 模块关系

| 模块 | 职责 |
| --- | --- |
| `core/` | Settings、日志、连接资源、模型密钥加密。 |
| `common/` | 错误模型、中间件、文本与资产工具。 |
| `gateway/` | 读取运行策略及会话模式，调度 RAG/Support 和记录回退。 |
| `rag/` 与 `services/rag.py` | legacy 查询改写/文档路由，以及 V2 的双 LLM、文档级检索、隐藏全文上下文、答案/图片校验；共同负责嵌入、重排、Trace 和 RAG 文档卡片。 |
| `support/` | 技术支持 Profile 解析、聚焦工作流、文档交付、风险校验、幂等。 |
| `ingestion/` 与 `services/ingestion.py` | DOCX 安全校验、解析、图像理解、分块、嵌入、索引构建。 |
| `document_profiles/`、`releases/` | Profile 生成、审核、发布与回滚。 |
| `assistant/` | 对外 Assistant API 的门面、公开响应过滤、游标及遗留运行请求恢复。 |
| `tasks/` | RQ 任务、任务记录、GC、保留期清理、恢复和密钥轮换。 |
| `observability/` | Trace 与未处理请求错误记录。 |

## 后台任务和外部依赖

RQ 队列逻辑名为 `ingestion`、`index_build`、`maintenance`。开发/生产均将索引 Worker 和维护 Worker 分开：索引 Worker 负责索引构建、启动时的索引恢复/GC；维护 Worker 负责 Profile/维护任务、过期错误日志和周期 GC 调度。不要因方便把它们合并，长索引任务会阻塞维护工作。

`RAG_DOCUMENT_CARD_GENERATE` 运行在 `maintenance` 队列，可对活动/指定就绪索引全量回填或生成单文档卡片。同一索引只允许一个活动任务，支持合作取消和孤儿任务恢复；索引构建成功后会尽力提交该任务，但卡片或模型失败不改变索引成功状态。卡片 Prompt 携带实际 JSON Schema；仅对大小写及语义明确的类型别名做确定性归一化，其他非法结构仍失败。结构错误可使用离线重试预算；整批 0 成功必须把任务标为失败，部分失败保留成功卡片并报告计数。Embedding 使用 `knowledge_index.embedding_model_id` 冻结快照；每份保存必须使用该 `document_id` 对应 revision，失败卡片不可标为 `READY`。

管理 API 在 `/knowledge-bases/{knowledge_id}/rag-document-cards` 下提供列表、就绪检查和生成，在同一知识库的文档路径下提供单卡查看、编辑与审核。编辑/审核携带 `expected_revision`，冲突返回 409；自动生成是 `AUTO`，人工编辑后重新回到 `AUTO`，只有显式审核成为 `CONFIRMED/REJECTED`。已确认卡片默认不被自动生成覆盖，显式替换成功后也回到待确认状态。以上端点只属于管理认证，不加入外部 Assistant 契约。

外部服务包括 PostgreSQL、Redis、MinIO，以及可配置的模型服务。代码支持 SiliconFlow 嵌入/重排/OCR、智谱 LLM/视觉，以及阿里云百炼 DashScope OpenAI 兼容 LLM；DashScope 当前只注册为 LLM Provider，不据兼容地址推定其 Embedding、Rerank、OCR 或 Vision 能力。`auto` 模式会按环境或数据库模型快照中的凭据选择。Embedding、LLM、Rerank 在 staging/production 中禁止显式或隐式回退到 fake，缺少凭据时失败关闭；fake 仅用于开发/测试的受控场景。实际模型、超时和阈值来自 Settings、模型快照及环境变量，不能仅根据本文推断部署值。

## 关键实现约束

- RAG 上下文先按相关性决定保留集合，再按文档阅读顺序渲染；预算裁剪不按阅读顺序删除末尾步骤。邻接块继承种子的重排分数，仍保留块完整性提示，不截断块内正文。
- DOC_QA、CHUNK_QA 与 ROUTING 的最终提示词都检查字符和估算 Token 上限。焦点全文超预算时，复用本轮改写查询及查询向量，在同知识库、会话冻结索引和已确认文档内重新混合检索/重排；无相关片段、重排降级或超时不使用其他文档代答，保留原焦点。查询模型不新增调用，文档内二次重排仅在全文超预算时发生，受本轮剩余时限限制。
- 生成诊断 `runtime_snapshot.generation` 记录实际计划类型、最终提示词 SHA256、估算 Token 与实际上下文来源；原 `selected_context` 保留检索阶段语义，不能混作实际生成来源。`runtime_snapshot.retrieval` 保存 Gateway 覆盖后的有效参数；原 `runtime_snapshot.prompt` 仍为准备阶段 Active Prompt 的模板哈希。
- 运行时 DOC_QA、CHUNK_QA、ROUTING 模板为版本 3。条件不满足不能反推无需更换或维修成功；目录标题不构成步骤、参数或维修判断的事实依据。查询改写识别“之前/之后/排查后/是否/如果/文档说”等承接信号，保持原 3 秒默认上限。改写失败或无效时，仅“还是失败怎么办/然后呢”等明确省略对象的短追问可补同知识库、仍有效的焦点文档标题，并记录 `query_rewrite.fallback=FOCUS_TITLE`；这只补检索查询，不强制 STAY、不改变原问题或路由阈值。
- `M3_HISTORY_STRATEGY=focused` 时，RAG 历史只选取显式关联、用户和助手均为 `COMPLETED`、且属于当前 `focus_epoch` 的完整问答轮次；关联优先使用 `message.rag_user_message_id`，旧 Assistant 记录可使用 `assistant_request` 的既有消息关联。旧内部会话若没有可靠关联或焦点代次，不猜测相邻消息。选择从最新轮开始，按完整轮次同时受消息数和 Token 预算限制；最新有效轮次超预算时停止，不跳过它拼接更旧上下文。总 Prompt 超预算时先移除最旧完整轮次，再减少本轮文档证据。
- RAG 历史 Prompt 将用户内容标为未独立核实的陈述，将旧助手回答标为非事实证据；原文保留否定、条件、假设和较新纠正，不做 LLM 摘要。文档切换确认只把触发确认的原问题作为新焦点轮次的用户陈述桥接，不带入旧焦点回答。Trace 的 `runtime_snapshot.history` 和 `generation.history` 只记录策略、边界及消息 ID，不复制历史正文。
- 模型问答上下文使用文档管理中的最新显示名称，并保守去除首个原生 TEXT 元素中不超过 300 字的开头标题行；分块问答还要求候选确实包含该元素且正文前缀精确匹配。后续正文中的同名文字不做全局替换，异常长合并首行保留，避免误删正文。此规则不改变原文交付链路的标题去重、文档索引内容或历史回答快照。
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

- 嵌入维度在 Settings 与数据库契约中固定为 1024，变更需要迁移与重建计划。
- 查询嵌入优先使用与 Active Index 模型一致的数据库模型快照，无法使用时才尝试环境配置；Trace 的 `retrieval_result` 同时记录索引模型及实际查询 Embedding provider/model，便于识别索引与查询向量不一致。
- 明显低信息输入（纯符号、单字符、同字符重复、连续键盘字符）在检索和模型调用前返回 `UNCONFIRMED`；待确认选项存在时，`1` 等合法选择回复不受该门禁影响。
- Rerank 降级为无分数排名时不得据此选中或沿用文档；即使会话已有焦点也返回不带来源和下载引用的 `UNCONFIRMED`，但保留焦点供下一轮有效追问。任何 `UNCONFIRMED`、`OUT_OF_SCOPE` 或通用解释响应都不得公开文档引用。
- 运行策略有 `RAG_ONLY` 和 `SUPPORT_WITH_RAG_FALLBACK`；会话模式可覆盖引擎选择。
- RAG 首轮保护位于 `rag/first_turn.py` 和 `services/rag.py`，不修改共享 `doc_router.py`，不读取 Support Profile/标准问题/标签。首轮指无历史、无焦点、无待确认选项；重排成功且非寒暄时，聚合直达目标与原始最高分块所属文档冲突就转为两份文档澄清，不自动改选。用户确认仍走既有交付与会话版本校验。
- 精确字母数字型号可在首轮补召回当前知识库、当前会话索引内的文档块：按最新显示名称匹配，最多三份文档、每份一块；匹配超过三份时不随机截取。候选仍参加原问题 Rerank，不因标题含型号而直达；`rerank_result.title_recall` 记录补召回来源。
- RAG 单强直达默认 `shadow`：原始最高块分数至少 0.85、领先另一文档最高块至少 0.18，且至少两份不同候选文档；只在原 NO_MATCH/CLARIFY 且无聚合冲突时判断。影子结果在路由 thresholds 的 `rag_single_strong` 中记录；`enabled` 才改变回答，并交付整份文档步骤和图片，不只交付单块。降级排名、既有焦点和待确认回复不触发新规则。
- DOCX 解析支持旧式 `v:imagedata`，保留重复引用的实际出现次数，兼容分支只取一份；图片未完整提取时失败，正文稀疏时告警。少于 80 字符正文且至少十个小型 VML 图片时进入文字碎片恢复；仅顺序内联版式允许真实 OCR、预处理和异常输出校验。绝对定位的浮动文字图片以 `LEGACY_FLOATING_TEXT_LAYOUT` 中止构建，不按 XML 锚点顺序拼接步骤；普通小图标仍走原图片处理流程。
- 摄取结果记录 `content_revision=ooxml-vml-1`，构建计划及执行 REUSE 时都检查版本，执行时同时检查源哈希与 READY 状态。无当前版本标记的旧结果须重新解析，不能冒用新版本；旧活动索引不因此被修改。该摄取规则为公共基础能力，RAG/Support 都受影响，但两条问答路由没有混用。DC24V 浮动版式正文仍需转换核验，不能宣称已恢复其检索质量。
- 对外 Assistant 请求使用幂等记录与外部用户隔离；终态幂等重放会原子累计 `replay_count` 并刷新最后重放时间，其响应通过 `public_payload` 过滤内部字段。
- Assistant 消息分页从数据库正序返回的最近 `limit+1` 条中取末尾 `limit` 条，以本页最早序号作为 `next_before`；不丢弃最新消息。游标仅接受正的 ASCII 十进制 int32 序号；会话列表仍按倒序取前 `limit` 条，两种分页不可共用截取方向。
- 失败幂等重放使用公开错误字段白名单，保留保存的 `retryable`、请求/会话/消息/Trace 标识与 `partial_content`；新 HTTP 请求的标识在响应头和 `accepted` 中，旧错误快照的 `request_id` 不被覆盖。旧记录缺少布尔重试标志时按已知错误码补默认值，未知错误不建议重试。
- 新失败/取消请求在收口事务中同步关联 Message 的公开错误快照；失败同时同步关联 Trace，终态请求不被重复收口覆盖。历史错误优先使用 Message 快照，再兼容旧 Trace；Trace 详情过期不影响新消息已保存的错误语义。旧历史不批量回填。
- RAG 原文交付不再使用 Token 预算截断元素，完整返回当前索引中已解析的正文、表格和图片引用，保留顺序、重复图片出现次数、标题去重及 `include_images`。同步、流式、Support→RAG 回退共用同一构造；Support 原有完整交付不变。`M3_DOC_DELIVERY_MAX_TOKENS` 仅保留为废弃兼容配置，不再控制交付；LLM Prompt、生成预算与超时不变。此保证不包括摄取前已经缺失的源内容或 DOCX 页面排版。
- `include_images` 默认 `true`。Gateway 在同步/流式直入 RAG 及 Support 回退 RAG 时传递该参数；RAG 在结果保存前过滤图片块及交付来源中的图片 ID，不改变用于检索、重排和文档问答的图片证据。Support 原有图片控制保持不变。新 Assistant 请求保存过滤后的公开终态，历史与幂等重放沿用该快照。
- Assistant 历史消息优先采用 `message.response_payload` 的公开答案和内容块；显式空 `content` 不得回退到内部 `content_blocks`。没有公开内容快照的旧记录才读取内部块，并递归移除 `url`/`download_url`；历史读取和旧终态重放均执行同一地址过滤，不回写原记录、不重新调用模型。修复前已经保存的错误带图结果不会按新规则追溯重算，重新验证需使用新幂等键。
- 对外 Assistant 只列出 `assistant_online=true` 且知识库本身启用的项目；欢迎语、问答、会话和图片入口都执行同一上线边界及知识库归属检查。新知识库默认不对外，迁移前已经启用的知识库保持原可见性。
- Assistant 列表的 `ready` 按知识库当前执行策略使用完整 Gateway 就绪条件计算；Chat 在预留幂等请求前再次执行同一策略就绪检查。已上线但依赖异常的知识库可以保留在列表中，但必须返回 `ready=false`。
- 对外 Assistant 与管理端 DemoChat 的就绪态欢迎语使用同一简洁用户侧文案，不暴露知识库名称、描述或文档数量。Assistant 的三条快捷问题读取当前知识库的 `assistant_suggestions`，与 Profile 标准问题独立维护；DemoChat 现有快捷问题仍从文档目录生成。未就绪时 Assistant 只返回通用准备中文案，DemoChat 保留管理员侧索引/上传指引。欢迎语、列表、管理上线和 Chat 共享 Gateway 的策略就绪判断。
- Assistant Chat 会在创建 `StreamingResponse` 前完成会话权限、知识库及 Active Index 可用性、策略、幂等和同会话并发检查；这些前置失败保持 HTTP JSON 错误，`accepted` 后的生成期失败才使用 SSE `error`。
- Assistant Chat 的已预留请求由专用流响应持有；ASGI 发送失败或客户端断连时会在响应收口阶段幂等地将仍为 `RUNNING` 的请求及其关联消息/Trace 标记为取消，正常终态与幂等重放不会被改写。进程强制终止等无法执行收口的情况仍由遗留请求恢复命令处理。
- Assistant SSE `context` 只允许输出会话、用户消息、Assistant 消息和 Trace 标识，不透传 Index 等内部 Trace 字段；已知短暂上游/超时错误保留可重试语义。
- Assistant 不提供原文下载接口；公开回答中的文档 ID 和标题只用于来源展示。图片接口只允许读取状态为 `STORED` 的所属文档图片，按 64 KiB 分块代理 MinIO，并转发可信的内容类型和可用的对象长度。
- 管理 API 允许修改 `document_source.display_name`，但不修改原始文件名、对象存储键、文档 ID 或索引内容，也不触发索引重建；同一知识库内新增或重命名后的有效显示名称不得重复。后续 RAG/Support 回答从文档源读取最新显示名称，既有 Message、Assistant 幂等终态和 Trace 保持原快照。
- 回答正文中出现文档标题时使用 Markdown `**《文档标题》**`；文档直达回答的 `answer` 只包含该标题，不添加“已找到”或“以下是文档内容”等引导语。按源文档首行必为标题的约定，RAG/Support 文档交付统一无条件删除第一条非空 TEXT 元素中的首条非空行，每份文档只删除一次，不比较显示名称或旧标题前缀；标题独占文本块时也不继续删除下一块。图片、表格及后续正文保持原样，源文档和索引不修改。结构化 `document.title` 和 `references[].title` 保持无 Markdown 的纯文本契约。
- 超过指定时间仍为 `RUNNING` 的 Assistant 请求可通过 `app.assistant.recovery` 预览并事务恢复；恢复会保留请求行，并同步将关联的流式消息和 Trace 标为失败。
- 维护 Worker 启动时及之后每天清理超过 `ASSISTANT_REQUEST_RETENTION_DAYS`、未关联 Conversation 且处于终态的 Assistant 请求；关联会话的请求随 Conversation 级联清理，`RUNNING` 请求只能经正常收口或遗留恢复处理。
- API 密钥可存储为加密密文，密钥轮换由 `app.tasks.key_rotation` 支持；文档只可记录变量名，不能记录值。

## 测试

`backend/tests/` 覆盖 API、网关、RAG、Support、摄取、任务和 PostgreSQL 集成。常规全套校验由根目录 `scripts/check.sh` 编排；以当前配置和实际结果为准，详见 [`environment.md`](environment.md) 与 [`known-issues.md`](known-issues.md)。

## RAG V2 边界与当前实现

- ADR-0019 已停止原 P3 六策略和 P4 `rag_case` 的继续实施。V0～V4 已交付 `app.rag.v2` 及运行时收口：最小 Query Interpreter 只输出独立问题、补充查询、用户条件和范围，不决定回答形式；V2 不调用旧 `_PRECISE_TOKEN_PATTERN` 校验、Query Rewrite、首轮标题召回、焦点路由或 P2/P3 Planner，也不写旧三类 Planner Trace。
- V2 复用文档卡片、Chunk/卡片文档级融合、Rerank 和 `get_document_blocks()`。多查询结果先做 RRF 文档聚合，再以标题、已确认卡片及最多 3 个代表 Chunk 复用现有 Rerank Provider 做文档级排序，最后把前 1～3 份同库、冻结索引候选的有序元素送入 LLM 2；排序超时或协议失败保持原 RRF 顺序。LLM 2 输出答案和图片别名；后端校验文档/图片白名单并映射为公开结构，`references=[]`，不公开 `DOCUMENT_DELIVERY` 或完整原文。文档标题、文件名和普通 D/S/I 技术编号可按回答需要出现在正文，不作为秘密拦截。
- `document_source.status='STORED'` 已统一应用于向量、BM25 和按 Chunk ID 读取，避免已软删除文档成为 V2 全文候选；全文加载还会再次校验知识库归属。V2 准备 Trace 不保存 legacy Chunk Prompt，`selected_context` 只记录候选标识与内容 SHA-256；生成 Trace 记录全文包/答案 SHA-256、数量、预算和耗时，不复制正文。
- 管理试问和 Assistant 的有效 V2 路径均进入 `_generate_v2()`；管理端额外信息只从 Trace 取得。最终答案发布前只检测 Prompt 标记、内部服务主机、未经本轮证据支持的相对管理路径和对象存储字段，证据 D/I key 仍做归属白名单校验；不因标题、文件名、技术编号、产品私网 URL 或普通产品 API 直接失败。IP、版本、带单位数值和端口只写入 `runtime_snapshot.generation.answer.parameter_observations`，按文档、用户输入或无来源分类，不参与回答拒绝或修复。Assistant Facade 以公开字段白名单移除模型用量、检索/重排诊断、候选分数、管理 URL 和 MinIO 位置；欢迎语和外部响应不公开文档总数。
- V2 不恢复 `rag_issue`、不消费待选文档，也不读写 RAG 焦点。`begin_turn` 使用独立的完整轮次选择器：只接受 `message.rag_user_message_id` 或 `assistant_request.user_message_id` 显式关联且双方成功完成的轮次，按最近连续轮次、消息数和 Token 预算裁剪；失败、取消、运行中、无法配对及当前复用轮次排除。历史 Prompt 不携带消息 ID，Trace 只保存关联 ID 与裁剪诊断。共享焦点字段仍被 Support/Gateway 使用，不能删列。
- 开发配置显式 `v2`，代码 `auto` 在 development 解析为 V2，生产示例与 Compose 显式 `legacy`。V4 已停止有效 V2 对旧 Planner 的运行时调用；旧实现仅为回滚保留，V5 才物理删除。具体阶段、删除顺序和门槛见 [V2 执行计划](../technical/rag-full-document-generation-v2-execution-plan.md)。

## 知识库 Assistant 管理

- 管理凭据下新增 `PATCH /api/v1/knowledge-bases/{id}`，请求只含公开 `description` 和必填 `expected_updated_at`；空白说明归一化为 `null`，过期版本返回 409 `KNOWLEDGE_BASE_UPDATE_CONFLICT` 及当前更新时间，删除中拒绝修改。成功返回完整 `KnowledgeBaseResponse`。该操作不创建任务、不改 Active Index/Chunk、不触发重建；外部 Assistant API 没有新增管理入口。
- 管理凭据下提供 `GET /api/v1/knowledge-bases/{id}/assistant-config`（配置及 `ready`/`blockers`）、`PUT` 同路径（`revision`、`suggestions`）和 `PUT /api/v1/knowledge-bases/{id}/assistant-publication`（`revision`、`assistant_online`）。成功写入返回 `knowledge_id`、`assistant_online`、`suggestions` 和新 `revision`；外部 `/assistant/*` 不增加管理接口，公开欢迎响应结构不变。
- 每条推荐问题去除首尾空白后为 4 至 200 字，最多三条，NFKC、忽略大小写/空白及末尾问号后不得重复。下线库可保存 0～3 条，线上库必须保留三条；上线检查完整性和当前执行策略的就绪条件。缺失问题或运行未就绪返回 409，文本校验失败返回 422。
- `AssistantConfiguration` 在同一事务内锁定知识库、检查修订和执行写入；上线就绪检查复用该事务连接。冲突返回 409 `ASSISTANT_CONFIG_CONFLICT`，需重新加载；下线不查询运行就绪，问题保存不隐式切换上线状态。删除中的知识库拒绝配置写入。
- 不建立审批状态机或冻结运行版本；管理员通过管理 Chat 试问后确认上线，修改线上问题立即生效。索引、模型和 Profile 调整后由管理员重新试问；需要检查期间停止对外时先下线。下线阻止后续入口访问，不承诺撤回已经发送或缓存的内容，也不主动中断已建立的流。
- 使用方法：管理台“知识库”→ 点击目标库 → “Assistant 上线与推荐问题”→ 填写并保存 → 分别试问并人工检查 → “检查就绪”→ “确认上线”。正式部署前执行 Alembic `upgrade head` 至 `0043`，再更新后端和前端。


## Assistant 回答优化（管理侧）

- `api/answer_reviews.py` 仅注册到管理认证边界。按知识库提供 `GET /knowledge-bases/{id}/answer-records`（`query`、`marked`、`status`、`limit`、`offset`）、`GET /answer-records/{request_id}`、`PUT /answer-records/{request_id}/review` 和 `POST /answer-reviews/{review_id}/retests`，后三者同样带知识库前缀。对外 Assistant 路径、事件和响应结构不变。
- 列表以 `assistant_request.id` 为单位，不因幂等重放或引擎回退重复计数。详情展示已保存的公开终态响应、前置消息及 Trace 诊断；不读取当前索引来重建历史回答。无问题内容或快照的旧请求明确显示缺失。
- `assistant/reviews.py` 使用独立 `answer_review` 表保存原始快照，问题分类、预期文档、预期表现、备注和三种处理状态 `OPEN/VERIFY/CLOSED`；`ACCEPTABLE` 表示合理回答无需修改。首次保存要求 `revision=0`，后续写入需携带当前修订；并发首次创建与后续修改均串行校验。预期文档必须为同库 `STORED` 文档。
- `assistant_request.input_payload` 仅在新预留请求时保存 `question`、`choice_id`、`include_images`，幂等重放不覆盖；旧数据不猜测补写。问题标记保存该输入及前置消息，不保存管理凭据。
- `assistant/retest.py` 用新内部会话逐轮重问前置问题和当前问题，复用 Gateway 当前配置与真实模型，Trace 来源为 `INTERNAL_CHAT`；不创建外部 Assistant 请求、不复用原会话。每次复测保存期望与标记修订，以及各轮实际响应、索引、运行快照和耗时；人工判断正文，不自动把执行成功当作质量通过或关闭问题。
- 复测请求携带 `run_id` 和 `revision`；相同标识重放已有记录，不重复调用模型，每条问题同时最多一次复测。总时限180秒、最多20轮；上下文缺失拒绝执行，前置澄清目标变化停止续问。中断/失败保存已完成轮次；进程强制终止留下的运行记录在超过5分钟后再次发起复测时收敛为失败。
- 人工保存的问题及复测不随会话、Trace 或源文档删除而丢失；知识库删除会级联清理。仅保存图片资产引用，不延长图片/索引保留期，资源过期时前端明确显示不可用。JSON 用例导出是保存快照，本期尚未接入 Golden 批量执行或自动调参。
