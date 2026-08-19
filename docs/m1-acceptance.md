# M1 验收记录

验收日期：2026-08-17，验收环境为当前 Linux 工作区。

## 工程骨架

- [x] 根目录 README、EditorConfig、GitIgnore 和 `.env.example`。
- [x] FastAPI 包、API 分层、配置、日志、请求中间件和安全错误响应。
- [x] RQ Worker 共享同一应用包和配置，监听 `ingestion/index_build/maintenance`。
- [x] Vue 3、TypeScript、Vite、Element Plus 应用壳层。
- [x] Python 完整依赖树及 npm lockfile 已提交。
- [x] Bash 和 PowerShell Bootstrap/统一检查脚本。
- [x] Git 仓库初始化为 `main`，秘密、虚拟环境、依赖和构建产物已忽略。

## 基础设施

- [x] PostgreSQL 17.11 + pgvector 0.8.6 实际启动并通过健康检查。
- [x] Alembic `0001` 已执行，数据库查询确认 `vector:0.8.6`。
- [x] Redis 8.8.1 持久化、密码和健康检查实际通过。
- [x] MinIO Server 和初始化 Client 实际启动。
- [x] `rag-documents`、`rag-images` Bucket 存在且没有匿名访问策略。
- [x] 所有容器镜像使用固定版本标签。
- [x] 为避免占用主机已有服务，开发端口使用 `15432/16379/19000/19001`。

## 运行与观测

- [x] `/health/live` 实机返回 HTTP 200。
- [x] `/health/ready` 并行检查 PostgreSQL、扩展、Redis、MinIO 和 Bucket，实机返回 HTTP 200。
- [x] 请求 ID 接收、生成、日志绑定和响应头返回。
- [x] 依赖失败返回 HTTP 503 和脱敏组件结果。
- [x] `/api/v1` 服务级 Bearer Token 认证骨架及测试。
- [x] 开发环境 CORS 白名单。
- [x] RQ Worker 实际消费 `app.tasks.health.ping`，任务结果为 `SUCCESSFUL`。
- [x] Vite 实际启动，首页、主模块和代理后的 Readiness 均返回 HTTP 200。

## 质量门禁

- [x] Ruff 检查与格式检查。
- [x] mypy 严格模式：18 个源文件无问题。
- [x] pytest：8 项通过，后端覆盖率 87%。
- [x] ESLint 和 vue-tsc 通过。
- [x] Vitest：6 项通过，语句/行/函数覆盖率 100%，分支覆盖率 85%。
- [x] Vite生产构建成功。
- [x] npm生产依赖审计为0个已知漏洞。

## M1 边界

M1 不包含业务 Schema、知识库 CRUD、文档上传、正式摄取 Pipeline 或 Model Gateway 业务实现。这些属于后续里程碑。M0 后续已通过真实 Provider 契约探测，并将 `EMBEDDING_DIMENSION` 冻结为 `1024`。

## M2 前置门禁

- [x] 根据真实 Embedding Provider 响应冻结 `EMBEDDING_DIMENSION=1024`。
- [ ] 完成 DOCX/Mammoth/OOXML 真实样本验证。
- [x] `pg_search` 许可证风险已按“未修改、仅服务端托管、不分发”接受；生产检索集成仍需完成 M4 技术门禁。
