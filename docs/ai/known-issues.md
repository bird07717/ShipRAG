# 已知问题与注意事项

## 已确认的限制与风险

| 项目 | 状态 | 影响与处理 |
| --- | --- | --- |
| 外部模型延迟/限流 | 已确认风险 | 嵌入、重排和 LLM 依赖上游服务；RAG 有超时、重试或降级路径，但真实延迟仍应以 Trace 和黄金集观察。参见 `rag-acceptance/README.md`。 |
| 问答结果存在非确定性 | 已确认限制 | 阈值边缘、上游模型和生成会导致单题波动；回归应比较整体分类通过率、错误交付和延迟，不以一次单例断言系统退化。 |
| 完整 Trace 生命周期有限 | 已确认约束 | 默认完整 Trace 7 天、摘要 30 天（均可配置）。运营排查不要错过保留窗口。 |
| Support 旧验收链路不是主线上链路 | 已确认范围 | `support-acceptance/` 保留为 Profile/Support 开发回归；上线 RAG 主链路使用 `rag-acceptance/`。 |
| 在线 LLM 仲裁 | 已决策暂不实施 | 当前使用可审计的确定性路由，达到既定数据触发条件后才重新评审。详见 ADR-0002。 |
| 后端全量 Pytest 无法完成 | 已解决 | 2026-09-11 在本地基础设施健康且数据库升级到 Alembic `0041` 后，完整 615 项 Pytest 与三项 smoke 均通过；原 `test_service_api_requires_bearer_token` 超时未再复现。 |
| 前端覆盖率门槛 | 已解决 | 2026-09-11 补齐管理 API、composables 和 App 分支测试后，19 个测试文件共 127 项通过；`src/api/**` 函数 100%，`src/composables/**` 语句/行 90.31%、函数 95.78%、分支 81.63%，`App.vue` 分支 94.64%，全部达到现有门槛。 |
| 前端生产包体积 | 已确认警告 | 2026-09-11 生产构建通过，但 Vite 警告入口 JavaScript 压缩后为 1,295.09 kB（gzip 428.50 kB），超过 500 kB 警告阈值；可评估动态导入或手动分包。 |
| Assistant 请求记录保留 | 代码已解决，待生产部署 | `0042` 增加终态孤儿清理索引，维护 Worker 启动时及之后每天只删除超过 `ASSISTANT_REQUEST_RETENTION_DAYS`、`conversation_id IS NULL` 且为 `COMPLETED`/`FAILED`/`CANCELLED` 的请求；默认 7 天，0 禁用，明确不删除 `RUNNING`。已关联请求仍随 Conversation 级联删除。生产目前仍为 `0041`，部署前原有 1 条无会话 `FAILED` 记录仍可能存在。 |
| Assistant 幂等指标口径 | 代码已解决，待生产部署 | `0042` 为 `assistant_request` 增加 `replay_count` 和 `last_replayed_at`，终态重放在同一事务内原子更新；`/api/v1/metrics/assistant` 已改为按 `assistant_request.knowledge_id` 统计保护请求及累计重放。生产目前仍为 `0041`，部署新迁移和后端前指标仍保持旧口径。 |
| Assistant 发布配置管理 | 已确认待实现 | `0041` 已提供新建默认下线和统一外部访问门禁，但在“不新增接口”的当前范围内，管理台只读展示状态，不能切换上线或按知识库维护欢迎语及三条标准问题；现有知识库继续使用全局 Golden Set 欢迎配置。增加管理侧写契约后再实现配置、验证和发布闭环。 |
| Assistant 知识库列表就绪检查规模 | 当前规模可接受 | 为保持 `ready` 与真实问答条件一致，列表会按每个已上线知识库读取策略和完整就绪态；当前生产只有 1 个知识库。若扩展到数十个知识库或列表请求频率明显上升，应改为批量快照查询或短时缓存并在配置变化时失效，避免查询次数线性增长。 |
| 服务器 GitHub 非交互拉取 | 已确认操作约束 | 生产服务器仓库的 `origin` 使用 GitHub SSH 地址；`cm` 账号可在交互式输入 SSH 私钥口令后正常拉取。非交互自动化无法提供该口令，后续服务器 `git pull` 由用户手动执行。 |
| 生产 Nginx 后端地址缓存 | 已解决 | 2026-09-11 已在生产部署共享 upstream、Docker DNS 和运行时 `resolve`；后端容器地址变化不再要求同步重启前端。Production Compose Smoke 已覆盖“后端换 IP、前端不重启”的恢复场景，生产外部健康入口当前正常。 |
| 生产模型凭据缺失导致 fake 查询链路 | 已解决 | staging/production 已禁止 M2/M3/M5 显式或隐式回退 fake，并记录实际查询模型。2026-09-11 生产 Golden Trace 确认 58 个检索轮次实际使用 SiliconFlow Embedding、Rerank 和 LLM，5 个噪声轮次在模型调用前由输入门禁短路；查询模型与 Active Index 快照一致。 |
| 管理台无凭证请求刷屏 | 已解决 | 管理台凭证门禁和 401 后停止轮询已部署。生产核对时，认证失败最后集中发生在新前端部署前，最近 15 分钟新增为 0；历史错误日志按现有 30 天策略清理。后端仍按认证契约记录真正到达的未授权请求。 |

## 已核实的测试基线

2026-09-11 的最新验证结果：

- `bash scripts/check.sh` 完整通过：Ruff lint/format、Mypy（113 个源文件）、后端 635 项 Pytest、摄取/RAG/管理三项 fake smoke、前端 ESLint/类型检查/覆盖率测试和生产构建均成功。
- 后端 smoke 累计覆盖率精确为 80.06%，达到 80% 门槛；前端 19 个测试文件共 127 项通过，API、composables、utils、components、views 和 `App.vue` 的各自门槛全部通过。
- 先前记录的后端 `test_service_api_requires_bearer_token` 超时与前端 API/composables 覆盖率不足均已解决，不再构成提交阻塞。
- Production Compose Smoke 已使用全新数据库验证 Alembic `0042`、Assistant 默认私有、发布门禁、SSE、两个 Worker、服务健康及 Nginx 后端换 IP 自动恢复；生产服务器当前容器健康，但数据库仍位于 Alembic `0041`，待下一次部署升级。
- 生产 Assistant Golden Run 11 已真实执行 53 个用例/63 轮，原断言为 51/53（96.2%），错误交付、范围外误命中、噪声误命中和 HTTP/SSE 错误均为 0；5 个噪声用例全部通过。两项不一致确认是断言与安全引用规则/拒答语义不一致，修正断言后离线重放同一响应为 53/53，真实模型全量复跑待执行。

确认方式：在已 bootstrap 的环境运行 `bash scripts/check.sh`，或针对每项运行相应 Pytest/Ruff 命令；将真实输出与当前工作树对应后更新本文件。

## 排查注意

- API 404 的统一错误消息可能提示服务进程仍是旧版本；部署或路由变更后检查后端是否重启、容器镜像是否更新。
- 构建中/激活中索引、Profile 发布版本与会话冻结版本不同是设计行为；排查回答时同时检查 Trace、会话索引和知识库活动版本。
- 生产 Docker Smoke 会创建和删除临时容器/卷；不要在不了解目标 Docker 上下文时执行。
- 不要在日志、Issue、认知文档或测试输出中复制 Token、API Key、数据库密码、MinIO 凭据或 `.env` 值。
