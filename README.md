# Enterprise RAG Platform

企业产品文档知识库与多模态 RAG 问答平台。当前代码、OpenAPI 和 Alembic 迁移头是系统行为、修改与发布的事实来源。

> 当前迁移头为 `0014`。对目标版本的发布结论必须来自相同代码、配置和 Schema 上重新执行的检查，不能沿用历史阶段性结论。

## 当前能力

- FastAPI 应用骨架、统一配置、结构化日志、请求 ID 和错误边界
- `/health/live` 与 PostgreSQL、pgvector、Redis、MinIO 真实就绪检查
- `/api/v1` 服务级 Bearer Token 认证骨架
- RQ Worker 与三条基础队列
- PostgreSQL 17 + pgvector、Redis、MinIO 私有 Bucket
- Alembic `0001`–`0014`：包括摄取、RAG、Parent/Child Chunk、文档聚焦、运行时配置、模型密钥审计和质量指标
- Vue 3 + TypeScript + Vite + Element Plus 的 RAG Studio 管理台
- 后端和前端静态检查、测试、覆盖率与生产构建门禁
- DOCX/OOXML、MinIO 图片传输、严格中文 BM25 和云模型契约探测套件
- Word 上传、幂等校验、RQ 全量索引构建、Element/Chunk 与 1024 维图文 Embedding
- 双索引快照、完整性检查、Active Index 原子发布和摄取预览 API
- Active Index 冻结、知识库隔离的 pgvector 文本向量检索和上下文预算
- 全局 Prompt、有限多轮历史、GLM-5.2 流式生成、引用校验、会话与 RAG Trace
- `POST /api/v1/chat/stream` 的 `trace/source/message/done/error` SSE 协议
- Chat 文档路由状态机：文档聚合决策、零 LLM 整文档投递（`DOC_DELIVERED`）、
  澄清选项解析、聚焦文档全文问答与切换（迁移 `0009`）
- SiliconFlow DeepSeek-OCR、智谱 GLM-5V-Turbo、独立重试与单项降级
- OCR/Caption 增强的 IMAGE/MIXED Chunk、图文 Embedding 与图片来源 API
- `pg_search` 严格中文 BM25、pgvector、确定性 RRF Hybrid Fusion
- Qwen3-VL-Reranker 图文重排、可审计降级与完整引用校验
- 自动/人工发布双索引、失败隔离、索引/任务管理 API 与原子切换
- RAG Playground 参数实验、PLAYGROUND Trace 和检索全阶段诊断
- 服务端质量统计：Chat 请求 `user_id` 透传、trace 持久化 `response_type`/`answer_mode`（迁移 `0014`）与 `/metrics` 无答案率、延迟分位数、token 成本聚合 API
- 全模型类型在线编辑（LLM/Rerank 下一轮生效；Embedding/OCR/Vision 下一次构建生效并标记重建）、API Key 加密写入/轮换/回退（ADR-0008 维度契约门禁与审计）
- Knowledge Base、Documents、三栏预览、Index Build、Models（全类型可编辑、密钥管理与连通性测试）、RAG 检索配置、Prompt（只读）和 Trace 管理页面
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

完整说明见 [工程文档索引](docs/README.md)、[生产部署指南](docs/deployment/production.md)、[当前 API 契约](docs/api-contract.md)及 [对话式技术支持重构计划](docs/technical-support-refactor-plan.md)。

## 质量检查

```bash
./scripts/check.sh
```

M0 本地验证（云凭据缺失时报告仍保留 BLOCKED 状态）：

```bash
./scripts/check-m0.sh --allow-blocked
```

使用非生产 SiliconFlow/智谱凭据执行完整验证时运行 `./scripts/check-m0.sh`。该命令会在本地生成脱敏探测报告；报告只代表当次端点、凭据、模型和配置的执行结果，不作为长期静态验收结论。Embedding 维度仍按当前配置与 ADR 冻结为 1024。

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
