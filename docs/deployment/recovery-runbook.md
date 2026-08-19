# 故障恢复 Runbook

## 通用处置顺序

1. 检查 `/health/live` 和 `/health/ready`，不要只看容器 `running`。
2. 保存 `request_id`、`trace_id`、`task_id`，检查结构化日志，禁止复制 Authorization 或模型密钥。
3. 将非就绪 Backend 从外层负载均衡摘除。
4. 先恢复持久依赖，再恢复 Backend，最后恢复 Worker 并观察队列。
5. 验证知识库 Active Index、一次 Playground 和一次 SSE Chat 后再恢复流量。

## PostgreSQL 不可用

现象：readiness 的 `postgres=error`，管理与 Chat API 返回依赖错误或 500，liveness 仍为 200。

处置：

1. 检查磁盘、容器日志、连接数和 PostgreSQL 进程。
2. 恢复同一版本的 ParadeDB 镜像，确认 `shared_preload_libraries` 包含 `pg_search,pg_cron`。
3. 验证 `vector`、`pg_search`、Alembic 版本和 BM25 索引。
4. Backend 的连接池启用 `pool_pre_ping`，依赖恢复后新请求会重建连接；readiness 变为 ready 后执行知识库查询。
5. 无法恢复时从 PostgreSQL 备份和相容 MinIO 恢复点建立新实例，验证后切换。

## Redis/RQ 不可用

现象：readiness 的 `redis=error`；在线 RAG 仍可能依赖 PostgreSQL和模型继续回答，但上传构建无法可靠投递。

处置：

1. 暂停文档变更操作，恢复 Redis AOF。
2. Redis 恢复后重启 RQ Worker；Worker 不作为永远自愈的事实来源。
3. 对照 PostgreSQL `task_record` 和 RQ 队列：`QUEUED` 且无 Job 的构建应通过管理 API重新提交，不能直接伪造成功。
4. 验证一个 Worker 停机期间入队的构建在 Worker 重启后完成。

## MinIO 不可用

现象：readiness 的 `minio=error`；Word 上传、图片预览、文档重建和多模态 Rerank 受影响。

处置：

1. 暂停文档变更，检查数据盘、Bucket 和访问凭据。
2. 恢复 `rag-documents`、`rag-images` 私有 Bucket；不得临时改为公共读。
3. readiness 恢复后读取一张 `image_asset`，再提交一次构建验证读写。
4. 若 PostgreSQL 与 MinIO 恢复点不一致，保留当前 Active Index，重新上传缺失源文档并全量重建。

## Worker 异常退出

RQ Job 仍为 `QUEUED` 时，重启 Worker 后继续消费。Worker 启动时会清理 RQ 注册表，
并对账 PostgreSQL 中的 `RUNNING/BUILDING` 任务：若对应 Job 已失败或不存在，且没有
Worker 持有该 Job，则原子标记为 `FAILED/ABANDONED_JOB`，随后通过正式 Retry API 创建
新的 `REPROCESS` 索引。不得直接复活已经部分写入的失败 Job，也不得把孤儿任务修改为成功。

整批索引 Job 的最长执行时间由 `RQ_INDEX_JOB_TIMEOUT_SECONDS` 控制，默认 7200 秒。
索引只有全部文档、Element、Chunk 和 Embedding 完整后才发布；失败索引不会替换当前
Active。长时间处于 `RUNNING` 的任务必须同时核对 Worker 当前 Job 和 RQ 状态，不能只按
数据库 `updated_at` 判断。

## 云模型故障

- Rerank 网络错误、超时、429、5xx：允许降级为 RRF 排序，Trace 标记 `DEGRADED`。
- Rerank 4xx 或协议错误：不降级，避免掩盖错误配置。
- LLM 失败：SSE 以 `error` 终止，Message/Trace 标记失败，不伪造答案。
- OCR/Vision 单项失败：图片状态记录失败并按策略降级；Embedding 或完整性失败时索引禁止发布。

先检查厂商状态、额度和凭据，再按指数退避重试。禁止把厂商原始鉴权响应写入普通日志或 Trace。

## M7 自动恢复演练

`python -m app.release.acceptance` 会顺序验证：

- Worker 停止时任务保持 `QUEUED`，Worker 重启后变为 `SUCCEEDED`。
- Redis、MinIO、PostgreSQL 分别停止时 readiness 变为 `not_ready`，liveness 保持可用。
- 每项依赖重新启动后 readiness 恢复为 `ready`，数据库 API 可继续访问。
- 最后执行逻辑备份恢复校验，并清理验收数据。
