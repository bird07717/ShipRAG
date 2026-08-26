# Support：高质量 Profile 优先与 RAG 文档兜底实施计划

## 1. 目标与边界

本次改造解决 Support 链路中两类问题：

1. 文档发布时自动生成的 Profile 只包含一个标准问题，无法覆盖用户的自然表达、故障现象和错误码。
2. 当 Profile 未命中时，Support 直接返回 `NO_MATCH`，没有利用已经完成索引的文档正文检索能力。

目标是形成以下两层路由，不引入新的在线“全库 LLM 决策器”：

```text
离线：文档正文 -> LLM 结构化 Profile 草稿 -> 管理员审核 -> 原子发布
在线：Profile 高置信命中 -> 交付文档
                    未命中/冲突 -> RAG 文档级检索兜底 -> 交付、澄清或 NO_MATCH
```

明确不在本次范围内：

- 不改变已锁定文档后的 Focused Support Assistant 回答和证据校验。
- 不将完整通用 RAG 回答链路接入 Support；兜底只负责选择文档。
- 不增加新的在线 LLM 裁决步骤。
- 不自动发布未经审核的 Profile 草稿。

## 2. 设计原则

1. **Profile 优先且保守。** 标准问题、别名和结构化元数据存在唯一明确匹配时，维持低延迟直接交付。
2. **正文检索只做兜底。** 只有 Profile 返回 `NO_MATCH` 或 `AMBIGUOUS` 时才调用检索、重排和文档聚合。
3. **按文档而非 chunk 决策。** 复用 RAG 的 BM25、向量、RRF、rerank 及 `doc_router` 的文档级聚合策略。只有满足锁定阈值才自动交付；候选接近时返回最多三项选择；无充分证据时拒绝。
4. **所有路由可解释。** Support trace 记录 `PROFILE` 或 `RAG_FALLBACK`、候选文档、得分归一化方式和最终决策。
5. **降级安全。** RAG 兜底的 embedding/rerank 异常不能误交付文档；降级为原有 Profile 的澄清或 `NO_MATCH`。

## 3. 离线 Profile 增强

### 3.1 Profile 草稿字段

在现有 `problem_profile_draft` 和线上 `document_problem_profile` 中加入：

- `routing_summary`：100--300 字，说明文档能解决什么、适用对象及关键前提；供人工审核和路由重排使用。
- `symptoms`：最多 12 个用户可能描述的故障现象、错误码或任务目标。
- `exclusions`：最多 8 个不适用范围或易混淆情形；仅用于审核和未来策略，不作为正向匹配词。

`canonical_question` 仍为一个稳定标准问题；`aliases` 限制为最多 10 条完整用户问法。若单文档包含多个互不相关的操作主题，应在文档治理中拆分文档；本次不为单篇文档创建多个 Profile。

### 3.2 生成与审核

索引构建完成后，现有 `ReleaseService.generate_for_index()` 继续读取该索引的重建文档正文。生成 Prompt 必须：

- 以正文事实为准，标题仅作辅助；
- 要求 aliases 覆盖标准问法、口语改写、症状/错误码问法；
- 禁止把文档中不存在的型号、版本、步骤写入字段；
- 要求摘要说明适用范围；
- 保持结构化 JSON 和 Pydantic 上限校验。

模型不可用时，保留当前保守 fallback；新增字段使用空数组/标题摘要，且仍进入人工审核。

管理页面允许查看并编辑新增字段。修改任何 Profile 内容都会把草稿状态退回 `DRAFT`，发布校验保持“全部草稿审核通过”的现有规则。

## 4. 在线 RAG 文档兜底

### 4.1 触发条件

`ProblemResolver.resolve()` 返回 `NO_MATCH` 或 `AMBIGUOUS` 时触发；显式 `document_id`、`problem_code`、会话中的 `choice_id` 和已经锁定文档的追问不触发。

### 4.2 检索流程

使用当前 active/frozen index，依次执行：

1. 对用户问题生成 embedding，并并发执行 BM25；
2. 查询 vector Top-K，和 BM25 结果以 RRF 融合；
3. 用已有 rerank provider 对融合候选重排；若 rerank 可降级失败，使用融合排序；
4. 用 `decide_doc_routing()` 聚合 chunk 为文档分数，并产出 `DELIVER`、`CLARIFY` 或 `NO_MATCH`；
5. `DELIVER` 时以 `method=RAG_FALLBACK` 调用现有 `_deliver()`；`CLARIFY` 复用 Support 既有的 `DOCUMENT_CHOICE` 协议；`NO_MATCH` 维持当前提示。

Support 不构造 chunk 上下文、不调用通用 RAG 生成回答、也不在此步骤加载图片。因此此路径只增加“定位文档”的代价。

### 4.3 配置与可观测性

复用当前 M3/M5 配置：`m3_vector_top_k`、`m5_bm25_top_k`、`m5_fusion_top_k`、`m5_rerank_top_n` 及文档路由阈值。Support trace 必须记录：

- `routing.source`：`PROFILE`、`RAG_FALLBACK` 或 `RAG_FALLBACK_DEGRADED`；
- Profile 决策原因；
- RAG 文档候选与 `doc_router` 决策；
- rerank 是否降级。

## 5. 实施步骤

1. 新增迁移，扩展草稿与线上 Profile 表；同步 Pydantic、repository 序列化和前端类型。
2. 修改 `GeneratedProfile`、LLM Prompt 和 fallback 生成；更新草稿保存、重新生成和发布 SQL。
3. 在管理端增加新增字段的显示与编辑，并保证编辑时草稿状态正确回退。
4. 抽取面向 Support 的只读文档检索器，复用 RAG repository/provider/doc router，而不复用通用回答服务。
5. 在 `TechnicalSupportService.handle()` 的无 focus 首问分支接入 fallback；复用既有 choice 协议和 `_deliver()`。
6. 为离线生成、Profile 命中优先、RAG 自动交付、RAG 澄清、RAG 失败降级增加单元测试；执行后端与前端类型检查。

## 6. 验收标准

- 新上传或变更的文档在构建成功后生成含新增字段的 `DRAFT` Profile；未审核时不会影响线上 Support。
- 已发布且唯一 Profile 匹配的问题不调用 RAG，响应仍为 `DOCUMENT_DELIVERED`。
- Profile 未命中的问题可由 RAG 文档级高置信决策交付正确候选文档。
- RAG 出现接近候选时返回 `DOCUMENT_CHOICE`，而不是随机交付。
- 检索/重排服务异常时，返回原有的澄清或 `NO_MATCH`，绝不交付未验证文档。
- 已锁定文档的后续问题保持原有单文档回答行为。
- 现有 Support、Release、RAG 单元测试全部通过；新增测试覆盖以上分支。

## 7. 上线前评估与回滚

项目当前没有线上评测集，因此本次仅以固定 fixture 和行为测试验证。后续应以每篇文档 10--20 条问题建立评测集，统计 Top-1、Top-3、误交付率、澄清正确率和 P95 延迟。

回滚开关为 Support RAG fallback 的配置项。关闭时维持现有 Profile-only 行为；数据迁移提供向后兼容的文本/空数组默认值，不影响已发布 Profile。

## 8. 实施记录（2026-08-25）

已完成：

- 新增 `0022_profile_routing_metadata` 迁移，为草稿和线上 Profile 添加 `routing_summary`、`symptoms`、`exclusions`；字段均具备安全默认值。
- 更新离线 LLM Prompt、fallback 草稿、重新生成、人工编辑、原子发布和前端审核表单，使新增字段从生成到发布完整透传。
- 新增 `SupportDocumentRouter`，在 Profile `NO_MATCH` 或冲突时复用 BM25、向量、RRF、rerank 和 `doc_router` 的文档级决策；不调用通用 RAG 回答，也不增加在线 LLM 分类。
- 增加 `SUPPORT_RAG_FALLBACK_ENABLED` 回滚开关，关闭时保留 Profile-only 行为。
- 新增 Profile 快路径、RAG 自动交付、RAG 澄清并确认、chunk 聚合路由的测试。

验证结果：

- 后端：`mypy`（9 个修改模块）、`ruff`、39 项定向 Support/Release/RAG fallback 测试通过；Alembic head 为 `0022`。
- 前端：`vue-tsc`、ESLint、Vitest 92 项测试通过。
- 完整后端测试套件在既有 API 测试阶段持续无输出，已中止，未作为通过项；本次修改涉及的定向套件均通过。
