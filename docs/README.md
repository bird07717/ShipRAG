# 工程文档索引

本目录记录当前工程契约、架构决策、开发部署说明和后续重构计划。涉及当前行为时，以运行中的 OpenAPI、`backend/app/` 和 Alembic 迁移头为准；质量与发布结论必须通过目标代码和配置上的可执行检查重新获得。

## 核心文档

| 文档 | 作用 |
|---|---|
| [architecture.md](architecture.md) | 系统边界、模块职责、运行拓扑和全局不变量 |
| [data-model.md](data-model.md) | 逻辑数据模型、约束、状态机和删除语义 |
| [api-contract.md](api-contract.md) | REST、异步任务、错误格式与 SSE 事件协议 |
| [ingestion-pipeline.md](ingestion-pipeline.md) | DOCX 上传、解析、图片处理、Chunk、Embedding 和构建发布 |
| [rag-pipeline.md](rag-pipeline.md) | 在线检索、融合、Rerank、Prompt、流式回答与引用 |
| [technical-support-refactor-plan.md](technical-support-refactor-plan.md) | 精简技术支持主链路、可选项、取消项和验证顺序 |
| [vdr600-support-readiness.md](vdr600-support-readiness.md) | VDR600 真实文档导入与 Support 验证记录 |
| [development.md](development.md) | 本地开发、专项检查和质量门禁 |
| [deployment/production.md](deployment/production.md) | 生产部署、迁移与发布流程 |
| [deployment/recovery-runbook.md](deployment/recovery-runbook.md) | 依赖故障、数据备份和恢复操作 |

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
| [ADR-0008](adr/0008-online-model-config-and-secrets.md) | 接受 | 全模型类型在线编辑（按消费时机生效）、密钥优先级链与维度契约门禁 |

ADR 描述的是已接受的架构决策，不自动等于每一项管理工作流均已落地。Support Studio 的
问答测试页调用正式 Support SSE；Generic RAG Playground 只用于检索诊断。ADR-0005 所述的
密钥存储与 ADR-0008 的在线模型编辑、密钥写入/轮换、维度门禁已作为管理能力提供。

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

## 可执行验证

当前仓库保留 M0–M7 专项检查脚本，但不提交可能过期的静态验收报告。每次质量或发布结论都必须在目标代码、模型配置和 Schema 上重新运行相应脚本，并将当次证据保存到发布系统或受控制品存储中。

常用入口：

```bash
./scripts/check.sh
./scripts/check-m0.sh --allow-blocked
./scripts/check-m2.sh
./scripts/check-m3.sh
./scripts/check-m4.sh
./scripts/check-m5.sh
./scripts/check-m6.sh
./scripts/check-m7.sh
```

其中 M7 会执行生产构建、依赖中断和恢复演练，不能与其他本地业务负载共用环境。完整说明见 [development.md](development.md) 和 [deployment/production.md](deployment/production.md)。
