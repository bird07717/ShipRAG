# 工程文档索引

本目录记录 RAG 平台 V1 的工程契约、实施说明和历史验收证据。涉及当前行为时，以运行中的 OpenAPI、`backend/app/` 和 Alembic 迁移头为准；验收文档只证明其记录日期的执行结果。

## 核心文档

| 文档 | 作用 |
|---|---|
| [architecture.md](architecture.md) | 系统边界、模块职责、运行拓扑和全局不变量 |
| [data-model.md](data-model.md) | 逻辑数据模型、约束、状态机和删除语义 |
| [api-contract.md](api-contract.md) | REST、异步任务、错误格式与 SSE 事件协议 |
| [ingestion-pipeline.md](ingestion-pipeline.md) | DOCX 上传、解析、图片处理、Chunk、Embedding 和构建发布 |
| [rag-pipeline.md](rag-pipeline.md) | 在线检索、融合、Rerank、Prompt、流式回答与引用 |
| [mvp1-chat-execution-plan.md](mvp1-chat-execution-plan.md) | MVP1 受控网站演示、四模式 Prompt、非流式 Chat API、DOCX 下载和验收计划 |
| [post-mvp-roadmap.md](post-mvp-roadmap.md) | 业务后台、移动端、流式、多模态、PDF 与生产化的后续版本边界 |
| [m0-review.md](m0-review.md) | M0 可执行验证、证据和验收结论 |
| [m1-acceptance.md](m1-acceptance.md) | M1 基础平台的实现及实机验收证据 |
| [m2-acceptance.md](m2-acceptance.md) | M2 Word 上传、解析、Element、Chunk 和 Embedding 验收证据 |
| [m3-acceptance.md](m3-acceptance.md) | M3 文本向量检索、Prompt、LLM 和 SSE 闭环验收证据 |
| [m4-acceptance.md](m4-acceptance.md) | M4 OCR、Vision、IMAGE/MIXED Chunk 与多模态来源验收证据 |
| [m5-acceptance.md](m5-acceptance.md) | M5 严格 BM25、Hybrid、VL Rerank 和引用验收证据 |
| [m6-acceptance.md](m6-acceptance.md) | M6 双索引切换、Playground、Trace 和管理页面验收证据 |
| [m7-acceptance.md](m7-acceptance.md) | 2026-08-17 的 M7 联调、压测、错误恢复、容器发布和备份恢复历史证据 |

## 架构决策记录

| ADR | 状态 | 决策 |
|---|---|---|
| [ADR-0001](adr/0001-bm25-in-postgresql.md) | 接受 | PostgreSQL 内使用严格 BM25；限定未修改、仅服务端托管、不分发 |
| [ADR-0002](adr/0002-document-index-versioning.md) | 接受 | 逻辑文档与索引快照处理结果分离 |
| [ADR-0003](adr/0003-docx-source-location.md) | 接受 | 章节、序号、Element、Chunk 是稳定引用；页码可空 |
| [ADR-0004](adr/0004-post-sse-protocol.md) | 接受 | Chat 使用 POST + `fetch` 流式解析 SSE |
| [ADR-0005](adr/0005-model-secret-storage.md) | 接受 | API Key 加密存储、输出掩码、日志禁止明文 |
| [ADR-0006](adr/0006-immutable-index-publishing.md) | 接受 | Active Index 不可变，全量快照构建后原子切换 |
| [ADR-0007](adr/0007-fixed-embedding-dimension.md) | 接受 | V1 使用部署级固定 Embedding 维度，改变维度走 Schema 迁移 |

ADR 描述的是已接受的架构决策，不自动等于每一项管理工作流均已落地。当前 RAG Studio 不实现 Chat SSE 客户端；ADR-0005 所述的模型配置写入、密钥轮换及运行时解密流程也尚未作为公开管理能力提供。

## 规范性语言

文档中的关键词含义如下：

- **必须**：实现与测试均不得违反。
- **应当**：默认遵守；偏离时必须通过 ADR 说明理由。
- **可以**：实现可选项。

## 术语约定

- API 请求字段使用 `knowledge_id`；数据库内部外键使用 `kb_id`，两者都指向 `knowledge_base.id`。
- 所有公开业务 ID 使用 UUID 字符串，不使用自增整数暴露给调用方。
- 数据库存储时间使用 UTC `timestamptz`；API 时间使用 RFC 3339 UTC 字符串。
- `Document Source` 指用户管理的逻辑文档；`Index Document` 指某个索引快照中的一次处理结果。
- `Active Index` 是在线请求唯一可见的索引快照。
- `BM25` 只指严格 BM25 实现；PostgreSQL `ts_rank`/`ts_rank_cd` 不得标记成 BM25。

## 变更规则

1. 产品范围变化先更新架构文档。
2. 数据兼容性变化先更新数据模型并设计迁移。
3. 外部接口变化先更新 API 契约。
4. 修改已接受的 ADR 必须新增替代 ADR，并引用被替代的决策。
5. 外部模型的名称、参数和输入格式以当时官方文档及在线契约测试为准；只有脱敏报告为 `passed` 才能声明已验证。

## 历史验收状态

以下状态是相应验收记录的历史结论，并非对当前未提交工作树的重新验证。当前迁移头为 `0008`；发布前应在目标代码和配置上重新运行适用验收。

## M0 状态

工程契约和可执行验证体系已经完成。DOCX、MinIO、严格 BM25 本地 POC 及 10 项真实云模型契约探测全部通过，Embedding 维度已冻结为 1024。`pg_search` 许可证风险已按限定部署方式接受，M0 状态为 **PASS**。详细证据见 [m0-review.md](m0-review.md)。

## M2 状态

Word 上传、解析、Element、Chunk、图文 Embedding 与索引发布闭环已经完成，状态为 **PASS**。详见 [m2-acceptance.md](m2-acceptance.md)。

## M3 状态

文本向量检索、全局 Prompt、多轮历史、GLM-5.2 流式回答、来源校验、会话与 Trace 已形成闭环，状态为 **PASS**。详见 [m3-acceptance.md](m3-acceptance.md)。

## M4 状态

图片 OCR、Vision Caption、IMAGE/MIXED Chunk、图文 Embedding 和图片来源已接入文档入库及文本 RAG，状态为 **PASS**。详见 [m4-acceptance.md](m4-acceptance.md)。

## M5 状态

严格中文 BM25、向量检索、RRF Hybrid Fusion、图文 Rerank、降级审计及最终引用已形成完整闭环，状态为 **PASS**。详见 [m5-acceptance.md](m5-acceptance.md)。

## M6 状态

双索引自动/人工发布、失败隔离、Playground、Trace 查询和 RAG Studio 管理页面已经完成，状态为 **PASS**。详见 [m6-acceptance.md](m6-acceptance.md)。

## M7 状态

V1.0.0 生产镜像与 Compose、真实 Backend/Worker 联调、并发基线、故障恢复和备份恢复在 2026-08-17 已通过，状态为 **PASS**。该记录验证到 Alembic `0006`；当前 Schema 迁移到 `0008` 后需要重新执行发布验收。详见 [m7-acceptance.md](m7-acceptance.md)。
