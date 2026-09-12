# RAG 解释能力与 Assistant 契约优化

日期：2026-09-12。本地工作区实现与真实 Assistant 接口验证；未部署生产。

## 实现

- 运行时 DOC_QA / CHUNK_QA / ROUTING 提示词升级至版本 3。允许组合文档事实、条件分析、计算、原理解释及明确标注的待验证假设；用户报告作为条件，历史助手回答不能独立作为事实依据。支持部分回答后说明缺口并补充通用知识。
- 文档事实与推断/通用知识混合用已有 PRODUCT_EXPLAINED；纯通用回答用 PRODUCT_GENERAL。通用建议放在末尾，不借用文档图片/引用；混合回答通过现有 disclaimer 标明依据范围。不能由报警暂时消除推出维修成功或无需更换。
- 首轮明确解释题在可靠选中文档后复用 DOC_QA；全文、完整操作流程、待确认交互保持原路径。M3_RAG_EXPLANATION_MODE 默认 enabled，可设 shadow/off 回退首轮原文交付；M3_RAG_FACT_QA_MODE 仍为 shadow。没有降低选文档门槛，焦点保存复用原乐观版本检查。
- 追问改写新增“如果/假如/文档说”等承接信号，解决“文档说打开 Windows 运行功能”被误当成新文档查询的问题。后续复验发现仅改写仍有路由波动，补充 FOCUS_EXCERPT：无新对象的明确引述必须逐字命中同库、冻结索引内仍有效的焦点文档正文，才进入 DOC_QA。原重排分数保留，确认交互、输入门禁和重排降级不可被覆盖；不强制将任意问题锁在原文档。
- 模型增量和最终文本隐藏 MODE / IMG / S 来源标记及控制字符，普通 Markdown 保留；图片通过已有结构化块交付。修复图片后标点替换写入控制字符的真实缺陷。
- RAG 同步/流式生成超时、空答案以及上游未正常结束或 length 截断均按失败收口。失败保留清理后的部分正文和可重试属性，不再把截断结果保存为 complete。流式终态、历史和幂等重放继续使用同一公开快照。

## 接口与业务侧影响

对照改动前后的 OpenAPI：7 条 Assistant 业务路径、17 个相关结构定义完全一致；就绪探针沿用原接口。没有新增接口、请求字段、响应类型或迁移。

真实 HTTP 契约检查通过 8 组场景：就绪/上线库/三条欢迎问题；凭据隔离和参数校验；SSE 顺序、头及关联 ID；完整文档、图片资源归属；历史快照和分页；跨用户隔离；解释回答无图片、免责声明、幂等及并发；主动断连后 CANCELLED 终态和原 Key 错误重放。

调用方按既有契约处理：delta 仅临时累计，complete 替换最终内容；普通 RAG 的 content 与 answer 是同一回答的富文本/纯文本表示，避免重复追加；文档交付需先标题后正文。展示 disclaimer 和来源范围，error 不当作成功。旧历史/幂等快照不重新生成。

验证脚本与报告：`check_assistant_contract.py`、`reports/assistant-contract-release.json`。脚本使用独立测试用户创建新会话，不改现有源文档和活动索引。外部业务后台/移动端源码未提供，因此此结果证明服务侧 HTTP 契约行为，不代表已在其实际页面或生产网络完成联调。

## 实验口径

同一 VDR600 知识库、冻结索引 v39、相同数据库模型配置，通过真实 Assistant SSE 接口比较。解释集 11 例/18 轮，正文质量集 20 例/30 轮，主 Golden 53 例/63 轮。断言包含正文事实、引用范围、图片、计算结果、无依据结论等；小样本不能证明普遍正确或统计显著性。

第一次解释基线 3/11 中有两个上游 Embedding 超时，不用这个数字归因质量提升；旧版本复跑 6/11。中间候选 11/11，但质量回归发现一次反向换件结论（19/20），据此修正提示词示例：只说观察到的改善，不宣称更换条件未触发。

主 Golden 的旧 rag_fup_001 禁止任何文档引用，与已接受的混合回答方案不一致。已保留“升级失败处理未覆盖”的声明要求，允许引用当前 PDC 升级文档，新增事实关键词和 disclaimer 要求，禁止其他文档混入。对照同时重评前后完整响应，不只给新结果换断言。同时修复评测将“某文档未涉及”标题提及算成实际引用的问题：NONE 只检查公开 references/sources；文档选择题 rag_xdoc_005 可从既有 next_suggestions 检查可选升级文档，仍须有符合预期的候选项。对应单元测试包含误挂引用、不相关候选及无正文依据的负例。rag_retrieval_001 另接受“没有与压力释放器直接相关的文档”这一等义缺资料表述，仍限定具体对象。没有恢复用户删除的 FFC600 文档或删除其失败用例。

## 最终结果（逐项记录实际完成验证）

- 解释集：旧版本复跑 6/11 → 最终 11/11；首轮 0/4 → 4/4，追问 6/7 → 7/7，无新增失败。最终 p50 4.710s、p95 10.127s、max 11.439s。相同断言对照：`reports/reasoning-comparison.json`。
- 正文质量：本轮前后均 20/20，无回归。最终 p50 3.595s、p95 8.388s、max 28.735s，存在上游/生成长尾，不能宣称整体加速。对照：`reports/reasoning-quality-comparison.json`。
- Windows 引述追问及报警消除的推断边界各重复 3 次，共 6/6；报告 `reports/reasoning-boundaries/baseline-20260912-012310.json`。
- 全量主 Golden：同一最终断言重评前后均为 52/53，唯一失败 rag_idf_003 依赖用户已删除的 FFC600 文档。最后一次真实 HTTP 全集 53 例/63 轮已执行，原始报告为 51/53，其中一例仅因上述等义缺资料表述不在旧词表而误判；保留原始报告，最终评分另见 `reports/reasoning-golden-comparison.json`，没有覆盖原始结果。最终 p50 3.363s、p95 8.717s、max 13.651s。
- Assistant 真实 HTTP 契约 8 组全部通过：`reports/assistant-contract-release.json`。包括主动取消后的原 Key 错误重放，以及新 Key 可继续同一会话；disclaimer 按契约为可选，有值时与历史/重放一致。
- 最终 `scripts/check.sh` 退出码 0：Ruff lint/format（236 文件）、Mypy（123 源文件）、后端 954 项测试、摄取/RAG/管理三项 fake 冒烟、累计覆盖率 82% 全通过；前端 22 文件 151 项测试、目录覆盖率门槛、lint/type-check/build 全通过，仅保留已有大包警告。队列隔离至确认空闲的 Redis DB 6，未清空已有库；日志 `/tmp/shipdemo-reasoning-gate.log`。
- 只读核对 141 个完成 Trace：索引 v39、有效检索参数及模型一致；55 个实际生成提示词哈希正确，4 次 FOCUS_EXCERPT 的焦点/实际来源一致。契约测试用户 3 个 COMPLETED、1 个 CANCELLED、0 个 RUNNING。活动索引仍为 `7b23aee9-f828-4ee6-b282-7de05bf0d283`，498 块；当前有效源文档 42 份。报告 `reports/reasoning-trace-verification.json`。
- OpenAPI 再次对照确认 7 条 Assistant 业务路径及 17 个相关结构定义均未改变。临时服务与测试凭据在验收后清理；测试会话保留用于复查，按原保留策略清理。

## 修改文件与生效方式

- `backend/app/rag/prompt.py`：三类运行时提示词；`backend/app/services/rag.py`：解释/引述路由、焦点关联与生成终态；`backend/app/rag/query_rewrite.py`：追问信号和原文引述识别。
- `backend/app/rag/presentation.py`（新增）、`routing.py`：增量/最终正文清理、图片标点、混合引用与依据说明；`llm.py`：检查上游结束事件。
- `backend/app/core/config.py`、`.env.example`、`.env.production.example`、`compose.production.yaml`：独立解释开关。
- `backend/tests/test_rag_reasoning.py`（新增）、`test_chat_prompt.py`、`test_chat_doc_routing_flow.py`、`test_query_rewrite.py`、`test_golden_runner.py`：相关边界和契约测试。
- `rag-acceptance/reasoning-cases.jsonl`（新增）、`check_assistant_contract.py`（新增）、`cases.jsonl`、`quality-cases.jsonl`、`run_golden.py`、`compare_golden.py`、`backend/app/rag/evaluation.py`：真实质量/接口验收及评测误判修正。
- 两份 Assistant 对接指南、`rag-acceptance/README.md`、本文，以及 `docs/ai/backend.md`、`environment.md`、`current-state.md`、`known-issues.md`、`decisions.md` 更新行为、验证与 ADR-0015。

本轮沿用前序长文档/检索预算等未提交改动，没有撤销用户已有改动。后端重启后运行时模板及默认解释策略生效；生产须按原部署流程重建后端镜像。无需新增接口或修改业务请求体。回退首轮解释交付策略可设 `M3_RAG_EXPLANATION_MODE=shadow` 或 `off` 后重启；这不会撤销正文清理和错误收口修复，也不重算历史回答。
