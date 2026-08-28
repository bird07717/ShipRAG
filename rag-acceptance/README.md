# RAG 链路黄金集（Golden Set）

对上线链路（`RAG_ONLY` → `RagService`）的黑盒回归评测：42 个用例 / 47 轮真实请求，
经 HTTP 调用真实后端与真实模型（SiliconFlow embedding/rerank/LLM），按文档命中判定。

## 运行

```bash
.venv/bin/python rag-acceptance/run_golden.py
# 可选: --base-url http://127.0.0.1:18000 --kb-id <uuid>
# 凭据: 环境变量 GOLDEN_TOKEN，或回退读取根目录 .env 的 SERVICE_TOKEN
```

约 10~15 分钟。报告写入 `reports/baseline-<时间戳>.json`，含每轮的 response_type、
命中文档、延迟、答案摘录与失败原因。

## 用例设计

| 类别 | 数量 | 考什么 |
| --- | --- | --- |
| standard | 10 | 标准问法直达（文档标题直译） |
| colloquial | 9 | 口语变体的词汇失配 |
| identifier | 5 | 型号/错误码/英文标识（FFC600、tftp、time battery low） |
| cross_doc | 6 | 近邻文档歧义（接线盒 PDC/FFC、保险丝 AC/DC/电池） |
| out_of_scope | 5 | 范围外问题不得命中任何文档 |
| knowledge | 2 | 文档内事实问题的定位 |
| follow_up | 5 | 多轮：焦点保持 + 显式换文档 |

判定规则（`expected` 字段）：

- `tolerance: EXACT`——须命中 `document_keys` 指定文档，且不得命中 `forbidden_keys`；
- `tolerance: ANY_OF`——命中列表中任一即可（歧义题的合理行为是命中或澄清）；
- `tolerance: NONE`——不得命中任何知识库文档。

命中提取自 `references[].title`、`sources[].document` 与答案文本中的《标题》
（对 LLM 转写幻觉做 ≥0.85 相似度容差，例如"-中文"被写成"-英文"）。

## 基线结果（2026-08-28，三次运行）

| 指标 | Run 1（基线） | Run 2（基线） | Run 3（P0-P2 优化后） |
| --- | --- | --- | --- |
| 通过率 | 39/42 (92.9%) | 41/42 (97.6%) | **42/42 (100%)** |
| 错交付（forbidden 命中） | **0** | **0** | **0** |
| 范围外误命中 | 0 | 0 | 0 |
| 延迟 p50 | 5.9s | 7.5s | **2.5s** |
| 延迟 p95 | 38.0s | 29.9s | 36.5s* |
| 延迟 max | 64.0s | 96.0s | 61.6s* |

*残余长尾集中在 DOC_QA 全文问答（~7.4k prompt tokens 预填充）与 NO_MATCH
路由 LLM 回答；隔离重测同类问题为 12s，批量运行时的长尾主要是连续请求触发的
上游 API 限流，非链路内在成本。

### 已实施的优化（Run 3 前合入）

1. **P0 — CLARIFY/OFFER_SWITCH 固定模板**：文档聚合已确定性算出候选时不再
   调 LLM 措辞，模板直出并枚举标题（`services/rag.py` `_plan_chat_turn`）。
   效果：澄清类问题 9~64s → 1~3s；"无标题澄清破坏闭环"缺陷消除；候选枚举
   更全（基线 LLM 偶尔漏列候选）。
2. **P1 — DOC_QA/CHUNK_QA 精炼约束**：模板增加"总长度控制在 500 字以内"
   规则（`rag/prompt.py`）。效果：追问答案 ~486 字（此前动辄两千字）。
3. **P2 — Rerank 文本带文档标题**：`_build_rerank_documents` 前缀
   "文档标题：X"（对齐 Support 路由器的做法）。效果：Run 2 中偶发的
   "FFC 接线盒拉回 PDC 文档"混淆在 Run 3 未复现。

按响应类型（Run 3）：`DOC_DELIVERED` 平均 ~1.9s；固定模板澄清 1~3s；
LLM 生成路径（DOC_QA/NO_MATCH）6~40s（受 API 限流影响）。

## 关键发现

1. **命中质量良好**：三轮运行通过率 92.9% → 97.6% → 100%，全程零错交付、
   零范围外误命中，多轮焦点保持 100%。快速交付路径（标题类问题 → 文档聚合
   锁定 → DOC_DELIVERED）表现优秀。
2. **延迟的主因是"用 LLM 装饰确定性结果"**（已修复）：CLARIFY/OFFER_SWITCH
   分支原本把已算好的候选交给 LLM 自由发挥措辞，产生 9~96s 的长尾和
   "不列标题"缺陷；改为固定模板后该类问题 1~3s 返回且标题必然齐全。
3. **同题非确定性**：基线两次运行失败用例不同（阈值边缘 + LLM 温度），属
   预期波动；做改动对比时应关注分类通过率而非单例。
4. **残余长尾**：DOC_QA 全文问答的 7.4k prompt tokens 预填充与 NO_MATCH
   路由 LLM 回答；批量连续请求会触发上游限流放大长尾（隔离重测同类问题
   12s vs 批量 61s）。后续可考虑 provider 复用与 prompt 前缀缓存。

## 作为回归门禁

改动 RAG 链路（检索参数、prompt、路由阈值、模型切换）前后各跑一次：

- 通过率（整体与分类）不得下降超过 2pp；
- `forbidden_keys` 命中必须保持为 0；
- 延迟 p50/p95 需对比记录。

## 与 support-acceptance 的关系

`support-acceptance/` 面向已旁路的 Support 链路（Profile 路由契约），保留作历史
回归；本目录面向上线链路。真实流量中出现代表性失败时，经人工确认后按上述
`expected` 格式追加用例，保持总量小而有效。
