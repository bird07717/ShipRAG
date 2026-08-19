# M5 完整检索验收记录

验收日期：2026-08-17。

## 结论

M5 状态：**PASS**。

指定知识库 Active Index 内的严格中文 BM25、pgvector、RRF Hybrid Fusion、Qwen3-VL-Reranker、Context、Prompt、LLM、SSE 和引用校验已形成完整可追踪闭环。

## 检索链路

```text
Question
  ├─ Query Embedding → pgvector Top K
  └─ pg_search BM25 Top K
             ↓
    RRF Fusion + Chunk ID 去重
             ↓
    Qwen3-VL-Reranker
             ↓
    Context Budget + Source Registry
             ↓
       GLM-5.2 + SSE
             ↓
    Citation Validation + Trace
```

Vector 与 BM25 都强制过滤本次冻结的 `knowledge_id + index_id`。RRF 只使用两路排名：

```text
score = Σ 1 / (rrf_k + rank)
```

不直接相加 cosine similarity 与 BM25 score；并列时按最佳单路名次和 Chunk UUID 确定性排序。

## 严格 BM25

- 默认镜像锁定为 ParadeDB PostgreSQL 17，显式预加载 `pg_search`。
- `document_chunk.id` 使用 UUID key field。
- `search_text` 使用 `pdb.lindera(chinese)` tokenizer。
- 索引字段包含 `kb_id`、`index_id`、`document_id` 和 `chunk_type`。
- Trace 明确记录 `lexical_engine=pg_search`、版本 `0.25.0` 和原始 BM25 名次/分数。
- 未使用 `ts_rank` 或其他近似方案冒充 BM25。

Alembic `0005_m5_complete_retrieval.py` 已在独立数据库验证：

```text
base → 0005 → 0004 → 0005
```

## VL Rerank 与降级

- TEXT/TABLE 发送文本。
- IMAGE/MIXED 发送 OCR、Caption、正文及受字节预算限制的原图 Data URL。
- Provider 返回的 index、重复项、分数有限性和 `top_n` 全部校验。
- 网络超时、429、5xx 可降级为 RRF 顺序，并写入 `status=DEGRADED`、错误码和模型。
- 4xx、非法索引、重复索引或协议错误不得静默降级，请求明确失败。

## 引用

来源编号只按最终 Rerank/Context 顺序由服务端注册。最终答案中的引用执行：

- 未注册来源剔除；
- 重复来源去重；
- 结构化来源按首次引用顺序返回；
- 文件、Element、Chunk 和图片 ID 均来自数据库；
- Trace 保存 `citation_missing`、有效/无效来源编号和注册来源数量。

## 验收证据

离线门禁：

```bash
./scripts/check-m5.sh
```

真实云端门禁：

```bash
M5_SMOKE_EMBEDDING_PROVIDER=siliconflow \
M5_SMOKE_RERANK_PROVIDER=siliconflow \
M5_SMOKE_LLM_PROVIDER=zhipu \
./scripts/check-m5.sh
```

2026-08-17 真实结果：

```text
SiliconFlow Query Embedding：HTTP 200，1024 维
pg_search BM25：候选 1
pgvector：候选 1
RRF：融合并去重为 1
SiliconFlow Qwen3-VL-Reranker：图文输入，HTTP 200，PASSED
智谱 GLM-5.2：HTTP 200
知识库隔离：PASS（相同内容的第二知识库未越界）
SSE：trace → source → message* → done
有效引用：1；citation_missing=false
Trace / Assistant Message：COMPLETED
```

单元测试覆盖 RRF 联集与确定性排序、Rerank 排序、图文载荷、协议错误、超时降级和引用缺失/伪造引用。烟测结束后临时数据库记录与 MinIO 图片均精确清理。

## 范围边界

- 当前 Retrieval Query 仍使用用户当前问题，不包含自动 Query Rewrite。
- 未实现自动评测、RAG Playground 和检索参数管理 UI。
- HA、复制、故障转移与生产容量压测属于部署验收，不影响 M5 功能闭环。
