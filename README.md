# Enterprise RAG Platform

企业产品文档知识库与多模态 RAG 问答平台。仓库包含 M0–M7 的实现与 2026-08-17 的 V1.0.0 验收证据；当前代码、OpenAPI 和 Alembic 迁移头才是后续修改与发布的事实来源。

> 当前迁移头为 `0008`。M7 记录中的 `0006` 是当次历史验收所使用的 Schema，不能替代对 `0007`、`0008` 之后代码的重新验收。

## 当前能力

- FastAPI 应用骨架、统一配置、结构化日志、请求 ID 和错误边界
- `/health/live` 与 PostgreSQL、pgvector、Redis、MinIO 真实就绪检查
- `/api/v1` 服务级 Bearer Token 认证骨架
- RQ Worker 与三条基础队列
- PostgreSQL 17 + pgvector、Redis、MinIO 私有 Bucket
- Alembic `0001`–`0008`：包括默认 RAG Prompt v2、Parent/Child Chunk、相邻 Chunk 和不完整内容标记
- Vue 3 + TypeScript + Vite + Element Plus 的 RAG Studio 管理台
- 后端和前端静态检查、测试、覆盖率与生产构建门禁
- DOCX/OOXML、MinIO 图片传输、严格中文 BM25 和云模型契约探测套件
- Word 上传、幂等校验、RQ 全量索引构建、Element/Chunk 与 1024 维图文 Embedding
- 双索引快照、完整性检查、Active Index 原子发布和摄取预览 API
- Active Index 冻结、知识库隔离的 pgvector 文本向量检索和上下文预算
- 全局 Prompt、有限多轮历史、GLM-5.2 流式生成、引用校验、会话与 RAG Trace
- `POST /api/v1/chat/stream` 的 `trace/source/message/done/error` SSE 协议
- SiliconFlow DeepSeek-OCR、智谱 GLM-5V-Turbo、独立重试与单项降级
- OCR/Caption 增强的 IMAGE/MIXED Chunk、图文 Embedding 与图片来源 API
- `pg_search` 严格中文 BM25、pgvector、确定性 RRF Hybrid Fusion
- Qwen3-VL-Reranker 图文重排、可审计降级与完整引用校验
- 自动/人工发布双索引、失败隔离、索引/任务管理 API 与原子切换
- RAG Playground 参数实验、PLAYGROUND Trace 和检索全阶段诊断
- Knowledge Base、Documents、三栏预览、Index Build、Models/Prompt（只读）和 Trace 管理页面
- Backend/Worker/Frontend 生产镜像、Nginx SSE 代理和带迁移门禁的生产 Compose
- 真实 HTTP/RQ 联调、并发基线、依赖恢复、备份恢复和发布 Runbook

## 快速启动

```bash
./scripts/bootstrap.sh
docker compose up -d
cd backend
../.venv/bin/alembic upgrade head
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18000
```

分别启动 Worker 和前端：

```bash
cd backend && ../.venv/bin/rag-worker
cd frontend && npm run dev
```

默认开发地址：

- RAG Studio：`http://127.0.0.1:5173`
- FastAPI：`http://127.0.0.1:18000`
- PostgreSQL：`127.0.0.1:15432`
- Redis：`127.0.0.1:16379`
- MinIO API/Console：`127.0.0.1:19000/19001`

完整说明见 [生产部署指南](docs/deployment/production.md)、[当前 API 契约](docs/api-contract.md)及 [M7 历史验收记录](docs/m7-acceptance.md)。

## 质量检查

```bash
./scripts/check.sh
```

M0 本地验证（云凭据缺失时报告仍保留 BLOCKED 状态）：

```bash
./scripts/check-m0.sh --allow-blocked
```

使用非生产 SiliconFlow/智谱凭据执行完整验证时运行 `./scripts/check-m0.sh`。提交的 2026-08-17 脱敏在线报告为 10/10 PASS，Embedding 维度已冻结为 1024；`pg_search` 风险已按“未修改、仅服务端托管、不分发”接受，M0 状态为 PASS。详见 [M0 核查](docs/m0-review.md)。

M2 文档入库验收：

```bash
./scripts/check-m2.sh
M2_SMOKE_PROVIDER=siliconflow ./scripts/check-m2.sh
```

M3 文本 RAG 验收：

```bash
./scripts/check-m3.sh
M3_SMOKE_EMBEDDING_PROVIDER=siliconflow M3_SMOKE_LLM_PROVIDER=zhipu ./scripts/check-m3.sh
```

M4 多模态 RAG 验收：

```bash
./scripts/check-m4.sh
M4_SMOKE_EMBEDDING_PROVIDER=siliconflow \
M4_SMOKE_OCR_PROVIDER=siliconflow \
M4_SMOKE_VISION_PROVIDER=zhipu \
./scripts/check-m4.sh
```

M5 完整检索验收：

```bash
./scripts/check-m5.sh
M5_SMOKE_EMBEDDING_PROVIDER=siliconflow \
M5_SMOKE_RERANK_PROVIDER=siliconflow \
M5_SMOKE_LLM_PROVIDER=zhipu \
./scripts/check-m5.sh
```

M6 索引与后台验收：

```bash
./scripts/check-m6.sh
```

M7 完整发布验收（会构建并启动独立临时生产栈，并短暂中断本地开发依赖执行恢复演练）：

```bash
./scripts/check-m7.sh
```
