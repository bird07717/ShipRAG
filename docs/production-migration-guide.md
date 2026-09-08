# Shipdemo 生产服务器完整迁移手册

目标服务器：`172.17.2.12`。入口：`http://172.17.2.12:18080`。

## 1. 迁移范围与原则

必须一起迁移：

- Git 固定版本；不迁移未提交工作树。
- PostgreSQL 全库：知识库、索引、文档元数据、向量、会话、消息、Trace、发布、配置和任务记录。
- MinIO 两个完整 Bucket：`rag-documents`、`rag-images`。以 Bucket 全量为准，不只复制数据库当前引用对象。
- 原 Redis 数据卷冷备。当前无活动任务时，目标 Redis 使用空卷，避免重放旧队列、锁和延迟任务；冷备保留到回滚窗口结束。
- 部署 Secret。恢复已有数据库时，`MODEL_SECRET_KEY` 和 `MODEL_SECRET_KEY_VERSION` 必须沿用源值；其余 Token/密码经安全渠道配置，不进入 Git、文档、日志或迁移包。

“冻结”只冻结迁移用 Git 提交/Tag 和最终停写窗口，不冻结后续开发。迁移后继续开发，按新提交构建镜像并滚动发布。

## 2. 上线前准备

1. 审阅并提交当前改动，创建迁移 Tag；记录提交 SHA、镜像名和镜像摘要。
2. 在非生产环境通过 `bash scripts/check-m7.sh`；使用真实模型运行 RAG/Support/Assistant 代表性验收。
3. 在目标服务器安装 Docker Engine 和 Compose，预留至少“数据库 + MinIO + 镜像 + 两份备份”的空间。
4. 仅向获准内网业务后台开放 TCP `18080`；PostgreSQL、Redis、MinIO 不对外暴露。
5. 从 `.env.production.example` 创建目标 Secret 文件，权限设为 `600`。不得复制开发默认密码。
6. 提前在目标机拉取/构建固定版本镜像并完成一次空库启动演练。

当前库存在已加密的模型凭据。迁移前只检查版本，不输出密钥：

```sql
SELECT api_key_key_version, count(*)
FROM model_config
WHERE api_key_ciphertext IS NOT NULL
GROUP BY api_key_key_version;
```

## 3. 第一次演练

停机前先做一次全量备份并恢复到临时环境，验证：

- `pg_restore --list` 可读，临时数据库可完整恢复并位于 Alembic head。
- 两个 MinIO Bucket 的对象数和总字节数与源端一致，随机抽查文档及图片哈希。
- 管理台、知识库、历史会话、文档下载、图片加载、RAG、Support、Assistant SSE 均可用。
- 记录备份、传输、恢复耗时，以此确定正式停写窗口。

演练通过后删除临时环境，不删除源环境和正式备份。

## 4. 正式停写与源端检查

1. 阻止管理写入和业务 Assistant 流量，停止 API、`worker-index`、`worker-maintenance`；保持 PostgreSQL、Redis、MinIO 运行以便导出。
2. 等待或取消在途任务，确认以下计数全部为 `0`：

```sql
SELECT count(*) FROM task_record WHERE status IN ('PENDING', 'QUEUED', 'RUNNING');
SELECT count(*) FROM assistant_request WHERE status = 'RUNNING';
SELECT count(*) FROM rag_trace WHERE status = 'RUNNING';
SELECT count(*) FROM message WHERE status = 'STREAMING';
```

3. Assistant 遗留请求先预览，异常时备份后再恢复：

```bash
cd backend
../.venv/bin/python -m app.assistant.recovery --stale-hours 24
# 确认停写及备份完成后才可追加 --apply
```

4. 记录迁移基线：Alembic revision、各核心表行数、两个 Bucket 的对象数/字节数、代码 SHA、镜像摘要和时间戳。

## 5. 源端全量备份

在仓库根目录执行。备份目录放在仓库之外并限制权限：

```bash
MIGRATION_DIR=/safe/path/shipdemo-migration-YYYYMMDD-HHMMSS
mkdir -p "${MIGRATION_DIR}/minio"
chmod 700 "${MIGRATION_DIR}"

docker compose exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "${MIGRATION_DIR}/postgres.dump"

docker compose run --rm --no-deps \
  -v "${MIGRATION_DIR}/minio:/backup" \
  --entrypoint /bin/sh minio-init -ec '
    mc alias set source http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
    mc mirror --overwrite "source/$MINIO_DOCUMENT_BUCKET" "/backup/$MINIO_DOCUMENT_BUCKET"
    mc mirror --overwrite "source/$MINIO_IMAGE_BUCKET" "/backup/$MINIO_IMAGE_BUCKET"
  '

docker compose stop redis
docker run --rm \
  -v enterprise-rag_redis-data:/source:ro \
  -v "${MIGRATION_DIR}:/backup" \
  alpine:3.22 tar -C /source -czf /backup/redis-data.tar.gz .

git rev-parse HEAD > "${MIGRATION_DIR}/git-commit.txt"
docker compose exec -T postgres pg_restore --list \
  < "${MIGRATION_DIR}/postgres.dump" > "${MIGRATION_DIR}/postgres.dump.list"
(
  cd "${MIGRATION_DIR}"
  sha256sum postgres.dump redis-data.tar.gz > SHA256SUMS
  find minio -type f -print0 | sort -z | xargs -0 sha256sum \
    > MINIO-SHA256SUMS
)
chmod -R go-rwx "${MIGRATION_DIR}"
```

另行生成不含真实值的 Secret 清单，确认目标已配置：`SERVICE_TOKEN`、`ASSISTANT_SERVICE_TOKEN`、数据库/Redis/MinIO 密码、模型 Provider 密钥、`MODEL_SECRET_KEY` 及版本。Secret 不与备份包同渠道传输。

## 6. 传输与目标恢复

1. 用受控的 SSH/内网文件通道传输备份，在目标备份目录执行 `sha256sum -c SHA256SUMS` 和 `sha256sum -c MINIO-SHA256SUMS`。
2. 在目标机检出 `git-commit.txt` 指定提交；配置权限为 `600` 的 `.env.production`。
3. 先启动空的基础设施：

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  up -d --wait postgres redis minio
docker compose --env-file .env.production -f compose.production.yaml \
  run --rm minio-init
```

4. 目标数据库必须是此次创建的空库。恢复全库：

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  exec -T postgres sh -lc \
  'pg_restore --clean --if-exists --no-owner --exit-on-error \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < /safe/path/postgres.dump
```

5. 恢复全部 MinIO 对象：

```bash
MIGRATION_DIR=/safe/path/shipdemo-migration-YYYYMMDD-HHMMSS
docker compose --env-file .env.production -f compose.production.yaml \
  run --rm --no-deps \
  -v "${MIGRATION_DIR}/minio:/backup:ro" \
  --entrypoint /bin/sh minio-init -ec '
    mc alias set target http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
    mc mirror --overwrite "/backup/$MINIO_DOCUMENT_BUCKET" "target/$MINIO_DOCUMENT_BUCKET"
    mc mirror --overwrite "/backup/$MINIO_IMAGE_BUCKET" "target/$MINIO_IMAGE_BUCKET"
  '
```

6. 不恢复 Redis AOF 到正式实例；保留 `redis-data.tar.gz` 作为冷备。运行迁移并启动应用：

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  run --rm migrate
docker compose --env-file .env.production -f compose.production.yaml \
  up -d --wait backend worker-index worker-maintenance frontend
```

## 7. 恢复后验收

按顺序完成，任何一项失败都停止业务切流：

1. `docker compose ... ps` 全部预期服务健康，`migrate` 成功退出。
2. `GET /health/live`、`GET /health/ready` 返回正常。
3. Alembic revision 为仓库 head；核心表行数与源端基线一致。
4. 两个 Bucket 的对象数、总字节数和哈希清单一致；抽查上传文档下载及引用图片。
5. 管理台可登录，知识库/索引/模型配置/发布/历史会话完整；模型连接测试通过。
6. RAG、Support 各完成一轮真实问答；Assistant 完成知识库列表、首次提问、续聊、历史分页、文档和图片代理验证。
7. 运行 `bash scripts/check-m7.sh` 对应的生产 smoke/验收项；正式数据环境不要运行会清理卷的临时 smoke 项目命令。
8. 观察日志、数据库连接、Redis 队列、MinIO 错误和模型 Provider 限流至少一个业务高峰。

业务后台最后切换到 `http://172.17.2.12:18080`。先小流量验证，再全量切换；Token 只通过双方服务端 Secret 配置。

## 8. 回滚与迁移后发布

- 切流前保留源机及源备份，不再允许源端写入；建议保留 72 小时观察窗口。
- 需要回滚时，先停止目标写入，再把业务入口切回仍保持原快照的源端；不要合并两端切流后产生的数据。
- 稳定后再下线源端。数据库、MinIO、Redis 冷备和校验清单按备份策略归档，销毁前二次确认。
- 后续修改不需要重新迁移全量数据：提交新版本、通过门禁、构建新镜像，执行 Alembic 向前迁移并滚动重启应用；涉及不可逆 schema 或对象格式变化时单独制定备份与回滚方案。
