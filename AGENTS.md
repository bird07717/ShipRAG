# Shipdemo AI 项目上下文入口

## 项目概要

Shipdemo 是一个企业知识库与技术支持问答平台。管理员通过 Web 管理台维护知识库、文档、索引、模型和运行策略；后端将文档摄取为可检索内容，并通过 RAG 或 Support 引擎提供流式问答。另提供带独立凭据的窄化 Assistant API，供业务后端集成。

## 核心技术栈

- 前端：Vue 3、TypeScript、Vite、Element Plus、Vitest。
- 后端：Python 3.11–3.14、FastAPI、SQLAlchemy async、Alembic、RQ。
- 基础设施：PostgreSQL 17 / ParadeDB（`vector`、`pg_search`）、Redis、MinIO、Docker Compose、Nginx。

## 重要入口

- `README.md`：面向开发者和使用者的项目概览、快速启动、验证与文档导航。
- `frontend/src/`：管理台应用；`App.vue` 管理 hash 导航，`components/AdminStudio.vue` 分发页面。
- `backend/app/main.py`：FastAPI 入口及应用生命周期。
- `backend/app/api/`：HTTP / SSE 接口；`router.py` 区分管理接口和外部 Assistant 接口。
- `backend/app/gateway/`：RAG 与 Support 的策略编排和回退。
- `backend/app/ingestion/`、`rag/`、`support/`：文档构建、检索问答、技术支持领域逻辑。
- `backend/alembic/versions/`：数据库架构的权威迁移历史。
- `compose.yaml`、`compose.production.yaml`、`Procfile`、`scripts/`：本地、生产和校验编排。

## AI 工作原则

1. 新会话先阅读本文件，再按下方路由读取与任务直接相关的认知文档，最后读取有关源码、配置、迁移和测试；不要无目的扫描全仓库。
2. 认知文档是导航和已验证事实的摘要，不能替代当前源码、配置、迁移、测试或实际运行结果。出现冲突时以交叉检查后的真实结果为准。
3. 不能确认的信息写为“待确认”，同时说明确认路径；不得将文件名、常见惯例或推测写成事实。
4. 不要把密码、令牌、密钥、Cookie 或 `.env` 的真实值写入文档。仅记录环境变量名称、用途和安全配置方式。
5. 保留用户已有的未提交改动；只触及当前任务所需文件。

## 上下文路由

| 需要回答的问题 | 先阅读 |
| --- | --- |
| 系统整体、调用链、跨模块影响 | [`docs/ai/architecture.md`](docs/ai/architecture.md) |
| 管理台、页面、组件、状态、前端测试 | [`docs/ai/frontend.md`](docs/ai/frontend.md) |
| API、服务、问答链路、任务、外部集成 | [`docs/ai/backend.md`](docs/ai/backend.md) |
| 表、迁移、索引、数据约束 | [`docs/ai/database.md`](docs/ai/database.md) |
| 安装、配置、启动、构建、测试、部署 | [`docs/ai/environment.md`](docs/ai/environment.md) |
| 当前阶段、正在进行项、阻塞和近期计划 | [`docs/ai/current-state.md`](docs/ai/current-state.md) |
| 已知故障、限制、临时措施、排查提示 | [`docs/ai/known-issues.md`](docs/ai/known-issues.md) |
| 架构/技术取舍与长期约束 | [`docs/ai/decisions.md`](docs/ai/decisions.md) |

按需读取，不要求每次读取 `docs/ai/` 的全部文件。需要新增认知文档时，先在本表增加路由。

## 认知维护规则

完成项目任务前执行一次“项目认知更新检查”。架构、模块职责、接口契约、数据模型、关键依赖、环境/部署/校验命令或长期约定变化时，更新相应稳定文档。里程碑、进行中事项、阻塞、已知问题或临时措施变化时，更新 `current-state.md` 或 `known-issues.md`。重要且长期的技术取舍以 ADR 格式追加到 `decisions.md`；已失效 ADR 标记为已废弃或已取代，不删除。

普通文案、样式和不影响长期认知的小修复通常不需要更新稳定文档。只读任务或用户禁止写入时，不修改认知文档，在最终答复列出建议更新项。

## 任务完成标准

遵循闭环：**读取本文件 → 路由相关认知 → 检查相关实现 → 执行任务 → 运行适当验证 → 检查认知影响 → 更新相关文档 → 报告结果**。

最终答复必须说明：完成内容、改动文件、实际执行的验证及真实结果、使用/验证方法、未解决或待确认项，以及“项目认知更新：已更新… / 无需更新… / 只读未修改…”的结论。
