# M7 性能基线

## 测试范围

M7 使用真实 FastAPI、RQ Worker、PostgreSQL/pgvector/pg_search、Redis 和 MinIO；Embedding、OCR、Vision、Rerank、LLM 使用确定性 Fake Provider，以隔离云模型网络延迟和额度波动。

默认命令：

```bash
cd backend
../.venv/bin/python -m app.release.acceptance \
  --load-requests 200 \
  --load-concurrency 32 \
  --rag-requests 50 \
  --p95-limit-ms 3000
```

场景：

- Liveness：无状态进程响应。
- Database：带鉴权的知识库详情查询。
- RAG：完整 Playground，包含 Query Embedding、Vector、BM25、RRF、Rerank、Prompt、LLM、引用和 Trace 持久化。

门槛：错误率必须为 0，三类请求 p95 均不超过 3000 ms。报告记录吞吐、p50、p95、p99，但该门槛不是对真实云模型 SLA 的承诺。

## 2026-08-17 实测结果

|场景|请求数|并发|错误率|吞吐 RPS|p50|p95|p99|
|---|---:|---:|---:|---:|---:|---:|---:|
|Liveness|200|32|0%|411.30|33.04 ms|311.92 ms|345.37 ms|
|Database|200|32|0%|165.45|125.83 ms|467.95 ms|633.17 ms|
|完整 Fake RAG|50|32|0%|35.80|1081.26 ms|1215.97 ms|1234.24 ms|

三类场景均满足错误率 0、p95 小于 3000 ms 的发布门槛。

## 容量解释

“最多 1000 用户”按注册/授权用户规模理解，不等于 1000 个并发问答。生产并发受以下因素共同限制：

- SiliconFlow/智谱账户 RPM、TPM、并发和费用额度。
- 文档数量、Chunk 数、图片大小和 Prompt 长度。
- Uvicorn Worker 数、PostgreSQL连接池、Redis/MinIO吞吐。
- 用户问题频率和 SSE 连接持续时间。

上线前应使用脱敏的真实文档长度分布和生产模型账号再跑一轮阶梯压测，并将业务目标并发、p95、错误率和预算写入发布单。云模型压测必须设置费用上限，不直接复用 M7 的 50 个 Fake RAG 请求作为厂商容量结论。
