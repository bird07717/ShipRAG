# 前端认知

## 技术栈与入口

- Vue 3.5、TypeScript、Vite 7、Element Plus；入口为 `frontend/src/main.ts`，Element Plus 组件经 `src/elementPlus.ts` 按需注册（见下文“加载、轮询与 URL 状态”）。
- 测试使用 Vitest、Vue Test Utils、jsdom；ESLint 与 `vue-tsc` 提供静态检查。
- Vite 别名 `@` 指向 `frontend/src`。开发服务器监听 `127.0.0.1:5173`，将 `/api` 与 `/health` 代理到 `127.0.0.1:18000`。

## 目录和职责

| 位置 | 职责 |
| --- | --- |
| `src/App.vue` | 应用壳、侧边菜单与基于 URL hash 的导航兼容层。 |
| `src/components/AdminStudio.vue` | 分发除 `support-chat` 外的管理视图组件。 |
| `src/views/` | 管理页面：概览、知识库、文档、索引、引擎路由、问答测试、会话浏览、Profile 审核、日志、回答复核（日志内页签）、配置与调试台。 |
| `src/components/` | `DemoChat`、`HealthPanel`、管理容器及可复用 UI 组件。 |
| `src/composables/` | 模块级响应式状态：`adminStore`、`ragAdminStore`、`engineRoutingStore`、`taskCenterStore`（全局后台任务）。 |
| `src/api/` | Fetch 封装及管理、聊天、健康检查的请求协议。 |
| `src/types/` | 前端接口类型。 |
| `src/utils/markdown.ts` | Markdown 渲染辅助；`styles/` 保存全局 token 和基础样式。 |
| `tests/` | API 适配、状态、视图、组件和交互测试。 |

## 页面与路由

没有 Vue Router。`App.vue` 维护 `#/view?query`，未知路径回落到 `#/dashboard`；旧路径会映射到当前视图。除 Dashboard 外的视图与 `DemoChat` 为 `defineAsyncComponent` 异步分包。主视图键包括：

- `dashboard`、`knowledge`、`documents`、`indexes`
- `engine-routing`、`support-chat`、`support-review`
- `conversations`（运维分组的会话浏览）、`logs`、`configuration`、`playground`

`support-chat` 是例外：`App.vue` 直接渲染 `DemoChat.vue`，不经过 `AdminStudio`。其余管理视图的精确分发以 `components/AdminStudio.vue` 为准。修改链接、深链或新增页面时，应更新 `App.vue` 的菜单/允许键；新增管理视图还应更新 `AdminStudio` 映射和相关测试，新增聊天类特例则应在 `App.vue` 明确处理。

## 状态与接口约定

`adminStore` 是模块级 `reactive` 单例，跨视图保持选中的知识库、文档、索引、Trace、模型和任务状态；选择的知识库 ID 使用 `localStorage`，跟踪任务使用 `sessionStorage`。`engineRoutingStore` 管理知识库运行策略；`ragAdminStore` 服务于 RAG 配置界面。

`adminStore.loadSystemInfo()` 使用模块级请求序号，首次加载与 Dashboard 手动刷新共用同一保护；只有最新发起的请求可更新版本，过期成功或失败均忽略。最新请求失败仍清空为未知，不回退旧请求结果。`LogsView` 的失败列表同样按请求序号和知识库 ID 校验成功、错误及加载终态；新查询清空旧行，换库、更新深链、离开失败页签或组件卸载使旧请求失效。页签进入时不重复发起已在加载的失败列表请求。这是响应提交保护，不取消网络请求、不新增轮询，也不扩展到其他日志查询。

配置页的 LLM Provider 可选择智谱、硅基流动或千问（阿里云百炼 / DashScope）。选择 DashScope 会填入北京地域共享 OpenAI 兼容地址；管理员仍可按业务空间或地域修改 Base URL。模型身份或密钥变更必须先通过后端连通性探测；同一部署/数据库内，LLM 保存后从下一轮管理 DemoChat 和 Assistant 请求共同生效，既有会话不冻结 LLM。2026-09-15 起 Embedding、Rerank、OCR、Vision 的编辑对话框同样提供 DashScope 选项（列表由后端 `supported_providers` 返回），Embedding 身份变更仍走全量重建确认，不能将所有模型都描述为下一轮即时生效；OCR/Vision 实际身份变化后，下一次构建不再复用旧解析内容，具体边界见 `backend.md`。

模型编辑弹窗按编辑代次、模型 ID 和凭证绑定异步提交；关闭、重新打开、凭证变化或卸载使旧回调失效。清密钥/保存期间锁定草稿并防止重复提交；Embedding 等待确认后再次校验弹窗身份才提交。已发出的写请求不取消，关闭弹窗不等于撤回服务端操作。“测试已保存配置”仅调用既有模型 ID 探测，不测试草稿；草稿变化清除连通徽标，并使旧探测结果失效，连通性不代表结构化回答质量通过。

模型保存/清密钥返回本次 `ModelActionResult`，弹窗据此关闭或显示错误，不再用共享 `store.error` 判断成功。仅模型写操作使用 `runModelAction`：保留原 `run()` 的加载计数，但按模型操作序号与凭证保护全局错误/成功提示，不让较早的模型操作覆盖较新操作的状态；其他管理操作的 `run()` 行为未改。模型列表首次加载、保存与密钥写入/清除统一调用 `loadModels()`，按列表请求序号和凭证提交；过期成功/失败忽略，不以旧响应补填，不新增轮询或取消服务端写操作。最新的列表加载失败：首次加载仍抛错并显示错误；但保存/写入/清除密钥在服务端已成功后经 `refreshModelsAfterWrite()` 刷新，刷新失败只把成功提示降级为 `warning`（文案附“刷新模型列表失败（原因），页面可能仍显示旧配置，请手动刷新”，Embedding 重建提交数仍保留在文案中），结果为 `{ ok: true, listStale: true }` 而不是失败：保存弹窗照常关闭，清除密钥弹窗也关闭（避免显示过期的密钥状态），不留 `store.error`，管理员不会因此重复提交并再次触发全量重建确认。

索引模型提示按类型区分：Embedding 同时用于查询和构建，身份切换须通过 1024 维校验并为启用且有有效文档的库提交重建，切换可能影响 Assistant 就绪；新索引记录实际 Provider/模型/端点/维度，查询及卡片生成拒绝身份不匹配，需核对活动索引、文档理解审核并用新会话验收，不承诺无中断。OCR/Vision 使用 Worker 开始时的快照，下一次构建按实际模型指纹控制缓存失效；身份不变才可复用，旧文档无指纹首次重建重新解析。停用数据库配置仍可能回退环境模型，生效看实际身份而非表单标签。保存提示不将提交重建写成重建完成。

知识库文档页可通过管理 API 修改文档显示名称；成功后就地更新列表和已打开的文档详情，不提交索引构建。原始文件名仍只读保留，用于管理员识别源文件。

文档上传抽屉支持一次多选 .docx / .pdf 文件：入队前做客户端预检（扩展名、大小上限与后端 `m2_docx_max_bytes` / `m2_pdf_max_bytes` 保持同步、选择内按文件名+大小+修改时间去重），队列逐份顺序上传且 `request_build=false`，队列结束后统一触发一次索引构建（复选框可关，复用 `store.buildIndex` 的任务跟踪）。服务端失败的条目保留在队列中可重试；客户端预检拒绝（`REJECTED`）的条目不可重试仅可移除。状态类名为小写（`upload-item--done/failed/rejected/uploading`），CSS 选择器必须同大小写。生成幂等键使用 `utils/uuid` 降级助手，不得直接调用 `crypto.randomUUID`（HTTP 环境不存在，见 known-issues）。

文档页显示当前活动索引的 RAG 文档理解覆盖/确认状态，可提交全索引补齐任务。任务提交后立即显示排队状态，并通过既有 3 秒任务轮询更新进度、成功/失败/总数及终态错误；部分失败使用警告而不是成功提示。全量按钮始终绑定活动索引，文档详情中的单卡生成才绑定当前检查索引。详情“RAG 文档理解”页签支持生成、编辑结构字段、确认/拒绝和显式重新生成已确认卡片；保存与审核使用 `revision` 防止旧页面覆盖。图片存在标志来自索引且只读；这套状态与“Profile 与别名”页签分离，但活动索引中每份有效文档全部确认是 Assistant 上线和持续可用的必要条件。

文档页对最近构建中被熔断跳过的文档（`last_build_status === "SKIPPED"`，ADR-0022）显示顶部告警：文件名 + 构建版本 + 失败原因，提示修复或替换源文件后重新上传并触发构建。索引构建详情页在索引失败时从任务 `metadata.current_document` 显示"失败时正在处理文档"，用于定位问题文档（不要用批量失败标记判断，见 backend.md）。

知识库详情可编辑“知识库说明 / RAG 专业范围（Assistant 对外可见）”。保存携带详情当前 `updated_at` 做乐观并发控制，成功后按知识库 ID 替换列表和详情；冲突或失败保留未保存草稿。在线知识库保存前显示影响确认；说明最多 5,000 字，空白保存为 `null`，界面明确禁止填写 Prompt、阈值、密钥、内部地址或仅管理员可见内容。修改不会触发索引构建。

`src/api/admin.ts` 通过 `VITE_API_BASE_URL`（缺省同源）请求管理 API，并从 `sessionStorage` 的 `rag_service_token` 读取 Bearer Token。`src/api/chat.ts` 手动解析 SSE，支持 `status`、`trace`、`message`、`fallback`、`done`、`error` 事件。不要将服务令牌或任何真实密钥写入源码、测试夹具或认知文档。

除凭证配置页外，`AdminStudio` 在当前浏览器会话没有 Service Token 时不挂载管理视图，也不启动索引/任务轮询；`App.vue` 对独立的问答测试页执行相同门禁。`api/serviceCredential.ts` 是凭证存储、拒绝事件和 `ApiRequestError` 类型的唯一工具层，管理 API 与 Chat API 共用同一个错误类型和同一套广播语义；缺失凭证时统一抛出 `ApiRequestError(status=401, details.request_blocked=true)`、完全不调用 `fetch`，调用方可用 `instanceof ApiRequestError` 区分本地拦截和真实网络/服务端错误（`admin.ts` 重新导出该类以兼容既有 `import { ApiRequestError } from "@/api/admin"`）；实际返回 401 时清除 `sessionStorage` 并广播拒绝事件。拒绝事件携带 `detail.reason`：`"missing"` 表示从未配置过凭证（请求层本地拦截，未触网），`"invalid"` 表示凭证已配置但被服务端拒绝；`adminStore.rejectCredential(reason)` 据此显示不同提示文案，旧的无 `detail` 事件按更保守的 `"invalid"` 处理。`App.vue` 在子组件挂载前注册全局监听，同步清空 Store 凭证并卸载 Dashboard 或 DemoChat；已挂载的凭证设置表单监听 Store 凭证，拒绝时也清空本地输入副本，不能重新保存刚失效的 Token。因此 Store、浏览器存储、设置表单和页面门禁不会因入口不同或挂载时序分叉。保存新凭证后解除拒绝状态并恢复加载。后端仍必须对每次实际到达的未授权请求记录和返回 401，前端门禁不是认证边界。

知识库列表展示 Assistant 的“已上线/未上线”状态；详情中的 `KnowledgeAssistantSettings` 按知识库编辑三条推荐问题、试问、检查运行就绪并确认上线或下线，并明确提示活动索引中的 RAG 文档理解必须全部人工确认。保存线上问题立即生效；未上线时可保存不完整问题，上线必须三条完整且不重复。试问复用管理 Chat，每次新会话，不需要对外上线，但要求问题非空且 Assistant 当前就绪；审核前的内部验证仍可使用独立 DemoChat。**试问结果与 DemoChat 使用相同的按块渲染语义（2026-09-16 修复）**：done 后按 `content` 块渲染（text/table → Markdown，image 块按 `image_asset_id` 经 `fetchImageBlobUrl` 懒加载预签名内容），流式期间与无 content 的回退路径显示累积 `answer`；不要恢复"answer + 文本片段拼接"双通道（含图片的答案会被 `_content_blocks` 拆成多块，片段与全文的去重比较必然失败导致双重渲染）。禁用按钮通过提示说明未就绪、问题为空或请求进行中的原因；“检查就绪”仅刷新运行状态，保留未保存问题及原配置修订号，“重新加载配置”才重新读取保存值。编辑组件以知识库 ID 为 key，关闭时取消试问并忽略过期响应；写请求携带配置修订号，成功后按返回的知识库 ID 更新列表及详情，不使用异步完成时的全局选中值。配置写入即使在详情关闭后完成仍按 ID 更新列表；列表加载比较 `assistant_config_revision`，不让旧响应覆盖更新后的上线状态。索引构建产生 `READY` 候选，不自动生效；候选索引通过已有激活接口明确设为当前 RAG，`DEPRECATED` 历史索引才使用回滚操作。

## 开发约定

- 模型配置页已移除不被 V2 消费的 temperature/max_tokens/thinking 编辑控件，改为阶段代码/Settings 来源的只读说明，不宣称展示实时运行值；保存身份、密钥或启用状态不提交 `parameters`，保留数据库已有 JSON。V2 结构化调用仍使用原阶段显式参数，未改变后端生成策略。
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

侧边栏按 RAG 优先重组为：概览 / 知识管理（含索引管理）/ 问答与调优（问答测试、引擎路由、调试台）/ 运维 / 遗留能力（Support · 已冻结，仅 `support-review`）。视图键（hash）未变，旧深链映射不受影响；2026-10-03 移除概览宣传横幅，改为运行状态与操作入口。

- `EngineRoutingView` 默认策略为 `RAG_ONLY`，`SUPPORT_WITH_RAG_FALLBACK` 选项与 Support 就绪卡片标注"已冻结（ADR-0021）"但保留可用（后端网关仍可执行该策略，前端不单独禁用）。
- `RagConfigurationView` 只保留 `vector_top_k`、`bm25_top_k`、`fusion_top_k` 三个 V2 实际消费的参数；`rerank_top_n`、`context_max_chunks` 已从界面移除（后端 PATCH 为部分更新，兼容缺省）。提示首先说明保存后下一轮对应策略的检索请求生效，无需重启或重建；每查询片段召回/融合上限不同于最终文档数，文档上限和全文预算由后端运行配置控制。已有会话使用新召回参数，但固定创建时的索引；切换索引后的验收需新会话。寒暄/非文档对话不受检索参数影响。
- `DemoChat` 与回答复核的 `next_suggestions` 渲染保留，但新回答恒为空（ADR-0040），都用“为空则不显示”处理；旧会话/旧复核记录仍可能显示历史快照里的建议。
- `DemoChat` 的模式标签收敛为 V2 五种 `answer_mode`；legacy 模式（`DOCUMENT_EXPLANATION`、`STEP_GUIDANCE` 等）与 `DOC_DELIVERED` 专用渲染分支已删除，未知值仍按原文兜底显示。
- `PlaygroundView` 结果面板按 V2 文档级语义展示：`selected_context` 渲染为"隐藏上下文候选文档"（document_key + 内容 SHA-256 前缀），不再按 chunk 分数/正文渲染；`SourceCitation`、`RetrievalChunk` 组件因此删除。`playgroundOptions` 已移除 `rerank_top_n`。
- `TracesView` 时间线包含 V2 各阶段耗时（`v2_query_interpreter_ms` 等）并保留 legacy 键（标注"遗留"，按存在与否自过滤）；详情的"RAG V2 生成详情"展示 Knowledge/Conversation 回答路径、查询理解、文档级重排或对话路径跳过状态、检索降级、隐藏上下文与合规修复。V2 重排折叠面板读取 `runtime_snapshot.generation.retrieval.document_rerank`（对话路径读取检索跳过状态），仅非 V2 Trace 读取旧 `rerank_result`；Support 决策段标注"遗留"，Prompt 折叠面板仅在存在时显示。
- `LogsView` 已移除"升级记录（遗留）"页签、专用详情抽屉及其加载逻辑；旧 `tab=escalations` 深链回落请求错误页签，不发起升级记录查询。后端历史数据及通用 Trace 的遗留诊断兼容保留。Trace 侧栏将完整时间独立成可换行的一行，不与问题/用户信息共用省略文本，长问题另提供悬停全文。
- `SupportReviewView` 顶部有 ADR-0021 冻结横幅，发布/重生成流程保留；`DocumentsView` 上传与空态文案不再宣传 Support Profile 审核，改为说明 RAG 文档卡片自动生成。
- `DashboardView` 的回答结果区只统计外部 `ASSISTANT_API`、`RAG`、近 7 天：分别展示直接回答率、待补充信息率、范围外请求率、失败率和延迟；范围外拒答不再计入负向“无答案”。独立“回答依据分布”以三类 `PRODUCT_*` 回答为分母，展示文档知识、文档加通用解释和通用工程扩展，不把欢迎语或历史未分类回答混入。待补充信息率仍按全部 `UNCONFIRMED` 统计；“值得人工复核的问题”列表额外排除 RAG V2 `diagnostic_reason=LOW_INFORMATION` 和历史确定性低信息门禁，仅保留近 7 天外部 RAG 中需要补充关键条件的有效问题。趋势图使用独立可调时间窗（见下文“管理台展示与反馈分级”），柱形下钻沿用趋势的引擎/来源条件与所点时段，待补充信息不再直接标作知识库缺口。Trace 的 RAG V2 详情将内部 `diagnostic_reason` 显示为输入信息不足、范围判断冲突或缺少关键条件，并保留原始输入门禁码；这些字段不出现在外部 Assistant 响应。冻结 Support 的文档交付、澄清率、升级次数和 Profile 待办已从概览移除，遗留能力仍保留在独立页面。失败待办深链携带当前 Overview 已显示的准确 from/to 和 engine/source，`LogsView` 解析并在失败列表刷新时保留；新深链重置旧条件与错误码。列表显示应用范围、最多 200 条，上方近 7 天失败汇总独立、不随列表筛选变化。
- `IndexBuildView` 索引管理页（ADR-0024，2026-09-17）：工具栏在 `rebuild_required` 时显示"文档有变更待重建"徽标；BUILDING 条目且任务活动时显示"停止"按钮（接 `cancelTask`，协作取消、文档边界生效）；FAILED/CANCELLED 条目显示"重建"按钮（接 `retryIndex`，确认框提示"同一文档同一错误失败 2 次后将自动跳过"）和"删除"按钮（CANCELLED 与 FAILED/DEPRECATED 同权）。重建后不自动激活——沿用全局 staged 候选语义，完成后需手动"设为当前 RAG"。删除按钮文案在详情抽屉为"删除此索引版本"，避免误以为删除文档。
- `DocumentsView` 工具栏（ADR-0025，2026-09-18）：readiness 计数旁新增"一键确认待确认（N）"按钮（`confirmRagDocumentCards` action，N 为 `auto_count`，为 0 时禁用），确认框说明只影响待确认卡片，完成后以响应携带的 readiness 与刷新后的卡片列表更新页面；REJECTED/FAILED/缺失仍由 blockers 展示。新增文档重建时，未变更文档的卡片经后端复制保留审核状态，页面仅需确认新增部分。

## 管理台口径与可操作性（2026-10-03）

- `utils/traceDiagnostics.ts` 是回答复核与 Trace 的管理诊断适配入口：V2 重排取 `runtime_snapshot.generation.retrieval.document_rerank`，实际采用文档取 `generation.answer.used_document_ids`；候选、隐藏上下文、实际采用是不同集合，不从公开空引用或候选反推实际采用。历史缺失字段显示无法判断，保留非 V2 的旧记录兼容。
- `ReviewImage.vue` 统一用于回答复核、文档预览、DemoChat 和上线试问：懒加载、点击放大、关闭预览、加载失败重试、资产不可用/凭证失效提示，以及中止请求和 Blob URL 释放。仅按资产 ID 经管理鉴权取图，不使用响应内任意 URL。外部 Assistant 图片 API 不变，业务端放大交互仍由业务前端实现。
- Dashboard 按“能否对外服务 → 当前待办 → 四个核心指标 → 趋势与人工复核 → 折叠诊断”展示。“值得人工复核的问题”使用原生 `details` 默认折叠，点击标题展开；记录查看和回答复核入口保留，折叠不改变指标查询或统计口径。外部轮次含寒暄/非文档对话；直接回答率与 P95 使用技术样本。零样本显示“—”；直接回答率不是正确率，`UNCONFIRMED` 不是自动判定的知识缺口。最近链路含内部调试，与外部运营指标分区说明。切换知识库/卸载忽略旧响应，缺凭证不轮询，部分刷新失败明确标记旧数据。
- 侧栏 `RuntimeVersion.vue` 同时显示前端静态构建与后端运行镜像的提交、时间、dirty 标记；差异提示核对镜像及缓存，缺标记显示未知。标记生成流程见 `environment.md`，不新增轮询；Worker 版本需另核对启动日志/只读命令。Dashboard 手动“刷新状态”重新读取 `store.systemInfo`，失败显示未知；60 秒指标刷新不读取版本。前端版本仍代表已加载的静态包，部署新前端后须重新加载页面。
- 上线配置显示服务端编码检查项（知识库、模型、索引、文档理解、策略）与去处理入口，加上推荐问题保存和人工试问建议；试问不等于自动质量合格，也不新增上线门禁。Dashboard 的清单入口使用 `#/knowledge?id=<ID>`，清单动作绑定原知识库。`openCheck` 在等待前后校验组件存活与凭证有效，等待后校验凭证未替换、组件知识库 ID 与全局选中 ID 仍匹配；卸载、凭证失效/替换或换库后不执行旧导航。

## 管理台展示与反馈分级（2026-10-05）

- **侧栏**：`components/AppNavigation.vue` 渲染菜单（分组、`aria-current`、进行中徽标；目前仅 `indexes` 显示 `store.buildingCount`）。`App.vue` 用 `.shell.is-collapsed` 控制图标模式：≤1024px 强制收起，>1024px 由底部“收起侧栏”按钮切换并写入 `localStorage`（`shipdemo.sidebarCollapsed`，读写包在 try/catch，仅为便利偏好）。收起时菜单项与 `RuntimeVersion`（`compact` 模式）用 Tooltip 显示名称/版本摘要。≤720px 隐藏侧栏，显示顶部栏与左侧抽屉导航（抽屉内导航 `v-if` 打开时才渲染，选择后自动关闭）。`collapsible` 分组（当前仅“遗留能力”）默认折叠，当前页属于该组时自动展开；条目用 `v-show` 保留在 DOM。各菜单图标不重复。
- **Dashboard**：顶部为精简工具栏（知识库选择、上次刷新、“刷新状态”），不再与 PageHeader 重复标题。上线状态卡按状态着色并配大图标；待办为带图标、色调与数量的可点击行，无待办时显示“未发现运行待办”且保留“不代表回答都正确”的口径。指标骨架屏数量与 4 张卡片一致并原位替换；`MetricCard` 新增 `success` 色调，图标底色随色调变化。
- **请求趋势**：时间范围可选 24 小时 / 7 / 14 / 30 / 90 天或自定义日期（≤92 天，对应后端 `_MAX_RANGE_DAYS`；超限只提示、不请求，并把日期框恢复为上次已应用的范围；不可选未来日期）。24 小时固定按小时；其余 ≤7 天的范围可切换按天/按小时。时间窗从后端桶边界开始，不画不完整的首桶：预设为“最近 N 个整日（含今天）”或“最近 N×24 个整点小时（含当前小时）”；自定义所选日期按服务端日历日解释（`from` = 所选首日服务端零点，`to` = 末日次日零点与当前时间取早）。桶偏移从首个响应学习（`serverOffsetMinutes`，首次按 UTC），偏移与请求所用不一致时自动重发一次对齐请求。“日均/每小时平均”不含仍在进行的当前桶，并注明“不含今天/当前小时”。换范围只重新请求 `/metrics/timeseries`，不重拉 7 天概览；趋势有独立请求序号，换库、卸载或新范围使旧响应失效，旧响应不算“部分刷新失败”。60 秒自动刷新沿用当前范围（预设范围按当前时间滑动）。`utils/metricsBuckets.ts` 补齐后端缺失的零请求时段（后端只返回有数据的桶），并按桶自带时区偏移对齐与标注（面板显示“按 UTC±hh:mm 分桶”）。`TrendChart` 为响应式宽度 SVG：按直接回答/待补充信息/范围外/寒暄与对话等/执行失败堆叠（“其他”= 总数减四类，最小为 0），图例可隐藏分类（至少保留一类）；纵轴由 `utils/chartScale.ts` 的 `niceAxis` 按约 5 个刻度取整（1/2/2.5/5×10ⁿ，2.5 仅在 ≥10 量级使用以保持整数刻度），最高柱一般占绘图区 80% 以上；右轴折线可选待补充信息率、直接回答率、P95 或不显示；无技术样本的时段折线断开而不画 0。悬停提示显示各类数量与比率；只有有请求的时段可点击/键盘（Enter/Space）下钻。面板上方汇总区间请求、平均、峰值、直接回答率与失败数；无数据显示空状态，失败只在趋势面板内警告一次（不再同时触发页顶“部分质量指标”警告），但趋势缺失时“上次完整刷新”不更新。
- **反馈分级**：Store 一律通过 `announce(message, level)` 设置提示（`run()` 的成功参数同样经过它），`noticeLevel` 为 `success` / `caveat` / `warning`。`AdminStudio` 消费后把等级复位为 `success`：`success` 显示为 `ElMessage` 轻提示（3–6 秒，可关闭）；`caveat`（操作已完成但带生效边界，如 Embedding/OCR/Vision 保存、重建已提交需手动设为当前、卡片保存待确认、知识库说明保存）与 `warning`（结果需要关注，如清除数据库密钥后的回退、发布前验证未通过、GC 跳过索引）显示为常驻 `ElNotification`，需手动关闭，且同一时间只保留一条（新提示替换旧的）。关闭提示时清空同文本，以便重复操作再次提示。新增提示时按含义选择等级，不要直接赋值 `notice`。`store.error` 仍为页内可关闭的错误 Alert。后台任务完成/部分失败/失败只通过 `ElNotification` 提示一次，不再写入 `notice`。低风险、可恢复的确认用 Popconfirm（索引“停止/停止构建”、上线设置有未保存问题时的“重新加载配置”）；切换/回滚在线索引、删除、上线/下线、Embedding 重建等不可逆或影响线上的操作仍用 `ElMessageBox` 模态确认。约定见 ADR-0032。
- 文档列表移除写死为“Word 文档”的“类型”列（文件列已有 PDF/DOCX 标识）；Support 生命周期卡片标为“已冻结”并弱化显示，入口保留。
- 测试中 Element Plus 的 Popper（Popconfirm/Tooltip）按定时器打开，需用 `vi.waitFor` 等待 `document.body` 中的弹层，不要依赖单次 `flushPromises`；`ElMessage`/`ElNotification` 也渲染在 `document.body`，且会跨用例残留，断言时应遍历全部实例。

## 运行可信度、会话浏览与后台任务（2026-10-08）

- **请求层连接追踪**：`api/connectivity.ts` 的 `trackedFetch` 是 `admin.ts`、`chat.ts`、`health.ts` 全部请求的出口，只记录可达性：后端应用产生的响应（含 JSON 的 4xx/5xx、`/health/ready` 503、`SERVICE_BUSY` 503）记为可达；网络异常或无 JSON 体的 5xx（Vite 代理 ECONNREFUSED 的 500、nginx 502/503/504）记为不可达；中止请求不计。请求层自身不发重试。新增请求必须经 `trackedFetch`，不要直接调用 `fetch`。
- **后端连接横幅**：`BackendStatusBanner.vue`（`App.vue` 全局，含问答测试页）在不可达时显示目标地址、离线起始时间和倒计时，并持有唯一的探测循环：按 3/5/10/20/30 秒退避请求 `/health/live`；任一请求（探测或页面请求）成功即停止，并显示“已重新连接”，`App.vue` 随后用 `store.run(refreshAll)` 重载外壳数据。卸载即停止探测。
- **运行环境与模型可信度**：`adminStore.loadModelRuntime()` 读取 `/system/model-runtime`（`refreshAll` 与每次模型写操作成功后加载；请求序号 + 凭证保护；失败或凭证失效置为 `null`，不保留旧结论）。`ModelRuntimeBanner.vue` 仅在后端报告模拟（红）或不可用（黄，含 `EXPLICIT_PROVIDER` 无 Key）时显示，可展开原因并跳转 `configuration?tab=models`；状态未知时不显示，横幅缺席只表示“未报告问题”。`DISABLED` 为显式配置，不提示。2026-10-08 起后端禁止静默模拟（ADR-0034），开发环境缺 Key 也会显示为“不可用”而非“模拟”；“模拟”只会来自显式 `*_PROVIDER=fake`（开发/测试）或自动化测试环境。环境徽标（`utils/modelRuntime.ts:environmentBadge`）来自 `systemInfo.environment`（回退 `modelRuntime.environment`），仅有凭证时显示于侧栏品牌区、移动顶栏，收起侧栏时作为品牌标识的悬停提示。
- **会话浏览**：视图键 `conversations`（“运维”分组，`ConversationsView.vue`），读取管理端 `GET /conversations`，默认只看“外部 Assistant”，可切换全部/内部问答测试与试问/调试台，用户 ID 精确查询，按会话 `updated_at` 最近活动时间范围筛选（服务端先筛选，再按最近活动倒序服务端分页（默认每页 50 条，显示总数，2026-10-09 起））。来源取最近一轮链路，过滤为“任一轮来自该渠道”。行点击复用 `store.openConversation` 的只读会话回放抽屉；换库、改条件、卸载均使旧请求失效并中止。回放显示对话、错误提示和 Trace 入口，不再展示 Support 专属的工作流/升级详情。深链 `#/conversations?source=ALL|ASSISTANT_API|INTERNAL_CHAT|PLAYGROUND&user=<ID>`。开发库中的会话多为内部测试，默认外部筛选可能为空，属正常。
- **质量预警**：Dashboard 读取 overview 的 `v2_quality`（与概览同为外部 Assistant · RAG · 近 7 天）。待办规则：未执行重排（模型不可用，含缺 Key 时记录为 DEGRADED/MODEL_NOT_CONFIGURED 的情况）≥1 次即列为红色；重排降级或查询理解降级需同时满足 ≥3 次且 ≥5%（`QUALITY_ALERT_MIN_COUNT`/`QUALITY_ALERT_RATE`）才列为黄色。点击待办展开“更多指标与诊断”并滚动到“阶段质量（RAG V2）”。后端未提供 `v2_quality` 时明确提示，不显示 0。旧的引用缺失率/Rerank 降级率字段对 V2 恒为 0，不得展示。查询向量化失败（`query_embedding_*`，2026-10-09 本地，未部署）使用同一阈值（≥3 次且 ≥5%）列为黄色待办“查询向量化失败，检索仅用关键词召回”，明细格显示降级轮次/已执行检索轮次；这三个字段在类型中为可选，后端未提供时不显示该行、不显示 0。
- **运行容量**：`RuntimeCapacityPanel.vue` 位于 Dashboard 底部“基础服务与运行容量”折叠区，挂载时读取一次、手动刷新、不轮询。`/metrics/runtime` 明确标注为“当前响应进程快照”（多进程下不可当合计），连接池按 `size + overflow` 换算已建立连接、正值才显示“超出常驻池”；任务积压来自 `taskCenterStore`（全服务、数据库口径）。
- **全局后台任务**：`composables/taskCenterStore.ts` 用 `adminApi.listTaskRecords`（与 `listTasks` 同一 `GET /tasks`，完整 `TaskRecord` 类型；`listTasks` 保持原类型供日志页使用）分别读取 RUNNING、QUEUED（各 200 条上限，满额标注为下限）与抽屉打开时最近 24 小时结束的任务。“疑似停滞”只指 RUNNING 且 30 分钟无进度更新（管理台提示，不是后端判定：`tasks/recovery.py` 只按 RQ 任务状态回收索引构建、仅对两类维护任务结合时间判断），不计入徽标；QUEUED 始终计入积压，超过 10 分钟只标注“已排队较久”（可能在等待长任务）。`TaskCenter.vue` 在 `App.vue` 根部只挂载一次并持有唯一轮询（30 秒；抽屉打开 5 秒；页面隐藏或无凭证时暂停；本浏览器提交新任务时立即刷新；多个触发重叠时只有最新一次调用能排定下一次轮询，避免产生多条轮询链）；`TaskCenterButton.vue` 只是触发器（侧栏、移动顶栏）。任务所属知识库不在当前列表时显示“知识库已删除或不在当前列表”且不提供跳转；可跳转时先切换全局选中知识库再导航。
- 测试约定：App 外壳测试需桩掉 `TaskCenter`（挂载即请求）；测试只设置 Store 凭证而不写 `sessionStorage` 时，任何真实请求都会触发“凭证缺失”广播并卸载页面。

## 加载、轮询与 URL 状态（2026-10-09，已随 `4ccf19a` 部署到生产，浏览器验收待确认）

- **按需注册与分包**：`main.ts` 调用 `installElementPlus()`，只注册 `src/elementPlus.ts` 列出的组件（父组件 install 同时注册子组件，如 ElTable→ElTableColumn）及 `v-loading` 指令；`ElMessage/ElMessageBox/ElNotification/ElImageViewer` 在使用处直接导入。新增模板中的 `<el-*>` 组件必须加入该列表，否则生产构建会把它渲染成原样标签且不报错；`tests/elementPlusRegistry.spec.ts` 扫描全部 `.vue` 模板防护。组件测试仍用整包 `ElementPlus` 插件，不能代替该扫描。`vite.config.ts` 的 `manualChunks` 拆出 `vendor-element-plus`、`vendor-markdown`、`vendor`；Element Plus 样式仍整包引入。
- **异步视图与测试**：`AdminStudio` 视图映射和 `App.vue` 的 `DemoChat` 是异步组件。挂载这两个外壳的测试需 `beforeAll(preloadLazyViews)`（`tests/helpers/lazyViews.ts`）预热模块；不要放进全局 `tests/setup.ts`，否则会先于各文件 `vi.mock` 加载真实模块。
- **轮询**：后台轮询统一用 `utils/poller.ts` 的 `createPoller()`：setTimeout 串行调度、在途跳过、页面隐藏或 `backendConnectivity` 离线时暂停、恢复可见立即补一次，`enabled()` 每轮判断凭证等条件。AdminStudio 构建中 5s 调 `store.refreshBuildingState()`（知识库列表 → 文档/索引 → 仅构建中索引的任务；发现构建结束则一次 `loadKnowledgeDetails()` 完整刷新），跟踪任务 3s 调 `pollTrackedTasks()`；Dashboard 60s、Support 审核 3s 同样使用。`pollTrackedTasks()` 多处调用共享同一在途 Promise，结束任务只通知和刷新一次。`refreshOperationalState()` 改为先 `loadKnowledgeBases()` 再 `loadKnowledgeDetails()`，避免以旧列表推算活动索引。TaskCenter 保留其原有自调度轮询。
- **最新请求保护**：视图级加载新增可复用 `utils/latestRequest.ts`（`begin()` 返回 `isCurrent()` 与 `signal`，`cancel()` 用于卸载/换范围）；TracesView 列表与详情、LogsView 失败任务已使用。既有手写序号保护未批量迁移。
- **URL 写回**：`utils/viewQuery.ts` 的 `replaceViewQuery(view, params)` 以 `replaceState` 写回当前视图状态并派发 `VIEW_QUERY_REPLACED_EVENT`，`App.vue` 据此重新读取 hash，使 `navigationQuery` 始终等于 URL（重复点同一深链仍会触发视图）。视图收到自己写回的值必须是幂等的：LogsView 写 `tab`（失败轮次页签附带 engine/source/from/to），状态未变时 watcher 直接返回；ConfigurationView 写 `tab`；KnowledgeView 打开详情写 `?id=`、关闭清除，并先标记 `openedFromQuery` 防止重开。文档、索引、Trace 的筛选与选中项尚未写回 URL。
- **共享文案**：`utils/statusLabels.ts` 统一状态徽标（`StatusBadge` 读取）、筛选下拉中文名、Trace 来源名称/标签色（`TRACE_SOURCE_LABELS`、`isTraceSource`）与响应类型标签（中文 · 原始码）；Traces、Logs、会话浏览不再各自定义来源文案。
- **页面行为**：「回收旧索引」与知识库列表「重建索引」有确认；索引表“状态”列标出“当前 RAG / 非当前 RAG”，详情抽屉按 ID 实时读取 `store.indexes`；文档表的搜索、最近构建状态与 RAG 理解筛选、名称/大小/上传时间排序及分页均在服务端执行（见下节），另有无匹配空态及“详情”按钮；Trace 折叠面板与文档元素/切片页签只在展开时渲染，JSON 按记录缓存；INDEX_BUILD 与 RAG 卡片任务完成通知带跳转下一步按钮并常驻至关闭；≤720px 时右侧抽屉全宽、对话框不超过视口；DemoChat 仅在用户停留底部附近时跟随流式滚动，已完成消息的 Markdown 结果缓存，消息区为 `role="log"`；下载文档的 Blob URL 延迟 1 秒释放。`adminStore` 中与 `ragAdminStore` 重复且无页面调用的 `ragConfig/loadRagConfig/saveRagConfig` 已删除；Support/Release 相关 Store 代码按冻结约定保留。

## 服务端分页与页面布局（2026-10-09，已随 `4ccf19a` 部署到生产，浏览器验收待确认，ADR-0038）

- **API 适配**：`api/admin.ts` 拆出 `apiFetch`（凭证、错误、401 广播），`apiRequest` 解析 JSON，`apiRequestPage` 另读 `X-Total-Count` 返回 `Page<T> = { items, total }`；缺少该头（旧后端）时以本页行数作为总数。分页函数：`listTracesPage`（新增 `question` → `q`、`offset`）、`listTasksPage`、`listDocumentsPage(knowledgeId, DocumentListQuery)`；`listConversations` 增加 `offset` 并读取可选 `total`。原数组函数 `listTraces`/`listTasks`/`listDocuments` 保留给 Dashboard 等不分页的调用方。
- **视图**：TracesView、LogsView（失败轮次、失败任务）、ConversationsView、DocumentsView 均用 `components/ui/ListPagination.vue`（每页 20/50/100，默认 50，`utils/paging.ts` 的 `PAGE_SIZES`；共享样式 `.list-pagination`）；它在“总数超过最小页”或“不在第 1 页”时显示，保证列表变短后仍能翻回。加载结果为空但总数大于 0 且不在第 1 页（删除或筛选使当前页越界）时，用 `clampedPage` 跳到最后一页重新加载。筛选、排序、换库及每页条数变化回到第 1 页（`applyFilters`/`load*`），翻页只改 `offset`（`load*Page`）。Trace 问题搜索改为服务端 `q`，输入 400ms 防抖，覆盖全部记录；与上次请求的搜索词相同时（包括“重置”后清空）不再重复请求。分页条放在空态 `v-if/v-else` 链之外，避免打断空态判断。
- **文档列表状态在 Store**：`documents` 是当前服务端页，配套 `documentTotal`、`documentPage`、`documentPageSize`、`documentFilters`（`buildStatus`、`cardState`、`sort`、`order`）与 `documentSearch`；`documentListQuery(activeIndexId)` 生成查询：有当前 RAG 索引时总是附 `card_index_id`，每行带服务端 `card_state`，表格用 `DOCUMENT_CARD_STATES`（`utils/statusLabels.ts`）显示，不再下载整个索引的卡片列表（Store 已删除 `ragDocumentCards`，就绪数改用 `getRagDocumentCardReadiness`）；知识库没有当前索引时清空 RAG 理解筛选，而不是保留一个不会生效的条件。卡片保存、审核、一键确认后重新加载当前页。`reloadDocuments()` 回到第 1 页，`loadDocumentPage()` 保持页码；`loadKnowledgeDetails()` 与构建中轮询 `refreshBuildingState()` 都按当前查询取页，并以模块级 `documentQueryGeneration` 丢弃旧查询的晚到结果；所有加载路径结束时都经 `settleDocumentLoad` 为仍然最新的查询清除 `documentsLoading`，构建轮询抢占用户翻页也不会让遮罩卡住。构建熔断告警改由独立的 `build_status=SKIPPED` 查询写入 `skippedDocuments`（最多 50 条，`skippedDocumentTotal` 显示总数与未列出数），换库时清空，查询失败时为空而不沿用其他知识库的结果。`filteredDocuments` getter 已删除。“最近构建”列显示 `last_build_status`（未参与构建显示文字而非徽标），替代原先恒为“已存储”的状态列。
- **布局**：`.panel` 统一 `min-width: 0; max-width: 100%`，宽表格在面板内部滚动而不撑开页面；≤720px 时工具栏中的日期选择器、`list-toolbar` 的下拉/输入框占满整行。Traces 的筛选条移到列表/详情两栏之上的整行面板；文档页把“RAG 文档理解”就绪数与一键确认/生成按钮合并为独立条带，生成任务只在运行中或需要关注（失败、停止、部分失败）时显示完整进度块，正常完成仅在条带内一行摘要；文件名最多两行显示。会话来源下拉通过 `:empty-values="[null, undefined]"` 让 `""`（全部来源）正常显示标签。
- **验证方式**：本地 API + 构建产物 + 无头 Chromium 注入本机凭证，在 1440/1024/390px 宽度下逐页检查页面横向滚动（`scrollWidth - clientWidth`）与页面异常，并实际点击翻页、排序、搜索核对请求参数。
