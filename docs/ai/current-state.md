# 当前项目状态

> 本文记录当前有效状态，不是任务流水账。最后按仓库静态内容核对：2026-09-02。

## 开发阶段

项目已有可运行的 v1.0.0 前后端与生产 Compose 交付形态，并已实现管理 API、对外 Assistant API、RAG/Support 双引擎、文档摄取、Profile 发布和验收脚本。近期提交聚焦 Assistant 接口设计、RAG 链路优化与测试补全。

## 当前目标

- 保持企业知识库问答的 RAG 主链路质量、可观测性与生产可部署性。
- 为后续 Agent 建立并维护基于仓库事实的 AI 项目认知体系（本次已建立）。

## 正在进行的工作

- 工作树存在未提交的生产相关改动：`.env.production.example`、`backend/pyproject.toml`、`backend/requirements.prod.lock`、`scripts/smoke-production.sh`。静态差异显示其涉及生产依赖可用性、Assistant Token 与 smoke 校验；改动的负责人、验收状态和是否应合并均**待确认**，不应视为已完成发布。

## 当前阻塞

- 2026-09-02 的 `scripts/check.sh` 未通过：Ruff 格式检查发现 2 个待格式化文件并停止后续步骤。绕过该门禁的后端诊断测试在 `tests/test_api.py::test_service_api_requires_bearer_token` 超过 180 秒未完成；因此后端完整 Pytest、Smoke 与覆盖率结果仍不可得。
- 同次前端校验中，ESLint、Vue Type Check 与生产构建通过，18 个测试文件共 108 项测试通过；但 API 和 composables 目录覆盖率低于配置门槛，`test:coverage` 失败。详情、已确认格式技术债及排查注意见 [`known-issues.md`](known-issues.md)。
- 未启动 Compose、运行迁移或真实模型黄金集；当前环境的端到端可运行性仍待确认。

## 近期下一步

1. 由当前改动负责人确认并验证上述未提交生产改动；若合并，应更新环境/已知问题文档。
2. 在具备依赖与服务的环境运行与任务相关的最小校验，必要时运行 `scripts/check.sh` 或 `scripts/check-m7.sh`。
3. RAG 或生产配置变更前后，按 `rag-acceptance/README.md` 执行并记录黄金集对比。
