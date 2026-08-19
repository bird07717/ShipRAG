# ADR-0003：DOCX 来源定位与页码

- 状态：接受
- 日期：2026-08-17

## 背景

产品希望来源包含文档、页码、章节和 Chunk。DOCX 是流式排版格式，最终页码取决于字体、页面设置、Office/渲染器版本、打印机度量和版式环境。Mammoth 解析语义结构，不提供可靠的 Word 最终页码映射。

根据段落数量或 OOXML 节点位置估算页码会产生看似精确但不可追溯的引用。

## 决策

V1 的稳定来源身份为：

```text
document_id
section_path
element_ids
chunk_id
sequence_no
```

公开来源中的 `page` 字段保留但可空：

```json
{
  "document": "部署手册.docx",
  "section_path": ["部署指南", "数据库配置"],
  "page": null,
  "element_ids": ["uuid"],
  "chunk_id": "uuid"
}
```

只有在未来引入固定版式渲染并建立经过验证的 Element-to-page 映射后，才允许写入 `page`。禁止估算页码或让 LLM 填写页码。

RAG Studio 的来源跳转优先使用 Element/Chunk 高亮；可以显示“章节 + 内容序号”，而不是虚假页码。

## 后果

### 正面

- 引用稳定、可验证，不依赖不可控的 Word 排版环境。
- Element 与 Chunk 可以直接在三栏预览中定位。
- 为未来 PDF/固定版式映射保留兼容字段。

### 代价

- 第一版部分来源没有页码。
- 若业务验收强制页码，必须扩大范围，引入固定渲染、页面资产及映射测试。

## 未来扩展门槛

引入页码前必须通过新的 ADR 决定：

- 统一渲染器及版本；
- 字体和操作系统环境；
- DOCX → PDF 失败策略；
- Element 与页区域映射方法；
- 页码变化后的缓存和索引重建语义。
