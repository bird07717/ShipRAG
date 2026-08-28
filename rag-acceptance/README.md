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

## 基线结果（2026-08-28，两次运行）

| 指标 | Run 1 | Run 2 |
| --- | --- | --- |
| 通过率 | 39/42 (92.9%) | 41/42 (97.6%) |
| 错交付（forbidden 命中） | **0** | **0** |
| 范围外误命中 | 0 | 0 |
| 延迟 p50 | 5.9s | 7.5s |
| 延迟 p95 | 38.0s | 29.9s |
| 延迟 max | 64.0s | 96.0s |

按响应类型（Run 2）：`DOC_DELIVERED` 平均 **1.5s**（max 3.1s）；
`ANSWERED` 平均 **18.7s**（max 96s）。34% 轮次超过 15 秒。

## 关键发现

1. **命中质量良好**：两轮均 >92%，零错交付、零范围外误命中，多轮焦点保持 100%。
   快速交付路径（标题类问题 → 文档聚合锁定 → DOC_DELIVERED）表现优秀。
2. **延迟是当前最大的上线风险**：慢的根因是 LLM 生成路径——ROUTING 自由文本回答
   （prompt 含 46 份文档目录 + 弱候选，生成冗长候选列表）与 DOC_QA 全文问答。
   确定性交付路径不受影响。
3. **同题非确定性**：两次运行失败用例不同（MU报警 run1 失败/run2 通过；FFC 接线盒
   追问 run1 通过/run2 失败），源于文档聚合阈值边缘 + LLM 温度。黄金集测的是
   通过率基线，单用例的波动属预期；做改动前后对比时应关注分类通过率而非单例。
4. **澄清回答不列标题会破坏闭环**："MU报警了怎么处理"的回答让用户在"排查/更换"
   之间选择，却不列出文档标题——而 `resolve_pending_option` 靠标题匹配用户回复，
   无标题澄清导致下一轮无法收敛（真实产品缺陷，值得修复）。
5. **近邻文档偶发混淆**："FFC怎么检查接线盒"偶发拉回 PDC 接线盒文档（标题仅设备
   名不同），属向量/重排对设备名前缀不敏感的表现，与"embedding/rerank 文本不含
   文档标题"的已知缺口一致。

## 作为回归门禁

改动 RAG 链路（检索参数、prompt、路由阈值、模型切换）前后各跑一次：

- 通过率（整体与分类）不得下降超过 2pp；
- `forbidden_keys` 命中必须保持为 0；
- 延迟 p50/p95 需对比记录。

## 与 support-acceptance 的关系

`support-acceptance/` 面向已旁路的 Support 链路（Profile 路由契约），保留作历史
回归；本目录面向上线链路。真实流量中出现代表性失败时，经人工确认后按上述
`expected` 格式追加用例，保持总量小而有效。
