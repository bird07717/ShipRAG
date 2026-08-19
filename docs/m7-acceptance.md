# M7 发布验收

- 版本：V1.0.0
- 验收日期：2026-08-17
- 结论：**PASS**
- 总入口：`./scripts/check-m7.sh`

> **证据范围**：本记录是 2026-08-17 的历史验收结果。当次备份恢复验证到 Alembic `0006`。当前仓库的迁移头为 `0008`，因此本记录不能证明 `0007`、`0008` 或其后改动已经完成发布验收。

## 验收组成

1. M0–M6 全量静态检查、单测、覆盖率、前端生产构建和真实基础设施冒烟。
2. Backend、Worker、Frontend 生产镜像构建。
3. 从空 PostgreSQL/Redis/MinIO 卷启动完整生产 Compose，执行 Alembic并通过 Nginx 检查前端和 readiness。
4. 真实 HTTP 上传 DOCX，经 Redis/RQ Worker 完成 OCR/Vision、Element、Chunk、Embedding 和 Active Index 发布。
5. Playground 与 Chat SSE 联调，验证来源和事件顺序。
6. Liveness、数据库和完整 Fake RAG 并发压测。
7. Worker、Redis、MinIO、PostgreSQL错误恢复演练。
8. PostgreSQL 自定义格式备份恢复到独立数据库并审计。

## 已通过证据

- 鉴权边界：无 Token 返回 401，正确服务凭据返回 200。
- 文档入库任务：`SUCCEEDED`；样例生成 6 个 Elements、5 个 Chunks。
- Playground：`COMPLETED` 且包含有效 Sources。
- SSE 顺序：`trace → source → message* → done`。
- Worker 恢复：停机期间任务保持 `QUEUED`，重启后 `SUCCEEDED`。
- 依赖恢复：Redis、MinIO、PostgreSQL 停止时 `not_ready`，恢复后均为 `ready`；liveness 全程可响应。
- 备份恢复：当次环境的 Alembic `0006`、`vector`/`pg_search` 两个扩展和验收知识库数据通过审计。
- 独立生产栈：Backend/Worker/Frontend 镜像成功构建，从空卷启动，Nginx 前端与代理 readiness 通过，临时卷最终删除。
- npm 生产依赖和完整开发依赖审计均为 0 个已知漏洞；Vite/Vitest 已升级到修复版本。

发布档压测使用并发 32：Liveness 200 请求 p95 311.92 ms，数据库 200 请求 p95 467.95 ms，完整 Fake RAG 50 请求 p95 1215.97 ms；全部错误率为 0，低于 3000 ms 门槛。

压测最终数字以本文件执行后的控制台报告为准，基线、边界和复测方法见 [performance-baseline.md](deployment/performance-baseline.md)。

## 发布边界

- Element Plus 包体积告警按项目决策不阻塞 V1.0.0。
- 本次是单机故障恢复，不是 PostgreSQL/Redis/MinIO 跨节点自动故障转移或容灾切换验收。
- Fake Provider 压测证明应用数据链路，不代表云模型 SLA 或账号额度。
- 生产 TLS、企业身份、WAF 和外部负载均衡由部署环境提供。
