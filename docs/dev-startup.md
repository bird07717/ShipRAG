# 开发环境启动指南

## 一键启动（推荐）

```bash
bash scripts/dev-up.sh        # 基础设施 + Alembic + API + 双 worker + 前端
```

`Ctrl-C` 一次优雅停掉全部进程（worker 释放队列锁、API 关闭连接、Vite 退出，无残留进程）。

## 替代的旧流程（4 个终端）

| 旧流程 | 新流程 |
| --- | --- |
| 终端1: `alembic upgrade head` + `uvicorn ...` | 全部后端进程先经过共享迁移门槛（串行、幂等）→ `api` 再启动 `uvicorn --reload` |
| 终端2: `RQ_QUEUES=ingestion,maintenance ... app.worker` | `worker-maintenance` 条目（同名环境变量已写入 Procfile） |
| 终端3: `RQ_QUEUES=index_build ... app.worker` | `worker-index` 条目 |
| 终端4: `npm run dev` | `frontend` 条目 |
| 手工排序、SSH 断开全死、日志四散 | 一条命令、统一日志、Ctrl-C 全停 |

## 文件说明

- `Procfile` — 进程编排的唯一真相源（含 worker 队列分配）
- `scripts/wait-infra.sh` — 基础设施端口就绪等待（兜底，最多 30s）
- `scripts/dev-migrate.sh` — 等基础设施 → 使用文件锁串行执行 `alembic upgrade head`。
- `scripts/dev-api.sh` — 通过迁移门槛后启动 `uvicorn --reload`。
- `scripts/dev-up.sh` — 启动 Docker Compose 并交给 Honcho 运行全部开发进程。
- `honcho` 已加入 `backend/pyproject.toml` dev 依赖并锁定于 `requirements.lock`

## 设计说明

1. **两个 worker 不合并**：`backend/app/worker.py` 规定 index worker 独占崩溃恢复与索引 GC
   的启动职责；合并会让索引构建长任务阻塞维护队列。
2. **`--reload` 只给 API**：RQ worker 无热重载概念，改代码后需重启（`honcho start` 再来一次）。
3. **任一进程崩溃 → honcho 停掉全部**（foreman 系默认行为）：开发态宁可全停暴露问题，
   也不要一个进程悄悄死了继续开发。

## 常用操作

```bash
honcho start                 # 全部
honcho start api             # 只起 API（调试单一进程）
honcho start worker-index frontend   # 任意子集
```

## 常见问题

- **`There exists an active worker named 'rag-xxx-worker' already`**：旧方式启动的 worker
  没停干净。检查 `pgrep -f app.worker`，`kill` 掉残留后重试。
- **Vite 提示 `Port 5173 is in use`**：旧的 `npm run dev` 还在跑，会自动改用 5174；
  建议先停掉旧的。
- **基础设施等待超时**：先跑 `docker compose up -d --wait`。

## 与上线态的关系

这套流程**仅用于开发**。周一上线使用 `compose.production.yaml`（迁移一次性容器门控、
健康检查依赖链、`restart: unless-stopped`、`read_only` 加固），不要用手工/honcho 流程
跑生产。

生产部署还必须在项目根目录的部署 `.env`（或 CI/CD Secret 注入）中设置
`ASSISTANT_SERVICE_TOKEN`；不能使用 `.env.example` 的示例值。生成方式及业务后台对接要求见
[Assistant 接口对接文档](integration/assistant-api-integration-guide.md)。
