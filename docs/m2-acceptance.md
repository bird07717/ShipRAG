# M2 文档入库验收记录

验收日期：2026-08-17。

## 结论

M2 状态：**PASS**。

Word 上传、DOCX 安全校验、MinIO 保存、RQ 构建调度、Mammoth + OOXML 解析、Element、图片提取、Chunk、图文 Embedding、pgvector 持久化和 Active Index 原子发布已经形成完整闭环。

## 已实现范围

### 数据库

Alembic `0002_m2_ingestion_schema.py` 创建：

- `model_config`
- `knowledge_base`
- `knowledge_index`
- `document_source`
- `index_document`
- `document_element`
- `image_asset`
- `document_chunk`，Embedding 列为 `vector(1024)`
- `chunk_element`
- `task_record`
- `idempotency_record`

约束包含 UUID 主键、构建/Active 部分唯一索引、文档哈希去重、顺序唯一、状态检查、外键级联和 HNSW 向量索引。M2 不创建 BM25 索引，`bm25_engine=NOT_BUILT`，避免提前宣称 M4 检索能力。

### 上传与调度

上传接口：

```http
POST /api/v1/knowledge-bases/{knowledge_id}/documents
Content-Type: multipart/form-data
Idempotency-Key: <key>
```

同步阶段完成：

1. `.docx` 扩展名、ZIP/OOXML 结构校验。
2. 文件大小、压缩条目、解压总量、单条目和压缩比限制。
3. 拒绝损坏 ZIP、宏/加密包和不安全 ZIP 路径。
4. 流式 SHA-256 与大小统计。
5. 原始文件写入私有 MinIO Bucket。
6. 事务创建逻辑文档、全量索引快照、Index Document 和业务任务。
7. RQ `index_build` 队列投递。

相同 Idempotency-Key 和相同请求返回已有结果；Key 对应不同请求返回 409。同一知识库已有 BUILDING 索引时设置 `rebuild_required=true`，当前构建结束后创建下一快照。

### 解析与 Element

- Mammoth 执行 DOCX 基础转换并记录脱敏警告。
- OOXML 按 `word/document.xml` 和 relationship 顺序生成 Element。
- 支持 `TEXT`、Markdown `TABLE`、`IMAGE`。
- 标题生成 `section_path`，`sequence_no` 从 1 稳定递增。
- 图片按确定性对象键保存到索引专属 MinIO 路径。
- Element、图片资产及后续 Chunk ID 使用确定性 UUID，重复消费不会产生重复记录。

### Chunk

- TEXT 按章节和段落边界聚合。
- TABLE 整体保留，超限时按行拆分并重复表头。
- 图片与前后文本生成 `MIXED` Chunk；无相邻文本时生成 `IMAGE` Chunk。
- `chunk_element` 保存完整溯源映射。
- `content` 与 `search_text` 分开保存，Token 数使用固定估算规则。

### Embedding 与发布

- `auto` 模式存在 SiliconFlow Key 时使用 `Qwen/Qwen3-VL-Embedding-8B`，否则使用确定性 Fake Provider。
- TEXT/TABLE 使用文本输入，IMAGE/MIXED 使用 Base64 Data URL 图文联合输入。
- 支持配置化批量、429/5xx/网络重试、数量/维度/有限数值校验。
- 所有 Chunk 必须具有 1024 维向量，否则索引失败且不能发布。
- 完整性检查通过后执行 `READY → ACTIVE` 原子切换，旧 Active Index 转为 `DEPRECATED`。

M2 只负责图片提取和图文 Embedding；`image_asset.ocr_status` 与 `vision_status` 当前明确记录为 `SKIPPED`。OCR/Vision Caption 属于后续多模态增强，不冒充已完成。

## 查询接口

```text
POST /api/v1/knowledge-bases
GET  /api/v1/knowledge-bases
GET  /api/v1/knowledge-bases/{knowledge_id}
POST /api/v1/knowledge-bases/{knowledge_id}/documents
GET  /api/v1/knowledge-bases/{knowledge_id}/documents
GET  /api/v1/documents/{document_id}
GET  /api/v1/documents/{document_id}/index-results
GET  /api/v1/documents/{document_id}/elements
GET  /api/v1/documents/{document_id}/chunks
GET  /api/v1/indexes/{index_id}
GET  /api/v1/tasks/{task_id}
```

Elements/Chunks 未指定 `index_id` 时严格选择 Active Index；知识库未发布时返回 409，不会偷偷展示 BUILDING 数据。

## 实机证据

执行：

```bash
M2_SMOKE_PROVIDER=siliconflow ./scripts/check-m2.sh
```

结果：

```text
HTTP 创建知识库：201
HTTP 上传 DOCX：202
RQ 投递：PASS
Embedding Provider：siliconflow
文档数：1
Element 数：6（含 TABLE、IMAGE）
Chunk 数：5（含 MIXED）
Embedding：全部 1024 维
索引状态：ACTIVE
```

烟测使用随机 UUID，结束后只清理本次知识库及其精确 MinIO 前缀。

## 质量门禁

- Ruff：PASS
- mypy strict：PASS，38 个源文件
- 后端单元测试：24 PASS
- 单元覆盖率：70.61%
- 单元测试 + M2 实机烟测合并覆盖率：81%
- 前端测试：6 PASS
- 前端生产构建：PASS
- Alembic 当前版本：`0002`

完整质量检查：

```bash
./scripts/check.sh
```
