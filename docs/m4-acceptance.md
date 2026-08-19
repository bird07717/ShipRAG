# M4 多模态 RAG 验收记录

验收日期：2026-08-17。

## 结论

M4 状态：**PASS**。

Word 图片抽取、MinIO 保存、DeepSeek-OCR、GLM-5V-Turbo 图片理解、IMAGE Element 增强、IMAGE/MIXED Chunk、图文混合 Embedding、Active Index 发布和多模态来源已形成端到端闭环。

## 已实现范围

### OCR 与 Vision Model Gateway

- OCR：SiliconFlow `deepseek-ai/DeepSeek-OCR`，OpenAI Multimodal Chat 格式，温度 0。
- Vision：智谱 `glm-5v-turbo`，Data URL 图片输入，关闭 thinking，输出面向产品知识检索的事实性 Caption。
- 每项调用有独立超时、429/5xx/网络异常重试和返回结构检查。
- API Key、上游响应正文和 Data URL 不进入日志或错误信息。
- 图片级保存 Provider、模型、状态、脱敏错误码、耗时、用量和请求标识。
- `fake` Provider 用于离线测试；`disabled` 会明确写入 `SKIPPED`，不会冒充处理成功。

### 图片处理与降级

Worker 在 Chunk 构建前对图片执行有界并发增强。IMAGE Element 的 `content` 按可用结果组合：

```text
图片替代文本
图片描述（Vision Caption）
图片文字（OCR）
```

OCR 或 Vision 单项失败时，失败项记为 `FAILED`，保留另一项和原图继续构建；两项均不可用时仍保留图片及替代文本，并尝试图片 Embedding。最终 Chunk Embedding 失败仍会使索引构建失败，不能发布不完整索引。

### IMAGE/MIXED Chunk 与检索

- 独立图片生成 `IMAGE` Chunk。
- 图片两侧存在相邻正文时生成 `MIXED` Chunk，并保留有序 `chunk_element` 映射。
- `content/search_text` 包含章节、邻近正文、OCR 和 Caption。
- IMAGE/MIXED 向 Qwen3-VL-Embedding 同时发送文本和原图，写入固定 1024 维向量。
- Chunk metadata 保存关联 `image_asset_ids`、Embedding 模型及策略。
- M3 向量检索可直接命中 IMAGE/MIXED Chunk；结构化来源返回真实 `image_asset_ids`。

### 数据库与接口

Alembic `0004_m4_multimodal_ingestion.py`：

- 写入全局 OCR/Vision 模型配置。
- 扩展 `image_asset` 的 Provider、模型、错误码和完成时间。
- 迁移已在独立临时数据库验证 `base → 0004 → 0003 → 0004`。

新增/增强接口：

```text
GET /api/v1/documents/{document_id}/elements
GET /api/v1/image-assets/{image_asset_id}
GET /api/v1/image-assets/{image_asset_id}/content
POST /api/v1/chat/stream
```

元数据接口不暴露 MinIO Bucket/Object Key；图片内容经受服务认证保护的后端接口读取，并设置私有短缓存及 `nosniff`。

## 自动化与实机证据

离线验收：

```bash
./scripts/check-m4.sh
```

真实云服务验收：

```bash
M4_SMOKE_EMBEDDING_PROVIDER=siliconflow \
M4_SMOKE_OCR_PROVIDER=siliconflow \
M4_SMOKE_VISION_PROVIDER=zhipu \
./scripts/check-m4.sh
```

2026-08-17 结果：

```text
SiliconFlow DeepSeek-OCR：HTTP 200，状态 READY
智谱 GLM-5V-Turbo：HTTP 200，状态 READY
SiliconFlow Qwen3-VL-Embedding：HTTP 200，1024 维
DOCX：1；Element：6；Chunk：5
IMAGE Element：存在
MIXED Chunk：存在
RQ 投递：PASS
Active Index 发布：PASS
图片元数据/内容 API：PASS
```

后端单元测试覆盖 Provider 契约、无敏感正文错误、单项降级、显式禁用、IMAGE/MIXED Chunk 和图片来源。烟测使用随机 UUID，结束后数据库与 MinIO 临时数据均清理；验收后数据库残留计数为 0。

## 范围边界

M4 完成多模态入库与向量 RAG 增强，以下仍不在本里程碑：

- 严格 BM25、Hybrid Fusion 和 VL Rerank。
- 图片问题作为 Chat 输入；当前 Chat 请求仍是文本问题。
- 图片区域级坐标、版面分析和 Word 页码还原。
- RAG Playground、自动评测及管理端模型写配置。
