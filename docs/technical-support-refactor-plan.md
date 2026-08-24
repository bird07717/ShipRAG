# 精简技术支持设计

更新日期：2026-08-24。

## 1. 项目定位

本项目不是通用企业知识平台，也不是以 BM25、Embedding 或 Agent 为中心的产品。它只解决：

1. 从约 50–60 篇“每篇解决一个问题”的技术文档中确定用户所问的问题；
2. 首次明确问题后，按原顺序返回该文档的完整文字、表格和图片；
3. 后续对话由 LLM 在当前文档事实边界内解释步骤、补充理解、询问信息，并在证据不足或高风险时停止猜测。

## 2. 当前主链路

```text
企业软件后台
  → POST /api/v1/support/chat[/stream]
  → 显式 document_id/problem_code
  → 标准问题或可选 Alias
  → LLM 在小型问题目录中闭集选择
  → 唯一命中 / 候选澄清 / NO_MATCH
  → 首轮完整文档交付
  → Conversation 锁定 document_id + index_id
  → 后续文档约束回答
```

正式 Support 链路不调用 BM25、Embedding、RRF 或 Rerank。Generic RAG 只保留：

- `/api/v1/rag/playground` 检索调试；
- `/api/v1/chat` 的 `legacy_rag` 手动回退。

运行模式只有 `technical_support` 和 `legacy_rag`。

## 3. 问题文档配置

每篇文档最少需要 `document_id`、稳定的 `problem_code`、`canonical_question` 和 `enabled`。
Alias、Keywords、Product、Model 和 Task Type 全部是可选优化项。

Alias 为空不影响 `support_ready`。发布就绪只检查：

- Profile 已启用；
- 文档仍存在；
- 知识库存在 Active Index；
- 文档属于 Active Index；
- 文档 Element 存在；
- 图片 Element 均有 Image Asset。

## 4. 首轮与后续职责

明确命中文档后直接交付完整内容，不调用回答 LLM，不用 Chunk 重新拼接。后续 LLM 输入只
包括当前聚焦文档、有限会话历史、用户问题和安全规则。输出必须经过服务端验证：

- 文档事实引用当前文档 Element；
- 图片属于当前文档；
- 通用建议明确标注，不得伪装成产品事实；
- 文档外现象可以请求更多信息；
- 高风险且无文档证据时停止并建议人工处理；
- 可能转题时先让用户确认文档切换。

## 5. 管理后台

Support 的两个核心入口是：

1. **问题文档配置**：配置标准问题、可选 Alias、关联文档并检查发布状态；
2. **Support 问答测试**：验证首轮完整文档、图片、澄清和后续回答。

Documents、Index Build、Models、Trace 等原有工程页面继续承担文档发布、模型连接和诊断职责。
RAG Playground 明确标为 Generic RAG 调试工具，不代表正式 Support 行为。

## 6. 开发验收与数据处理

`support-acceptance/cases.jsonl` 保留 25 条小型回归案例，覆盖路由、无匹配、后续、安全和
文档切换。它不是运行时依赖，不建设数据库、任务系统或前端管理页面。真实使用中发现有
代表性的失败问题后再人工替换或增加案例，不追求固定配额。

技术文档不做默认脱敏。只有真实用户数据、账号、密码、Token 等敏感信息在进入日志、测试
文件或外部系统前按实际风险处理。

## 7. 明确取消或暂停

当前阶段取消或暂停：

- Alias 必填和 `ALIASES_MISSING` 发布门禁；
- 200 条构造 Golden Set；
- Golden Set 管理后台和评测平台；
- Shadow 请求、对比持久化和页面；
- 百分比 Rollout、哈希分桶和灰度管理页面；
- 正式 Support 路由中的 BM25、Embedding、RRF、Rerank；
- 为没有真实需求的技术文档统一脱敏。

只有未来确实出现大规模生产迁移、真实流量风险或专门运营团队时，才重新评估 Shadow 和复杂
灰度；不得提前建设。

## 8. 验证顺序

1. 选择 5–10 篇真实文档创建独立试点知识库；
2. 构建并激活 Index；
3. 每篇创建问题编号和标准问题，Alias 可空；
4. 检查完整 Element 和图片；
5. 测试标准问题、目录选择、歧义和无匹配；
6. 测试首轮完整文档；
7. 测试步骤解释、信息不足和文档外现象；
8. 测试高风险拦截和文档切换；
9. 验收通过后再扩展到剩余文档；
10. 企业后台接入正式 Support API，并演练切回旧 `/chat`。

完成定义不是“增加更多组件”，而是：选对文档、完整交付、后续不越界、图片可用、错误可
解释、企业后台可以稳定转发。
