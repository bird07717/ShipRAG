# 线上运营手册 —— 每周能力例会

> 目的：把生产数据变成能力改进。每周 30 分钟，三个固定动作：**拉数据 → 分类归因 → 按手册行动并验证**。
>
> 适配范围：VDR600 知识库（B2B Assistant 调用）。
> 注意：全量 Trace 只保留 **7 天**（摘要 30 天）——例会不要跳周，否则细节已清理。

---

## 一、会前准备（5 分钟，拉三份数据）

### 1. 管理台 Dashboard（浏览器）

- **Assistant 调用面板**：外部调用错误率（>5% 会标红）、外部 P95 延迟（>10s 标黄）、外部 Assistant TOP 错误码
- **回答结果面板**：仅统计外部 Assistant API 的 RAG 请求；分别查看直接回答、待补充信息、范围外请求和失败，不能把范围外拒答计作回答失败
- **回答依据分布**：查看文档知识、文档加通用解释、通用工程扩展的占比；分母只含三类 `PRODUCT_*` 回答
- **待补充信息面板**：本周 `UNCONFIRMED` 问题清单，点行可看 Trace；该状态可能是正常追问条件，不直接等同于知识库缺口
- **请求错误日志**（LogsView 页）：HTTP 层 4xx/5xx 分布

### 2. 待补充信息问题 TOP（复制执行）

```bash
docker exec enterprise-rag-postgres-1 psql -U rag -d rag_platform -c "
SELECT left(question, 30) AS 问题, count(*) AS 次数, count(DISTINCT user_id) AS 用户数
FROM rag_trace
WHERE response_type = 'UNCONFIRMED'
  AND status = 'COMPLETED'
  AND engine = 'RAG'
  AND source = 'ASSISTANT_API'
  AND created_at > now() - interval '7 days'
GROUP BY question ORDER BY count(*) DESC LIMIT 20;"
```

### 3. 挫败感信号：UNCONFIRMED 后换法重问率

> 用户在待补充回答后继续追问，可能是在补齐正常条件，也可能是换个问法再试。该指标只用于发现高频问题，不能单独证明知识库覆盖不足。

```bash
docker exec enterprise-rag-postgres-1 psql -U rag -d rag_platform -c "
WITH unconf AS (
  SELECT conversation_id, created_at FROM rag_trace
  WHERE response_type = 'UNCONFIRMED'
    AND status = 'COMPLETED'
    AND engine = 'RAG'
    AND source = 'ASSISTANT_API'
    AND created_at > now() - interval '7 days'
)
SELECT count(*) AS \"UNCONFIRMED轮次\",
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM rag_trace nxt
         WHERE nxt.conversation_id = unconf.conversation_id
           AND nxt.created_at > unconf.created_at
       )) AS \"其后有追问\"
FROM unconf;"
```

### 4. 本周错误码分布

```bash
docker exec enterprise-rag-postgres-1 psql -U rag -d rag_platform -c "
SELECT error->>'code' AS 错误码, count(*) AS 次数
FROM rag_trace WHERE error->>'code' IS NOT NULL AND created_at > now() - interval '7 days'
  AND engine = 'RAG' AND source = 'ASSISTANT_API'
GROUP BY 1 ORDER BY count(*) DESC LIMIT 10;"
```

---

## 二、例会归因（20 分钟）：分类判定规则

对每个待补充信息问题，点开 Dashboard Trace 详情（问题理解、候选文档、文档级重排与生成详情），先看内部诊断原因，再按下表归类。`LOW_INFORMATION`、`SCOPE_CONFLICT`、`NEEDS_KEY_CONDITION` 分别表示确定性低信息门禁、两阶段范围冲突已收口、模型要求一个关键条件；没有原因或原因不适用时继续人工判断：

| 分类 | 判定特征 | 归因 |
| --- | --- | --- |
| **A 输入信息不足** | 问题确实缺少会改变结论的产品、部件、状态或操作目标 | 正常追问，不算缺陷 |
| **B 低信息/无效输入** | 纯符号、重复字符、无法形成技术目标 | 流量构成，不算知识库缺口 |
| **C 文档缺口** | 问题属于产品范围，人工确认知识库没有对应资料，且通用工程分析无法安全推进 | 知识覆盖不足 |
| **D 检索或生成误判** | 问题信息充分且存在相关资料，但候选错误，或模型仍要求补充无关条件 | 检索/生成质量问题 |
| **E 技术故障** | 错误码类（UPSTREAM_UNAVAILABLE / TURN_DEADLINE_EXCEEDED / INTERNAL_ERROR） | 基础设施/供应商；从失败日志处理，不计入 `UNCONFIRMED` |

---

## 三、行动手册（按分类执行）

### C 文档缺口 → 补文档

1. 从源手册（VDR600/）补充或修订对应文档
2. 管理台上传 → 自动构建候选索引 → 检查后明确设为 RAG 在线版本
3. 用原始问题在 DemoChat 复测 → 跑黄金集（见第四节）

### D 检索或生成误判 → 先固化案例

1. 先保存 Trace、问题和人工期望，并加入 Golden Set
2. 判断问题属于检索候选错误、任务理解错误还是生成阶段无效追问
3. 只提交诊断与拟修改范围；涉及 Query Interpreter、检索、Rerank 或 Answer Generator 的核心行为，评审同意后再修改
4. 修改后用原始问题、相邻问题和完整 Golden Set 对照验证

### E 技术故障 → 对症

| 错误码 | 含义 | 动作 |
| --- | --- | --- |
| UPSTREAM_UNAVAILABLE | 模型服务故障/超时 | 看 LogsView 明细；偶发属正常（有重试兜底）；持续出现查供应商状态 |
| TURN_DEADLINE_EXCEEDED | 45s 总时限截断 | 多为上游慢速；确认当前模型档位是否合理 |
| INTERNAL_ERROR | 内部错误 | 转开发，带 request_id |

---

## 四、验证纪律（每次改动的收尾，不可跳过）

任何改动（补文档/加同义词/调 Prompt/换模型档）完成后：

```bash
.venv/bin/python rag-acceptance/run_golden.py
```

- 与 `rag-acceptance/README.md` 中的最近基线对比：**不低于基线才算完成**
- 回归恶化 → 回退改动或修正后重跑
- 改动与结果记录追加到 `rag-acceptance/README.md` 运行历史

---

## 五、升级与留痕

- 单周例会无法解决的项 → 转**研发**处理，附 `trace_id`（Trace 详情页可复制）
- 每周例会结论（分类计数 + 本周改动 + 黄金集分数）留一行记录，累计即能力演进史
- 数据参考：`reports/baseline-*.json` 保留历次全量明细
