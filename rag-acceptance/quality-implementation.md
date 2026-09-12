# RAG 回答质量实现与实验（2026-09-12）

## 本次处理的具体问题

1. **长文档追问的证据关联**：焦点全文超过字符或输入 Token 预算时，改为在同知识库、会话冻结索引、已确认文档内执行 BM25/向量检索及重排；复用查询向量，不再拿全库片段回答却引用焦点文档。文档内重排失败、无片段时安全拒答；整个规划阶段受本轮剩余时限限制。
2. **预算裁剪**：先按相关性确定保留集合，再按原文顺序渲染。预算不足时移除相关性较低的块，避免因为强证据位于文档末尾而被删掉。全文交付仍保留当前索引已解析的步骤、表格和图片，不受模型输入预算裁剪。
3. **回答约束**：运行时 DOC_QA、CHUNK_QA、ROUTING 模板升至版本 2；“故障依旧则更换”不能反推出“报警消除就维修成功、无需更换”。目录标题只能引导选文档，不能提供其正文尚未给出的步骤和参数。
4. **追问补全**：补充“之前/之后/排查后/是否”等改写触发信号。发现真实改写超时会丢失“还是失败怎么办”的对象后，增加保守兜底：只给明确省略对象的短追问补上同库有效焦点标题；仍执行正常检索、重排和选文档门槛，原问题不变。
5. **可核查的诊断与评测**：Trace 新增实际生成 Prompt 哈希、估算 Token 和上下文来源，有效检索参数反映 Gateway 覆盖；原检索上下文保留原语义。Golden 保存完整公开响应，并检查正文关键内容、图片/表格、条件误推和引用关联。新增离线对照工具，用同一组断言重评前后报告。
6. **事实短答实验**：`M3_RAG_FACT_QA_MODE=enabled` 时，首轮已可靠选中的明确事实题复用 DOC_QA；完整流程和澄清确认仍交付原文。默认保持 `shadow`，记录候选，不切换线上回答方式。

没有新增 API、审核流程、数据库迁移或自动调参任务；本次未修改源文档、活动索引或生产环境。新代码不要求重建索引；原文缺失和已删除文档仍须单独处理。

改动文件按职责如下：

| 职责 | 文件 |
| --- | --- |
| 问答与证据边界 | `backend/app/services/rag.py`、`backend/app/rag/repository.py`、`models.py` |
| Prompt、追问及评测规则 | `backend/app/rag/prompt.py`、`query_rewrite.py`、`evaluation.py` |
| 实验开关 | `backend/app/core/config.py`、`.env.example`、`.env.production.example`、`compose.production.yaml` |
| 验证 | `backend/tests/test_rag_quality.py`、`test_rag_quality_postgres.py`、`test_chat_prompt.py`、`test_query_rewrite.py`、`test_golden_runner.py` |
| 固定用例及对照工具 | `rag-acceptance/run_golden.py`、`compare_golden.py`、`quality-cases.jsonl`、`fact-qa-cases.jsonl`、`README.md`、本报告 |
| 项目认知 | `docs/ai/backend.md`、`database.md`、`environment.md`、`current-state.md`、`known-issues.md`、`decisions.md`（ADR-0014） |

## 实验边界

本地 Assistant 公开 SSE 接口，新建测试会话；VDR600 活动索引版本 39，ID `7b23aee9-f828-4ee6-b282-7de05bf0d283`。对照基线是本次改动前的 `ff2f5d7`。使用数据库配置的真实 SiliconFlow 模型：Qwen3-VL-Embedding-8B、Qwen3-VL-Reranker-8B、DeepSeek-V4-Flash。未调整检索阈值或改动 Golden 主集的预期。

20 个质量用例依据当前索引原文制作，共 30 轮：10 个完整交付、10 个“交付后追问”。覆盖 LRU 线缆、PSU、PDC/FFC 升级、LRU 更换、自检、麦克风、U 盘格式、VAU 图像、UTC 时间；不是 20 条真实客户差评。单次前后实验不能证明所有问法或所有知识库都达到相同通过率。

## 内容质量与事实短答结果

| 评测 | 改动前 / shadow | 改动后 / enabled | 结论 |
| --- | --- | --- | --- |
| 内容质量 20 例 / 30 轮 | 18/20 | 20/20 | 10 个交付均通过；两个原先失败的追问恢复文档对象，LRU 不再直接宣称维修成功，PSU 回答包含拍照及标记 |
| 内容质量延迟 p50 / p95 | 2.276s / 7.250s | 2.669s / 6.210s | 最终完整复验；前一次 20/20 的 p95 为 10.835s，不能据单轮宣称稳定提速 |
| 首轮事实题 6 例 | 5/6 | 5/6 | PDC 格式题两组都进入多文档澄清，未直接给出 FAT32；没有通过率增益 |
| 事实题延迟 p50 / p95 | 1.962s / 3.207s | 4.217s / 5.504s | 生成短答付出额外模型延迟 |
| 5 道已选中文档事实题总正文字符 | 1,732 | 998 | 总体更短，但自检、PSU 等本来较短的原文生成后反而变长 |

核心修复初版在 20 例中仍为 18/20；结合 Trace 补充追问改写触发条件后才达到 20/20。不能把最终增益全部归因于长文档检索或 Prompt 改动。事实模式的 PDC 失败属于尚未唯一选中文档，不靠开启短答开关解决；当前保持 `shadow`。

第一次最终主集执行为 51/53：`rag_idf_003` 依赖的 FFC600 文档已删除；`rag_fup_001` 的查询改写出现 `UPSTREAM_TIMEOUT`，原始短句检索失去 PDC 对象，返回安全拒答但未满足上下文要求。保留该报告及失败断言，并补充焦点标题兜底后重新执行完整主集，不以放宽断言消除失败。

焦点兜底完成后，主集完整复跑为 **52/53**，与改动前相同；正文质量再次完整复跑 **20/20**，两次最终内容复验均通过。主集的追问 5/5、焦点覆盖 3/3、范围外 5/5、噪声 5/5 均通过，没有新增失败；唯一失败仍为已删除的 FFC600 文档。主集延迟 p50/p95 为 2.692s/8.297s（基线 2.835s/11.512s），max 20.265s（基线 16.196s）。未证明稳定延迟或容量改善。

另将临时测试进程的查询改写超时配置为 1 秒，在新会话重复 PDC 追问 3 次，**3/3 通过**；其中两次真实发生 `UPSTREAM_TIMEOUT`，Trace 记录 `fallback=FOCUS_TITLE`，经过正常检索后为 `STAY`。第三次改写正常完成。项目默认超时仍为 3 秒。

只读核验前后及对照报告共 204 个 Trace，均为 `COMPLETED`；两轮主集的 10 个噪声轮次在输入门禁短路，其余 194 轮查询 Embedding 均记录真实 SiliconFlow。模型配置、有效检索参数（5/5/8/5/5）和索引版本一致；40 个实际生成 Prompt 的哈希与保存 Prompt 逐一相符。最终版本相关报告共 111 轮没有 `RUNNING` 遗漏；这不代表全库历史记录均无遗留。

## 工作区门禁与报告

最终 `bash scripts/check.sh` **退出码 0**：Ruff lint/format（234 文件）、Mypy（122 个源文件）、后端 **900 项测试**、摄取/RAG/管理三项 fake 冒烟全部通过，累计覆盖率 **82%**；前端 **22 个文件 151 项测试**、各目录覆盖率门槛、ESLint、类型检查及生产构建通过。仅保留已有的大包警告，本次入口 JS 为 1,326.50 kB（gzip 438.31 kB）。

真实 PostgreSQL 测试验证文档、知识库和索引过滤发生在 Top K 之前，且删除状态不返回文档片段；数据在隔离 schema 的事务中回滚。新增规则回归确认原主集 47 个正向轮次均拒绝“只留正确引用、清空正文”的伪成功，并覆盖正文步骤、图片、表格和引用错配。

门禁使用确认空闲的本地 Redis DB 9 隔离队列，没有清空已有库或修改 `.env`。第一轮门禁曾因生成来源覆盖了检索阶段 `selected_context` 而使 RAG smoke 失败；已改为保留检索语义、独立记录 `runtime_snapshot.generation`，增加回归并完整复跑至通过。最终日志：`/tmp/shipdemo-rag-quality-gate.log`。本轮没有进行生产部署或生产 Compose 启动验收。

实验结束后已停止 7 个临时测试 API，删除临时凭据文件；保留诊断报告和日志。门禁后再次只读确认业务索引仍为 ACTIVE 版本 39，启用模型与实验前一致。

实验报告保存在忽略提交的 `reports/` 下；以下路径指本次本地实测产物，生成代码及固定用例纳入工作区：

- 主集：[改动前](reports/quality-before/baseline-20260911-233832.json)、[最终版本](reports/quality-complete-golden/baseline-20260912-000733.json)、[同断言对照](reports/quality-golden-comparison.json)。
- 内容质量：[改动前](reports/quality-content-before/baseline-20260911-234859.json)、[最终版本](reports/quality-complete-content/baseline-20260912-001201.json)、[同断言对照](reports/quality-content-comparison.json)。
- 事实题：[shadow](reports/quality-fact-shadow/baseline-20260912-000221.json)、[enabled](reports/quality-fact-enabled/baseline-20260912-000237.json)、[同断言对照](reports/quality-fact-comparison.json)。
- [改写超时复验](reports/quality-rewrite-timeout/baseline-20260912-001353.json)、[Trace/模型/索引/Prompt 核查](reports/quality-trace-verification.json)。
- 中间版本的失败现场仍保留在 `reports/quality-final-golden/baseline-20260911-235532.json`；没有用重跑结果覆盖原始失败记录。

## 如何复验和使用

1. 按项目开发流程启动后端，确认目标知识库已上线且策略为 `RAG_ONLY`；通过环境变量注入 `GOLDEN_ASSISTANT_TOKEN`，不将凭据写入报告。
2. 在根目录运行主集和质量集：

   ```bash
   .venv/bin/python rag-acceptance/run_golden.py --api-mode assistant --base-url http://127.0.0.1:18000
   .venv/bin/python rag-acceptance/run_golden.py --api-mode assistant --base-url http://127.0.0.1:18000 --cases rag-acceptance/quality-cases.jsonl
   ```

3. 对照使用 `.venv/bin/python rag-acceptance/compare_golden.py before.json after.json --cases rag-acceptance/quality-cases.jsonl --output rag-acceptance/reports/comparison.json`。必须是同知识库、同题、带完整 `response` 的两份 Assistant 报告；模型、索引和参数是否一致另核查 Trace。
4. 在“回答优化”页面继续人工标记问题、复测和查看文档；详细追问诊断查看 `rerank_result.query_rewrite`，实际生成依据查看 `runtime_snapshot.generation`。不把准备阶段的 `selected_context` 当成最终生成上下文。
5. 本地验证开关时分别以 `M3_RAG_FACT_QA_MODE=shadow/enabled` 启动独立后端进程，运行 `fact-qa-cases.jsonl`。生产默认保持观察；样本尚不足以建议全量开启。

`bash scripts/check.sh` 负责完整工作区门禁，使用 fake 模型；真实模型 Golden 与门禁分开运行，避免测试临时配置影响实验。规则评测不等于完整语义评审，表格和图片只验证本次指定的结构与数量，不验证所有图片视觉内容或页面排版。

## 后续建议

- 优先完成 FFC600 替代源文档和 DC24V 浮动正文的人工核验、候选索引构建与激活验收；先解决内容可用性。
- 继续从管理台“回答优化”积累少量有代表性的真实失败，分别确认是文档缺失、召回错误、追问对象丢失还是生成误推，再扩充固定用例。
- 暂不增加在线 LLM 仲裁或复杂审核链；不因单个事实题未直接命中就放宽全局选文档阈值。部署前在目标环境按相同断言执行回归。
