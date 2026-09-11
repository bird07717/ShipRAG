# 线上运营手册 —— 每周能力例会

> 目的：把生产数据变成能力改进。每周 30 分钟，三个固定动作：**拉数据 → 三分类归因 → 按手册行动并验证**。
>
> 适配范围：VDR600 知识库（B2B Assistant 调用）。
> 注意：全量 Trace 只保留 **7 天**（摘要 30 天）——例会不要跳周，否则细节已清理。

---

## 一、会前准备（5 分钟，拉三份数据）

### 1. 管理台 Dashboard（浏览器）

- **Assistant 调用面板**：外部调用错误率（>5% 会标红）、外部 P95 延迟（>10s 标黄）、TOP 错误码
- **未回答问题面板**：本周 UNCONFIRMED 问题清单，点行可看 Trace 归因
- **请求错误日志**（LogsView 页）：HTTP 层 4xx/5xx 分布

### 2. 无答案问题 TOP（复制执行）

```bash
docker exec enterprise-rag-postgres-1 psql -U rag -d rag_platform -c "
SELECT left(question, 30) AS 问题, count(*) AS 次数, count(DISTINCT user_id) AS 用户数
FROM rag_trace WHERE response_type = 'UNCONFIRMED' AND created_at > now() - interval '7 days'
GROUP BY question ORDER BY count(*) DESC LIMIT 20;"
```

### 3. 挫败感信号：UNCONFIRMED 后换法重问率

> 用户在未确认回答后继续追问，通常意味着"换个问法再试"——重问率高 = 覆盖缺口在真实伤害用户。

```bash
docker exec enterprise-rag-postgres-1 psql -U rag -d rag_platform -c "
WITH unconf AS (
  SELECT conversation_id, created_at FROM rag_trace
  WHERE response_type = 'UNCONFIRMED' AND created_at > now() - interval '7 days'
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
GROUP BY 1 ORDER BY count(*) DESC LIMIT 10;"
```

---

## 二、例会归因（20 分钟）：三分类判定规则

对每个无答案问题，点开 Dashboard Trace 详情（检索结果、rerank 得分、命中文档），按下表归类：

| 分类 | 判定特征 | 归因 |
| --- | --- | --- |
| **A 文档缺口** | 问题属于产品应有内容，但检索结果为空或全部低分；人工确认知识库里**没有**对应文档 | 知识库覆盖不足 |
| **B 检索未命中** | 知识库里**有**相关文档（人工确认），但该文档没进 rerank TOP（常见：用词不一致，如"检查"vs"自检"） | 检索通道盲区 |
| **C 回答质量** | 正确文档已命中并进入上下文，但答案错误/遗漏/冗余 | 生成环节问题 |
| **D 技术故障** | 错误码类（UPSTREAM_UNAVAILABLE / TURN_DEADLINE_EXCEEDED / INTERNAL_ERROR） | 基础设施/供应商 |

---

## 三、行动手册（按分类执行）

### A 文档缺口 → 补文档

1. 从源手册（VDR600/）补充或修订对应文档
2. 管理台上传 → 自动构建候选索引 → 检查后明确设为 RAG 在线版本
3. 用原始问题在 DemoChat 复测 → 跑黄金集（见第四节）

### B 检索未命中 → 优先加同义词（成本最低）

同义词是**查询时**扩展，**不需要重建索引**：

1. 编辑 `backend/app/rag/synonyms.py`，在 `DOMAIN_SYNONYM_GROUPS` 加一组
   （判断标准：用户词与文档词高频不一致时才加，宁缺毋滥——组越大精度越差）
2. 重启 API（开发态 `--reload` 自动生效；生产态重启 api 服务）
3. 用原始问题复测确认命中

> 加同义词解决不了的 B 类：检查该文档的分块是否把关键词切走了（此时转开发处理）。

### C 回答质量 → 调 Prompt 规则

1. 编辑 `backend/app/rag/prompt.py`（模式判定/DOC_QA/CHUNK_QA 规则与判例）
2. **先把该案例加入黄金集**（`rag-acceptance/cases.jsonl`），再调 Prompt
3. 跑黄金集前后对比——防止按下葫芦浮起瓢

### D 技术故障 → 对症

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
- 每周例会结论（三分类计数 + 本周改动 + 黄金集分数）留一行记录，累计即能力演进史
- 数据参考：`reports/baseline-*.json` 保留历次全量明细
