# 本地开发指南

## 1. 环境要求

- Python `>=3.11,<3.15`
- Node.js `>=20.19`
- npm 10 或更高
- Docker Engine 与 Docker Compose v2
- Git

Python 完整依赖树由 `backend/requirements.lock` 固定，前端依赖由 `frontend/package-lock.json` 固定。容器镜像禁止使用 `latest`。

## 2. 初始化

Linux/macOS：

```bash
./scripts/bootstrap.sh
```

PowerShell：

```powershell
.\scripts\bootstrap.ps1
```

脚本创建根目录 `.venv`、安装锁定依赖，并在缺少 `.env` 时复制 `.env.example`。脚本不会覆盖已有 `.env`。

## 3. 启动基础设施

```bash
docker compose up -d
docker compose ps -a
```

`minio-init` 是一次性初始化任务，显示 `Exited (0)` 表示成功。三个长期服务应显示 `healthy`。

|服务|开发地址|
|---|---|
|PostgreSQL|`127.0.0.1:15432`|
|Redis|`127.0.0.1:16379`|
|MinIO API|`http://127.0.0.1:19000`|
|MinIO Console|`http://127.0.0.1:19001`|

默认使用非标准宿主机端口，避免覆盖机器上已有的 PostgreSQL、Redis 或 MinIO；容器内端口仍为标准端口。

停止服务并保留数据：

```bash
docker compose stop
```

不要执行 `docker compose down -v`，除非明确要删除全部项目数据。

## 4. 数据库迁移

```bash
cd backend
../.venv/bin/alembic upgrade head
```

当前迁移头为 `0008`：`0001` 启用 pgvector，`0002` 创建文档入库 Schema，`0003` 创建文本 RAG Schema，`0004` 扩展图片理解快照，`0005` 启用 `pg_search`、创建中文 BM25 索引并加入 Rerank 与引用 Trace，`0006` 添加后台索引时间线和 Trace 筛选查询索引，`0007` 写入默认 RAG Prompt v2，`0008` 增加 Parent/Child Chunk、相邻 Chunk 与不完整内容元数据。

升级后请确认 `alembic current` 与 `alembic heads` 均为 `0008`。`0008` 会将已有 Active Index 对应知识库标为 `rebuild_required=true`，但不会原地修改历史快照；需通过构建接口为受影响知识库创建并发布一个新索引，才会产生 Parent/Child Chunk 数据。

## 5. 启动后端

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18000 --reload
```

- Liveness：`http://127.0.0.1:18000/health/live`
- Readiness：`http://127.0.0.1:18000/health/ready`
- OpenAPI：`http://127.0.0.1:18000/docs`

Readiness检查 PostgreSQL、pgvector、Redis、MinIO及两个私有Bucket。依赖不可用时在有限超时内返回503。

## 6. 启动 Worker

```bash
cd backend
../.venv/bin/rag-worker
```

Worker监听：

```text
ingestion
index_build
maintenance
```

`app.tasks.health.ping`用于基础队列联调；RQ Job ID 不是未来业务任务主键。

上传接口会向 `index_build` 队列投递 `app.tasks.ingestion.process_index_task`。Worker 必须与后端使用相同 `.env`。

## 7. 启动前端

```bash
cd frontend
npm run dev
```

访问 `http://127.0.0.1:5173`。Vite把 `/health` 和 `/api` 代理到 `127.0.0.1:18000`。

## 8. M0 技术门禁

先启动默认基础设施。无云凭据时，可重放 DOCX、MinIO、BM25 和 Mock 契约测试：

```bash
./scripts/check-m0.sh --allow-blocked
```

该选项只允许命令在“缺少凭据”时返回成功；此时生成的 `docs/evidence/m0-online-report.json` 仍明确记录 `blocked`，不能作为在线验收证据。

真实在线验证时，将以下非生产凭据仅写入本机 `.env`：

```dotenv
SILICONFLOW_API_KEY=...
ZHIPU_API_KEY=...
```

然后运行严格门禁：

```bash
./scripts/check-m0.sh
```

探测器不打印 Key、请求正文、签名 URL 或模型响应正文。2026-08-17 在线报告 10/10 通过，五种输入均返回 1024 维，`EMBEDDING_DIMENSION=1024` 已冻结。

## 9. M2 文档入库验收

本地 Fake Embedding 闭环：

```bash
./scripts/check-m2.sh
```

使用已配置的 SiliconFlow 非生产 Key 验证真实图文 Embedding：

```bash
M2_SMOKE_PROVIDER=siliconflow ./scripts/check-m2.sh
```

烟测通过真实 HTTP 上传、MinIO、PostgreSQL、RQ 投递和 Pipeline，完成后清理自身随机测试数据。完整接口与验收结果见 [M2 验收记录](m2-acceptance.md)。

## 10. M3 文本 RAG 验收

本地 Fake Provider 闭环：

```bash
./scripts/check-m3.sh
```

使用非生产云凭据验证 SiliconFlow 查询 Embedding 和智谱 GLM-5.2 流式输出：

```bash
M3_SMOKE_EMBEDDING_PROVIDER=siliconflow \
M3_SMOKE_LLM_PROVIDER=zhipu \
./scripts/check-m3.sh
```

烟测创建两个临时知识库验证严格隔离，并通过真实 HTTP 检查 SSE、会话消息和 Trace，结束后按 UUID 清理自身数据。

## 11. M4 多模态 RAG 验收

本地确定性 Provider 闭环：

```bash
./scripts/check-m4.sh
```

使用非生产云凭据验证真实 OCR、Vision 和图文 Embedding：

```bash
M4_SMOKE_EMBEDDING_PROVIDER=siliconflow \
M4_SMOKE_OCR_PROVIDER=siliconflow \
M4_SMOKE_VISION_PROVIDER=zhipu \
./scripts/check-m4.sh
```

烟测通过真实 HTTP 上传 DOCX，验证 RQ 投递、图片抽取、MinIO、OCR、Vision、IMAGE Element、MIXED Chunk、1024 维图文 Embedding 和 Active Index 发布，结束后精确清理测试数据。

## 12. M5 完整检索验收

```bash
./scripts/check-m5.sh
```

真实云端闭环：

```bash
M5_SMOKE_EMBEDDING_PROVIDER=siliconflow \
M5_SMOKE_RERANK_PROVIDER=siliconflow \
M5_SMOKE_LLM_PROVIDER=zhipu \
./scripts/check-m5.sh
```

## 13. M6 索引与后台验收

```bash
./scripts/check-m6.sh
```

该脚本会构建两个完整快照，验证第二个索引在 `BUILDING` 和人工验收后的 `READY` 阶段均不改变线上 Active Index，再通过管理 API 原子切换；随后运行 Playground、检查 Trace/模型脱敏接口，并执行前端 lint、类型检查、覆盖率和生产构建。

## 14. M7 发布验收

```bash
./scripts/check-m7.sh
```

M7 会执行全量工程门禁、生产镜像构建、独立空卷生产栈启动、Word/RQ/索引/Playground/SSE 联调、并发压测、Worker 重启、Redis/MinIO/PostgreSQL 中断恢复以及 PostgreSQL 备份恢复。执行期间本地开发依赖会被逐个短暂停止并自动恢复，不应与其他本地业务负载共用该环境。仓库内的历史 M7 记录只覆盖到 Alembic `0006`；对当前 `0008` Schema 或其后的代码发布，必须重新运行该门禁并保存新的证据。

部署与故障操作见 [生产部署指南](deployment/production.md)和[恢复 Runbook](deployment/recovery-runbook.md)。

默认 PostgreSQL 镜像已经切换为锁定摘要的 ParadeDB PostgreSQL 17，并显式预加载 `pg_search`。从旧 Debian 基础镜像升级已有数据卷时，应先 REINDEX 使用默认 collation 的对象，再对业务库及模板库执行 `ALTER DATABASE ... REFRESH COLLATION VERSION`。

## 15. 质量检查

```bash
./scripts/check.sh
```

检查包括 Ruff、mypy、pytest覆盖率、ESLint、vue-tsc、Vitest覆盖率和Vite生产构建。

质量检查包含单元测试、一次 M2 入库烟测和一次 M3 文本 RAG 烟测，因此执行前必须启动 PostgreSQL、Redis 和 MinIO。

## 16. 配置和安全

- `.env`不会被Git提交；`.env.example`仅提供开发模板。
- 生产环境必须设置非空 `SERVICE_TOKEN`。
- 浏览器端不得持有长期服务Token，生产RAG Studio应通过可信反向代理访问API。
- 数据库和Redis URL使用`SecretStr`，不得进入普通日志。
- `EMBEDDING_DIMENSION=1024`来自 M0 真实在线响应；改变它必须同步执行 Schema 迁移和全量索引重建。
- 默认 Compose 同时要求 `vector` 和 `pg_search`；BM25 使用 Lindera 中文 tokenizer，不以 PostgreSQL FTS 冒充。
