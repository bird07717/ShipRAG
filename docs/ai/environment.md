# 环境、启动与验证

## 前置条件

- Python：项目声明 `>=3.11,<3.15`；生产 Docker 镜像基于 Python 3.12.11。
- Node.js：前端声明 `>=20.19.0`；生产构建镜像使用 Node 20.20.0。
- Docker Compose：本地基础设施和生产编排均依赖它。
- 本地依赖安装脚本会创建根目录 `.venv`；不要提交 `.env` 或其中的真实敏感值。

## 安装与配置

```bash
bash scripts/bootstrap.sh
```

该脚本创建虚拟环境、依据 `backend/requirements.lock` 安装后端依赖、以 editable 模式安装后端、执行前端 `npm ci`，并在缺少 `.env` 时由 `.env.example` 复制。开始前审阅 `.env.example` 的变量名称和说明；敏感值仅写入本地安全配置或部署 Secret。

关键配置类别：应用与 CORS、PostgreSQL、Redis/RQ、MinIO、模型提供者、嵌入/检索阈值、保留期、模型密钥加密，以及生产的 `SERVICE_TOKEN` 和 `ASSISTANT_SERVICE_TOKEN`。`ASSISTANT_REQUEST_RETENTION_DAYS` 默认 7，控制未关联 Conversation 的 Assistant 终态请求保留期，设为 0 禁用清理；维护 Worker 启动时清理一次并注册每日任务，永不由该任务删除 `RUNNING`。`REQUEST_ERROR_LOG_RETENTION_DAYS` 默认 30，控制失败 API 请求记录保留期，设为 0 禁用清理；维护 Worker 启动时清理一次，之后通过 Worker 周期维护检查约每天向 `maintenance` 队列投递一次性任务，生产 Compose 显式传入该变量。`ASSISTANT_REQUEST_STALE_SECONDS` 默认 600，维护 Worker 启动时及每 15 分钟把超过该窗口仍 `RUNNING` 的 Assistant 请求收敛为失败（须大于轮次时限，0 禁用）。`CHAT_TURN_CONCURRENCY_LIMIT` 默认 64（0 禁用）与 `CHAT_TURN_WAIT_SECONDS` 默认 2.0 组成每进程问答轮次准入闸门，超限时快速返回 503 `SERVICE_BUSY`；生产 Compose 另支持可选 `BACKEND_LIMIT_CONCURRENCY`（uvicorn 每进程连接上限，不设置则不限制）。生产示例为 `.env.production.example`；Production Compose 对必要密码、服务凭据和 `MODEL_SECRET_KEY` 使用必填变量。恢复已有数据库时必须沿用能解密其中模型凭据的密钥及版本，不能生成新值替代。

千问（阿里云百炼 / DashScope）可在管理台模型配置中用于全部五类模型（LLM、Embedding、Rerank、OCR、Vision），填写模型名称、Base URL 和 API Key；默认 Base URL 为 `https://dashscope.aliyuncs.com/compatible-mode/v1/`。数据库加密密钥优先，也可用 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL` 作为环境兜底。不同地域或业务空间的 Base URL 与 API Key 必须匹配；生产推荐按阿里云百炼实际业务空间配置专属域名。Rerank 不在兼容地址上提供服务：后端会把保存的 compatible-mode 地址自动映射到原生 `/api/v1` 根并调用 `services/rerank/text-rerank/text-rerank`（模型如 `gte-rerank`/`gte-rerank-v2`）；Embedding 走兼容 `/embeddings`（模型如 `text-embedding-v3/v4`，仅文本、必须输出 1024 维，单请求批量上限 10 条，`M2_EMBEDDING_BATCH_SIZE` 默认 8 已在限内、调大需保持 ≤10）；OCR/Vision 使用 qwen-vl 系列模型的兼容 `chat/completions` 多模态调用。`M2_EMBEDDING_PROVIDER`、`M4_OCR_PROVIDER`、`M4_VISION_PROVIDER`、`M5_RERANK_PROVIDER` 均接受 `dashscope` 显式值。

禁止静默使用模拟模型（ADR-0034，2026-10-08）：五类模型（M2 Embedding、M3 LLM、M4 OCR/Vision、M5 Rerank）在 `auto` 下找不到 Key 时，只有自动化测试环境 `APP_ENV=test` 使用模拟实现；development/staging/production 一律明确失败（LLM/Embedding/Rerank 报未配置，OCR/Vision 逐图标记失败、构建继续）。显式 `*_PROVIDER=fake` 仅允许在 development/test 使用（管理台横幅会醒目提示），staging/production 的配置校验拒绝全部五类 `fake`。`auto` 必须能从对应环境变量或可解密的 `model_config` 快照取得有效凭据。本地开发若想离线跑通，应显式设置 `*_PROVIDER=fake`，不要依赖缺 Key 时的自动回退。生产冒烟脚本 `scripts/smoke-production.sh` 五类均使用 `auto`（不发起模型调用）。部署校验还要确认在线查询模型与 Active Index 的 Embedding 模型一致，不能只确认服务进程已启动。

## 本地启动

### RAG V2 运行配置

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `M3_RAG_FULL_DOCUMENT_LIMIT` | `3` | 进入隐藏上下文的候选文档上限。 |
| `M3_RAG_FULL_CONTEXT_TOKEN_BUDGET` | `20000` | 隐藏完整文档上下文的估算 Token 上限。 |
| `M3_RAG_ANSWER_MAX_TOKENS` | `4096` | LLM 2 最大输出 Token。 |
| `M3_RAG_ANSWER_TIMEOUT_SECONDS` | `45.0` | LLM 2 单次回答时限。 |
| `M3_RAG_ANSWER_THINKING_ENABLED` | `false` | 可选地为 RAG V2 的 LLM 2 和其修复调用请求内部思考；上线默认关闭，LLM 1 与其他结构化任务始终默认关闭。 |
| `M3_RAG_ANSWER_THINKING_BUDGET` | `2048` | 启用时 DashScope/Qwen 的最大内部思考 Token；关闭时不生效，与 `M3_RAG_ANSWER_MAX_TOKENS` 的最终回答预算分离。其他 Provider 当前不发送该扩展字段。 |
| `M3_RAG_HISTORY_MAX_ROUNDS` | `4` | 最近完整问答轮次上限。 |
| `M3_HISTORY_MAX_MESSAGES` | `10` | 技术历史消息上限，用户/助手成对选取；当前 Production Compose 未显式转发此变量。 |
| `M3_HISTORY_TOKEN_BUDGET` | `2000` | 技术历史估算 Token 预算；最新完整轮次超预算时历史可能为空。当前 Production Compose 未显式转发此变量。 |
| `M3_RAG_ANSWER_REPAIR_ATTEMPTS` | `1` | 结构、证据白名单或真实实现信息违规后的最多修复次数。 |
| `M3_RAG_SCOPE_MAX_TOKENS` | `800` | 每轮知识库名称/公开范围说明的 Prompt 快照估算 Token 上限；只截断运行时副本，不改数据库说明。 |
| `M3_RAG_QUERY_INTERPRETER_MAX_TOKENS` | `512` | LLM 1 结构化查询理解输出上限。 |
| `M3_RAG_QUERY_INTERPRETER_TIMEOUT_SECONDS` | `25.0` | LLM 1 单次调用时限；完整超时不重试，进入短追问历史兜底。 |
| `M3_RAG_DOCUMENT_CARD_TOP_K` | `5` | 文档卡片候选上限。 |
| `M3_TURN_DEADLINE_SECONDS` | `90.0` | 单轮 RAG 总时限；必须大于 Query Interpreter 与 Answer Generator 时限之和。 |
| `M3_QUERY_EMBEDDING_TIMEOUT_SECONDS` | `10.0` | 查询向量请求的单次快速失败时限。 |
| `M3_QUERY_EMBEDDING_RETRIES` | `1` | 查询向量超时后的额外重试次数。 |
| `M3_RERANK_TIMEOUT_SECONDS` | `15.0` | Rerank 快速失败时限；超时后进入既有排序降级路径。 |

V5 后 RAG V2 是唯一实现，不再配置 `M3_RAG_PIPELINE_MODE`，也不能用环境变量切回 legacy。旧首轮、单强、事实/解释分流、技术助手总开关、焦点历史和 Query Rewrite 变量已从 Settings、环境示例与 Production Compose 删除；部署方 `.env` 中遗留同名变量会被忽略，应在部署前清理。修改生产配置需重新创建相应容器，代码更新需重建镜像。模型密钥未处于轮换状态时，不要在 `.env` 中保留空的 `MODEL_SECRET_KEY_PREVIOUS` 或 `MODEL_SECRET_KEY_PREVIOUS_VERSION`；轮换时两者必须同时填写有效值。

解析版本升级不自动重建索引；但下一次构建遇到旧版本结果会重新解析，可能产生全库 OCR/视觉/嵌入耗时和费用。DC24V 的浮动文字 DOCX 目前会被安全拦截，应先转换为可编辑正文并人工核对步骤，再构建候选索引、验收后手动激活；不得为绕过错误直接修改版本元数据。现有活动索引保持不变。

根目录执行 `PYTHONPATH=backend .venv/bin/python rag-acceptance/verify_legacy_docx.py <本地DOCX路径>` 可只读查看解析计数和告警；追加 `--real-ocr --output <报告路径>` 使用本地数据库的 OCR 配置验证恢复，可能产生模型费用，不上传、不构建索引。无法可靠恢复时保存 `BLOCKED` 报告并退出码 2；识别完成仍须人工核验内容与顺序。

### 开发进程

推荐：

```bash
bash scripts/dev-up.sh
```

该命令先用 `docker compose up -d --wait postgres redis minio` 等待长期运行的基础设施健康，再以 `docker compose run --rm --no-deps minio-init` 完成一次性 Bucket 初始化，最后由 Honcho 启动 `Procfile` 中的 API、`worker-index`、`worker-maintenance` 和前端。这样不会因 `minio-init` 正常 `exited (0)` 而提前中止。每个后端进程会通过 `scripts/dev-migrate.sh` 的文件锁串行运行 `alembic upgrade head`。默认地址：前端 `http://127.0.0.1:5173`，后端 `http://127.0.0.1:18000`；前端开发代理转发 `/api`、`/health`。

也可按 `docs/dev-startup.md` 使用 `honcho start` 选择进程。不要用生产编排代替开发热重载流程。

## 校验命令

当前 V2 专项 Golden 用例：`.venv/bin/python rag-acceptance/run_golden.py --api-mode assistant --cases rag-acceptance/technical-assistant-cases.jsonl`。旧质量/事实/解释用例分别为 `quality-cases.jsonl`、`fact-qa-cases.jsonl`、`reasoning-cases.jsonl`，其历史模式和文档名断言须先按当前 V2/目标语料核对，不再配置已删除的 shadow/enabled 开关。Golden 调用真实配置模型并创建隔离新会话，不修改源文件或活动索引。业务契约检查使用 `rag-acceptance/check_assistant_contract.py --kb-id <uuid> --base-url <URL>`，创建独立测试用户会话并核验重放、分页、图片、并发与断连。普通全量门禁仍只使用 fake 模型。

离线对照：`.venv/bin/python rag-acceptance/compare_golden.py <before.json> <after.json> --cases <cases.jsonl> --output <comparison.json>`。仅支持同库 Assistant 报告，使用同一份当前断言重评两边；报告必须保存完整 `response`，只有答案摘要的旧报告不能用作正文对比。

| 范围 | 命令 | 来源与效果 |
| --- | --- | --- |
| 后端/前端常规全量 | `bash scripts/check.sh` | Ruff、格式检查、mypy、pytest + smoke 累计覆盖率、前端 lint/type-check/test coverage/build。 |
| 前端单项 | `npm --prefix frontend run lint`、`type-check`、`test`、`build` | `frontend/package.json` 脚本。 |
| 后端单项 | `cd backend && ../.venv/bin/python -m pytest` | Pytest 默认带覆盖率门槛；定向测试可显式覆盖其默认 `addopts`。 |
| RAG 回归 | `.venv/bin/python rag-acceptance/run_golden.py` | 真实 HTTP/模型回归；需要可用服务、知识库及安全配置。 |
| Support 回归 | `cd backend && ../.venv/bin/pytest -q -o addopts='' tests/test_support_evaluation.py` | `support-acceptance` 说明中定义的开发回归。 |
| 生产门禁 | `bash scripts/check-m7.sh` | 常规检查、依赖审计、production compose 校验、生产 smoke 与发布验收。 |

本地执行 `scripts/check.sh` 时，常驻 Honcho `index_build` Worker 可能抢走摄取冒烟刚提交的临时任务，形成非确定性失败。稳定复验应先停止开发 Worker，或把门禁指向确认空闲的隔离 Redis/队列；若发生“索引存在未完成文档”，先保留失败记录并单独复跑摄取冒烟，再执行一次完整门禁，不能只凭单项复跑宣称全量通过。
| Assistant 遗留请求恢复 | `cd backend && ../.venv/bin/python -m app.assistant.recovery --stale-hours 24` | 默认只预览；停写并完成数据库备份后追加 `--apply`，事务同步修复请求、消息和 Trace。 |

以上命令是从仓库脚本/配置读取的“可执行入口”，不代表本次已运行或必然成功；任务报告必须单独说明实际运行的命令和结果。

## 生产部署

使用 `compose.production.yaml` 和部署环境变量。默认应用镜像为 `enterprise-rag-backend:1.0.1` 和 `enterprise-rag-frontend:1.0.1`；正式部署应在 `.env` 中显式固定镜像名，避免默认值变化导致不可预期升级。编排顺序为 PostgreSQL/Redis/MinIO → 一次性 `migrate` → 后端与两个 Worker → Nginx 前端。生产后端和 Worker 使用只读根文件系统及 `/tmp` tmpfs；Nginx 代理 `/api/` 与 `/health/` 并托管 SPA。Nginx upstream 使用共享内存区、`resolve` 参数和 Docker 内置 DNS `127.0.0.11` 动态解析 `backend` 服务名，后端容器重建及 IP 变化不再要求重启前端；生产 smoke 会实际替换后端 IP 验证恢复。`/api/` 代理关闭响应缓冲，并显式转发后端的 `X-Accel-Buffering` 响应头以保持 SSE 对接契约。Assistant API 需要强独立的 `ASSISTANT_SERVICE_TOKEN`。

生产示例将 `APP_HTTP_BIND_ADDRESS=0.0.0.0`、`APP_HTTP_PORT=18080`，供同内网其他服务器访问；多网卡服务器可把绑定地址缩小到指定内网 IP。先用 `sudo ufw status verbose`、`sudo ufw status numbered` 和 `sudo ufw show added` 审核主机规则，在保持当前 SSH 会话的同时，先允许管理员来源访问实际 SSH 端口，再收紧默认入站策略。不要运行 `ufw reset`。

Docker 发布端口的流量会在到达 UFW 使用的常规 `INPUT` 链之前被转发，因此只添加 `ufw allow/deny 18080` 不能可靠限制 Compose 发布的 `18080`。该端口必须使用上游网络防火墙，或在 Docker 的 `DOCKER-USER` 链按业务后台来源地址限制；不要通过关闭 Docker 的 `iptables` 功能规避该问题，这会破坏容器网络。变更前后分别检查 `sudo iptables -S DOCKER-USER`、`docker compose ... ps`，并从允许和不允许的来源各测试一次。

生产 smoke 脚本是 `scripts/smoke-production.sh`，会创建临时 compose 项目并清理卷。它在 production 配置校验中为 M2/M3/M5 使用 `auto`，遵守禁止 fake 的门禁；脚本不发起模型支持的摄取或聊天，因此不会产生外部模型调用。仅在已理解 Docker 影响、可执行相关容器操作的环境运行。

Docker 构建使用根目录 `.dockerignore` 缩小上下文：本地 `.env` 文件（保留可提交的示例）、虚拟环境、Node 依赖、Python 测试/静态检查缓存、Coverage 分片、前端构建/覆盖率产物、`VDR600/` 本地资料和 RAG 验收报告均不进入镜像构建上下文。修改 Dockerfile 的 `COPY` 范围或新增构建输入时，应同步检查该文件。

## 生产核验与待确认

### 带版本标记的构建（2026-10-03）

```bash
bash scripts/build-production.sh --env-file .env.production backend frontend
docker compose --env-file .env.production -f compose.production.yaml up -d --force-recreate
```

环境文件名按实际部署替换；使用根目录默认 `.env` 时可省略 `--env-file`。脚本只构建、不启动，写入构建期 `BUILD_COMMIT`、`BUILD_TIME`（UTC）、`BUILD_DIRTY`，不输出凭据。正式发布前提交工作区，避免 dirty 标记导致同一提交无法唯一识别代码。后端/API、两个 Worker 共用镜像内不可变标记；前端将同一信息编译入静态资源。直接 `docker compose build` 且未提供三个构建参数仍可构建，但版本显示“未标注”；启动时设置同名环境变量不能给旧镜像补版本。上述 `up` 不加 `--build`，避免无构建参数的二次构建覆盖标记。

上线后刷新管理页面，展开侧栏“运行版本”核对前后端提交；Worker 可执行 `docker compose --env-file .env.production -f compose.production.yaml exec worker-index python -m app.core.build_info`（维护 Worker 同理，服务名 `worker-maintenance`）或查看启动日志。本批无需迁移、无需重建索引；必须更新前端、后端及两个 Worker 容器，不能仅 Git pull 或 restart。

- 历史验收：2026-09-11 曾核对生产提交 `49c799a`、Alembic `0041` 及 Assistant 鉴权/SSE/幂等/隔离等；已被 2026-09-29 的生产快照（HEAD `9cd6569`、Alembic `0047`）取代，不能据旧记录继续判断 `0042` 或 V2 尚未部署。2026-10-05 本次仅检查本地源码与迁移图，未重新核对生产容器/数据库；9 月 30 日后提交与当前未提交管理台改动是否已部署，仍待核验。
- 正式上线前仍需由具备 sudo 权限的管理员确认 UFW 与 `DOCKER-USER` 的实际规则，并轮换曾用于联调的临时凭据。服务器仓库通过 GitHub SSH 拉取，`cm` 账号需在交互式终端输入 SSH 私钥口令，因此服务器 `git pull` 由用户手动执行。

## 已删除的 RAG 聚焦历史方案（历史）

本节仅保留历史背景，不再作为部署指令。V5 已删除 `M3_HISTORY_STRATEGY` 和 RAG 焦点历史实现；共享数据库列仍因 Support/历史兼容保留。当前部署统一执行 Alembic head，RAG 回滚只能使用旧提交或旧镜像。

## 已删除的 RAG 分层技术助手方案（历史）

本节仅保留历史背景，不再作为部署指令。V5 已删除 `M3_RAG_TECH_ASSIST_MODE` 及其分层路由；当前 V2 由统一 Answer Generator 在隐藏全文上下文内决定短答、步骤、追问或有界通用回答。


## 回答优化功能验证

管理侧回答优化的表由 `0044` 引入；部署执行 `cd backend && ../.venv/bin/python -m alembic upgrade head`（当前仓库 head 为 `0047`），不能把 `0044` 当作当前目标版本。再更新后端、前端；该功能本身无需重建业务索引，实际数据库版本另行核验。

定向后端验证：`cd backend && ../.venv/bin/python -m pytest -q -o addopts='' tests/test_answer_reviews.py tests/test_assistant_facade.py`；后端 PostgreSQL 测试复用测试环境连接并清理临时知识库。前端验证：`npm --prefix frontend run test -- tests/answer-reviews.spec.ts tests/reviews-api.spec.ts`。人工验收入口：管理台概览 → 查看回答与优化 → 选择知识库 → 查看 → 保存标记 → 按当前配置复测。复测会调用配置的模型，最多20轮、180秒。

## Assistant 内容一致性验证

分页、失败重放与响应边界：`cd backend && ../.venv/bin/pytest -q -o addopts='' tests/test_assistant_boundaries.py tests/test_assistant_content_postgres.py tests/test_assistant_facade.py tests/test_assistant_content_consistency.py tests/test_chat_repository_state.py`。冻结 Support 回归另运行 `tests/test_support_core.py tests/test_support_document_router.py`。PostgreSQL 用例须实际执行，跳过不算通过。三项 fake 冒烟由 `scripts/check.sh` 执行，不执行 Golden Set；测试依赖本地 PostgreSQL/Redis/MinIO，数据库应已升级至当前 head。

RAG V2 不公开交付完整原文，完整候选只进入隐藏模型上下文；旧 `M3_DOC_DELIVERY_MAX_TOKENS` 已删除。业务后台仍须按 SSE 空行边界组装完整事件，不能按网络分块解析 JSON。旧消息和幂等重放继续按已保存快照返回，不重新生成。

定向命令：`cd backend && ../.venv/bin/pytest -q -o addopts='' tests/test_assistant_content_consistency.py tests/test_assistant_content_postgres.py tests/test_assistant_facade.py tests/test_gateway.py tests/test_rag_v2_pipeline.py`。数据库用例复用隔离测试数据并清理临时知识库，不能将数据库不可达而跳过的结果当作通过。

## RAG 文档卡片部署与验证（0046）

先执行 `cd backend && ../.venv/bin/alembic upgrade head`，再用 `../.venv/bin/alembic current` 确认为当前仓库 head（2026-10-05 为 `0047`，`0046` 是卡片功能首次迁移）；生产 Compose 仍由一次性 `migrate` 服务完成同一升级。迁移本身不调用模型、不回填数据。升级后在管理台“文档”页点击“生成/补齐 RAG 理解”，页面按 3 秒轮询显示 `RAG_DOCUMENT_CARD_GENERATE` 的进度、成功/失败/总数和终态错误；等待任务结束，再逐份进入“RAG 文档理解”检查并确认。活动索引中全部有效文档确认后，Assistant 才允许上线并保持 `ready=true`。整批 0 成功视为任务失败，部分失败保留已成功卡片并明确警告；再次全量补齐只选择缺失/失败/过期项。已确认卡片重新生成需要明确确认，成功后重新变成待确认并令已上线 Assistant 暂时 `ready=false`；失败时保留原已确认卡片。

定向自动化：`cd backend && ../.venv/bin/pytest --no-cov -q tests/test_rag_document_cards.py tests/test_rag_document_cards_postgres.py tests/test_maintenance_jobs.py tests/test_task_recovery.py`；前端：`cd frontend && npx vitest run tests/admin-api.spec.ts tests/viewInteractions.spec.ts`。PostgreSQL 用例必须实际执行，跳过不算通过。RAG V2 会读取当前冻结索引中有效的卡片参与文档召回；卡片缺失时仍可走 Chunk 通道，但不能把“卡片已生成”单独表述为回答质量已经通过。

## 已删除的 RAG legacy 配置

V5 已删除旧 P2/P3 `shadow/enabled` 运行时、后台任务、Query/Response Planner、六策略、首轮/焦点/Query Rewrite 代码和对应 Settings。旧 `M3_RAG_QUERY_PLANNER_*` 已改名为 `M3_RAG_QUERY_INTERPRETER_*`；`M3_RAG_TITLE_LEAK_REPAIR_ATTEMPTS` 已改名为 `M3_RAG_ANSWER_REPAIR_ATTEMPTS`。

历史 Trace 继续只读兼容；旧变量不要重新加入环境文件。V5 后回滚只能部署旧 Git 提交或旧镜像。

`app.rag.prompt.DEFAULT_RAG_PROMPT` 仅为历史 Alembic `0007/0012` 在全新数据库重放时保留的稳定种子，不是当前 RAG Prompt，也不要求数据库 Active Prompt。不要因运行时已切 V2 而删除该兼容符号；可用 `cd backend && ../.venv/bin/alembic current` 和 `tests/test_alembic_revisions.py` 验证迁移图。

## RAG V2 配置（V0～V5 本地已生效）

RAG V2 为唯一主路径，开发与 Production Compose 使用同一执行逻辑。V5 本身无需数据库迁移或重建索引；部署需要重建后端镜像并重启 API，回滚使用旧镜像。

V2 配置见上表。Query 与 Answer 两段超时之和必须小于 `M3_TURN_DEADLINE_SECONDS`；开发和生产示例总时限均为 90 秒。

表中默认值来自 Settings，不代表所有值都能只改生产 `.env` 覆盖。当前 Production Compose 显式转发历史轮次上限，但未转发历史消息数/Token 预算；镜像也不带项目 `.env`。若未来调整后两项，须同时为容器声明对应环境变量并重建容器，本次未修改编排或预算。

V4.1 文档级排序复用现有 Rerank Provider、`M3_RERANK_TIMEOUT_SECONDS` 和模型快照，不增加环境变量或第三次 LLM。候选池内部最多取 `max(M3_RAG_FULL_DOCUMENT_LIMIT, M3_RAG_DOCUMENT_CARD_TOP_K)` 份用于排序，最终全文上限仍由 `M3_RAG_FULL_DOCUMENT_LIMIT=3` 控制。当前未调整 Chunk、向量/BM25 Top-K、卡片 Top-K 或 20,000 Token 全文预算；后续只有在多轮 Trace 显示稳定的召回或上下文问题时才单变量调整。

V4.2 没有新增配置。有效 V2 的 `prepare()` 只冻结轮次和覆盖参数，查询解释完成后将去重后的原问题、独立请求和可选任务锚点合并为一次批量 Embedding，再执行一次文档级 Rerank；LLM 1 不再输出补充检索查询。短追问由 LLM 1 输出 `FOLLOW_UP/CORRECTION + active_task`，任务锚点参与召回和排序；新任务不加入该锚点。修改后无需迁移或索引重建，只需重启 API 进程并使用新会话/新幂等键验证。

V4.3 同样没有新增配置、迁移或索引要求。它只增强 LLM 2 的多文档证据覆盖规则，并在公开组装层兼容成组图片别名；部署后重启 API 即可。验证需检查回答正文不含 `[IMG:`，图片仍为结构化 `content` 块，并复测“更换后/下一步/仍失败”类多文档追问。

V2 历史只受 `M3_RAG_HISTORY_MAX_ROUNDS`、`M3_HISTORY_MAX_MESSAGES` 与 `M3_HISTORY_TOKEN_BUDGET` 约束。Support 专用 `M3_DOC_*` 阈值仍保留，因为冻结的 Support 代码继续编译和回归；当前 RAG 不读取它们。

`python -m app.rag.smoke --embedding-provider fake --llm-provider fake` 直接验证 V2 的检索、隐藏全文、结构化图片、SSE、Trace 和持久化。真实回答质量仍须单独执行 Golden Set，不能由 fake smoke 代替。

人工验收：在同一已上线测试库用新幂等键分别发送 `include_images=true/false`，检查 `complete.content` 的正文、表格、来源和图片块；再加载历史消息，并用原请求体和原 Key 重放，对照三个结果。分别覆盖 RAG、Support 直接交付和 Support 回退 RAG。关闭图片不应改变文档命中，历史与重放均不得返回管理 `url`/`download_url`。该修复本身仅需更新后端，无新增迁移或索引重建要求；同批其他功能的迁移要求仍按对应说明执行。
