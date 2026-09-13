# Shipdemo

企业知识库与技术支持问答平台。管理员可维护知识库、文档、索引、模型和运行策略；平台将 DOCX 文档摄取为可检索内容，并通过 RAG 或 Support 引擎提供流式问答。项目还提供独立认证的 Assistant API，供业务后端集成。

## 核心能力

- 文档上传、DOCX 解析、图像处理、分块、向量化与版本化索引构建。
- 向量检索、BM25、融合与重排组成的 RAG 问答链路。
- Support Profile 审核和独立发布；RAG 索引与 Support 发布生命周期互不阻塞。
- 管理台支持知识库、文档及其显示名称、索引、模型、运行策略、链路追踪与问答测试。
- 对外 Assistant API 支持会话隔离、SSE 流式输出、幂等请求和受限的图片读取；文档只作为回答内容与来源展示，不提供原文下载。

## 架构概览

```text
Vue 管理台 / 业务后端
        │ HTTP、SSE
        ▼
FastAPI
  ├─ 管理 API（Service Token）
  ├─ Assistant API（独立 Token + 外部用户 ID）
  ├─ Gateway ──┬─ RAG
  │            └─ Support ──(策略允许时)─► RAG 回退
  └─ 摄取与管理任务 ─► Redis RQ ─► Index / Maintenance Workers
                         │
                PostgreSQL（vector、pg_search） + MinIO
```

技术栈：Vue 3、TypeScript、Vite、Element Plus；FastAPI、SQLAlchemy async、Alembic、RQ；PostgreSQL/ParadeDB、Redis、MinIO、Docker Compose、Nginx。

## 快速开始

前置条件：Python `>=3.11,<3.15`、Node.js `>=20.19.0`、Docker Compose。

```bash
# 创建 .venv、安装后端和前端依赖；缺少时由 .env.example 创建 .env
bash scripts/bootstrap.sh

# 启动基础设施、迁移、API、两个 Worker 和前端开发服务器
bash scripts/dev-up.sh
```

默认开发地址：

- 管理台：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:18000`

启动前请审阅 `.env.example`，仅在本地安全配置或部署 Secret 中设置敏感值。不要提交 `.env`、Token、API Key 或数据库密码。

## 问答与接口边界

- 管理 API 使用 `SERVICE_TOKEN`，面向管理台和运维操作。
- Assistant API 位于 `/api/v1/assistant/*`，使用独立的 `ASSISTANT_SERVICE_TOKEN`，并要求每个请求携带稳定的 `X-External-User-ID`。
- 内部管理聊天 SSE 使用 `status`、`trace`、`message`、可选 `fallback`、`done`/`error` 事件。
- Assistant SSE 会转换为公开的 `accepted`、`progress`、`context`、`delta`、`complete`/`error` 事件，并含心跳；完整字段契约见 [Assistant API 对接文档](docs/integration/assistant-api-integration-guide.md)，实际调用和后台实现见 [Assistant API 业务后台对接指南](docs/integration/assistant-api-business-backend-guide.md)。

## 验证

```bash
# 常规后端、前端和覆盖率校验
bash scripts/check.sh

# RAG 真实链路回归（需要已启动服务、目标知识库和可用模型配置）
.venv/bin/python rag-acceptance/run_golden.py

# 生产门禁：配置校验、容器 smoke 和发布验收
bash scripts/check-m7.sh
```

这些命令的可用性与结果取决于当前环境；运行结果不能由本文替代。生产部署使用 `compose.production.yaml` 和部署环境变量，详见 [环境、启动与验证](docs/ai/environment.md)；[开发启动指南](docs/dev-startup.md) 仅适用于开发环境。

## 文档导航

| 主题 | 文档 |
| --- | --- |
| Agent 工作入口、文档路由和维护规则 | [AGENTS.md](AGENTS.md) |
| 系统架构与数据流 | [docs/ai/architecture.md](docs/ai/architecture.md) |
| 前端、后端、数据库 | [frontend.md](docs/ai/frontend.md)、[backend.md](docs/ai/backend.md)、[database.md](docs/ai/database.md) |
| 环境、命令和生产部署 | [environment.md](docs/ai/environment.md) |
| 当前状态、已知问题、技术决策 | [current-state.md](docs/ai/current-state.md)、[known-issues.md](docs/ai/known-issues.md)、[decisions.md](docs/ai/decisions.md) |
| 对外 Assistant API 完整契约 | [assistant-api-integration-guide.md](docs/integration/assistant-api-integration-guide.md) |
| 业务后台对接实施指南 | [assistant-api-business-backend-guide.md](docs/integration/assistant-api-business-backend-guide.md) |
| 小体量 RAG 技术助手优化执行方案（本地已实现，默认关闭） | [rag-technical-assistant-optimization-plan.md](docs/technical/rag-technical-assistant-optimization-plan.md) |
| 运维与 RAG 回归 | [ops-runbook.md](docs/ops-runbook.md)、[rag-acceptance/README.md](rag-acceptance/README.md) |

## 协作约定

处理项目任务前，先阅读 [AGENTS.md](AGENTS.md)，再按任务读取相关 `docs/ai/` 文档，并以当前源码、配置、迁移和实际验证结果为准。完成涉及架构、环境、数据模型、当前状态或已知问题的改动后，同步更新对应认知文档。
