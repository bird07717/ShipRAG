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

知识库文档页可通过管理 API 修改文档显示名称；成功后就地更新列表和已打开的文档详情，不提交索引构建。原始文件名仍只读保留，用于管理员识别源文件。

`src/api/admin.ts` 通过 `VITE_API_BASE_URL`（缺省同源）请求管理 API，并从 `sessionStorage` 的 `rag_service_token` 读取 Bearer Token。`src/api/chat.ts` 手动解析 SSE，支持 `status`、`trace`、`message`、`fallback`、`done`、`error` 事件。不要将服务令牌或任何真实密钥写入源码、测试夹具或认知文档。

除凭证配置页外，`AdminStudio` 在当前浏览器会话没有 Service Token 时不挂载管理视图，也不启动索引/任务轮询；`App.vue` 对独立的问答测试页执行相同门禁。管理 API 返回 401 时会标记凭证失效、卸载管理视图并停止后续自动请求。保存新凭证后解除该状态并恢复加载，避免无凭证或失效凭证持续请求造成认证错误刷屏。后端仍必须对每次实际到达的未授权请求记录和返回 401，前端门禁不是认证边界。

知识库列表和详情只读展示 Assistant 的“已上线/未上线”状态；当前没有管理侧写接口，因此页面尚不提供上线开关或按知识库配置标准问题。索引构建产生 `READY` 候选，不自动生效；候选索引通过已有激活接口明确设为当前 RAG，`DEPRECATED` 历史索引才使用回滚操作。

## 开发约定

- 使用 TypeScript 严格类型；对 API 契约改动应同步 `types/`、适配层、视图与测试。
- UI 文字主要为中文；复用 `components/ui/`、设计 token 和 Element Plus，而非复制样式。
- API/Store/工具核心逻辑维持较高覆盖率；当前覆盖率门槛定义在 `vite.config.ts`，按目录分别计算。
- 可用命令见 [`environment.md`](environment.md)。
