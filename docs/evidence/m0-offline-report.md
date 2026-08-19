# M0 本地验证证据

- 执行日期：2026-08-17
- 执行命令：`./scripts/check-m0.sh --allow-blocked`
- 结论：本地技术验证通过；随后执行的云模型在线验证 10/10 通过

## DOCX

- 生成并解析包含一级标题、段落、简单表格、PNG 和图片前后说明的真实 `.docx` 包。
- Mammoth 找到标题、段落、表格和一张图片，无解析警告。
- OOXML 找到 `word/media/image1.png`，并保持以下块顺序：标题 → 段落 → 表格 → 图片前文字 → 图片 → 图片后文字。

## MinIO

```json
{
  "status": "passed",
  "bucket_private": true,
  "anonymous_fetch_status": 403,
  "signed_url_fetch": true,
  "content_matches": true,
  "external_strategy": "base64_data_url",
  "signed_url_disclosed": false
}
```

探测对象使用随机名称，验证结束后按精确对象名删除。报告不保存签名 URL。

## 严格 BM25

- 固定镜像：`paradedb/paradedb:0.25.0-pg17@sha256:6a334b612cadfeb92c416ecf3816dd9a277c10976e2e931e2c33f7289867c7c9`
- PostgreSQL 17.10，`pg_search 0.25.0`，`vector 0.8.4`
- `pdb.lindera(chinese)` 中文分词通过。
- 中文、数字、英文型号和 Linux 文件路径检索断言通过。
- “数据库 默认 端口 3306”查询的预期文档为 Top 1。
- `pg_dump -Fc` 后恢复到新数据库，索引可继续查询且 Top 1 不变。

## 在线模型

探测代码及 Mock 契约测试通过，真实在线请求也已执行：Embedding 5 项、Rerank 2 项、OCR、LLM stream、Vision 共 10 项全部通过。五种 Embedding 输入均返回 1024 维。详情见 [m0-online-report.json](m0-online-report.json)。
