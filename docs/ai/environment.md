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

关键配置类别：应用与 CORS、PostgreSQL、Redis/RQ、MinIO、模型提供者、嵌入/检索阈值、保留期、模型密钥加密，以及生产的 `SERVICE_TOKEN` 和 `ASSISTANT_SERVICE_TOKEN`。`ASSISTANT_REQUEST_RETENTION_DAYS` 默认 7，控制未关联 Conversation 的 Assistant 终态请求保留期，设为 0 禁用清理；维护 Worker 启动时清理一次并注册每日任务，永不由该任务删除 `RUNNING`。生产示例为 `.env.production.example`；Production Compose 对必要密码、服务凭据和 `MODEL_SECRET_KEY` 使用必填变量。恢复已有数据库时必须沿用能解密其中模型凭据的密钥及版本，不能生成新值替代。

staging/production 的 M2 Embedding、M3 LLM、M5 Rerank 不允许显式或隐式使用 fake。`auto` 必须能从对应环境变量或可解密的 `model_config` 快照取得有效凭据；否则相关摄取/问答请求明确失败。部署校验还要确认在线查询模型与 Active Index 的 Embedding 模型一致，不能只确认服务进程已启动。

## 本地启动

### RAG 首轮与旧式文档配置

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `M3_RAG_CONFLICT_GUARD` | `true` | 聚合直达与最强块文档冲突时改为澄清。 |
| `M3_RAG_TITLE_RECALL` | `true` | 精确型号的有界标题补召回，仍参加 Rerank。 |
| `M3_RAG_SINGLE_STRONG_MODE` | `shadow` | `off` 关闭、`shadow` 只记录、`enabled` 开启单强直达。 |
| `M3_RAG_FACT_QA_MODE` | `shadow` | 首轮明确事实问法：`off` 关闭、`shadow` 记录、`enabled` 在可靠选文档后复用 DOC_QA。完整操作请求仍交付全文。 |
| `M3_RAG_EXPLANATION_MODE` | `enabled` | 可靠选文档后的首轮解释问答；`shadow/off` 回退原文交付，与事实短答独立，不降低选文档门槛。 |
| `M3_HISTORY_STRATEGY` | `focused` | RAG 使用同焦点、完整轮次的历史；`legacy` 可临时回退旧历史选择。只影响 RAG，不改变 Support 自身工作流。 |
| `M3_RAG_SINGLE_STRONG_FLOOR` | `0.85` | 单强原始最高块分数下限。 |
| `M3_RAG_SINGLE_STRONG_GAP` | `0.18` | 相对另一文档最高块的分差下限。 |

以上配置已接入两个环境示例和 Production Compose。修改生产配置需重新创建相应容器；代码更新需重建镜像。单强直达及事实短答保持 `shadow`；解释问答默认 `enabled`，可改为 `shadow/off` 回退首轮原文交付。`M3_HISTORY_STRATEGY=legacy` 是历史选择的运行时回退，不会撤销 `0045` 数据库结构，也不会恢复旧标题展示。生产采用前用目标环境的真实 Golden 及 Trace 核对质量与延迟。

解析版本升级不自动重建索引；但下一次构建遇到旧版本结果会重新解析，可能产生全库 OCR/视觉/嵌入耗时和费用。DC24V 的浮动文字 DOCX 目前会被安全拦截，应先转换为可编辑正文并人工核对步骤，再构建候选索引、验收后手动激活；不得为绕过错误直接修改版本元数据。现有活动索引保持不变。

根目录执行 `PYTHONPATH=backend .venv/bin/python rag-acceptance/verify_legacy_docx.py <本地DOCX路径>` 可只读查看解析计数和告警；追加 `--real-ocr --output <报告路径>` 使用本地数据库的 OCR 配置验证恢复，可能产生模型费用，不上传、不构建索引。无法可靠恢复时保存 `BLOCKED` 报告并退出码 2；识别完成仍须人工核验内容与顺序。

### 开发进程

推荐：

```bash
bash scripts/dev-up.sh
```

该命令先执行 `docker compose up -d --wait`，然后以 Honcho 启动 `Procfile` 中的 API、`worker-index`、`worker-maintenance` 和前端。每个后端进程会通过 `scripts/dev-migrate.sh` 的文件锁串行运行 `alembic upgrade head`。默认地址：前端 `http://127.0.0.1:5173`，后端 `http://127.0.0.1:18000`；前端开发代理转发 `/api`、`/health`。

也可按 `docs/dev-startup.md` 使用 `honcho start` 选择进程。不要用生产编排代替开发热重载流程。

## 校验命令

RAG 正文质量用例：`.venv/bin/python rag-acceptance/run_golden.py --api-mode assistant --cases rag-acceptance/quality-cases.jsonl`。事实问答实验用例为 `rag-acceptance/fact-qa-cases.jsonl`，分别在 shadow/enabled 的测试进程运行。两者都调用真实配置模型，并创建隔离的新会话，不修改源文件或活动索引。解释边界用例为 `rag-acceptance/reasoning-cases.jsonl`；业务契约检查使用 `rag-acceptance/check_assistant_contract.py --kb-id <uuid> --base-url <URL>`，创建独立测试用户会话并核验重放、分页、图片、并发与断连。普通全量门禁仍只使用 fake 模型。

离线对照：`.venv/bin/python rag-acceptance/compare_golden.py <before.json> <after.json> --cases <cases.jsonl> --output <comparison.json>`。仅支持同库 Assistant 报告，使用同一份当前断言重评两边；报告必须保存完整 `response`，只有答案摘要的旧报告不能用作正文对比。

| 范围 | 命令 | 来源与效果 |
| --- | --- | --- |
| 后端/前端常规全量 | `bash scripts/check.sh` | Ruff、格式检查、mypy、pytest + smoke 累计覆盖率、前端 lint/type-check/test coverage/build。 |
| 前端单项 | `npm --prefix frontend run lint`、`type-check`、`test`、`build` | `frontend/package.json` 脚本。 |
| 后端单项 | `cd backend && ../.venv/bin/python -m pytest` | Pytest 默认带覆盖率门槛；定向测试可显式覆盖其默认 `addopts`。 |
| RAG 回归 | `.venv/bin/python rag-acceptance/run_golden.py` | 真实 HTTP/模型回归；需要可用服务、知识库及安全配置。 |
| Support 回归 | `cd backend && ../.venv/bin/pytest -q -o addopts='' tests/test_support_evaluation.py` | `support-acceptance` 说明中定义的开发回归。 |
| 生产门禁 | `bash scripts/check-m7.sh` | 常规检查、依赖审计、production compose 校验、生产 smoke 与发布验收。 |
| Assistant 遗留请求恢复 | `cd backend && ../.venv/bin/python -m app.assistant.recovery --stale-hours 24` | 默认只预览；停写并完成数据库备份后追加 `--apply`，事务同步修复请求、消息和 Trace。 |

以上命令是从仓库脚本/配置读取的“可执行入口”，不代表本次已运行或必然成功；任务报告必须单独说明实际运行的命令和结果。

## 生产部署

使用 `compose.production.yaml` 和部署环境变量。默认应用镜像为 `enterprise-rag-backend:1.0.1` 和 `enterprise-rag-frontend:1.0.1`；正式部署应在 `.env` 中显式固定镜像名，避免默认值变化导致不可预期升级。编排顺序为 PostgreSQL/Redis/MinIO → 一次性 `migrate` → 后端与两个 Worker → Nginx 前端。生产后端和 Worker 使用只读根文件系统及 `/tmp` tmpfs；Nginx 代理 `/api/` 与 `/health/` 并托管 SPA。Nginx upstream 使用共享内存区、`resolve` 参数和 Docker 内置 DNS `127.0.0.11` 动态解析 `backend` 服务名，后端容器重建及 IP 变化不再要求重启前端；生产 smoke 会实际替换后端 IP 验证恢复。`/api/` 代理关闭响应缓冲，并显式转发后端的 `X-Accel-Buffering` 响应头以保持 SSE 对接契约。Assistant API 需要强独立的 `ASSISTANT_SERVICE_TOKEN`。

生产示例将 `APP_HTTP_BIND_ADDRESS=0.0.0.0`、`APP_HTTP_PORT=18080`，供同内网其他服务器访问；多网卡服务器可把绑定地址缩小到指定内网 IP。先用 `sudo ufw status verbose`、`sudo ufw status numbered` 和 `sudo ufw show added` 审核主机规则，在保持当前 SSH 会话的同时，先允许管理员来源访问实际 SSH 端口，再收紧默认入站策略。不要运行 `ufw reset`。

Docker 发布端口的流量会在到达 UFW 使用的常规 `INPUT` 链之前被转发，因此只添加 `ufw allow/deny 18080` 不能可靠限制 Compose 发布的 `18080`。该端口必须使用上游网络防火墙，或在 Docker 的 `DOCKER-USER` 链按业务后台来源地址限制；不要通过关闭 Docker 的 `iptables` 功能规避该问题，这会破坏容器网络。变更前后分别检查 `sudo iptables -S DOCKER-USER`、`docker compose ... ps`，并从允许和不允许的来源各测试一次。

生产 smoke 脚本是 `scripts/smoke-production.sh`，会创建临时 compose 项目并清理卷。它在 production 配置校验中为 M2/M3/M5 使用 `auto`，遵守禁止 fake 的门禁；脚本不发起模型支持的摄取或聊天，因此不会产生外部模型调用。仅在已理解 Docker 影响、可执行相关容器操作的环境运行。

Docker 构建使用根目录 `.dockerignore` 缩小上下文：本地 `.env` 文件（保留可提交的示例）、虚拟环境、Node 依赖、Python 测试/静态检查缓存、Coverage 分片、前端构建/覆盖率产物、`VDR600/` 本地资料和 RAG 验收报告均不进入镜像构建上下文。修改 Dockerfile 的 `COPY` 范围或新增构建输入时，应同步检查该文件。

## 生产核验与待确认

- 2026-09-11 已确认目标生产服务器使用提交 `49c799a`，PostgreSQL、Redis、MinIO、后端和前端健康，两个 Worker 正常运行，数据库位于 Alembic `0041`；Assistant 鉴权、知识库、会话、真实 SSE、幂等、用户隔离以及当时仍存在的文档/图片资源代理均已完成生产验收。当前源码已删除 Assistant 原文下载接口，但该变更尚待生产部署；工作树已新增 `0042`，生产需在下一次部署执行迁移后才能使用 Assistant 重放计数和终态孤儿保留任务。生产 Golden Trace 已确认真实模型链路，不再使用 fake 查询链路。
- 正式上线前仍需由具备 sudo 权限的管理员确认 UFW 与 `DOCKER-USER` 的实际规则，并轮换曾用于联调的临时凭据。服务器仓库通过 GitHub SSH 拉取，`cm` 账号需在交互式终端输入 SSH 私钥口令，因此服务器 `git pull` 由用户手动执行。

## RAG 聚焦历史部署与验证

部署本次 RAG 历史改动必须先让一次性 `migrate` 服务成功升级到 `0045`，再启动新后端和 Worker。生产 Compose 的正常命令仍为 `docker compose --env-file .env -f compose.production.yaml up -d --build --wait`；该编排会自动运行 `migrate`，后端和 Worker 只有在迁移成功后才启动。结束后检查 Compose 状态，并在后端容器中核对 `alembic current` 为 `0045 (head)`。若迁移失败，不要绕过依赖单独启动新后端，因为新代码会读取新增列。

该变更无需重建文档索引。运行时回退可设置 `M3_HISTORY_STRATEGY=legacy` 后重建后端容器；数据库保持 `0045` 即可。上线前用新会话覆盖连续追问、文档切换确认、失败后重试和长历史预算；上线后检查 Trace 的 `generation.history`，应只出现消息 ID、轮次数和裁剪边界，不应出现历史正文。真实模型质量仍需按 `rag-acceptance/` 执行多轮 Golden，本次本地门禁未执行该项。


## 回答优化功能验证

部署管理侧回答优化前执行 `cd backend && ../.venv/bin/python -m alembic upgrade head` 至 `0044`，再更新后端、前端。无需重建业务索引；本地已升级，生产是否升级必须另行核验。

定向后端验证：`cd backend && ../.venv/bin/python -m pytest -q -o addopts='' tests/test_answer_reviews.py tests/test_assistant_facade.py`；后端 PostgreSQL 测试复用测试环境连接并清理临时知识库。前端验证：`npm --prefix frontend run test -- tests/answer-reviews.spec.ts tests/reviews-api.spec.ts`。人工验收入口：管理台概览 → 查看回答与优化 → 选择知识库 → 查看 → 保存标记 → 按当前配置复测。复测会调用配置的模型，最多20轮、180秒。

## Assistant 内容一致性验证

分页、失败重放与长文档边界：`cd backend && ../.venv/bin/pytest -q -o addopts='' tests/test_assistant_boundaries.py tests/test_assistant_content_postgres.py tests/test_assistant_facade.py tests/test_assistant_content_consistency.py tests/test_chat_doc_routing_flow.py tests/test_chat_repository_state.py tests/test_support_core.py`。PostgreSQL 用例须实际运行，跳过不算通过。三项 fake 冒烟由 `scripts/check.sh` 执行，不执行 Golden Set；测试依赖本地 PostgreSQL/Redis/MinIO，数据库应已升级至当前 head。

长文档原文交付不再受 `M3_DOC_DELIVERY_MAX_TOKENS` 限制，旧变量仅为启动兼容保留，不必提高数值；模型上下文和生成超时仍使用原配置。完整交付会增大 SSE 单个 `complete` 事件、消息快照和历史页体积；业务后台应按 SSE 空行边界组装完整事件，不能按网络分块解析 JSON，可降低历史消息页 `limit`。没有新增接口、下载入口、迁移或索引重建要求；需要以新请求验证，旧截断的历史快照不重新生成。

定向命令：`cd backend && ../.venv/bin/pytest -q -o addopts='' tests/test_assistant_content_consistency.py tests/test_assistant_content_postgres.py tests/test_assistant_facade.py tests/test_gateway.py tests/test_chat_doc_routing_flow.py`。数据库用例复用隔离测试数据并清理临时知识库，不能将数据库不可达而跳过的结果当作通过。

人工验收：在同一已上线测试库用新幂等键分别发送 `include_images=true/false`，检查 `complete.content` 的正文、表格、来源和图片块；再加载历史消息，并用原请求体和原 Key 重放，对照三个结果。分别覆盖 RAG、Support 直接交付和 Support 回退 RAG。关闭图片不应改变文档命中，历史与重放均不得返回管理 `url`/`download_url`。该修复本身仅需更新后端，无新增迁移或索引重建要求；同批其他功能的迁移要求仍按对应说明执行。
