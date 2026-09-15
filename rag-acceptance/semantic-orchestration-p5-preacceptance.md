# RAG 语义编排 P5 前置验收报告（P2/P3 范围）

记录日期：2026-09-14。本文是双 LLM 语义编排的 **P5 前置验收**执行记录：在本地隔离
环境对 P2/P3 已交付代码执行 50 题专项 Golden 的 `off/shadow/enabled` 三模式对照、
1/3/5 并发验证和关闭开关回滚演练。P4（`rag_case` 结构化排障状态）尚未实施，
完整工程师逐题评审尚未执行，因此本文不是 P5 放行报告；结论为 **enabled 未达到
第十六节放行硬门槛，不建议生产启用**，维持 `shadow`。

## 环境与输入快照

| 项 | 值 |
| --- | --- |
| 执行环境 | 本地隔离后端（127.0.0.1:18001，独立进程，环境变量覆盖模式开关；用户 18000 实例全程未触碰） |
| 知识库 / 活动索引 | VDR600（Assistant 上线、ready）/ Index v39 `ACTIVE` |
| 文档卡片 | 42/42 `READY/CONFIRMED`，`AUTO/REJECTED/FAILED/STALE/MISSING` 均为 0 |
| 模型快照（每轮冻结） | LLM：`qwen3.7-flash`（DashScope，双 Planner 与生成共用）；Embedding：`Qwen/Qwen3-VL-Embedding-8B`；Rerank：`Qwen/Qwen3-VL-Reranker-8B`（SiliconFlow） |
| 评测输入 | 六个冻结文件 SHA-256 与 P0 基线完全一致（含 `technical-assistant-cases.jsonl` `bd207131…`、`semantic-orchestration-expectations.jsonl` `6d99208c…`） |
| 真实模型轮次 | 72×3（Golden）+ 27（并发）+ 6（回滚）= 249 轮 |

原始报告（本地 `reports/` 被 Git 忽略，关键数字以本文为准）：
`reports/p5-semantic/baseline-20260914-132539.json`（off）、
`baseline-20260914-133856.json`（shadow）、`baseline-20260914-135437.json`（enabled）。

## 一、公开 Golden 对照（50 用例 / 72 轮，Assistant HTTP）

| 指标 | off（旧路径） | shadow（公开=旧路径） | enabled（新路径） |
| --- | --- | --- | --- |
| 通过用例 | 33/50（66.0%） | 32/50（64.0%） | 31/50（62.0%） |
| 传输/HTTP/SSE 错误 | 0 | 0 | 0 |
| 延迟 p50 | 4.854s | 6.200s | 7.199s |
| 延迟 p95 | 21.017s | 24.586s | 16.194s |
| 延迟 max | 23.402s | 27.298s | 32.742s |
| 公开 `DOC_DELIVERED` | 12 | 12 | 20 |
| 公开 `UNCONFIRMED` | 16 | 15 | 4 |
| 公开 `OUT_OF_SCOPE` | 6 | 7 | 6 |

分类通过率（off → enabled）：

| 分类 | off | shadow | enabled |
| --- | --- | --- | --- |
| complete_operation | 3/8 | 3/8 | **5/8** |
| local_answer | 1/10 | 1/10 | **6/10** |
| condition_boundary | 3/4 | 3/4 | **1/4** |
| continuous_troubleshooting | 7/8 | 6/8 | **5/8** |
| domain_assist | 9/10 | 9/10 | **5/10** |
| domain_boundary | 10/10 | 10/10 | **9/10** |

off 与 shadow 公开结果差 1 例（`ta_trouble_006`），属旧路径模型非确定性；
shadow 与 off 的公开行为一致性符合设计（差异仅来自上游非确定性）。

### 结构性收益（off 失败 → enabled 通过，共 7 例）

`ta_full_003`（U 盘升级 PDC 全文）、`ta_full_007`（**配置 VAU 图像完整步骤——
本计划的标志性动机用例**，off 因单 Chunk 0.61 低于 0.70 门槛错误 `UNCONFIRMED`，
enabled 正确 `DOC_DELIVERED`《故障解决文档5—如何配VAU图像》）、
`ta_local_001/005/006/007/009`（首轮全文 + 追问聚焦短答）。

### 结构性退化（off 通过 → enabled 失败，共 9 例）

| 模式 | 用例 | 失败断言 |
| --- | --- | --- |
| 通用回答泄漏文档引用（7 例） | `ta_boundary_008`、`ta_domain_005/006/009`、`ta_trouble_005#2`、`ta_trouble_008#1/2` | `回答不应包含文档引用`：期望 `GENERAL_TECH` 的轮次，Response Planner 选择了 `DOCUMENT_ASSISTED` 并携带引用 |
| 歧义问题错误交付全文（2 例） | `ta_condition_001#1`、`ta_condition_002#1` | “如何升级设备？”期望 `CLARIFY`，实际 `DOC_DELIVERED`《…tftp…3359核心板》 |

另有 `ta_domain_010`（通用拆装问题）enabled 交付《…更换VDR600 OPU单元》全文，
同属错误交付（该例 off 通过、enabled 失败，已计入上表 domain_assist）。

## 二、错误文档交付硬门槛核对（计划 16.4）

| 检查 | 结果 |
| --- | --- |
| 错误文档全文交付 = 0 | **违反**：`ta_full_004`（问“升级 FFC”交付《故障解决文档11—如何U盘升级PDC》）、`ta_domain_010`（通用问题交付 OPU 更换全文）、`ta_condition_001#1`/`ta_condition_002#1`（歧义交付 3359 升级文档）≥ 4 轮 |
| 范围外/噪声/注入 | 基本通过：噪声 5/5 门禁短路；`ta_boundary_008` 混合请求回归（引用泄漏） |
| 任务级文档关键题 | 5/8：`ta_full_004` 选错文档；`ta_full_006/008` 未交付（见 Planner 降级） |
| 局部短答 ≥90% | local_answer 6/10（60%），未达 |
| 连续排查 | 5/8 且引用泄漏回归，未达 |
| Assistant 契约/并发/断流 | 249 轮 0 传输错误；1/3/5 并发全部成功 |

**结论：enabled 不满足放行硬门槛（NO-GO）。**

## 三、双 Planner 统计（Trace 聚合，噪声门短路 3 轮不计）

| 指标 | shadow | enabled |
| --- | --- | --- |
| Query Planner 通过 | 55/69（79.7%） | 59/69（85.5%） |
| Query Planner 降级原因 | `QUERY_PLAN_SEMANTIC_INVALID` ×14 | `QUERY_PLAN_SEMANTIC_INVALID` ×10 |
| Query Planner 延迟 p50/p95/max | 2.056s / 7.709s / 8.433s | 2.119s / 4.667s / 7.425s |
| Response Planner 通过 | 61/69（88.4%） | 59/69（85.5%） |
| Response Planner 降级原因 | `ValueError` ×7、`STRUCTURED_OUTPUT_INVALID` ×1 | `ValueError` ×6、`STRUCTURED_OUTPUT_INVALID` ×4 |
| Response Planner 重试 | 1 轮需要 2 次 | 3 轮需要 2 次 |
| Response Planner 延迟 p50/p95/max | 2.825s / 10.831s / 24.012s | 3.343s / 11.282s / 16.139s |
| 初始 Embedding 失败 | 未观察到 | 未观察到 |

策略与冻结旁路期望一致率：shadow 48/72（66.7%）、enabled 48/72（66.7%）；
enabled 分类：complete_operation 5/8、condition_boundary 4/8、
continuous_troubleshooting 10/16、domain_assist 4/10、domain_boundary 9/10、
local_answer 16/20。最大偏差簇为 `GENERAL_TECH → DOCUMENT_ASSISTED`（10 轮）。

降级后旧路由兜底行为正常：所有 Planner 降级轮次公开请求仍完成，未出现半成品
回答或双回答。

## 四、1/3/5 并发验证（enabled，3 个代表问题 × 各并发级）

| 并发 | 成功 | p50 | p95 | max |
| --- | --- | --- | --- | --- |
| 1 | 3/3 | 10.688s | 12.791s | 12.791s |
| 3 | 9/9 | 6.497s | 14.548s | 14.548s |
| 5 | 15/15 | 7.272s | 22.308s | 26.785s |

27 请求全部到达终态、响应类型正确。**复核更正（2026-09-14）**：Response Planner
降级 9/27 经按问题维度拆分后，全部集中在 delivery 类问题（“请给出检查 LRU 通讯线缆
的完整操作流程”，9/9 降级，原因均为 `ValueError` 字段校验拒绝、约 3.2–3.7 秒完成、
未触及超时，与顺序金集 `ta_full_001/008` 同簇）；short/assist 两类问题 18/18 全部
通过（含 5 并发）。初版报告将 33% 对比顺序 14% 解读为"并发放大上游限流"系误判——
差异由问题构成不同造成（并发样本 1/3 为 delivery 类），并发本身只影响延迟
（p95 12.8→14.5→22.3s），未观察到 Planner 失败率被并发放大。delivery 类问题另有
7/9 同时出现 Query Planner `QUERY_PLAN_SEMANTIC_INVALID` 降级。

## 五、关闭开关回滚演练（off + 进程重启）

5 例代表样本（含 `ta_full_007`、`ta_local_005`、`ta_domain_010`）：

- 6 条 Trace 均无 `semantic_orchestration` / `response_plan` 键：Planner 完全不再运行。
- `ta_full_007` 恢复旧路径失败特征（未交付全文）；`ta_local_005#2` 恢复 `UNCONFIRMED`；
  `ta_domain_010` 恢复通用 `ANSWERED`。
- 仅切换 `M3_RAG_SEMANTIC_ORCHESTRATION_MODE=off` 并重启进程即可恢复旧路由，
  不需要回滚数据、卡片或索引。

## 六、根因与修复建议（按影响排序）

1. **LLM 2 决策矩阵偏离**（影响 12+ 轮）：通用工程问题倾向选 `DOCUMENT_ASSISTED`
   并携带引用、歧义问题不澄清直接交付，违反 9.1 矩阵的 `GENERAL_TECH`/`CLARIFY` 行。
   需修改 Response Planner Prompt 的决策矩阵约束与判例，并考虑对
   `require_no_reference` 语义类问题在执行器侧禁止引用。
2. **`DOCUMENT_DELIVERY` 校验拒绝**（`ValueError` 6–7 轮/次）：模型在全文交付时
   输出了非法 `answer` 字段组合，确定性校验拒绝后回退旧路由，`ta_full_001/008`
   等全文题因此失败。需在 Prompt 中强化“全文交付 answer 置空”约束或放宽为
   单句定位提示。
3. **候选文档误选**（`ta_full_004` FFC→PDC）：LLM 2 从白名单中选择了错误文档，
   说明候选包内相似文档（升级类）区分不足；需在候选包中强化组件/任务差异描述。
4. **Query Planner 语义校验失败率 14–20%**（`QUERY_PLAN_SEMANTIC_INVALID`）：
   降级路径正常（回退原问题），但损失多查询召回收益。**2026-09-14 复现诊断已定位主因**：
   用真实模型在 14 个失败轮次上重放（每题 2 次），拒绝几乎全部来自校验自身设计——
   `_PRECISE_TOKEN_PATTERN` 把任何 ≥2 字母英文单词当作"精确标识"（中文问题 +
   英混查询即误判），且斜线复合词（`FFC/FFC600`）无法与其部分（`FFC600`）匹配；
   而真正的纯数字标识（如 3359）反而不被捕获。修复方向：将严格检查收缩到
   含数字/带单位的标识，修正分词边界。
5. **可观测性缺陷**：`backend/app/rag/response_planner.py:394` 以
   `getattr(exc, "code", type(exc).__name__)` 记录原因，校验类 `ValueError` 的
   具体信息（如“DOCUMENT_DELIVERY 字段组合非法”）丢失，Trace 只剩类名，
   妨碍 P5 要求的降级原因统计。建议修复为包含 `str(exc)`。

## 七、结论与后续

- **enabled 未达放行门槛**：错误文档全文交付 ≥4 轮（硬门槛要求 0）、
  local_answer 60%（门槛 90%）、整体 62.0% 低于 off 基线 66.0%。
- **核心假设成立**：文档卡片 + 双 LLM 编排确实修复了旧路径的结构性缺口
  （标志性 VAU 用例、局部短答 1/10→6/10、任务级全文 3/8→5/8、
  `DOC_DELIVERED` 12→20、p95 21.0s→16.2s），并发与回滚机制可靠。
- **后续顺序**：先按第六节修复 Prompt/校验/候选包问题并复跑三模式对照；
  P4（`rag_case`）仍未实施，其连续排查类用例的完整验收依赖 P4；
  工程师逐题盲评尚未执行。生产保持 `off/shadow`，不启用 `enabled`。

## 复现方式

```bash
# 每模式一次（off/shadow/enabled），独立端口避免影响既有实例
cd backend && M3_RAG_SEMANTIC_ORCHESTRATION_MODE=<mode> \
  ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18001
GOLDEN_ASSISTANT_TOKEN=<token> PYTHONPATH=backend \
  .venv/bin/python rag-acceptance/run_golden.py --api-mode assistant \
  --base-url http://127.0.0.1:18001 \
  --kb-id 5ae9881a-a568-436b-9330-2da5e9a2a9c9 \
  --cases rag-acceptance/technical-assistant-cases.jsonl
# Planner 统计：按 request_id 前缀聚合 rag_trace.runtime_snapshot
# 的 semantic_orchestration / response_plan；并发与回滚脚本为本轮临时工具
# （/tmp/opencode/p5-semantic/），长期化前不作为仓库依赖。
```
