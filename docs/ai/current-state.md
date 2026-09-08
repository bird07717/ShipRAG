# 当前项目状态

> 本文记录当前有效状态，不是任务流水账。最后结合仓库与本地运行数据核对：2026-09-08。

## 开发阶段

项目已有可运行的 v1.0.0 前后端与生产 Compose 交付形态，并已实现管理 API、对外 Assistant API、RAG/Support 双引擎、文档摄取、Profile 发布和验收脚本。近期提交聚焦 Assistant 接口设计、RAG 链路优化与测试补全。

## 当前目标

- 保持企业知识库问答的 RAG 主链路质量、可观测性与生产可部署性。
- 为后续 Agent 建立并维护基于仓库事实的 AI 项目认知体系（本次已建立）。

## 正在进行的工作

- 工作树存在未提交改动，包含生产编排/环境示例、Assistant 恢复工具和对接/迁移文档；完成发布门禁并形成固定 Git 提交或 Tag 前，不应视为可迁移版本。
- 已新增 Assistant 遗留请求预览/恢复命令；当前数据库原有的 25 条遗留 `assistant_request` 及对应 Assistant 消息/Trace 已保留记录并恢复为失败终态。另有 2 条确认无业务价值的内部 Support 轮次，已在完整数据库备份后精确删除 2 条 Trace 与 4 条消息，未删除所属会话。
- Assistant 对接契约已统一：会话归属、知识库/Active Index、幂等及并发前置失败在 SSE 建立前返回 HTTP JSON；SSE `context` 使用公开字段白名单，已知短暂错误保留可重试语义；流发送失败或客户端断连会事务取消仍为 `RUNNING` 的预留请求及其关联消息/Trace；资源代理转发可用的 `Content-Length`，且不再暴露已删除文档的图片；业务后台新会话锁使用稳定 `client_chat_id`。
- 对外业务后台指南已按标准服务契约重构，包含可跳转目录、整体接入及恢复流程、详细接口关系、逐接口请求/响应字段说明、SSE 事件契约、错误处理和验收清单；文档注明测试阶段使用临时 Token、正式上线前必须轮换，但不记录真实凭据，也不包含平台核心逻辑或我方运维操作。

## 当前阻塞

- 2026-09-02 的 `scripts/check.sh` 未通过：Ruff 格式检查发现 2 个待格式化文件并停止后续步骤。绕过该门禁的后端诊断测试在 `tests/test_api.py::test_service_api_requires_bearer_token` 超过 180 秒未完成；因此后端完整 Pytest、Smoke 与覆盖率结果仍不可得。
- 同次前端校验中，ESLint、Vue Type Check 与生产构建通过，18 个测试文件共 108 项测试通过；但 API 和 composables 目录覆盖率低于配置门槛，`test:coverage` 失败。详情、已确认格式技术债及排查注意见 [`known-issues.md`](known-issues.md)。
- 本地 PostgreSQL、Redis、MinIO Compose 容器已确认健康，数据库位于 Alembic `0040`；迁移前运行态检查结果为 `assistant_request RUNNING=0`、`rag_trace RUNNING=0`、`message STREAMING=0`、活动 `task_record=0`。隔离 Production Compose Smoke 已验证迁移、API、两个 Worker、前端和 Assistant 鉴权入口；使用当前持久化数据的正式启动、真实模型黄金集及生产迁移仍待执行。

## 近期下一步

1. 完成未提交改动审阅和生产门禁，提交并创建不可变迁移 Tag。
2. 按[生产服务器完整迁移手册](../production-migration-guide.md)完成演练、最终停写快照、恢复、业务验收和回滚窗口。
3. RAG 或生产配置变更前后，按 `rag-acceptance/README.md` 执行并记录黄金集对比。
