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
| RAG | `backend/app/rag/v2/`、`services/rag.py` | 两阶段 LLM、混合检索、文档级重排、隐藏全文上下文、答案/图片校验、会话和 Trace 持久化。 |
| Support | `backend/app/support/`、`document_profiles/` | 已冻结的兼容链路；保留 Profile、聚焦工作流、风险/证据校验、文档交付与幂等，不参与当前 RAG 运行。 |
| 文档摄取 | `backend/app/ingestion/` | DOCX/PDF 安全校验、解析、图片理解、分块、嵌入及索引版本构建。 |
| 发布 | `backend/app/releases/` | Support Profile 草稿审核、发布、回滚与与 RAG 索引分离的生命周期。 |
| Worker | `backend/app/worker.py`、`tasks/` | RQ 消费、索引构建、维护、回收、孤儿任务恢复和保留期清理。 |
| 可观测性 | `observability/`、`rag/metrics.py` | 请求错误、问答 Trace、性能与质量指标。 |

## 关键调用链

### 文档入库与索引

1. 管理 API 接收知识库、文档和构建请求；原始对象存入 MinIO，元数据写入 PostgreSQL。
2. 请求创建 `task_record` 并投递 Redis 队列。
3. `index_build` Worker 使用摄取管线按文档格式校验/解析 DOCX 或 PDF（PDF 会剔除跨页重复的页眉/页脚）、处理图像、分块并生成 1024 维嵌入。
4. 结果写入按版本隔离的索引表；成功构建可激活 RAG 索引。构建末尾会以独立维护任务为该冻结索引生成 RAG 文档理解卡片，失败不回滚已完成的技术索引；Support Profile 的审核发布仍是独立生命周期。

### 问答

1. `/api/v1/chat*` 或 `/api/v1/assistant/chat/stream` 接收请求；后者使用独立服务凭据并要求 `X-External-User-ID`。
2. 对外 Assistant 先检查知识库独立的 `assistant_online` 发布状态、知识库/会话归属、当前策略完整就绪态，以及活动索引内全部有效文档的 RAG 文档理解是否已人工确认；未上线或未完成确认的知识库不能进入问答。随后 Gateway 读取知识库的 `engine_runtime_policy` 和会话模式：`RAG_ONLY` 直入 RAG；`SUPPORT_WITH_RAG_FALLBACK` 先入 Support。
3. RAG 只有 V2 主路径：准备阶段冻结轮次和运行参数；纯寒暄等确定性门禁在模型前短路。其余输入由 LLM 1 根据当前问题、知识库产品档案和最近完整技术轮次判断产品范围、任务关系、查询及二值 `response_path`。`KNOWLEDGE` 保持原链路：一次批量 Embedding 后，以冻结索引的 Chunk/文档卡片聚合文档，RRF 候选经 Rerank Provider 做一次文档级排序，将前 1～3 份完整有序元素作为隐藏上下文交给技术 LLM 2；`CONVERSATION` 跳过 Embedding、检索、Rerank 和文档加载，只把当前消息、公开产品档案及最近一轮传给受约束的对话 LLM 2，用于确认、致谢、结束、能力介绍或改写既有回答。混合、需要新事实/判断/步骤或不确定的输入必须走 `KNOWLEDGE`；LLM 1 缺失/无效路由及路由与查询字段冲突也保守回退 `KNOWLEDGE`。LLM 1 的 429/5xx/连接类即时失败最多快速重试一次，完整超时不重试；超时或不可用时，仅有最近完整成功轮次且当前是省略对象的短追问才确定性继承上一轮用户任务和已用文档，新任务继续使用原问题。LLM 1 仍是范围决定的唯一来源，服务端确定性收口两阶段冲突且不增加第三次调用。公开结果不交付原文、候选列表、来源元数据或文档总数。旧首轮、焦点、Query Rewrite、双 Planner 和六策略实现已删除；Support 代码冻结保留，但当前运行策略为 `RAG_ONLY`。
4. 策略允许且 Support 返回指定回退类型时，Gateway 复用消息上下文调用 RAG，并建立父子 Trace 关联。
5. 内部管理聊天流（经 Gateway）按 SSE 发送 `status`、`trace`、`message`、可选 `fallback` 和终态 `done`/`error`。对外 Assistant 流不是原样透传：它先发送 `accepted`，随后按需要发送 `progress`、`context`、`delta`，并以 `complete` 或 `error` 结束；上游长时间无帧时每 15 秒发送 SSE 注释心跳 `: ping`。两套接口的字段和事件名不能混用。

### 可观测性与保留

请求失败可写入 `request_error_log`，问答写入 `rag_trace`；定时维护清理过期会话、Trace 细节、错误日志，以及没有关联 Conversation 的 Assistant 终态请求。默认保留期由环境配置决定，当前代码默认会话 7 天、Assistant 终态孤儿请求 7 天、完整 Trace 7 天、Trace 摘要 30 天、错误日志 30 天；部署配置可覆盖，`RUNNING` Assistant 请求不由保留期任务删除。

## 架构约束

- 管理 API 与对外 Assistant API 不能共用凭据边界；后者只暴露窄化契约。
- 知识库后台可维护状态、RAG/Support 发布状态和 Assistant 对外上线状态彼此独立；新知识库默认不对 Assistant 上线。管理台按库保存推荐问题并确认上下线，使用管理凭据和配置修订检查；Assistant 欢迎接口只消费已上线库的保存值，保持外部契约。
- 聊天会话会冻结使用的索引/发布版本，避免在线版本切换改变既有会话语义。
- RAG 索引自动激活与 Support Profile 人工审核发布彼此独立。
- RAG 文档卡片绑定知识库、冻结索引和文档，使用索引冻结的 Embedding 模型；显示名称查询时联接 `document_source`。V2 使用有效卡片参与文档级召回；Assistant 上线和持续对外可用还要求活动索引中每份 `READY/STORED` 文档均存在当前结构版本、生成成功且人工确认为 `CONFIRMED` 的卡片。该门禁不改变管理 Chat 的内部调试能力。
- 生产运行时容器采用只读根文件系统与 `/tmp` tmpfs；部署细节见 [`environment.md`](environment.md)。

## RAG V2 实施状态

ADR-0019 与 [RAG 隐藏全文双 LLM V2 执行计划](../technical/rag-full-document-generation-v2-execution-plan.md) 已取代继续收口原 P3/P4 的方向。V0～V5 已在本地实现“独立完整轮次历史 → LLM 1 还原问题/任务关系、确定产品范围和补充查询 → 单次批量 Embedding 的 Chunk/卡片聚合 → 文档级 Rerank → 加载有界完整候选文档 → LLM 2 统一生成答案并选图 → 服务端范围一致性与公开边界校验”。知识库名称和公开说明是可信产品档案；文档是产品专有事实的优先证据而不是回答许可证。LLM 2 会区分主任务与补充证据，补充文档不能替代主任务文档明确要求的并列、前置或后续动作；图片内部别名由服务器确定性转换为有序结构化图片，成组或异常标记不会进入公开正文。完整文档作为用户不可见上下文，不再直接交付给用户；管理试问和 Assistant 共用 V2 生成终态，有效 V2 已旁路旧首轮、Query Rewrite、焦点/待确认状态、legacy 预检索和 P2/P3 Planner，并停止旧 Planner 任务和 Trace 写入。标题、文件名、正常技术编号、产品私网 URL 和普通产品 API 可作为技术正文；严格隐藏的是文档总数、Prompt、候选诊断、对象存储位置与未经证据支持的内部服务/管理信息。参数来源只做 Trace 观测，不作为硬失败。

V5 已在本地完成：RAG V2 成为唯一实现，旧模式开关和旧运行代码已删除，回滚必须使用 Git 提交或旧镜像，不能再通过环境变量切换。Support 的焦点工作流、Profile 和文档交付被冻结但保留；V2 `begin_turn` 只读取显式关联、成功完成的最近问答轮次，不读取旧焦点或 `chat_context`，并保持共享数据库状态原样。共享 `conversation.focus_*` 等字段不得在 Support 解耦前删除。
