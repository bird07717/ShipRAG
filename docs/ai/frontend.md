# 前端认知

## 技术栈与入口

- Vue 3.5、TypeScript、Vite 7、Element Plus；入口为 `frontend/src/main.ts`。
- 测试使用 Vitest、Vue Test Utils、jsdom；ESLint 与 `vue-tsc` 提供静态检查。
- Vite 别名 `@` 指向 `frontend/src`。开发服务器监听 `127.0.0.1:5173`，将 `/api` 与 `/health` 代理到 `127.0.0.1:18000`。

## 目录和职责

| 位置 | 职责 |
| --- | --- |
| `src/App.vue` | 应用壳、侧边菜单与基于 URL hash 的导航兼容层。 |
| `src/components/AdminStudio.vue` | 分发除 `support-chat` 外的管理视图组件。 |
| `src/views/` | 管理页面：概览、知识库、文档、索引、引擎路由、问答测试、Profile 审核、日志、配置与调试台。 |
| `src/components/` | `DemoChat`、`HealthPanel`、管理容器及可复用 UI 组件。 |
| `src/composables/` | 模块级响应式状态：`adminStore`、`ragAdminStore`、`engineRoutingStore`。 |
| `src/api/` | Fetch 封装及管理、聊天、健康检查的请求协议。 |
| `src/types/` | 前端接口类型。 |
| `src/utils/markdown.ts` | Markdown 渲染辅助；`styles/` 保存全局 token 和基础样式。 |
| `tests/` | API 适配、状态、视图、组件和交互测试。 |

## 页面与路由

没有 Vue Router。`App.vue` 维护 `#/view?query`，未知路径回落到 `#/dashboard`；旧路径会映射到当前视图。主视图键包括：

- `dashboard`、`knowledge`、`documents`、`indexes`
- `engine-routing`、`support-chat`、`support-review`
- `logs`、`configuration`、`playground`

`support-chat` 是例外：`App.vue` 直接渲染 `DemoChat.vue`，不经过 `AdminStudio`。其余管理视图的精确分发以 `components/AdminStudio.vue` 为准。修改链接、深链或新增页面时，应更新 `App.vue` 的菜单/允许键；新增管理视图还应更新 `AdminStudio` 映射和相关测试，新增聊天类特例则应在 `App.vue` 明确处理。

## 状态与接口约定

`adminStore` 是模块级 `reactive` 单例，跨视图保持选中的知识库、文档、索引、Trace、模型和任务状态；选择的知识库 ID 使用 `localStorage`，跟踪任务使用 `sessionStorage`。`engineRoutingStore` 管理知识库运行策略；`ragAdminStore` 服务于 RAG 配置界面。

配置页的 LLM Provider 可选择智谱、硅基流动或千问（阿里云百炼 / DashScope）。选择 DashScope 会填入北京地域共享 OpenAI 兼容地址；管理员仍可按业务空间或地域修改 Base URL。模型身份或密钥变更必须先通过后端连通性探测，保存后从下一轮管理 DemoChat 和 Assistant 请求共同生效。2026-09-15 起 Embedding、Rerank、OCR、Vision 的编辑对话框同样提供 DashScope 选项（列表由后端 `supported_providers` 返回），Embedding 身份变更仍走全量重建确认。

知识库文档页可通过管理 API 修改文档显示名称；成功后就地更新列表和已打开的文档详情，不提交索引构建。原始文件名仍只读保留，用于管理员识别源文件。

文档上传抽屉支持一次多选 .docx / .pdf 文件：入队前做客户端预检（扩展名、大小上限与后端 `m2_docx_max_bytes` / `m2_pdf_max_bytes` 保持同步、选择内按文件名+大小+修改时间去重），队列逐份顺序上传且 `request_build=false`，队列结束后统一触发一次索引构建（复选框可关，复用 `store.buildIndex` 的任务跟踪）。服务端失败的条目保留在队列中可重试；客户端预检拒绝（`REJECTED`）的条目不可重试仅可移除。状态类名为小写（`upload-item--done/failed/rejected/uploading`），CSS 选择器必须同大小写。生成幂等键使用 `utils/uuid` 降级助手，不得直接调用 `crypto.randomUUID`（HTTP 环境不存在，见 known-issues）。

文档页显示当前活动索引的 RAG 文档理解覆盖/确认状态，可提交全索引补齐任务。任务提交后立即显示排队状态，并通过既有 3 秒任务轮询更新进度、成功/失败/总数及终态错误；部分失败使用警告而不是成功提示。全量按钮始终绑定活动索引，文档详情中的单卡生成才绑定当前检查索引。详情“RAG 文档理解”页签支持生成、编辑结构字段、确认/拒绝和显式重新生成已确认卡片；保存与审核使用 `revision` 防止旧页面覆盖。图片存在标志来自索引且只读；这套管理状态与“Profile 与别名”页签分离，当前不会直接改变在线 RAG 回答。

文档页对最近构建中被熔断跳过的文档（`last_build_status === "SKIPPED"`，ADR-0022）显示顶部告警：文件名 + 构建版本 + 失败原因，提示修复或替换源文件后重新上传并触发构建。索引构建详情页在索引失败时从任务 `metadata.current_document` 显示"失败时正在处理文档"，用于定位问题文档（不要用批量失败标记判断，见 backend.md）。

知识库详情可编辑“知识库说明 / RAG 专业范围（Assistant 对外可见）”。保存携带详情当前 `updated_at` 做乐观并发控制，成功后按知识库 ID 替换列表和详情；冲突或失败保留未保存草稿。在线知识库保存前显示影响确认；说明最多 5,000 字，空白保存为 `null`，界面明确禁止填写 Prompt、阈值、密钥、内部地址或仅管理员可见内容。修改不会触发索引构建。

`src/api/admin.ts` 通过 `VITE_API_BASE_URL`（缺省同源）请求管理 API，并从 `sessionStorage` 的 `rag_service_token` 读取 Bearer Token。`src/api/chat.ts` 手动解析 SSE，支持 `status`、`trace`、`message`、`fallback`、`done`、`error` 事件。不要将服务令牌或任何真实密钥写入源码、测试夹具或认知文档。

除凭证配置页外，`AdminStudio` 在当前浏览器会话没有 Service Token 时不挂载管理视图，也不启动索引/任务轮询；`App.vue` 对独立的问答测试页执行相同门禁。管理 API 返回 401 时会标记凭证失效、卸载管理视图并停止后续自动请求。保存新凭证后解除该状态并恢复加载，避免无凭证或失效凭证持续请求造成认证错误刷屏。后端仍必须对每次实际到达的未授权请求记录和返回 401，前端门禁不是认证边界。

知识库列表展示 Assistant 的“已上线/未上线”状态；详情中的 `KnowledgeAssistantSettings` 按知识库编辑三条推荐问题、试问、检查运行就绪并确认上线或下线。保存线上问题立即生效；未上线时可保存不完整问题，上线必须三条完整且不重复。试问复用管理 Chat，每次新会话，不需要对外上线，但要求问题非空且当前运行就绪。禁用按钮通过提示说明未就绪、问题为空或请求进行中的原因；“检查就绪”仅刷新运行状态，保留未保存问题及原配置修订号，“重新加载配置”才重新读取保存值。编辑组件以知识库 ID 为 key，关闭时取消试问并忽略过期响应；写请求携带配置修订号，成功后按返回的知识库 ID 更新列表及详情，不使用异步完成时的全局选中值。配置写入即使在详情关闭后完成仍按 ID 更新列表；列表加载比较 `assistant_config_revision`，不让旧响应覆盖更新后的上线状态。索引构建产生 `READY` 候选，不自动生效；候选索引通过已有激活接口明确设为当前 RAG，`DEPRECATED` 历史索引才使用回滚操作。

## 开发约定

- 使用 TypeScript 严格类型；对 API 契约改动应同步 `types/`、适配层、视图与测试。
- UI 文字主要为中文；复用 `components/ui/`、设计 token 和 Element Plus，而非复制样式。
- API/Store/工具核心逻辑维持较高覆盖率；当前覆盖率门槛定义在 `vite.config.ts`，按目录分别计算。
- 可用命令见 [`environment.md`](environment.md)。


## Assistant 回答优化与文档预览

- 概览的“查看回答与优化”跳转 `#/logs?tab=assistant-answers`；日志新增“Assistant 回答优化”，必须选择具体知识库。`AnswerReviewsView.vue` 提供问题搜索、分页、仅已标记及处理状态筛选。
- `AnswerReviewDetail.vue` 显示原公开响应、前置对话、候选文档、诊断、问题标记、单题复测与 JSON 快照导出。保存成功后刷新列表；保存冲突保留输入，要求重新加载。复测按钮要求标记已保存且上下文完整；结果显示对应标记版本、原回答和新回答，文档匹配只作辅助提示。
- `AnswerContent.vue` 渲染正文、表格、图片、引用、澄清及建议；Markdown 经 DOMPurify 消毒。`ReviewImage.vue` 按可见范围用管理凭据读取图片并释放 Blob URL，不使用外部响应中的任意 URL；资产过期显示不可用。
- 文档详情增加 `DocumentPreview.vue`，按所选索引顺序展示解析文本、表格和图片，保留首行以便核对，原元素/切片 JSON 入口继续保留。回答详情的引用检查使用历史索引；索引缺失时提示去文档页选版本，不静默替换为当前索引。
- 记录视图和详情按知识库/请求 ID 绑定；换库取消旧列表读取并忽略晚到结果，关闭详情后忽略晚到的读写/复测结果。复测的服务端结果仍保留，可重新打开查看；测试流量不混入外部 Assistant 记录。

## V5 后管理台与 RAG V2 对齐（2026-09-16）

侧边栏按 RAG 优先重组为：概览 / 知识管理（含索引管理）/ 问答与调优（问答测试、引擎路由、调试台）/ 运维 / 遗留能力（Support · 已冻结，仅 `support-review`）。视图键（hash）未变，旧深链映射不受影响；概览横幅改为"RAG V2 知识发布"。

- `EngineRoutingView` 默认策略为 `RAG_ONLY`，`SUPPORT_WITH_RAG_FALLBACK` 选项与 Support 就绪卡片标注"已冻结（ADR-0021）"但保留可用（后端网关仍可执行该策略，前端不单独禁用）。
- `RagConfigurationView` 只保留 `vector_top_k`、`bm25_top_k`、`fusion_top_k` 三个 V2 实际消费的参数；`rerank_top_n`、`context_max_chunks` 已从界面移除（后端 PATCH 为部分更新，兼容缺省）。Rerank 数量与全文上下文预算由环境变量控制，界面只作说明。
- `DemoChat` 的模式标签收敛为 V2 五种 `answer_mode`；legacy 模式（`DOCUMENT_EXPLANATION`、`STEP_GUIDANCE` 等）与 `DOC_DELIVERED` 专用渲染分支已删除，未知值仍按原文兜底显示。
- `PlaygroundView` 结果面板按 V2 文档级语义展示：`selected_context` 渲染为"隐藏上下文候选文档"（document_key + 内容 SHA-256 前缀），不再按 chunk 分数/正文渲染；`SourceCitation`、`RetrievalChunk` 组件因此删除。`playgroundOptions` 已移除 `rerank_top_n`。
- `TracesView` 时间线包含 V2 各阶段耗时（`v2_query_interpreter_ms` 等）并保留 legacy 键（标注"遗留"，按存在与否自过滤）；详情新增"RAG V2 生成详情"段（查询理解、文档级重排、检索降级、隐藏上下文、合规修复），Support 决策段标注"遗留"；Prompt 折叠面板仅在存在时显示。
- `LogsView` 的"升级记录"页签标注"（遗留）"并说明其仅来自已冻结 Support 链路，保留历史查询能力。
- `SupportReviewView` 顶部有 ADR-0021 冻结横幅，发布/重生成流程保留；`DocumentsView` 上传与空态文案不再宣传 Support Profile 审核，改为说明 RAG 文档卡片自动生成。
