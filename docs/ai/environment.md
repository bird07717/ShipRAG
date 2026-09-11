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

关键配置类别：应用与 CORS、PostgreSQL、Redis/RQ、MinIO、模型提供者、嵌入/检索阈值、保留期、模型密钥加密，以及生产的 `SERVICE_TOKEN` 和 `ASSISTANT_SERVICE_TOKEN`。生产示例为 `.env.production.example`；Production Compose 对必要密码、服务凭据和 `MODEL_SECRET_KEY` 使用必填变量。恢复已有数据库时必须沿用能解密其中模型凭据的密钥及版本，不能生成新值替代。

## 本地启动

推荐：

```bash
bash scripts/dev-up.sh
```

该命令先执行 `docker compose up -d --wait`，然后以 Honcho 启动 `Procfile` 中的 API、`worker-index`、`worker-maintenance` 和前端。每个后端进程会通过 `scripts/dev-migrate.sh` 的文件锁串行运行 `alembic upgrade head`。默认地址：前端 `http://127.0.0.1:5173`，后端 `http://127.0.0.1:18000`；前端开发代理转发 `/api`、`/health`。

也可按 `docs/dev-startup.md` 使用 `honcho start` 选择进程。不要用生产编排代替开发热重载流程。

## 校验命令

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

生产 smoke 脚本是 `scripts/smoke-production.sh`，会创建临时 compose 项目并清理卷。仅在已理解 Docker 影响、可执行相关容器操作的环境运行。

Docker 构建使用根目录 `.dockerignore` 缩小上下文：本地 `.env` 文件（保留可提交的示例）、虚拟环境、Node 依赖、Python 测试/静态检查缓存、Coverage 分片、前端构建/覆盖率产物、`VDR600/` 本地资料和 RAG 验收报告均不进入镜像构建上下文。修改 Dockerfile 的 `COPY` 范围或新增构建输入时，应同步检查该文件。

## 待确认

- 2026-09-10 已确认目标生产服务器的 PostgreSQL、Redis、MinIO、后端和前端健康，两个 Worker 正常运行，数据库位于 Alembic `0040`，迁移备份校验通过；Assistant 鉴权、知识库、会话、真实 SSE、幂等、用户隔离以及文档/图片资源代理均已完成生产验收。
- 正式上线前仍需由具备 sudo 权限的管理员确认 UFW 与 `DOCKER-USER` 的实际规则，并轮换曾用于联调的临时凭据。服务器仓库通过 GitHub SSH 拉取，`cm` 账号需在交互式终端输入 SSH 私钥口令，因此服务器 `git pull` 由用户手动执行。
