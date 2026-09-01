# 开发态一键启动：bash scripts/dev-up.sh
# 每个进程先经过 dev-migrate.sh；文件锁确保 Alembic 只会串行执行。
# worker 队列拆分是设计行为：index worker 独占崩溃恢复与索引 GC 的启动职责
# （见 backend/app/worker.py 注释），合并会因长任务阻塞维护队列。
api:                bash scripts/dev-api.sh
worker-maintenance: bash -c 'bash scripts/dev-migrate.sh && cd backend && export RQ_QUEUES=ingestion,maintenance RQ_WORKER_NAME=rag-maintenance-worker && exec ../.venv/bin/python -m app.worker'
worker-index:       bash -c 'bash scripts/dev-migrate.sh && cd backend && export RQ_QUEUES=index_build RQ_WORKER_NAME=rag-index-worker && exec ../.venv/bin/python -m app.worker'
frontend:           bash -c 'cd frontend && exec npm run dev'
