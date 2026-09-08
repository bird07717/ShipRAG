# 已知问题与注意事项

## 已确认的限制与风险

| 项目 | 状态 | 影响与处理 |
| --- | --- | --- |
| 外部模型延迟/限流 | 已确认风险 | 嵌入、重排和 LLM 依赖上游服务；RAG 有超时、重试或降级路径，但真实延迟仍应以 Trace 和黄金集观察。参见 `rag-acceptance/README.md`。 |
| 问答结果存在非确定性 | 已确认限制 | 阈值边缘、上游模型和生成会导致单题波动；回归应比较整体分类通过率、错误交付和延迟，不以一次单例断言系统退化。 |
| 完整 Trace 生命周期有限 | 已确认约束 | 默认完整 Trace 7 天、摘要 30 天（均可配置）。运营排查不要错过保留窗口。 |
| Support 旧验收链路不是主线上链路 | 已确认范围 | `support-acceptance/` 保留为 Profile/Support 开发回归；上线 RAG 主链路使用 `rag-acceptance/`。 |
| 在线 LLM 仲裁 | 已决策暂不实施 | 当前使用可审计的确定性路由，达到既定数据触发条件后才重新评审。详见 ADR-0002。 |
| 后端 Ruff 格式检查 | 已确认技术债 | 2026-09-02 执行 `ruff format --check backend` 时，`app/rag/doc_router.py` 与 `app/services/rag.py` 会被重新格式化；尚未在本次任务中修改它们。 |
| 后端全量 Pytest 无法完成 | 已确认阻塞 | 2026-09-02 以详细进度运行并设置 180 秒上限时，前 14 项测试通过后停在 `tests/test_api.py::test_service_api_requires_bearer_token`，未产生终态并以超时退出。根因待确认；应先在隔离环境复现并检查该测试的应用生命周期、认证依赖和本地服务/网络等待。 |
| 前端覆盖率门槛 | 已确认门禁失败 | 2026-09-02 中 18 个测试文件、108 项测试均通过，但 `test:coverage` 未达到 `vite.config.ts` 的目录阈值：`src/api/**` 函数 56.79%（要求 80%）、分支 79.50%（要求 80%）；`src/composables/**` 语句/行 75.92%（要求 80%）、函数 72.04%（要求 75%）、分支 71.65%（要求 80%）。 |
| 前端生产包体积 | 已确认警告 | 生产构建通过，但 Vite 警告入口 JavaScript 压缩后为 1,291.66 kB（gzip 427.76 kB），超过 500 kB 警告阈值；可评估动态导入或手动分包。 |
| Assistant 请求记录保留 | 已确认容量风险 | `assistant_request` 当前没有独立的终态记录清理任务；已关联 Conversation 的记录可随会话级联删除，但关联前失败且 `conversation_id` 为空的记录可能持续保留。正常 ASGI 发送失败和客户端断连会幂等取消仍为 `RUNNING` 的请求及其关联运行态；进程强制终止等无法执行响应收口的遗留状态已提供 `app.assistant.recovery` 预览/事务恢复命令。容量仍需通过保留策略和表增长监控处理。 |
| Assistant 幂等指标口径 | 已确认观测缺口 | `/api/v1/metrics/assistant` 的 `idempotency` 区块当前查询 `support_idempotency`，没有统计 Assistant 门面的 `assistant_request`，因此不能表示外部 Assistant 请求保护数或重放率；应由业务后台指标或受控数据分析补充，后续再修正平台指标。 |

## 已核实的测试基线

2026-09-02 的验证结果：

- `cd backend && ../.venv/bin/python -m pytest --collect-only -q -o addopts='' -p no:cacheprovider` 成功收集 558 项测试，其中包含 `tests/test_api.py`；当前 `backend/` 中不存在 `get_release_repository` 引用。因此，旧技术文档所述的该收集错误不再是当前已知问题。
- 后端 Mypy 对 111 个源文件通过，Ruff lint 通过；但 `scripts/check.sh` 在 Ruff 格式门禁停止，后端完整 Pytest/Smoke/覆盖率未完成，具体阻塞见上表。
- 前端 ESLint、Vue Type Check 与生产构建通过；18 个测试文件、108 项测试通过，但覆盖率门禁失败，具体缺口见上表。
- `scripts/check.sh` 仍会在后端测试完成后额外执行三个 smoke 任务累计覆盖率；本次未能到达这些步骤。
- 2026-09-07 对 Assistant 恢复功能执行 Ruff、Mypy 及相关定向 Pytest，后续扩展到 Assistant/config 共 37 项定向测试通过；当前数据库 25 条遗留请求、25 条 Assistant 消息和 25 条 Assistant Trace 已事务恢复。另精确删除 2 条无业务价值的内部 Support Trace 和对应 4 条消息；最终 `assistant_request RUNNING=0`、`rag_trace RUNNING=0`、`message STREAMING=0`、活跃 `task_record=0`。隔离 Production Compose Smoke 同日通过。
- 2026-09-08 统一 Assistant 前置 HTTP 错误、资源长度响应和业务后台 `client_chat_id` 约束；Ruff、格式、Mypy 及 13 项 Assistant 定向测试通过，隔离 Production Smoke 确认无效会话返回 HTTP 404 JSON、未建立 SSE。`test_api.py` 定向执行仍复现上述 ASGI 测试夹具超时，未计为通过。

确认方式：在已 bootstrap 的环境运行 `bash scripts/check.sh`，或针对每项运行相应 Pytest/Ruff 命令；将真实输出与当前工作树对应后更新本文件。

## 排查注意

- API 404 的统一错误消息可能提示服务进程仍是旧版本；部署或路由变更后检查后端是否重启、容器镜像是否更新。
- 构建中/激活中索引、Profile 发布版本与会话冻结版本不同是设计行为；排查回答时同时检查 Trace、会话索引和知识库活动版本。
- 生产 Docker Smoke 会创建和删除临时容器/卷；不要在不了解目标 Docker 上下文时执行。
- 不要在日志、Issue、认知文档或测试输出中复制 Token、API Key、数据库密码、MinIO 凭据或 `.env` 值。
