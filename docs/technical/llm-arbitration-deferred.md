# 在线 LLM 仲裁：延迟决策记录

状态：已决策（不实施，数据触发后重新评审）
日期：2026-08-28
范围：Support 文档路由 NO_MATCH 路径的在线仲裁 LLM
关联：`document-hit-capability-execution-plan.md`（该方案已将"在线 LLM Catalog 分类"列为非目标，本记录延续该决策）

## 1. 决策

不在当前阶段为文档命中链路添加在线"LLM 管理者 / 仲裁者"。规则的确定性路由保持唯一决策来源。

## 2. 理由

1. **分工已经正确**。LLM 已在环内承担语义部分：生成时 `[MODE:]` 自分类、查询改写
   （`query_rewrite.py`，门控 + 超时 + 降级）、离线 Profile 三阶段生成（含质检审核员）。
   路由决策（`ProfileResolver` / `decide_doc_routing`）留给规则：可审计、零热路径延迟、可回归测试。
2. **本场景低误交付率优先**。在线 LLM 仲裁引入非确定性 + 延迟 + 新失败模式；
   出错时兜底仍是规则，等于多一层依赖没有消除任何风险。
3. **运维成本教训**。Profile 生成的 LLM 调用曾长期存在慢、易失败、孤儿任务问题
   （已于 2026-08 修复：并发、协作式取消、重试、孤儿对账）。在线链路再挂一个 LLM 依赖会重复该成本。

## 3. 先行落地的替代措施（已实施）

弱候选不丢弃、也不进入自动交付回路，以"提示 + 引导"形式消化 NO_MATCH：

- `services/rag.py` NO_MATCH 路由回合：检索弱候选（`doc_scores` 中 `hits>=1` 的 top-3）
  注入 ROUTING prompt 上下文，仅供 LLM 澄清参考；**不挂 `pending_options`**，
  不进入 `resolve_pending_option` 自动交付解析。
- 同一回合的 done payload 附带 `next_suggestions`（弱候选标题派生的 ≤3 个可点问题，
  回退目录派生），DemoChat 在失败回复后重显建议条。
- Support 引擎 `support/service.py` 的固定 NO_MATCH 话术（RAG 兜底被策略禁用时）
  本期未改动，保持提示文案现状。

## 4. 重新评审的量化触发条件

满足任一条件时，重新评审是否引入仲裁 LLM（届时还需未解决问题聚类数据，见第 5 节）：

1. 连续 14 天首轮 NO_MATCH 率 > 15%（基线取当期 `rag/metrics.py` support 统计），
   且 top 问题簇在上述弱候选提示上线后无改善；
2. 弱候选展示后的用户确认率 < 20%（即标题提示不足以闭环，用户仍大量流失）。

## 5. 数据依赖：未解决问题聚类（二期）

现有 metrics 只有比率没有问题分布。二期新增 admin 只读接口
`GET /admin/rag/unresolved-clusters?days=7`：对 UNCONFIRMED / OUT_OF_SCOPE / NO_MATCH
的用户问题做规则聚类（复用 `normalize_for_match` + 关键词重叠），返回 top-N 簇。
该数据同时服务第 4 节的触发判断与 Profile / 文档覆盖的运营决策。

## 6. 届时的接入约束（如触发）

- 接入点：`decide_doc_routing` 返回 NO_MATCH 之后、进入 Generic RAG 之前；
- 必须：门控开关、独立超时、失败降级回原 NO_MATCH 路径；
- 禁止：触碰 DELIVER 锁定逻辑；禁止替代 `resolve_pending_option` 的用户确认闭环；
- 上线方式：A/B 对照（仲裁开 / 关），以误交付率与首轮 NO_MATCH 率双指标验收。
