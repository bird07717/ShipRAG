# 核心产品主链路问题修复执行计划

## 1. 目标与范围

本计划修复“添加文档 → 候选索引与 Profile 发布 → 双引擎回答 → 管理台 Trace/日志/错误排查”主链路中已确认的问题。范围覆盖 Profile Support、Generic RAG、Support→RAG 降级、文档候选审阅、运行策略就绪检查和管理台可观测性。

本轮不改变两条引擎的业务边界：

- Profile Support 继续负责 Profile 匹配、完整文档交付、冻结文档版本和基于全文的持续支持。
- Generic RAG 继续负责查询改写、混合检索、重排、上下文生成和引用回答。
- `SUPPORT_WITH_RAG_FALLBACK` 仍只允许 `NO_MATCH` 自动降级。

## 2. 执行顺序

### T1：统一流式回答终态契约

- 所有 RAG `done` 事件必须返回 `trace_id`、`conversation_id`、`message_id` 和 `index_id`。
- 网关必须据此持久化 Support→RAG 父子 Trace，并在最终 `done` 中返回 fallback 元数据。
- 测试必须使用与真实 RAG 相同的 SSE 终态字段，防止测试替身掩盖契约漂移。

验收：流式降级后 RAG Trace 包含 `parent_trace_id/fallback_from/fallback_reason`，客户端最终事件可关联 Trace。

### T2：补齐流式错误落库与关联信息

- 在 Trace 创建之前发生的问答错误也必须写入 `request_error_log`。
- 网关和强制 Support/RAG 流式入口不得只吞掉异常并返回匿名错误。
- SSE `error` 返回 `code`、`message`、`request_id`，有 Trace 时同时返回 `trace_id`。
- 前端保留结构化错误信息，至少向用户展示错误码和 Request ID。

验收：索引未发布、Prompt/LLM/Rerank 未配置等错误，可以在 Request Errors 页面按 Request ID 定位；已创建 Trace 的错误仍进入对应引擎 Errors 页面。

### T3：消除降级产生的重复业务消息

- Support 的内部 `NO_MATCH` 探测和 RAG 正式回答仍分别保留 Trace。
- 对外会话历史中，一次用户请求只能形成一组可见 USER/ASSISTANT 消息。
- RAG 生成历史不得包含本次内部 Support NO_MATCH 消息，也不得重复包含本次用户问题。

验收：首次降级后会话消息数增加 2，Trace 数增加 2；RAG 上下文不包含内部探测消息。

### T4：支持候选索引内容审阅

- 文档详情允许在 Active Index 与当前候选 Index 之间明确选择。
- 从发布工作区检查新文档时默认使用当前候选 Index。
- 页面明确显示正在检查的索引版本，禁止静默回退导致“0 Elements/0 Chunks”。

验收：候选发布前可查看新文档的 Elements、Chunks、图片处理结果和实际 `index_id`。

### T5：把运行策略就绪校验下沉到后端

- Support 策略检查已发布可用 Profile；需要持续问答时检查 LLM。
- RAG 策略检查 Active Index、Prompt、LLM、Rerank 和查询 Embedding 兼容性。
- 策略读取接口返回结构化 readiness；策略更新接口拒绝不可运行的配置。
- 前端只展示后端 readiness，不自行猜测完整运行条件。

验收：管理台不能保存一条确定无法执行的策略；接口返回具体 blocker。

### T6：关闭 Profile 线上直接写入旁路

- 删除或禁用公开的 Problem Profile 直接 PUT 写入路径。
- 删除前端遗留的直接保存方法与 API 客户端方法。
- 线上 Profile 变更只允许经候选草稿审核和原子发布。

验收：不存在绕过 Knowledge Release 修改线上 Profile 的可调用入口。

### T7：修正 Support 错误阶段

- Support 在 Profile 路由、文档加载、LLM、证据校验、风险门禁和工作流阶段更新 Trace stage。
- 所有失败记录携带最终失败阶段，管理页不再用初始阶段冒充失败阶段。

验收：Support Errors 中的阶段与实际失败位置一致。

### T8：统一日志筛选口径

- 知识库筛选只应用于具备 `kb_id` 的 Trace；全局 Request Errors 必须在接口和页面明确标注为全局口径。
- “失败轮次”同时展示 Support/RAG 时明确标注引擎，不再描述成仅技术支持轮次。

验收：页面不会把知识库级和全局统计呈现为同一筛选范围；失败轮次能区分引擎。

## 3. 验证要求

- 为每项后端行为增加或更新单元测试/接口测试。
- 为候选索引检查、结构化流错误、readiness 和日志口径增加前端测试。
- 执行后端 Ruff、Mypy、相关 Pytest；执行前端 type-check、lint、Vitest。
- 若本地服务可用，再执行真实数据库/容器主链路冒烟；不可用时明确记录未执行项。

## 4. 完成记录

执行过程中按以下状态更新：`TODO`、`IN_PROGRESS`、`DONE`、`BLOCKED`。

| 任务 | 状态 | 结果 |
|---|---|---|
| T1 流式终态契约 | DONE | 三类 RAG `done` 均返回 Trace/Index；网关真实流可持久化 fallback 关系。 |
| T2 流式错误落库 | DONE | 三个 SSE 入口统一观察错误、写入 Request Error，并向客户端补充 Request ID。 |
| T3 降级消息去重 | DONE | RAG 复用 Support 探测消息对，生成历史排除本轮探测消息，两条 Trace 保持独立。 |
| T4 候选索引审阅 | DONE | 文档抽屉显式选择候选/Active Index，展示实际索引 ID 和完整 Element/Chunk 数据。 |
| T5 后端就绪校验 | DONE | Policy API 返回 Support/RAG readiness，更新策略时由后端拒绝未就绪配置。 |
| T6 关闭 Profile 写旁路 | DONE | 删除 PUT 路由、Repository 写方法及前端遗留 API/Store 方法。 |
| T7 Support 错误阶段 | DONE | 运行中更新阶段，失败时记录 Profile、文档、LLM、校验或工作流实际阶段。 |
| T8 日志筛选口径 | DONE | 汇总返回 scope，页面标注请求错误为全局，并在失败轮次显示引擎。 |

## 5. 执行验证结果

- 后端完整测试：346 passed，3 skipped，覆盖率 70.93%。
- 前端 Vitest：89 passed。
- 后端 Ruff、Mypy：通过。
- 前端 TypeScript type-check、ESLint、生产构建：通过。
- Docker 中 PostgreSQL、Redis、MinIO 均为 healthy。
- 新增 PostgreSQL 降级消息复用集成测试；当前执行环境虽可读取 Docker 状态，但测试进程连接宿主机映射端口时被运行沙箱阻止并超时，因此该用例在普通无环境变量测试中按设计 skip，需在 CI 或宿主机执行。

## 6. 重新沿核心链路检查后的追加修复

| 问题 | 状态 | 最小影响修复 |
|---|---|---|
| R1 构建接口仍接受自动激活参数，可绕过 Release | DONE | 构建契约只接受 `false`，重试接口固定生成候选索引，Repository 默认值同步收紧。 |
| R2 上传已落库但首次队列投递失败后，幂等重试不再投递 | DONE | 复用原 Index/Task，仅对 `QUEUE_UNAVAILABLE` 精确恢复并重新投递；前端同一文件重试复用 Idempotency-Key。 |
| R3 Support→RAG 成功降级后，幂等重放返回 Support 的 `NO_MATCH` | DONE | 在既有 `support_idempotency.response` 中保存网关最终响应，普通与流式重放均直接返回已完成的 RAG 结果。 |
| R4 降级的两条 Trace 同时指向一条可见消息，父子关系写入过晚 | DONE | RAG 开始时在同一事务复用消息对、解除 Support Trace 的消息归属并写入父子关系；失败后的同键重试可复用失败消息。 |
| R5 首次完整文档交付失败被标成文档加载阶段 | DONE | 直接交付进入独立 `DOCUMENT_DELIVERY` 阶段，错误码映射不再覆盖实际阶段。 |
| R6 RAG 非预期异常只记录通用错误，管理页缺少根因 | DONE | 所有非预期异常在 Trace 中保存截断后的异常类型与信息，客户端仍只收到安全的通用提示。 |
| R7 日志页会把 Top-N 分组当总数，加载失败又显示为空数据 | DONE | 总数改用按日完整聚合；列表、详情和抽屉均显示错误/加载状态并清理跨筛选旧数据。 |
| R8 Trace 页不能明确区分双引擎及查看降级关系 | DONE | 列表与详情展示引擎，RAG 降级 Trace 展示父 Trace、原因和跳转；请求失败不再伪装成空列表。 |

追加修复没有新增发布旁路、队列表或幂等表；均复用现有 Release、Task、Trace、Message 和 `support_idempotency` 数据模型。
