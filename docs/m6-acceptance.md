# M6 索引与后台验收

- 验收日期：2026-08-17
- 结论：**PASS**
- 自动化入口：`./scripts/check-m6.sh`

## 已实现范围

### 双索引发布

- Active Index 继续作为不可变在线快照。
- 管理 API 支持自动激活或 `activate_on_success=false` 的人工验收模式。
- 新索引在 `BUILDING`、`READY` 阶段不会修改 `knowledge_base.active_index_id`。
- 人工激活在同一数据库事务内将旧 `ACTIVE` 标为 `DEPRECATED`、新 `READY` 标为 `ACTIVE` 并更新知识库指针。
- 构建失败保留 `FAILED` 快照和任务错误，当前 Active Index 不受影响。
- 支持索引列表、任务列表、手动激活和 FAILED 构建重试；重试始终创建新索引 ID。

### Playground 与 Trace

- `POST /api/v1/rag/playground` 运行完整 Embedding、Vector、BM25、RRF、VL Rerank、Context、Prompt、LLM 和引用校验。
- 仅允许覆盖 Top-K 和 Context 数量等白名单参数，不接受模型凭据、Base URL 或 SQL 条件。
- Playground 记录独立的 `mode=PLAYGROUND` Trace，响应包含检索候选、Rerank、Context、Prompt、答案、来源、引用校验及耗时。
- `GET /api/v1/traces` 支持按知识库、状态、模式筛选；Trace 详情继续使用稳定 `trace_id`。
- 模型列表只返回 `api_key_configured`，不返回密文、Nonce 或可恢复密钥字段。

### RAG Studio 管理页面

- Dashboard：知识库、文档、Active Chunk 和 Trace 概况及依赖健康状态。
- Knowledge Base：创建和选择知识库。
- Documents：上传 Word、查看文档列表及“源文档 / Element / Chunk”三栏预览。
- Index Build：索引时间线、自动/人工发布、READY 激活和失败信息。
- RAG Playground：检索参数调节与 Retrieval、Rerank、Context、Prompt、Citation 分阶段展示。
- Trace：列表、耗时、状态与完整链路详情。
- Models & Prompt：全局模型安全快照和 Active Prompt 查看。
- Settings：服务凭据只保存在浏览器 `sessionStorage`，不做持久登录系统。

## 自动验收证据

M6 冒烟测试使用真实 PostgreSQL/pgvector/pg_search、Redis/RQ 和 MinIO，模型使用确定性 Fake Provider，验证：

1. 首个完整索引自动发布为 `ACTIVE`。
2. 第二个完整索引在构建期间仍由旧索引在线服务。
3. 第二个索引完成后保持 `READY`，旧索引继续在线。
4. 管理 API 激活后，新索引为 `ACTIVE`，旧索引为 `DEPRECATED`。
5. 模拟后续构建投递失败，在线 Active 指针保持不变。
6. Playground 使用新 Active Index 并生成 `COMPLETED / PLAYGROUND` Trace。
7. 索引、任务、Trace 和模型脱敏接口均返回预期结果，临时数据库与 MinIO 数据最终清理。

前端门禁包括 ESLint、`vue-tsc`、Vitest 覆盖率（语句/分支/函数/行均不低于 80%）及 Vite 生产构建。

## 范围边界

- 第一版仍无登录、用户权限和多租户；生产管理台必须置于可信反向代理之后。
- 索引是内部发布快照，不构成用户可见的文档版本管理。
- DEPRECATED/FAILED 索引的定时保留与异步物理清理策略属于后续运维增强；M6 不在激活事务内执行大批删除。
- 模型与 Prompt 页面第一版以安全查看为主，密钥轮换和 Prompt 编辑审计不在 M6 范围。
