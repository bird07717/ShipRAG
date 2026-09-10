# 当前项目状态

> 本文记录当前有效状态，不是任务流水账。最后结合仓库与生产运行数据核对：2026-09-10。

## 开发阶段

项目已有带 `v1.0.1` Git 标签的前后端与生产 Compose 交付形态，并已实现管理 API、对外 Assistant API、RAG/Support 双引擎、文档摄取、Profile 发布和验收脚本。生产镜像默认标签已统一为 `1.0.1`。

## 当前目标

- 保持企业知识库问答的 RAG 主链路质量、可观测性与生产可部署性。
- 为后续 Agent 建立并维护基于仓库事实的 AI 项目认知体系（本次已建立）。

## 正在进行的工作

- 生产服务器已完成源码、PostgreSQL 和 MinIO 数据迁移，并于 2026-09-10 快进到提交 `51e79ebfb35c59731b69fb10dd916018621b68a3`；服务器工作树与 `origin/main` 一致，生产 `.env` 权限为 `600`，迁移备份校验通过。
- 生产 PostgreSQL 位于 Alembic `0040`，共有 1 个知识库、64 条文档记录、2004 个分块和 1096 条图片记录；MinIO 有 70 个文档对象和 1232 个图片对象。活动索引状态为 `ACTIVE`，当前没有排队或运行中的任务。
- PostgreSQL、Redis 和 MinIO 保持原数据卷持续运行；2026-09-10 已使用现有 `v1.0.1` 源码构建并切换后端、前端和两个 Worker 至 `1.0.1` 镜像。随后仅重建前端以显式转发 Assistant SSE 的 `X-Accel-Buffering` 响应头；前后端健康检查均返回 HTTP 200，Alembic 仍为 `0040`，知识库和文档数量不变。
- Assistant 生产验收已覆盖鉴权、知识库与欢迎语、会话与消息、真实 SSE、幂等重放与冲突、外部用户隔离、文档/图片下载及删除资源拒绝。SSE 经生产 Nginx 返回 HTTP 200、`text/event-stream`、`Cache-Control: no-cache, no-transform`、`X-Accel-Buffering: no`，并到达 `complete` 终态，已具备内网联调条件。
- 已新增 Assistant 遗留请求预览/恢复命令；当前数据库原有的 25 条遗留 `assistant_request` 及对应 Assistant 消息/Trace 已保留记录并恢复为失败终态。另有 2 条确认无业务价值的内部 Support 轮次，已在完整数据库备份后精确删除 2 条 Trace 与 4 条消息，未删除所属会话。
- Assistant 对接契约已统一：会话归属、知识库/Active Index、幂等及并发前置失败在 SSE 建立前返回 HTTP JSON；SSE `context` 使用公开字段白名单，已知短暂错误保留可重试语义；流发送失败或客户端断连会事务取消仍为 `RUNNING` 的预留请求及其关联消息/Trace；资源代理转发可用的 `Content-Length`，且不再暴露已删除文档的图片；业务后台新会话锁使用稳定 `client_chat_id`。
- 对外业务后台指南已按标准服务契约重构，包含可跳转目录、整体接入及恢复流程、详细接口关系、逐接口请求/响应字段说明、SSE 事件契约、错误处理和验收清单；文档注明测试阶段使用临时 Token、正式上线前必须轮换，但不记录真实凭据，也不包含平台核心逻辑或我方运维操作。

## 当前阻塞

- 2026-09-02 的 `scripts/check.sh` 未通过：Ruff 格式检查发现 2 个待格式化文件并停止后续步骤。绕过该门禁的后端诊断测试在 `tests/test_api.py::test_service_api_requires_bearer_token` 超过 180 秒未完成；因此后端完整 Pytest、Smoke 与覆盖率结果仍不可得。
- 同次前端校验中，ESLint、Vue Type Check 与生产构建通过，18 个测试文件共 108 项测试通过；但 API 和 composables 目录覆盖率低于配置门槛，`test:coverage` 失败。详情、已确认格式技术债及排查注意见 [`known-issues.md`](known-issues.md)。
- 正式上线安全收口尚未完成：UFW 已启用，但当前检查账户不能读取具体规则；Docker 发布端口可能绕过普通 UFW 入站规则，必须核对 `DOCKER-USER` 或上游防火墙是否仅允许获准业务来源访问 `18080`。
- 服务器 `cm` 账号的 SSH 公钥尚未获得 GitHub 仓库读取权限，直接执行 `git pull` 会返回 `Permission denied (publickey)`；当前使用经校验的一次性 Git bundle 快进同步，后续应配置只读 Deploy Key。

## 近期下一步

1. 完成 UFW/`DOCKER-USER` 来源限制和临时凭据轮换。
2. 为服务器 `cm` 账号配置 GitHub 仓库只读 Deploy Key，恢复标准的 `git pull --ff-only` 部署流程。
3. RAG 或生产配置变更前后，按 `rag-acceptance/README.md` 执行并记录黄金集对比。
