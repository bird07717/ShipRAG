# ADR-0007：部署级固定 Embedding 维度

- 状态：接受
- 日期：2026-08-17

## 背景

Embedding API 可能支持可选输出维度，Knowledge Index 也会记录构建所用模型。但 V1 将所有 Chunk 存在同一 `document_chunk` 表的 pgvector 列中。使用 `vector(N)` typmod 和 ANN 索引时，不能在普通的双索引切换窗口内让同一列同时容纳任意不同维度。

如果仅在 `knowledge_index.embedding_dimension` 记录不同值，却没有相应 Schema 迁移方案，会导致新索引无法写入或无法使用既有 ANN 索引。

## 决策

1. M1 通过目标 Embedding Provider 在线契约测试选择并冻结部署级 `EMBEDDING_DIMENSION=N`。
2. `document_chunk.embedding` 使用 `vector(N)`。
3. 每个 Knowledge Index 仍记录模型配置、模型名和维度快照，但维度必须等于 N。
4. 新 Embedding 模型只有在能显式、稳定输出 N 维向量时，才能通过普通全量 Knowledge Index 重建上线。
5. 构建和查询两端都必须校验向量长度、有限数值和模型身份。
6. 被 Active Index 引用的历史 Embedding Model Config 不得删除；即使不再被选为新建索引的全局 enabled 配置，也必须保持查询可用。
7. 改变 N 需要新的 ADR，并至少解决：
   - 新列或新表的 Schema；
   - 新 ANN 索引；
   - 新旧查询路由；
   - 所有知识库重建；
   - 切换和回滚；
   - 旧列/表清理。

不得把“创建新 Knowledge Index”当作维度迁移的充分方案。

## 后果

### 正面

- pgvector Schema、ANN 索引和查询 SQL 简单且可验证。
- 双索引切换可以让不同模型共存，只要输出维度相同。
- 维度错误在构建发布前即可被阻止。

### 代价

- 不能随意利用模型的任意维度输出。
- 改变维度是数据库级迁移项目，而非普通模型配置变更。
- M1 必须有真实 Provider 契约测试后才能确定 N，不能凭模型名称猜测。

## 不采用的方案

- 无 typmod 的任意维度 `vector` 加动态表达式索引：增加查询、索引和迁移复杂度，不适合 V1。
- 每个 Knowledge Index 单独建 Chunk 表：表生命周期、查询路由和迁移复杂度过高。
- 在应用内补齐或截断向量：破坏模型语义，禁止使用。
