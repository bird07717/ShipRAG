# V1.0.0 生产部署指南

> **版本边界**：V1.0.0/M7 的历史验收运行在 Alembic `0006`。当前代码的迁移头为 `0008`；部署当前代码时，发布验证和恢复演练必须在目标镜像与 `0008` Schema 上重新执行，不能只引用历史 M7 结论。

## 1. 发布拓扑

`compose.production.yaml` 是 V1 的单节点参考部署，包含：

- Nginx + RAG Studio：唯一对外 HTTP 入口，默认绑定 `127.0.0.1:18080`。
- FastAPI Backend：默认两个 Uvicorn Worker，仅在内部网络监听 `8000`。
- RQ Worker：消费 `ingestion`、`index_build`、`maintenance` 队列。
- Alembic Migrate：一次性任务，成功后 Backend/Worker 才启动。
- PostgreSQL 17 + pgvector + pg_search、Redis AOF、MinIO 私有 Bucket。

数据库、Redis 和 MinIO 不映射宿主机端口。公网 TLS、WAF、限流和企业身份接入应放在此 Compose 入口之前。该拓扑面向最多约 1000 个注册用户的第一版单节点交付，不代表 1000 个同时在线模型请求，也不提供跨节点高可用。

## 2. 主机基线

建议起始规格：

- Linux amd64；8 vCPU、16 GiB RAM、200 GiB SSD。
- Docker Engine 29+、Docker Compose v2/v5。
- PostgreSQL/MinIO 数据盘启用宿主机快照或等价持久备份。
- 出站 HTTPS 允许访问 SiliconFlow 和智谱 API；入站只开放外层 HTTPS 入口。
- NTP 正常，系统时区可任意，数据库数据统一保存为 UTC。

实际 CPU、内存、磁盘、模型额度和并发上限必须依据企业文档数量与真实云模型延迟调整。M7 的本地 Fake Provider 压测只验证应用和数据链路容量，不代替云厂商额度压测。

## 3. 发布前配置

从 `.env.production.example` 创建仅部署机可读的环境文件，至少替换：

```text
APP_ENV=production
SERVICE_TOKEN=<随机高强度服务凭据>
POSTGRES_PASSWORD=<随机数据库密码>
REDIS_PASSWORD=<随机 Redis 密码>
MINIO_ROOT_USER=<独立访问账号>
MINIO_ROOT_PASSWORD=<随机对象存储密码>
SILICONFLOW_API_KEY=<非测试生产凭据>
ZHIPU_API_KEY=<非测试生产凭据>
CORS_ORIGINS=https://rag.example.internal
```

要求：

- `.env` 不进入 Git、镜像、工单或日志；正式环境优先由 Secret Manager 注入。
- `SERVICE_TOKEN` 只发给企业 API 网关/可信业务系统。RAG Studio 的会话输入仅用于受控内网调试。
- 镜像发布时把 `BACKEND_IMAGE`、`FRONTEND_IMAGE` 设置为仓库中的不可变 Digest，不复用浮动标签。
- 生产不得使用 `fake` Provider。

## 4. 构建与首次部署

```bash
docker compose -f compose.production.yaml config --quiet
docker compose -f compose.production.yaml build backend frontend
docker compose -f compose.production.yaml up -d --wait --wait-timeout 240
docker compose -f compose.production.yaml ps
```

如果生产配置不放在项目根目录的 `.env`，所有命令显式增加 `--env-file /secure/path/rag.env`。

依赖顺序由 Compose 固化：基础设施健康 → MinIO Bucket 初始化和 Alembic 完成 → Backend/Worker → Frontend。

发布验证：

```bash
curl --fail http://127.0.0.1:18080/health/live
curl --fail http://127.0.0.1:18080/health/ready
curl --fail -H "Authorization: Bearer ${SERVICE_TOKEN}" \
  http://127.0.0.1:18080/api/v1/system/info
```

`/health/live` 只说明进程可响应；流量接入必须以 `/health/ready` 返回 `200` 且三项依赖全部为 `ok` 为准。

## 5. 升级流程

1. 记录当前 Backend/Frontend 镜像 Digest、数据库版本和 `alembic_version`。
2. 执行 PostgreSQL 自定义格式备份、MinIO 版本化备份/快照和 Redis AOF 备份。
3. 在与生产同版本的恢复环境执行 `pg_restore` 并验证 pgvector、pg_search 和 BM25 查询。
4. 在隔离的验收环境运行 `./scripts/check-m7.sh`，确认目标代码、镜像、空卷部署、联调、压测、恢复全部通过。该脚本会短暂停止本地开发依赖，不能直接当作无副作用的生产升级命令。
5. 拉取新 Digest，执行 `docker compose ... up -d --wait`。Migrate 失败会阻止应用启动。
6. 检查 readiness、错误率、Trace、队列积压和一个真实知识库问答。

Alembic 默认采用向前升级。不得让旧代码连接到它不兼容的新 Schema。

## 6. 回滚

- 应用回滚：将 Backend/Frontend Digest 改回上一发布并重新 `up -d --wait`。
- 索引回滚：M6 不提供面向用户的版本回滚；索引发布失败时旧 Active 自动保留。错误内容应修正文档后重新构建。
- Schema 回滚：仅在已验证 downgrade 且没有新版本写入数据时执行。否则停止写入，从发布前备份恢复到独立数据库，验收后切换连接。
- 数据恢复期间 `/health/ready` 必须保持非就绪，禁止将不完整实例接入业务流量。

## 7. 备份策略

- PostgreSQL：至少每日 `pg_dump -Fc`，并结合数据盘快照缩短恢复点；定期在独立数据库恢复演练。
- MinIO：原始 DOCX 和索引图片必须与 PostgreSQL 使用相容的恢复点；启用对象版本化或跨盘复制。
- Redis：开启 AOF；它只保存队列，不是业务事实源。Redis 丢失后以 PostgreSQL `task_record` 审计并人工重提未完成构建。
- 备份必须加密、限制访问并配置保留周期。只验证“备份成功”不算完成，必须验证可恢复。

2026-08-17 的 M7 曾用当时完整数据库执行 `pg_dump -Fc → 新数据库 pg_restore`，并验证 Alembic `0006`、两个扩展和验收知识库数据。当前发布至少还应验证迁移头为 `0008`，以及 `0008` 升级后新建索引的 Parent/Child Chunk 数据可正常构建、查询和恢复。

## 8. 停止与卸载

普通停止不删除数据：

```bash
docker compose -f compose.production.yaml stop
```

`down --volumes` 会删除数据库、队列和对象数据，只允许在明确废弃环境且备份已验证后执行。M7 的生产冒烟使用独立项目名和专用临时卷，脚本结束后才执行该清理。
