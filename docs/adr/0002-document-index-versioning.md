# ADR-0002：逻辑文档与索引快照分离

- 状态：接受
- 日期：2026-08-17

## 背景

交接模型中的 `document` 同时包含 `kb_id` 和 `index_id`。如果每次全量重建都创建一组新的 Document 记录，后台会看到重复文档，删除、重命名、文件去重和原始对象生命周期也会变得含糊。

另一方面，解析结果必须按 Index 隔离，否则构建期间可能把新旧 Element、Chunk 和向量混入在线查询。

## 决策

将文档拆分为两个层次：

- `document_source`：用户管理的逻辑文档及原始 DOCX。
- `index_document`：某个 Knowledge Index 对该逻辑文档的一次处理结果。

Element、Chunk 和索引专属图片全部关联 `index_document` 和 `index_id`。原始 DOCX 只属于 `document_source`。

构建创建时冻结：

```text
document_source.id
document_source.file_hash
document_source.minio_object_key
```

构建期间逻辑文档发生变化，不修改该快照。

## 身份与替换语义

- 上传新文件默认创建新的 `document_source.id`。
- 同一知识库相同未删除文件哈希视为重复，返回 `409 DUPLICATE_DOCUMENT`，除非命中相同 Idempotency Key 的重放。
- V1 的“替换文档”使用“软删除旧 Document Source + 上传新 Document Source + 请求构建”，不复用 ID。
- 修改 `display_name` 不改变原始内容，可以不触发重建；来源展示是否使用构建时名称或当前名称在 M2 API 实现中固定，默认使用当前逻辑显示名。
- 删除 Document Source 只影响未来构建，当前 Active Index 在切换前保持一致。

## 后果

### 正面

- 后台文档列表稳定，不随索引重建重复。
- 每次解析结果和模型输出可按索引完整追踪。
- 原始文件与派生图片具有清晰生命周期。
- 可以比较不同索引对同一逻辑文档的处理结果。

### 代价

- 增加 `index_document` 表和显式冗余外键。
- 删除逻辑文档与在线内容失效之间存在“下一索引发布”的明确延迟。
- 一致性检查必须确认 Chunk、Element 与 Index Document 属于同一快照。

## 不采用的方案

- 单表 `document(index_id, ...)`：无法稳定表达用户管理的逻辑文档。
- Active Index 中原地覆盖解析结果：破坏快照一致性和无中断切换。
