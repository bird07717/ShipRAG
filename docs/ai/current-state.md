# 当前项目状态

> 本文记录当前有效状态，不是任务流水账。本地 RAG 路由与真实模型验证更新至 2026-09-24；生产端未部署本轮改动。

## Dashboard 回答结果指标收口（2026-09-20，本地已实施，未部署）

- 原“无答案率”把正常 `OUT_OF_SCOPE` 拒答和 `UNCONFIRMED` 相加，且概览、趋势、下钻的分母、时间与来源不一致；Dashboard 还展示冻结 Support 的文档交付、澄清、升级和 Profile 待办。现改为直接回答率、待补充信息率、范围外请求率和独立失败率，外部拒答不再记作负向无答案；`UNCONFIRMED` 明确只表示需要更多条件或低信息输入，不自动归因为知识库缺口。
- 回答结果卡片、趋势、待补充列表和柱形下钻统一过滤 `source=ASSISTANT_API`、`engine=RAG` 和同一个近 7 天窗口；列表额外要求 `COMPLETED/UNCONFIRMED`。管理指标 overview/timeseries 新增可选 `engine`、`source` 过滤及三项新比例字段，移除旧 `no_answer_rate`；前后端类型、Schema、API 测试和运营手册同步。Assistant TOP Trace/HTTP 错误同时收窄到外部 Assistant 路径，避免混入内部 Chat。
- Dashboard 新增回答依据分布：独立聚合 `PRODUCT_KNOWLEDGE`、`PRODUCT_EXPLAINED`、`PRODUCT_GENERAL`，分母只取这三类已分类回答。RAG V2 的既有内部 Trace 新增三类确定性 `diagnostic_reason`：`LOW_INFORMATION`、`SCOPE_CONFLICT`、`NEEDS_KEY_CONDITION`，管理 Trace 显示中文含义和原始输入门禁码。同步/流式、管理 Chat/Assistant 共用 `_generate_v2()` 的完成持久化入口，因此记录口径一致。
- 本轮观测增强没有修改范围判定、Prompt、检索、生成、`response_type`、回答正文或公开 Assistant/SSE 契约，没有新增数据库迁移和环境变量，也不回填历史 Trace。三类原因不能覆盖证据不足和检索误判；精确知识库缺口仍需人工结合候选与文档判断。
- 完整门禁 `bash scripts/check.sh` 退出码 0：Ruff/格式、Mypy（139 个源文件）、后端 836 项、摄取/RAG V2/管理三项 fake smoke、累计覆盖率 81%、前端 23 个文件 176 项、覆盖率 91.42%、ESLint、类型检查和生产构建全部通过，仅有既有 Vite 大包警告。本轮未运行 Golden Set、真实模型、浏览器人工验收或生产部署。

## RAG 产品内扩展与双模型范围一致性（2026-09-18，本地已实施，未部署）

- 生产 SC200 Trace 只读复核确认：首轮“SC200是什么”已被 LLM 1 判为 `IN_SCOPE`，检索也取得同库 FU 标定文档，但 LLM 2 再次输出 `OUT_OF_SCOPE`；用户纠正后 LLM 2 改用 `GENERAL`，却给出当前产品档案和文档均未支持的厂商/正式产品类别。根因是范围所有权重叠，以及“无直接文档时的产品内通用扩展”缺少稳定契约，不是单纯检索失败。
- Query Interpreter 现在明确把知识库名称和公开说明视为可信产品档案，并将产品身份、用途、组成、原理、使用、维护和故障纳入产品范围；把产品名用于无关创作/闲聊仍不算产品问题。Answer Generator 使用同一档案作为身份锚点，文档优先但不再是回答许可证；无直接文档时优先给专业通用分析，型号专有精确事实无档案/文档依据时不猜值，而是说明核验位置并继续给可执行步骤。
- LLM 1 成为范围决定的唯一来源：`IN_SCOPE` 下 LLM 2 的范围外结果确定性转为一次产品内澄清，`UNCERTAIN` 的范围外结果转为关键条件澄清，`OUT_OF_SCOPE` 则覆盖 LLM 2 的误回答。该收口不发起第三次模型调用；Trace 新增 `generation.answer.scope_authority` 记录上游范围、模型原始 kind、最终 kind 与是否解决冲突。外部 Assistant/SSE 字段、数据库、环境变量、检索参数和 Support 均未改变。
- 自动验证：`scripts/check.sh` 退出码 0；Ruff/格式、Mypy（139 个源文件）、后端 832 项、摄取/RAG V2/管理三项 fake smoke、累计覆盖率 81%、前端 23 个文件 176 项、覆盖率 91.36%、ESLint、类型检查和生产构建全部通过，仅保留既有 Vite 大包警告。新增定向用例覆盖范围内零文档的 `GENERAL` 回答、两阶段范围冲突、范围外强制收口、无第三次调用、产品档案 Prompt 和既有历史/图片/证据边界。首次完整门禁因本地 PostgreSQL 未启动在 smoke 阶段失败，启动本地基础设施后原命令完整通过；该环境问题不计为代码通过。
- 真实模型抽样 Golden：2026-09-18 在本地 VDR600 活动索引运行 `technical-assistant-cases` 中 10 个代表案例、14 轮，自动通过 9/10（90%），p50 12.374 秒、p95 21.282 秒、最大 22.588 秒；完整操作、3 个领域扩展、2 个连续排障、2 个范围边界和条件补充均按现有机器断言通过。唯一机器失败为“更换 PSU → 操作时使用哪个端口”：LLM 1 在 15 秒超时后降级为 `UNCERTAIN/NEW_TASK`，未继承任务或上一轮证据，LLM 2 因而返回澄清；不是本次 scope 冲突归一化触发。人工复核还发现混合请求“VAU 没图像，顺便写诗”虽通过现有关键词断言，实际同时输出了诗歌，不符合用例 `manual_review`；若干回答偏长，Trace 另观测到少量无正文来源的电压/端口表达。因此 9/10 只代表自动断言结果，不等同于 9/10 人工质量通过。报告位于本地忽略目录 `rag-acceptance/reports/baseline-20260918-161040.json`。尚未运行完整 Golden Set、SC200 身份题或部署生产；SC200 的正式产品身份仍需由业务方核验后写入知识库说明，代码不会猜测该事实。
- 针对上述抽样暴露点已完成稳定性收口：LLM 1 单次时限由 15 秒增至 25 秒；即时 `UPSTREAM_UNAVAILABLE` 最多快速重试一次，完整 `UPSTREAM_TIMEOUT` 不重试；解释器失败时仅明确省略对象的短追问继承最近完整成功轮次的用户任务及已用文档软候选。LLM 1/LLM 2 同时约束混合请求只处理产品技术部分，未加入具体诗句或案例特判。Trace 增加超时、尝试次数、历史兜底采用/拒绝原因和兜底检索结果。
- 同一组 10 案例/14 轮真实 Assistant 复跑为 10/10（100%），p50 10.113 秒、p95 13.885 秒、最大 15.532 秒，报告为 `rag-acceptance/reports/baseline-20260918-171603.json`。原 PSU 短追问正常返回 `FOLLOW_UP`，LLM 1 本轮 2.106 秒成功、上一轮两份实际证据进入软候选；因此真实复跑验证了提高时限后的正常路径，未直接触发超时兜底，后者由自动化故障注入验证。混合 VAU/写诗案例经人工复核只回答技术排查，没有诗歌。机器全绿仍不代表答案事实逐项审定：PSU 追问虽然包含断言要求的 `3359`，但回答扩展到多个电源端口并出现“文档 D1/D3”措辞；PDC 2015 年追问仍重复询问年份。这些属于现有 Golden 断言覆盖和生成精度边界，未在本轮继续增加特判。

## 索引构建重试与中止改为人工决策（2026-09-17，本地已实施，未部署）

- 按 ADR-0024 移除自动 followup：构建失败、worker 崩溃回收与协作取消均不再自动排重建任务，`create_followup_build_if_needed` 已删除；失败/取消时 `rebuild_required` 重新置位，由索引管理页"文档有变更待重建"徽标提示，重试经"重建"按钮（`POST /indexes/{id}/retry`，白名单扩至 CANCELLED）由管理员触发，ADR-0022 熔断计数由手动重试推进（第 3 次构建自动跳过坏文档）。
- INDEX_BUILD 接入协作取消（`POST /tasks/{id}/cancel` 白名单扩容）：取消是任务行终态转换，构建循环文档边界轮询生效，`_update_task` 带守卫防止进度心跳覆盖 CANCELLED；排队期取消在管线入口短路（零模型消耗）；索引与未处理文档落 `CANCELLED` 终态，与 FAILED 同权可删可重试、不进失败计数。迁移 `0047` 为 `knowledge_index.status` 增加 CANCELLED，并补上 ADR-0022 遗漏的 `index_document` `SKIPPED` 约束值（此前该路径一旦触发即撞 CHECK 约束崩溃）。
- 删除升级为前缀整清：单索引删除与 GC 在标记 DELETING 后按 `indexes/{index_id}/` 前缀扫删全部 MinIO 图片对象（含无记录孤儿，v43 类残留根治）再删数据库行；文档原件与 document_source 不受影响。
- 任务记录保留策略：`task_record_retention_days=90`（0=永久）由周期 GC 修剪终态超期记录，每库保底最新 10 条，活动记录不动。
- 验证：后端 819 项测试全过（新增 `tests/test_index_build_cancel.py` 11 项：取消短路/边界停止/失败置位/CANCELLED 重试/保留修剪，含真实 PostgreSQL 集成 5 项）；Ruff lint/format、Mypy 139 源文件通过；迁移 `0047` 已在本地数据库应用；前端 174 项（新增 4 项：停止/重建/删除按钮与徽标）、ESLint、类型检查通过。未执行生产部署；生产更新须 Alembic 升级至 `0047 (head)` 并重建后端、前端与索引/维护 Worker。
- 部署注意：移除自动 followup 后，`rebuild_required=true` 的库不会再自愈，上线后以徽标提示管理员手动重建；用户 WIP（RAG V2 会话历史相关）未合入本次提交，部署前需先处理。

## Assistant 上线要求 RAG 文档理解全部确认（2026-09-17，本地已实施，未部署）

- Assistant 的统一运行门禁新增活动索引 RAG 文档理解审核：每份 `index_document.status='READY'` 且源文档为 `STORED` 的文档，都必须存在当前卡片结构版本、`generation_status='READY'`、`review_status='CONFIRMED'` 的卡片；无可确认文档、缺失、失败、过期、拒绝或 `AUTO` 待确认均阻止上线。
- 门禁只接入 Assistant 的管理发布、公开知识库 `ready`、欢迎语和问答入口，不改变通用 Gateway 引擎策略就绪态；管理员仍可在独立 DemoChat 中生成、检查未上线库的回答。已上线库切换到未确认的新活动索引，或显式重生成已确认卡片后，会保留上线标志但立即变为 `ready=false`，欢迎语/问答返回 409，全部重新确认后自动恢复。
- 外部 Assistant 路径、请求字段和响应结构不变；知识库列表中 `ready` 的语义扩展为包含平台侧内容理解审核。没有新增环境变量或数据库迁移，部署前数据库仍须升级到现有 `0046 (head)`。
- 验证：`scripts/check.sh` 退出码 0；Ruff、格式、Mypy（139 个源文件）、后端 809 项、摄取/RAG V2/管理三项 fake smoke、累计覆盖率 81%、前端 23 个文件 170 项、覆盖率 91.45%、ESLint、类型检查和生产构建全部通过，仅保留既有 Vite 大包警告。定向真实 PostgreSQL 回归覆盖卡片“缺失→AUTO→全部 CONFIRMED”及公开门禁，33 项通过；本地 VDR600 统一 Assistant 门禁实测为 ready。本轮未运行 Golden Set、真实模型或生产部署。

## 千问扩展为全模型类型 Provider（2026-09-15，本地已实施，未部署）

- 管理台模型配置中，千问（阿里云百炼 / DashScope）不再仅限 LLM：`model_providers.py` 的能力注册表为 EMBEDDING、RERANK、OCR、VISION 增加了 `dashscope`，前端 Provider 下拉由后端 `supported_providers` 驱动，自动跟随，无需前端代码改动。
- Embedding、OCR、Vision 复用既有 OpenAI 兼容实现（`/embeddings` 与多模态 `chat/completions`，qwen-vl 默认不思考，不发送 thinking 控制）；Rerank 新增 `DashScopeRerankProvider`（`app/rag/rerank.py`），请求 DashScope 原生 `/api/v1/services/rerank/text-rerank/text-rerank` 端点，payload 为嵌套 `input`/`parameters` 结构并解析 `output.results`；`dashscope_rerank_base_url()` 把管理台保存的 compatible-mode 地址映射到 `/api/v1` 根（业务空间/国际站域名仅替换路径前缀）。
- `model_probe.py` 的连通性探测对 DashScope Rerank 使用同一原生端点与嵌套 payload；`M2_EMBEDDING_PROVIDER`、`M4_OCR_PROVIDER`、`M4_VISION_PROVIDER`、`M5_RERANK_PROVIDER` 的 Literal 均新增 `dashscope` 显式值（默认仍 `auto`，现有部署不受影响）。
- 验证：后端定向测试 `tests/test_model_secrets.py`（含 DashScope rerank 原生端点/payload/解析/URL 映射、embedding/vision 兼容端点、probe 三类新用例）、`tests/test_build_model_config.py`、`tests/test_m4_multimodal.py`、`tests/test_m5_retrieval.py`、`tests/test_config.py`、`tests/test_m3_rag.py`、`tests/test_api.py`、`tests/test_admin_config.py`、`tests/test_rag_v2_pipeline.py`、`tests/test_m2_ingestion.py` 全部通过；Ruff lint/format 与 Mypy 定向检查通过；前端 `tests/viewInteractions.spec.ts` 19 项（含新增"非 LLM 模型也提供千问选项"用例）、`npm run lint`、`npm run type-check` 通过。未执行真实 DashScope 嵌入/重排/OCR/Vision 端到端调用（协议按官方文档与公开实现交叉核对：兼容模式 `dimensions` 参数、原生 rerank 嵌套结构），未运行完整 `scripts/check.sh`，无数据库迁移。
- 待确认：真实百炼账号下 gte-rerank-v2 与 text-embedding-v4 的实测连通性和批量/速率限制；生产部署后建议先用"测试连接"探测再启用。

## 并发加固：RUNNING 自动恢复、轮次准入闸门与运行指标（2026-09-15，本地已实施，未部署）

- 崩溃 API 进程遗留的 `RUNNING` Assistant 请求不再只靠手动命令：`app/tasks/assistant_recovery.py` 让维护 Worker 启动时及每 15 分钟将超过 `ASSISTANT_REQUEST_STALE_SECONDS`（默认 600，校验器强制大于轮次时限——Support 优先已冻结、RAG 轮次被时限包络，见 ADR-0021；0 禁用）的 RUNNING 请求及关联消息/Trace 用与 `app.assistant.recovery` 相同的 `FOR UPDATE SKIP LOCKED` 单语句收口为失败；Redis 哨兵键防多 Worker 重复注册周期链，`with_scheduler` 条件已包含该链。恢复任务与维护队列共享串行 Worker，长维护任务可能推迟恢复（见 known-issues）。
- 新增每进程问答轮次准入闸门 `app/common/turn_gate.py`（`CHAT_TURN_CONCURRENCY_LIMIT` 默认 64、`CHAT_TURN_WAIT_SECONDS` 默认 2.0，0 禁用）：管理 `/chat`、`/chat/stream` 与对外 Assistant 流在预留/生成前获取按事件循环隔离的信号量，超限等待后快速失败 503 `SERVICE_BUSY`；Assistant 路径在预留幂等请求前判定（容量拒绝不烧幂等键），信号量随流收口释放；管理端复测不走闸门。生产 Compose 支持可选 `BACKEND_LIMIT_CONCURRENCY`（uvicorn 每进程连接上限，不设置不限）。
- 监控补齐：`/metrics/assistant` 新增 `concurrency` 段（RUNNING 数量/最老年龄/超陈旧窗口计数 + `CONVERSATION_BUSY`/`REQUEST_IN_PROGRESS`/`SERVICE_BUSY` 拒绝计数）；新增管理端 `/metrics/runtime` 返回本进程闸门占用与数据库连接池快照（多 Worker 需按实例抓取）。两份对外集成指南错误表已补 `SERVICE_BUSY`。
- 验证：Ruff lint/format、Mypy（143 源文件）、后端 1181 项测试（新增 turn gate 8 项、恢复任务 9 项含真实 PostgreSQL 集成 1 项、门面闸门 3 项、metrics 4 项、配置校验 3 项、管理端闸门行为 1 项）、摄取/RAG/管理三项 fake 冒烟及累计覆盖率 83%（门槛 80%）全部通过；RAG 冒烟含 Assistant 流在默认闸门下正常完成；新 metrics SQL（`make_interval(secs => ...)`、RUNNING/忙拒绝查询）已在本地真实 PostgreSQL 执行验证。未执行真实模型、并发压测、生产部署或数据库迁移（无新迁移）。
- 明确未修：`begin_turn` 每轮 FOR UPDATE 锁知识库行、同幂等键并发竞态语义、Assistant 列表 N+1 就绪检查——均已记录到 known-issues 并给出触发条件。

## PDF 摄取与首行标题删除退役（2026-09-15，feat/PDF 分支本地已实施）

- 已按 ADR-0020 新增 PDF 直接解析：`ingestion/parser_pdf.py`（pdfplumber 0.11.10，MIT）产出与 DOCX 相同的 TEXT/IMAGE 元素契约，页眉/页脚（跨页重复文本/页码/Logo 图片）在输出前剔除，区域内一次性内容保留；图片解码 DCT/Flate(SMask)/JPX/**ICCBased（按 Alternate/N 解析，真实样本驱动）**，失败即中止构建；加密 PDF 上传即拒。上传按扩展名分发校验/对象键/MIME，DOCX 路径不变；`content_revision` 按格式分派（docx=`ooxml-vml-1`，pdf=`pdf-1`），REUSE 判定双路径（构建计划期 + 执行期）均按格式比较，不触发既有 DOCX 重解析。
- ADR-0011 已废弃：`strip_leading_document_heading` 及 4 处调用（RAG 文档 Prompt、路由内容块、Chunk 标题去重、Support 交付）全部移除，交付按元素原文逐块输出；`_generation_candidates` 仅保留候选文档显示名归一化。`index_document.metadata` 的 `mammoth_warnings` 键泛化为 `parser_warnings`。前端上传入口支持 `.docx/.pdf`（accept、文案、按文件名显示 PDF/DOCX 徽标；DemoChat 来源图标改为中性 DOC）。
- 完整门禁 `scripts/check.sh` 退出码 0：Ruff lint/format、Mypy（141 个源文件）、后端 1155 项测试（含 `test_pdf_ingestion.py` 20 项 + REUSE 按格式用例）、摄取/RAG/管理三项 fake 冒烟及累计覆盖率 83% 通过；前端 22 个文件 161 项测试、lint、类型检查、覆盖率和生产构建通过，仅既有大包警告。数据库无迁移。
- **真实样本验收已通过（2026-09-15，临时知识库隔离执行）**：VDR600 两份真实 PDF（ECDIS600 英文 5 页 9 图 / 中文 2 页 3 图）在仅含该两份文档的临时库完成真实模型全链路构建——INDEX_BUILD、DOCUMENT_PROFILE_GENERATE、RAG_DOCUMENT_CARD_GENERATE 三任务全部 SUCCEEDED；元素数与只读干跑完全一致（19/7 元素、16/6 分块）、阅读顺序与图文交错正确、`parser=pdfplumber-pdf`/`content_revision=pdf-1`、OCR 12/12 READY、卡片 2/2 READY（AUTO/PROCEDURE）；临时库已通过应用删除生命周期完整清理（含 MinIO 对象）。验收中发现智谱 Vision 瞬时失败 6/12（同尺寸图片跨文档成功可证为服务端抖动，按设计降级为 OCR 内容不阻塞构建）；两份 PDF 文档记录保留在 VDR600（STORED），等待遗留 DOCX 处置后随全库构建一起入库。
- **VDR600 全库构建阻塞已定位为单文档（预先存在，与 PDF 改造无关；2026-09-15 二次核查更正）**：v40/v41/v42 三次构建失败点均为 `故障解决文本46—DC24V` 一份（浮动版式，`legacy_text.py` 拒绝）；此前"18 份被阻断"系误读批量失败标记（`_mark_failed` 将未轮到处理的文档统一标错，16 份结构正常的文档被连坐，已逐份结构验证+单独解析排除）。活动索引 v39 早于 content_revision 体系，任何全库构建必然全部 REBUILD（约 11 分钟真实模型成本）。本次误触发产生的 v42/v43 构建产物、DRAFT release 及孤儿 MinIO 对象已全部按应用路径清理（活动索引 v39 未受影响）；处置该单份文档（转 PDF/修复/删除）前不应触发 VDR600 构建。

## 开发阶段

项目已有带 `v1.0.1` Git 标签的前后端与生产 Compose 交付形态，并已实现管理 API、对外 Assistant API、RAG/Support 双引擎、文档摄取、Profile 发布和验收脚本。生产镜像默认标签已统一为 `1.0.1`。

## RAG 隐藏全文双 LLM V2（2026-09-15，V0～V5 本地已实施）

- 已批准 [RAG 隐藏全文双 LLM V2 执行计划](../technical/rag-full-document-generation-v2-execution-plan.md) 和 ADR-0019。V2 每轮由 LLM 1 根据最近完整问答还原问题并辅助检索，Chunk/卡片聚合到文档后加载前 1～3 份完整有序元素作为隐藏上下文，LLM 2 统一生成答案和选择图片。
- V2 淘汰 RAG 的首轮专用命中、单强/事实/解释分支、文档选择交互、公开原文交付、硬焦点停留与计划中的 `rag_case`。RAG 每轮重新检索；只保留普通完整轮次历史，不按 `focus_epoch` 过滤。
- V5 已将 V2 设为唯一 RAG 运行路径，删除 `M3_RAG_PIPELINE_MODE` 与旧首轮、焦点、Query Rewrite、Query/Response Planner、六策略和 RAG 原文交付代码；开发与 Production Compose 不再存在环境模式切换，回滚只能部署旧 Git 提交或旧镜像。
- V1 已新增 `app/rag/v2/`：LLM 1 输出最小检索解释，原问题与补充查询做 Chunk/卡片文档级检索，前 1～3 份同库冻结索引文档的有序元素作为隐藏上下文交给 LLM 2，后端只公开最终答案和白名单图片。旧查询改写、首轮标题召回及原 P2/P3 Planner 在有效 V2 路径不执行。
- V2 已新增非焦点完整轮次历史：只使用 `message.rag_user_message_id` 或 Assistant 请求留下的显式关联，且用户消息、助手消息和外部请求均须成功完成；失败、取消、运行中、无法配对和当前复用轮次不进入 Prompt。最近历史按完整轮次、消息数和 Token 预算裁剪，模型输入不携带消息 ID。
- P1 已有的 42 份 VDR600 `READY/CONFIRMED` 文档卡片、冻结索引、混合检索、Rerank、完整元素/图片读取继续复用。原 P2 多查询和文档聚合按 V2 重构；原 P3 六策略和 P4 不再继续。
- `conversation.focus_*`、`chat_context`、`support_context_version` 和 `message.focus_epoch` 仍服务 Support/Gateway。V2 `begin_turn` 不读取这些值作为 RAG 输入，返回空的兼容状态且新 V2 消息的 `focus_epoch=0`；数据库原值不清空、不改写。删列仍须等待 Support 独立迁移和至少两个发布周期零旧列读取。
- V2 的 Assistant 正常结果为 `ANSWERED`，对外不展示完整原文、候选文档列表或文档总数，当前实现令 `references=[]`，只返回最终答案和白名单图片；必要的标题、文件名或产品内部编号可以作为技术正文出现。`include_images=false` 在持久化前过滤，Assistant 首次完成、历史和幂等重放使用同一终态快照。
- V3 已修正管理试问仍走 legacy 流式生成的偏差；管理试问与 Assistant 在 V2 下共用同一生成终态。V4/V4.1 将最终文本检查收窄为 Prompt 标记、内部服务主机、无证据相对管理路径和对象存储字段，避免标题、filename、技术编号、产品私网 URL 与普通产品 API 误伤大部分回答；Assistant 公开白名单仍移除模型用量、候选分数、检索/重排诊断和 MinIO 位置。
- V4.1 在多查询 Chunk/卡片 RRF 后复用现有 Rerank Provider 做文档级重排，再取前 3 份全文；排序异常保持 RRF 原顺序。IP、版本、带单位数值和端口只在 Trace 标注 `DOCUMENT/USER_PROVIDED/UNSUPPORTED`，不拦截、修复或改变公开答案。现有 Chunk 大小、向量/BM25 Top-K、全文文档上限 3 和 20,000 Token 上下文预算未调整。
- V4.2 让 LLM 1 显式区分新任务、追问和纠正，并从最近完整轮次的用户请求提取当前任务；追问任务锚点同时进入召回、文档级 Rerank 和 LLM 2，换题不携带旧锚点。有效 V2 的 `prepare()` 只冻结轮次和参数，不再运行 legacy Embedding/BM25/向量检索或混入其候选；本轮所有去重查询只批量 Embedding 一次。
- 2026-09-16 完成短追问收口：LLM 1 历史输入去除“消息窗口 + 上一轮”重复，只保留按轮次组织的成功历史，并强化“当前问题不能独立确定对象/任务时继承最近用户请求”的通用规则；`FOLLOW_UP/CORRECTION` 可把最近成功回答实际采用的同库、同冻结索引、`STORED/READY` 文档作为零初始分软候选加入本轮文档级 Rerank，`NEW_TASK` 不继承，未恢复焦点或新增模型调用。LLM 2 的内容规则收敛为七条优先级策略，继续允许产品范围内通用工程分析，图片保持按需软选择。真实 Assistant 连续对话“检查 LRU 通讯线缆 → 断电后要等待多久？”将追问判为 `FOLLOW_UP`，软候选进入最终第 3 位并短答约 5 分钟（总耗时 7.402 秒）；同会话明确切换 VAU 判为 `NEW_TASK`，`previous_evidence.applied=false`（总耗时 8.263 秒）。当前仍观察到短答可能采用多份同结论文档并略作产品级泛化，因此只增加“不把任务条件泛化为全产品规则”的提示，不增加硬阈值、第三次模型调用或问题特判。最终 `scripts/check.sh` 退出码 0：Ruff/格式/Mypy、后端 806 项、三项 fake smoke、累计覆盖率 81%、前端 23 个文件 169 项、覆盖率 91.43% 和生产构建全部通过，仅保留既有 Vite 大包警告；未新增配置/迁移、未重建索引、未部署生产。
- 上述短追问收口后的同口径真实 Assistant Golden 已完整执行：48/50（96%，50 题/72 轮），完整操作 8/8、条件边界 4/4、连续排障 8/8、领域扩展 10/10、范围边界 10/10、局部回答 8/10；p50 10.385 秒、p95 14.974 秒、最大 15.983 秒，较 2026-09-15 的 49/50 基线延迟下降。两项失败均有文档证据：FFC 题命中正确升级文档，但模型将歧义的“网络 IP”只回答成电脑端 `172.16.8.175`，漏掉设备 Telnet 地址 `172.16.8.100`；PSU 的“操作时使用哪个端口？”将上一轮 3359 文档加入 Rerank，但该文档排第 4、被前三份全文截断，模型转而解释 PSU 接线端口。前者应优先把 Golden 问法明确为设备/Telnet 地址或要求区分两类地址，后者属于真实的模糊追问证据保留边界；均不应添加标题、编号或固定问法特判。报告为 `rag-acceptance/reports/baseline-20260916-142103.json`。
- V2 准备 Trace 的上下文只保留 Chunk/文档/元素/图片 ID 和 SHA-256，生成 Trace 另记隐藏全文上下文与公开答案 SHA-256；不保存 V2 完整 Prompt。全文和图片继续按知识库与冻结索引加载，图片外部读取已有跨库 404 PostgreSQL 用例。
- V2 图片公开组装已统一“完整句子及句末标点在前、关联图片在后”：LLM 2 Prompt 明确要求标点写在 `[IMG:...]` 前，若模型仍输出 `文字[IMG:I1]。` 或连续多图后置标点，后端会在生成公开 `content` 前确定性调整为 `文字。` 后接原序图片，不增加模型调用、不改图片白名单和 Assistant 字段。
- V2 已撤销 LLM 1 的 `SHORT/FOCUSED/COMPLETE` 回答深度分类：LLM 1 只负责问题还原、任务继承和检索辅助，LLM 2 结合当前问题、历史及完整候选文档自行生成“最小充分回答”。单一事实短答，普通操作即使未显式说“完整”也应覆盖完成任务所需步骤，局部追问不重复整套流程，排查和解释按其真实信息需求组织。LLM 1 始终默认关闭思考；LLM 2 及一次合规修复的有界思考能力保留，但上线默认关闭。开启实验使用 DashScope/Qwen 2048 Token 独立思考预算，推理正文不进入答案或 Trace，思考模式下不强制 JSON Mode 而由本地 Schema 校验。图片只在正文明确放置有效 `[IMG:...]` 标记时公开，未定位图片不追加到末尾，实际图片计数与公开块一致。开启思考后的真实 Assistant 定向 4 个用例/6 轮全部通过，但耗时约 22～37 秒；关闭思考的最新完整 Golden 为 48/50、p50 10.385 秒，两组并非同题 A/B，尚不能证明思考带来稳定质量增益，因此保持默认关闭。报告为 `baseline-20260915-155214.json`、`baseline-20260915-155525.json` 和 `baseline-20260916-142103.json`。该调整无接口字段或数据库迁移。
- 本地活动索引已只读核验 42/42 份文档卡片均为 `READY/CONFIRMED`。真实 Assistant 冒烟已覆盖 VAU 完整步骤、IP 短答、同会话无图排查、多文档综合和范围外收口；相同幂等键 46ms 重放且未重新生成。中途发现旧检索可返回已软删除文档，现已令向量、BM25 和按 ID 读取统一限定 `document_source.status='STORED'`。
- 当前已完成 V0～V5。`RagService` 只准备冻结轮次并进入 `_generate_v2()`；运行时 Prompt 只展示 Query Interpreter 与 Answer Generator。数据库 Active Prompt 不再是 RAG 就绪条件，旧数据库 Prompt 和 Trace 结构仅保留管理/历史读取兼容。Support 文档路由策略已移至 `app.support.document_routing_policy` 并冻结，RAG 不导入；共享焦点列和历史迁移因 Support 仍使用而保留。
- V5 最终 `scripts/check.sh` 退出码 0：Ruff、格式、Mypy（139 个源文件）、后端 778 项、摄取/RAG V2/管理三项 fake smoke、后端累计覆盖率 81%、前端 lint/类型检查、22 个文件 161 项、覆盖率 91.36% 和生产构建全部通过；仅保留既有 Vite 大包警告。Production Compose 用占位凭据完成 `config --quiet`。
- V5 同口径真实技术助手 Golden 为 49/50（98%，50 题/72 轮），完整操作、条件边界、连续排障、领域扩展和范围边界均为 100%，局部短答 9/10；p50 11.542 秒、p95 16.661 秒、最大 19.366 秒。唯一失败是“操作时使用哪个端口？”未包含评测期待的 `3359`，正确文档仍已命中；同题单独复跑 1/1 通过，确认存在模型非确定性，不为该题加入关键词特判。报告为 `rag-acceptance/reports/baseline-20260915-140516.json`，定向复跑为 `baseline-20260915-142448.json`。
- 真实启动复核发现不可删除的历史兼容边界：Alembic `0007/0012` 在构建迁移图时导入 `app.rag.prompt.DEFAULT_RAG_PROMPT`。现保留明确标注为“仅迁移种子兼容”的常量，并新增全迁移图加载测试；`alembic current` 实测为 `0046 (head)`。另将模型正确选择 `ASK_ONE/OUT_OF_SCOPE` 但附带内部证据键的结构偏差改为本地归一化，不增加第三次模型调用；三条原硬失败已改为正常澄清响应。
- 本阶段未新增迁移、未重建业务索引、未部署生产。普通 53 题旧基线仍混有“禁止相关补充文档进入候选”和旧噪声文案等与 V2 目标不一致的断言，本轮实跑 37/53 只作为评测债务证据，不作为 V2 上线闸门。
- V0/V1 最终 `scripts/check.sh` 退出码 0：Ruff/Mypy、后端 1114 项测试、摄取/RAG/管理三项 fake 冒烟和累计覆盖率 83% 通过；前端 22 个文件 161 项测试、lint、类型检查、覆盖率和生产构建通过，仅有既有大包警告。通用 RAG fake smoke 固定在 legacy 以验证其 SSE 标记契约；V2 由专用测试和真实 Assistant 冒烟验证。本轮没有运行 Golden Set、并发测试或生产部署。
- V2 历史收口最终 `scripts/check.sh` 退出码 0：Ruff/format、Mypy（140 个源文件）、后端 1123 项、三项 fake 冒烟及累计覆盖率 83% 通过；前端 22 个文件 161 项、lint、类型检查、覆盖率和生产构建通过，仅有既有大包警告。V2 历史单元与 PostgreSQL 定向用例 23 项通过；本轮未执行 Golden Set、真实模型、并发测试、生产部署、迁移或索引重建。
- V3 聚焦回归 140 项通过、20 项在无 PostgreSQL 测试变量的定向命令中按设计跳过。完整门禁首次在受限沙箱内因同步 ASGI 线程调度卡住，沙箱外同一用例 0.14 秒通过；随后完整门禁发现管理 fake 冒烟会继承开发 V2、而通用 Fake LLM 无结构化完成值，现将该管理发布/Trace 冒烟显式固定为 legacy，V2 管理试问分派由专用结构化测试覆盖。
- V3 最终 `scripts/check.sh` 退出码 0：Ruff/format、Mypy（140 个源文件）、后端 1134 项、三项 fake 冒烟及累计覆盖率 83% 通过；前端 22 个文件 161 项、lint、类型检查、覆盖率和生产构建通过，仅保留既有大包警告。本阶段未执行 Golden Set、真实模型、并发性能、生产部署、迁移或索引重建。
- V4 的真实 50 题/72 轮 Golden 首轮为 44/50（88%），其中完整操作、条件、多轮排障和领域扩展 30/30；失败集中于噪声状态、Prompt 提取、混合范围外空答和一条“升级地址”的歧义表述。用通用输入边界、结构化空答和多文档约束修复，并将该歧义评测题明确为“网络 IP 地址”，没有增加产品编号/标题穷举；六个失败点定向复跑 6/6，但不表述为完整集 50/50。验收允许非关键模型波动，优先保证总体正确并避免特判影响多数回答。
- V4 Assistant 契约八组全部通过；RAG/Assistant/Gateway/Support/PostgreSQL 聚焦回归 198 项通过。真实 1/3/5 并发全部成功，墙钟约 12.5/16.0/18.9 秒，5 并发单请求最大约 18.9 秒；所有检查的 V2 Trace 均无旧三类 Planner 键。两次连续 `scripts/check.sh` 均退出码 0：每次 Ruff/format、Mypy（140 个源文件）、后端 1130 项、三项 fake 冒烟、83% 覆盖率和前端 22 个文件 161 项、lint、类型检查、覆盖率、生产构建通过，仅有既有 Vite 大包警告。
- V4.1 完整真实 Assistant Golden 为 49/50（98%，50 题/72 轮）：完整操作 8/8、条件边界 4/4、连续排障 8/8、领域扩展 10/10、范围边界 10/10、局部回答 9/10；p50 12.736 秒、p95 18.261 秒、最大 24.596 秒。唯一失败是“更换 PSU”后的模糊短追问把“修改船名”文档排首，3359 文档虽在候选中但未被采用；不增加产品标题、编号或问法特判。参数来源观测也明确不能替代候选与当前任务的语义一致性判断。
- V4.1 最终 `scripts/check.sh` 退出码 0：Ruff/format、Mypy（140 个源文件）、后端 1139 项、三项 fake 冒烟和累计覆盖率 83% 通过；前端 22 个文件 161 项、覆盖率、lint、类型检查和生产构建通过，仅保留既有 Vite 大包警告。本轮未修改数据库结构、活动索引或生产配置，也未部署生产。
- V4.2 定向真实 Assistant 3/3 用例、6/6 轮通过；同口径完整 Golden 49/50（98%，50 题/72 轮），原 PSU 端口失败修复，p50 12.068 秒、p95 18.180 秒、最大 24.179 秒。新唯一失败为 LRU 更换后的软件操作追问：任务与更换/升级 LRU 候选正确，模型只采用升级文档并遗漏“配置/导入”断言，按非关键选证/生成波动记录，不增加特判。真实 Trace 同时确认追问有 `CONTEXT_ANCHOR`、换题无锚点，旧准备检索耗时键消失。
- V4.2 最终 `scripts/check.sh` 退出码 0：Ruff/format、Mypy（141 个源文件）、后端 1155 项、三项 fake 冒烟和累计覆盖率 83% 通过；前端 22 个文件 161 项、lint、类型检查、累计覆盖率 91.36% 和生产构建通过，仅保留既有 Vite 大包警告。本阶段未新增环境配置、迁移、索引重建或生产切换。
- V4.3 上线必要优化已完成：LLM 2 增加主任务/补充文档证据覆盖检查；公开组装兼容成组图片别名并保持标记顺序，异常或未声明标记不进入正文。真实定向 2/2 用例、3/3 轮通过；同口径完整 Golden 50/50（100%，50 题/72 轮），上一轮 LRU 软件操作失败已通过。p50 16.418 秒、p95 25.221 秒、最大 30.674 秒；回答中位长度降至 504 字，公开图片标记为 0，但结构化图片单轮最高 19，作为上线观察项，不设硬截断。
- V4.3 最终 `scripts/check.sh` 退出码 0：Ruff/format、Mypy（143 个源文件）、后端 1181 项、三项 fake 冒烟和累计覆盖率 83% 通过；前端 22 个文件 161 项、lint、类型检查、累计覆盖率 91.36% 和生产构建通过，仅保留既有 Vite 大包警告。没有新增接口、配置、迁移、索引重建或生产切换。

## 已取代的 RAG P2/P3 实施记录（2026-09-14，运行代码已于 V5 删除）

- 原 [RAG 文档智能与双 LLM 语义编排执行计划](../technical/rag-semantic-orchestration-execution-plan.md) 已被 V2 取代；下列内容保留为已完成代码、测试和 NO-GO 验收记录，不是继续执行 P4/P5 的依据。
- 目标回答规则是“局部问题短答、任务级操作文档优先、故障问题文档辅助推理、无文档产品问题使用通用工程能力”；文档优先不等于命中即全文，已交付文档的后续追问不重复原文。连续排查计划把 `rag_issue` 扩展为带用户消息来源的 `rag_case`，模型假设不得写成确认事实。
- 计划只在 RAG 范围内取代 ADR-0002 的在线仲裁限制并扩展 ADR-0017；Support 路由和 Profile 生命周期不变。P1 已加入 Alembic 结构、离线卡片生成/向量、活动索引回填任务和管理审核；P2 已交付 Query Planner、多查询 Chunk/文档卡片召回与 Trace；P3 已交付 Response Planner、候选白名单校验和六策略执行器。P4 结构化问题状态、P5 完整真实模型/工程师/性能验收和回滚演练仍未实施或完成。
- P0 已完成：五套评测输入及 50 题 / 72 轮语义期望以 SHA-256 冻结，逐轮标注目标策略、允许/禁止文档和通用知识边界；2026-09-13 的本地真实小样本 5 题 / 6 轮保留为前置基线（自动 4/5，p50 10.294s，p95 35.681s）。就绪态 DemoChat/Assistant 使用相同简短欢迎语，不显示知识库名称、文档数或说明；RAG 非范围外的正常技术响应统一使用一行短提醒。Assistant 字段未变化，未执行新的 Golden Set 或生产操作。基线见 [`../../rag-acceptance/semantic-orchestration-p0-baseline.md`](../../rag-acceptance/semantic-orchestration-p0-baseline.md)。
- P0 完整门禁退出码 0：Ruff lint/format（242 个 Python 文件）、Mypy（125 个源文件）、后端 1024 项测试、三项 fake 冒烟和累计覆盖率 83% 通过；前端 22 个文件 155 项测试、覆盖率、lint、type-check 和生产构建通过，仅保留既有大包警告。
- P1 工程交付：迁移 `0046` 新增独立 `rag_document_card` 的 JSON、BM25/HNSW、内容指纹、生成/审核状态和 revision；`maintenance` 队列支持按冻结索引全量/单文档生成、单索引 single-flight、取消与孤儿恢复，索引构建后尽力提交且失败不回滚索引。管理端可查看覆盖、编辑、确认/拒绝和显式替换已确认卡片；文档软删除、索引/知识库 GC 与卡片一致清理。显示名称动态联接，Support Profile 不参与。
- 本地数据库已实际完成 `0045 -> 0046`，并验证 downgrade/upgrade 后回到 `0046 (head)`。2026-09-14 首次 VDR600 真实回填因提示词未携带实际 Schema，模型输出 `troubleshooting_guide` 等语义正确别名而 42/42 失败；修复为显式 Schema、受限枚举归一化和离线结构重试，并修正批量任务误用外层卡片 revision 的关联错误。当前活动 Index v39 已重新只读复核：预期文档 42、`READY/CONFIRMED` 42，`AUTO/REJECTED/FAILED/STALE/MISSING` 均为 0，`shadow_ready=true` 且 `ready=true`；P1 数据与审核门槛完成。
- P2 工程交付新增严格 `QueryPlanV1`、不可信历史/范围/焦点输入、精确型号/参数防编造校验、最多三条查询的归一化去重与来源追踪，以及同库/冻结索引/有效卡片与 Chunk 的向量、BM25、RRF 文档级融合。`shadow` 与旧检索并行启动并从公开路径分离，先记 `PENDING`、完成后独立合并 Trace；慢 Planner 不再消耗公开回答时限。补充 Embedding 失败时保留原问题候选并标记降级。4 个真实代表问题的 Planner 均通过且目标文档均为第一候选，但该结果不是完整 P2 Golden/并发验收。
- P2 最终完整门禁通过：Ruff lint/format、Mypy（131 个源文件）、后端 1066 项测试、摄取/RAG/管理三项 fake 冒烟及累计覆盖率 83%；前端 22 个文件 160 项测试、覆盖率门禁、lint、type-check 和生产构建均通过，仅保留既有大包体积警告。另有 RAG/Assistant/Support 定向回归 164 项、真实 PostgreSQL 卡片/Trace/状态测试 41 项通过，生产 Compose 使用一次性占位 Assistant 凭据静默展开成功。本轮未执行 Golden Set、真实在线 Planner、并发性能、生产部署或业务索引修改；P2 质量/性能验收仍待后续明确授权。

- P3 工程交付新增严格 `ResponsePlanV1`、最多三份文档的有界候选包、文档/Source/引用白名单和六种策略执行器。局部事实禁止全文交付，完整文档只允许已确认流程卡片且正文由后端读取，模型的预留状态补丁在 P3 被清空；响应规划诊断在旧路由或公开失败前独立写入 Trace。`shadow` 会在后台生成并记录 LLM 2 计划但不执行、不修改公开答案或焦点，`enabled` 才接管；Support 不读取卡片或两个 Planner。
- 当前本地 `.env` 保持 `shadow`。真实 Shadow 局部 IP 请求的公开旧回答耗时 1.876 秒，后台 Query/Response Planner 分别约 6.466/9.683 秒且均 `PASSED`，LLM 2 计划为 `SHORT_ANSWER`，证明双 Planner 诊断不阻塞公开结果。对 `glm-5.3-flash` 的隔离 `enabled` 复测最终同会话 3/3 通过：局部 IP 问题为 `SHORT_ANSWER/PRODUCT_KNOWLEDGE`（24.343 秒），无图像追问为 `DOCUMENT_ASSISTED/PRODUCT_EXPLAINED`（47.425 秒），纠正意图为 `DOCUMENT_DELIVERY/DOC_DELIVERED`（51.805 秒）；三轮 Query/Response Planner 与语义检索 Trace 均为 `PASSED`。此前调试轮次出现过 Query/Response Planner 超时、结构化输出非法、Embedding 两次超时及旧路由兜底耗尽 90 秒，因此一次 3/3 不能替代稳定性与并发验收，暂不建议生产 `enabled`。
- 最新完整门禁 `scripts/check.sh` 退出码 0：Ruff lint/format、Mypy（132 个源文件）、后端 1091 项测试、摄取/RAG/管理三项 fake 冒烟及累计覆盖率 83%；前端 22 个文件 160 项测试、覆盖率、lint/type-check 和生产构建均通过，仅保留既有大包体积警告。本轮没有执行 Golden Set、1/3/5 并发、生产部署或业务索引修改；P2/P3 的完整质量和性能放行仍属于 P5。
- 2026-09-14 P5 前置验收（P2/P3 范围）已在本地隔离实例（127.0.0.1:18001，环境变量覆盖开关，用户实例未触碰）执行完毕：50 题/72 轮 `off/shadow/enabled` 三模式真实模型对照、1/3/5 并发和 off 回滚演练，共 249 轮、0 传输错误；评测输入哈希与 P0 冻结一致，模型为 `qwen3.7-flash`（DashScope）+ SiliconFlow Embedding/Rerank。公开结果：off 33/50（66.0%，p50 4.854s/p95 21.017s）、shadow 32/50（公开与 off 仅差 1 例非确定性）、enabled 31/50（62.0%，p50 7.199s/p95 16.194s）。enabled 确认结构性收益：标志性用例 `ta_full_007`（VAU 完整步骤）从旧路径错误 `UNCONFIRMED` 变为正确全文交付，local_answer 1/10→6/10、complete_operation 3/8→5/8、`DOC_DELIVERED` 12→20、`UNCONFIRMED` 16→4、p95 下降。但未达 16.4 硬门槛：错误文档全文交付 ≥4 轮（`ta_full_004` 问 FFC 交付 PDC 升级文档、`ta_domain_010` 通用问题交付 OPU 全文、`ta_condition_001/002` 歧义问题不澄清直接交付 3359 文档），通用回答泄漏文档引用 7 例，local_answer 60% 低于 90% 门槛，整体低于 off 基线——**结论 NO-GO，生产不得 enabled**。双 Planner 统计：Query Planner 通过 79.7%（shadow）/85.5%（enabled），降级全部为 `QUERY_PLAN_SEMANTIC_INVALID`；Response Planner 通过 88.4%/85.5%，降级为 `ValueError` 校验拒绝与结构非法；并发 27/27 成功；Response Planner 降级 9/27 经复核全部为 delivery 类问题确定性触发 `DOCUMENT_DELIVERY` 字段校验拒绝（与顺序金集同簇，非并发放大——short/assist 两类 18/18 全通过，含 5 并发），并发只影响延迟（p95 12.8→22.3s）；回滚演练确认切 `off` 后 6 条 Trace 均无 Planner 键、旧路径特征恢复。发现 `backend/app/rag/response_planner.py:394` 降级原因只记录 `ValueError` 类名而丢失具体校验信息的可观测性缺陷。完整数据见 [`../../rag-acceptance/semantic-orchestration-p5-preacceptance.md`](../../rag-acceptance/semantic-orchestration-p5-preacceptance.md)。

## 已删除的小体量 RAG 分层方案（2026-09-13 历史记录，运行代码已于 V5 删除）

- 已按 [优化执行方案](../technical/rag-technical-assistant-optimization-plan.md) 落地确定性回答形式：明确全文/完整流程保留完整文档交付，局部、解释和排查问题在可靠正文内生成聚焦回答。无可靠正文且知识库公开说明非空时，`DOMAIN_ASSIST` 可给受控通用工程分析、只问一个关键条件或收口范围外请求；通用分析无文档引用/图片并带免责声明。
- 新增 `M3_RAG_TECH_ASSIST_MODE=off|shadow|enabled` 和 `M3_RAG_SCOPE_MAX_TOKENS=800`，代码、环境示例与生产 Compose 默认均为 `off`。`shadow` 不额外调用模型或写状态；生产公开行为在显式启用前不变。原事实短答和单强仍默认 `shadow`，解释默认 `enabled`。
- 复用公开 `knowledge_base.description` 作为专业范围：管理 API/知识库详情现可携带 `expected_updated_at` 修改，冲突保留前端草稿；新请求在 `begin_turn` 的知识库/索引锁定快照中记录名称、说明和版本。`off/shadow` 仍使用旧固定 Prompt 范围，只有 `enabled` 才让说明影响生成。修改不创建任务、不重建索引；空说明不影响有据问答，但不开放无据领域扩展。
- 启用新策略时，`chat_context.rag_issue` 只承接原问题、回答形式和一个待确认条件；最初用户消息、当前条件来源用户消息与追问助手消息均校验关联，状态与成功回答终态同事务 CAS 提交。失败、取消、噪声和来源损坏不推进状态，Support 不消费该命名空间。
- 外部 Assistant 接口、SSE 事件、请求/响应字段、图片及历史重放契约没有新增或删除；仅更新对接指南对 `UNCONFIRMED` 连续追问、无引用通用回答和公开说明生效范围的解释。没有新增 Alembic 迁移，没有修改源文档、业务索引或生产配置。
- 新增 50 个专业技术助手场景及相应确定性评测字段，JSONL 已通过格式校验。2026-09-13 已在本地 `enabled`、VDR600 当前索引和真实模型上复核5类代表题（6轮），自动断言4/5、p50 10.294s、p95 35.681s；明确 VAU 完整流程题因最佳 Chunk 未达锁定下限而错误进入 `UNCONFIRMED`，连续排查也只依赖普通历史而非结构化问题承接。完整50题、工程师语义评审、并发性能对照和生产部署仍未执行，详见执行方案8.5；预期效果不是生产成绩。关联决策见 ADR-0017。
- 最终本地门禁 `scripts/check.sh` 退出码 0：Ruff lint/format（242 个 Python 文件）、Mypy（125 个源文件）、后端 1021 项测试、三项 fake 冒烟和累计覆盖率 83% 通过；前端 22 个文件 155 项测试、覆盖率、lint、type-check 与生产构建通过，仅保留既有大包警告；定向边界与真实 PostgreSQL 用例均已纳入全量测试。

## RAG 会话历史收口（2026-09-12，当前验证结果）

- RAG 默认改为完整问答轮次、当前焦点代次和双重预算控制：失败/取消/运行中、跨焦点、无可靠关联或已失效焦点文档的轮次不进入模型上下文；总 Prompt 先裁剪最旧完整历史，再裁剪本轮证据。用户陈述、旧模型回答和当前文档事实在 Prompt 中明确分区，不引入额外 LLM 摘要调用。
- 新 RAG 轮次持久化用户/助手关联；旧 Assistant 轮次兼容既有 `assistant_request` 关联，旧内部无关联记录不猜测。文档切换确认保存触发问题作为新焦点的用户陈述桥；Trace 只保存历史消息 ID、轮次数和裁剪原因。模型上下文使用文档最新显示名称，并按元素身份保守去除开头旧标题，不全局替换正文。
- 外部 Assistant 接口、SSE 字段、Support 工作流、文档/图片交付契约均未增加或删除字段。新增迁移 `0045`，无需重建知识索引；本地数据库已核对为 `0045 (head)`，生产尚未部署，目标环境状态待确认。
- 最终完整门禁退出码 0：Ruff 240 文件、Mypy 124 源文件、后端 992 项测试、摄取/RAG/管理三项 fake 冒烟、累计覆盖率 83%；前端 22 文件 152 项测试、覆盖率、lint/type-check/build 全通过，保留现有大包警告。最终复跑隔离到确认空闲的 Redis DB 4，未清理其他队列；未运行真实模型或 Golden Set。
- 该次历史收口任务未编辑同时进行的 `AnswerReviewsView.vue` 及其测试改动，但上述完整门禁包含并通过了这些改动；此条记录当时的验证范围，不表示当前工作树仍有未提交改动。

## RAG 解释与 Assistant 契约（2026-09-12，当前验证结果）

- 运行时三类提示词升级 v3，允许有据推断、计算和明确标注的通用知识；区分现象改善与维修决策。首轮明确解释题默认启用 DOC_QA，事实短答仍 shadow；没有新增 Assistant 接口、字段或迁移。
- 修复跨增量的模型标记泄漏、图片标点控制字符、超时/截断被当作成功完成的问题；公开终态、历史与重放保持一致。明确原文引述须命中同库冻结索引内的有效焦点正文，才稳定进入问答，不覆盖确认或重排降级边界。
- 真实解释集 6/11 → 11/11；正文质量 20/20 → 20/20；两类关键边界各重复三次，6/6。主 Golden 按同一最终断言前后重评均为 52/53，唯一失败仍为已删除 FFC600 文档。原始末轮 51/53 中另有一项同义措辞误判，原始报告保留；评测调整及实际对照见实现报告。
- 真实 HTTP 契约 8 组通过，7 条 Assistant 业务路径及 17 个相关结构不变。核对 141 个完成 Trace、55 个生成提示词哈希、4 次引述焦点/来源一致性；测试会话 3 次完成、1 次取消，没有 RUNNING 遗留。活动索引仍为 v39，未改源文档或业务索引。
- 最终完整门禁退出码 0：Ruff 236 文件、Mypy 123 源文件、后端 954 项测试、三项 fake 冒烟、后端累计覆盖率 82%；前端 22 文件 151 项测试、覆盖率门槛、lint/type-check/build 全通过，保留已有大包警告。队列使用确认空闲 Redis DB 6，未清空已有库；日志 `/tmp/shipdemo-reasoning-gate.log`。
- 本地实现已完成，未部署生产；外部业务后台/移动端源码未提供，本轮验证服务侧真实 HTTP 契约。解释生成增加延迟：解释集 p50 3.317s → 4.710s，不能宣称整体加速。完整文件清单、复验和回退方式见 [`reasoning-implementation.md`](../../rag-acceptance/reasoning-implementation.md)。下方为前序质量优化记录。

## RAG 回答质量优化（2026-09-12）

- 已实现同库、冻结索引、确认文档内的长文档降级检索，按相关性裁剪预算，纠正条件反推及目录凭空作答约束；Trace 分开保存准备上下文和实际生成来源/Prompt 哈希。补齐追问改写信号及改写失败时明确省略对象短追问的焦点标题兜底。没有新增外部 API、迁移或审核流程。
- 本地 VDR600 索引版本 39 的真实模型 Assistant 完整 Golden 为 52/53，与改动前 52/53 相同，没有新增失败；唯一失败 `rag_idf_003` 依赖的 FFC600 文档处于 `DELETED`，需替代源文件及新索引验收。没有修改主集预期或恢复已删除文档来获得全绿。
- 新增 20 个原文质量用例（30 轮），前后对照从 18/20 到 20/20，两次最终内容复验均通过；改进来自 LRU/PSU 追问对象补全。3 次短超时追问复验全部通过，其中两次真实发生改写超时并走焦点标题兜底。6 个首轮事实题 shadow/enabled 均为 5/6，开启短答增加延迟且没有通过率增益，故 `M3_RAG_FACT_QA_MODE` 保持默认 `shadow`。原单强直达也保持 `shadow`。
- 最终完整门禁 `scripts/check.sh` 退出码 0：Ruff（234 文件）、Mypy（122 源文件）、后端 900 项测试、三项 fake 冒烟、后端累计覆盖率 82% 全部通过；前端 22 个文件 151 项测试、覆盖率门槛、lint/type-check/build 通过，仅保留大包警告。队列隔离至确认空闲的 Redis DB 9，未清空已有库或修改 `.env`。真实模型报告另核查 204 个 Trace，模型/索引/有效参数一致，40 个实际生成 Prompt 哈希与存储内容相符。
- 详细实现、实际验证和复验命令见 [`quality-implementation.md`](../../rag-acceptance/quality-implementation.md)。本轮未部署生产、未改源文档或业务活动索引；现有源内容缺失不能靠代码部署恢复。下方“未执行 Golden”条目均为对应前序任务的历史记录。

## Assistant 内容一致性修复

- 后续本地修复：历史消息分页不再丢弃最新记录，失败重放保留错误快照及 `retryable`；失败/取消同步关联消息公开错误，历史优先读取快照。RAG 文档直达不再按 Token 截断，尾部步骤、表格和图片随完整内容保存并重放；两引擎不混用路由，模型问答预算保持不变。
- 2026-09-11 本轮最终 `scripts/check.sh` 退出码 0：Ruff lint/format（231 文件）、Mypy（121 源文件）、后端 862 项测试全部通过；摄取/RAG/管理三项 fake 冒烟均 passed，累计覆盖率 82%；前端 22 个文件 151 项测试、覆盖率门禁、lint/type-check 和构建通过，仍仅有已知大包警告。新增边界单元测试 52 项，PostgreSQL 一致性用例由 2 项扩展到 13 项（实际执行通过）。
- 本地依赖最初未运行，数据库用例被跳过；获准启动本地 PostgreSQL/Redis/MinIO 后确认 Alembic `0044 (head)` 并实际复跑。最终门禁使用确认空闲的 Redis DB 11 隔离队列，未清空已有库、未修改 `.env`。未执行 Golden Set、未部署生产、未重建业务索引；旧回答快照不重算。以下 799 项及更早记录属于前序验证。

- 已在本地修复 `include_images=false` 在 RAG 同步/流式及 Support→RAG 回退路径失效的问题；只过滤输出，不改变检索证据或文档路由，也不切换单强 shadow 模式。
- 历史消息优先使用保存的公开内容，显式空列表不被旧内部图片覆盖；旧内容回退和幂等重放统一过滤管理资源地址，保留正文、图片 ID、表格和来源的原顺序。不批量改写历史记录、不新增外部接口或数据库迁移；生产尚未部署本次修复。
- 用户反馈有问题的源文档已删除并正在重制；新文件及重建索引的内容完整性尚待验收，本次没有操作源文档或业务索引。
- 2026-09-11 最终完整门禁通过：Ruff lint/format（230 文件）、Mypy（121 源文件）、后端 799 项测试（新增一致性测试 26 项、PostgreSQL 测试 2 项）、三项 fake smoke，累计覆盖率 82%；前端 22 个文件 151 项测试、各目录覆盖率门禁、lint/type-check 和生产构建通过，仅保留已知大包警告。使用确认空闲的本地 Redis DB 12 隔离验收队列；沙箱内无法访问依赖的未完成验证不计为通过，最终完整结果来自获准访问本地依赖后的复跑。未执行生产部署或真实模型 Golden。

## 本地 RAG 首轮优化（最新实施状态）

下方两次 RAG 实验条目保留实验时点事实；当前实现状态以本节和 [`first-turn-implementation.md`](../../rag-acceptance/first-turn-implementation.md) 为准。

- 首轮聚合/最强块冲突保护、精确型号有界标题补召回及单强影子判定已接入 RAG 服务；单强默认 `shadow`，不混用 Support 标准问题、标签或共享路由阈值，不增加对外接口。
- VML 图片漏解析已修正，公共摄取增加完整性、OCR 和解析版本复用保护。真实 DC24V 源文件现在提取 110 张图片、识别 105 个小型文字候选，但页面绝对坐标版式不能按 XML 顺序还原，最终验证明确 `BLOCKED/LEGACY_FLOATING_TEXT_LAYOUT`。需转换可编辑正文并人工核对后再构建候选索引；本次未重建业务索引、未恢复该文档的正文命中能力。
- 下一次构建不复用无当前解析版本标记的旧内容，可能产生全库重解析和模型费用；现有活动索引不变。先核验源文档，再候选构建、验收、激活，不可伪造版本标记绕过校验。
- 生产未部署本次改动；2026-09-12 已完成本地真实模型全量 Golden，结果见上方质量优化节。生产环境的实际召回收益和延迟仍待部署前后对照；历史全绿不代替当前工作区验证。
- 最终本地完整门禁通过：Ruff lint/format、Mypy 118 个源文件、后端 759 项测试及三项 smoke，累计覆盖率 81%；前端 20 个文件 139 项测试、覆盖率门禁、类型检查、lint 和构建通过，仍有已知大包警告。本次新增定向测试 36 项通过。全量使用确认空闲的 Redis DB 14 隔离开发 Worker；中途队列竞争及标题并行修改导致的失败不计为通过，最后按最新工作区复跑全绿。Production Compose 仅用临时验证凭据完成配置解析，未启动生产容器。

## 管理侧回答优化（最新实施状态）

- 已完成首期四项：日志中的 Assistant 回答记录与完整历史响应、人工问题标记、文档元素可视化预览、带前置问题的单题复测；另支持 JSON 用例快照导出。入口为概览“查看回答与优化”或日志“Assistant 回答优化”，必须先选知识库。
- 本地已执行迁移至 `0044`。管理写入校验知识库归属与修订，预期文档限同库；问题快照跨日志保留期存续。复测使用新内部会话和当前配置，保存实际运行版本，相同运行标识不重复执行；不计入外部 Assistant 请求列表，不修改历史回答或自动关闭问题。
- 2026-09-11 当前工作区完整门禁已通过：`bash scripts/check.sh` 退出码0，Ruff lint及228个文件格式检查、Mypy全量121个源文件、后端771项测试、摄取/RAG/管理三项fake smoke全部通过；后端累计覆盖率81%，达到80%门槛。前端ESLint、类型检查、22个文件151项测试及所有目录覆盖率门槛通过，整体语句/行覆盖率91.75%、分支80.33%；生产构建成功，仅保留已有大包警告。队列使用确认空闲的本地Redis DB 13作进程级隔离，未修改 `.env` 或业务代码；日志 `/tmp/shipdemo-workspace-gate-20260911.log`。未进行生产部署或真实模型质量验收。
- 本地真实PostgreSQL/Redis/MinIO与fake模型的11组流程冒烟通过，覆盖现有发布边界、公开SSE、幂等重放、新管理记录快照、业务输入保存、跨库/修订拒绝、实际Gateway新会话复测、运行版本及来源隔离；临时数据已清理。报告为 `/tmp/shipdemo-answer-reviews-smoke.json`，脚本 `/tmp/shipdemo-answer-reviews-smoke.py` 为本次临时验证工具，长期定向测试入口见 environment.md。
- 待后续：生产升级0044及前后端部署、真实模型回归；批量Golden与自动调参尚未接入。旧记录缺失选择参数时不能精确复现选择操作，图片/索引资源按原保留策略可能过期，详情明确提示。

## 当前目标

- 保持企业知识库问答的 RAG 主链路质量、可观测性与生产可部署性。
- 为后续 Agent 建立并维护基于仓库事实的 AI 项目认知体系（本次已建立）。

## 正在进行的工作

- 2026-09-24 本地按 ADR-0027 调整双路径：LLM 1 不再输出补充检索查询/约束，服务端从原问题、独立请求和可选任务锚点构造查询；`response_path` 独占是否检索的决定，Conversation 扩展为无需文档依据的一般回答，允许谨慎的人工协助建议，删除本轮新增的联系/同意/改写句式正则和事后关系归一化。技术 LLM 2 增加“未见规定不等于允许自行操作”的通用证据边界。真实 Assistant 定向 5/5；同版本完整 Golden 58 案例/82 轮通过 56/58（96.5%，报告 `rag-acceptance/reports/baseline-20260924-092115.json`），Conversation 类 6/6，联系人工追问走 Conversation 且检索跳过。两项机器失败：含糊 PSU“哪个端口”追问未满足 `3359` 断言；询问文档是否要求联系时，技术 LLM 2 仍从未见联系规定推断“自行更换”。另有 PSU 拆板前追问机器通过但未命中 PSU 文档，需人工复核；因此本轮不构成生产放行。`bash scripts/check.sh` 退出码 0：Ruff/格式、Mypy（140 源文件）、后端 864 项与 81% 覆盖率、前端 179 项与 91.36% 覆盖率、lint/type-check/build 全部通过，仅有既有大包告警。生产未部署。
- 2026-09-23 已完成 RAG V2 第一版双路径：Query Interpreter 新增必填 `response_path=KNOWLEDGE|CONVERSATION`；纯寒暄仍在 LLM 1 前零模型短路，Knowledge 完整保留原检索与技术 LLM 2，Conversation 使用同一模型快照的受约束 LLM 2 并确定性跳过 Embedding、检索、Rerank 和文档加载。缺失/无效路由、查询字段冲突、混合或不确定输入保守进入 Knowledge；只有 `scope=IN_SCOPE` 才允许 Conversation，范围外或不确定的冲突组合强制回到既有 Knowledge/范围收口。Conversation LLM 2 失败固定兜底且不回跳 RAG。回答路径同时写入 Citation、Trace、Usage 与 Message 历史上下文；Conversation 保留在运营总量和审计记录，但从技术历史、技术质量和 RAG 延迟样本排除，管理 Trace 可直接显示回答路径。无迁移和新环境变量。定向 Ruff/Mypy 及后端 125 项测试通过；最终 `bash scripts/check.sh` 退出码 0：Ruff/格式、Mypy（140 源文件）、后端 861 项、三项 fake smoke、累计覆盖率 81%、前端 23 文件 179 项、覆盖率 91.36%、ESLint、vue-tsc 与生产构建全部通过，仅保留既有 Vite 大包警告。首次 50 案例/72 轮真实技术 Golden 为 47/50，三个失败均是 LLM 1 已判 `OUT_OF_SCOPE` 但 Conversation 抢先返回；增加上述硬约束及 5 个路由案例后，三个原失败与五个新案例统一复跑 8/8，p50 4.387 秒、p95/最大 6.404 秒。两个纯寒暄为 69–80ms，另外三个正向用例由 Trace 确认为 Conversation 且检索 `SKIPPED`。首次定向运行 7/8 的“谢谢，明白了”因 LLM 1 一次结构化输出无效后保守降级而失败，单例复跑及随后统一复跑均通过；完整扩充后的 55 案例/77 轮尚未执行。
- 2026-09-23 本轮凭证门禁、寒暄短路、关联不变量、内部轮次回收与延迟口径已随 `ca5cd60` 部署生产并完成只读复验：生产源码含全部改动（`stale_internal_traces`、`_total_latency_select`、绑定 NULL 放行），backend/frontend/worker 容器于 10:19（CST）重建，前端产物含新凭证模块与“样本 N / M 次”文案；`request_error_log` 401 计数归零，清理后仅剩 215 条真实业务错误（最新一条 2026-09-17）；在线的管理台轮询会话与外部 Assistant 集成全部 200。仍未做：Golden Set、真实模型回归和寒暄/组合输入的浏览器人工验收。
- 2026-09-23 修复工作区复审发现的三个一致性缺口：指标基础过滤不再删除寒暄，`overview`/`timeseries` 的运营请求总量、完成/失败数和用户口径保留所有真实请求，技术回答/范围/引用/重排质量只排除 `GREETING` 与会话控制类 `META_ACK`，低信息澄清继续保留，延迟仍排除全部零模型短路；V2 技术历史同时排除 `GREETING/META_ACK`，但保留低信息轮次用于条件补充；401 清空 Store/会话凭证时，已挂载的设置表单同步清空本地输入副本。针对性后端 47 项、前端交互 25 项通过；最终 `bash scripts/check.sh` 退出码 0：Ruff/格式、Mypy（139 源文件）、后端 854 项、三项 fake smoke、累计覆盖率 81%、前端 23 文件 179 项、覆盖率 91.36%、ESLint、vue-tsc 与生产构建全部通过，仅保留 Vite 大包警告。无数据库迁移和新环境变量，生产尚未部署。
- 2026-09-23 完成关联一致性复核后的三项修正：陈旧 Assistant 恢复只额外回收孤儿或请求本身已 stale 的外部 Trace，活跃请求持有的旧 Trace/Message 不受其他恢复批次影响；观测性上下文绑定以 `COALESCE` 保留既有非空 ID，缺失本身不报错，但后续单独到达的 Assistant/Trace 仍会与先前保留的用户/Assistant ID 交叉校验，终态继续验证 Message/Trace/用户配对；时序 P95 与 overview/latency/assistant 一致排除全部 `citation_result.shortcut`。新增真实 PostgreSQL 活跃请求保护、跨事件部分绑定回归及时序 SQL 断言；同时固定一个上传重复文件测试的 `lastModified`，消除测试构造的时间抖动。最终针对性测试 56 项通过；`bash scripts/check.sh` 退出码 0：Ruff/格式、Mypy（139 源文件）、后端 851 项、三项 fake smoke、累计覆盖率 81%、前端 23 文件 178 项、覆盖率 91.35%、ESLint、vue-tsc 与生产构建全部通过，仅保留 Vite 大包警告。未运行 Golden Set、真实模型或浏览器人工验收，生产尚未部署。
- 2026-09-22 已完成两项收尾：陈旧轮次恢复现在覆盖没有 `assistant_request` 的内部 Chat/Playground 轮次（独立的 `ABANDONED_CHAT_TURN` 错误与 `internal_trace_count` 计数），`complete_turn` 的 `TURN_STATE_CONFLICT` 附带冲突行真实状态便于排查；管理指标把延迟与质量口径分离，所有输入门禁短路统一写 `citation_result.shortcut`，延迟分位、均值和阶段耗时按该标记排除，新增 `latency.samples` 字段（`MetricsLatencySummary`，前端 Dashboard 两张延迟卡显示“样本 N / M 次”）。运营请求量、失败率和用户数不变。无迁移、无新环境变量。完整门禁 `bash scripts/check.sh` 退出码 0：Ruff/格式、Mypy（139 源文件）、后端 851 项 0 跳过（本地 PostgreSQL/Redis/MinIO 在线，含新增的内部轮次回收与延迟口径 PG 用例）、三项 fake 冒烟、累计覆盖率 81%、前端 23 文件 178 项、覆盖率 91.35%、ESLint、vue-tsc 与生产构建全部通过，仅有既有 Vite 大包警告。未运行 Golden Set、真实模型和浏览器人工验收，生产尚未部署。
- 2026-09-22 已完成管理台凭证门禁再次加固和 RAG 纯寒暄短路：管理 API 与 Chat API 共用凭证工具，`App.vue` 在子组件前接管拒绝事件并同步浏览器存储、Store 和页面门禁；无 Service Token 时请求层不调用网络，真实 401 后 Dashboard/DemoChat 均卸载。RAG 在 LLM 1 前仅按完整消息精确匹配纯寒暄，零 LLM/Embedding/Rerank 返回固定欢迎语，带技术内容的组合输入不命中。寒暄 Trace 正常落库并显示于近期记录，但通过 Message 持久标记从后续技术历史排除，也不计入技术回答率、趋势和延迟。Assistant 上下文优先使用 `rag_user_message_id`，终态写入会校验 Message、Trace 与 Assistant Request 的关联及原状态；关联绑定与终态收口把缺失标识按“尚未绑定”放行并保留已绑定值，后续新标识仍须与已绑定值交叉验证，实际关联冲突才判 409，避免已生成成功的回答仅因观测性关联缺失被转成 `INTERNAL_ERROR`。验证：本地 PostgreSQL 在线，绑定/终态 PG 回归用例实际执行；该轮改动已并入当日完整门禁（见上一条）。Golden Set、真实模型验证和生产部署仍未进行。
- 2026-09-15 已完成 RAG V4.2 本地收口：Query Interpreter 增加 `NEW_TASK/FOLLOW_UP/CORRECTION` 和脱敏任务锚点，短追问任务同时进入召回、文档级 Rerank 与 LLM 2；有效 V2 的 `prepare()` 不再运行 legacy Embedding/BM25/向量检索或混入其候选，本轮查询只批量 Embedding 一次。真实 Trace 确认追问存在 `CONTEXT_ANCHOR`、换题没有该锚点，且耗时字段不再出现旧预检索阶段。技术助手 Golden 仍为 49/50（98%，50 题/72 轮），原 PSU 端口失败已通过；新唯一失败为 LRU 更换追问虽召回正确更换/升级文档，但模型只采用升级文档并遗漏“配置/导入”断言。p50 12.068s、p95 18.180s、max 24.179s。没有新增配置、迁移、索引重建或生产切换。
- 2026-09-14 管理台 LLM 配置已新增千问（阿里云百炼 / DashScope）Provider，支持默认 OpenAI 兼容地址、管理台加密 API Key 与环境变量兜底。模型配置仍在保存前执行真实连通性探测，保存后由每轮 Gateway 模型快照同时作用于 DemoChat 和 Assistant；Embedding、Rerank、OCR、Vision 当时未开放 DashScope——该限制已于 2026-09-15 解除（见顶部记录）。
- 生产服务器已完成源码、PostgreSQL 和 MinIO 数据迁移，并于 2026-09-10 同步到完成 Assistant 生产验收的版本；核验时服务器工作树与 `origin/main` 一致，生产 `.env` 权限为 `600`，迁移备份校验通过。
- 生产 PostgreSQL 已位于 Alembic `0041 (head)`；此前核对共有 1 个知识库、64 条文档记录、2004 个分块和 1096 条图片记录，MinIO 有 70 个文档对象和 1232 个图片对象。活动索引状态为 `ACTIVE`。
- PostgreSQL、Redis 和 MinIO 保持原数据卷持续运行；生产服务器于 2026-09-11 使用提交 `49c799a` 重建后端、前端和两个 Worker，相关容器健康，外部 `/health/ready` 返回 ready，Alembic 为 `0041 (head)`。
- Assistant 生产验收已覆盖鉴权、知识库与欢迎语、会话与消息、真实 SSE、幂等重放与冲突、外部用户隔离、文档/图片下载及删除资源拒绝。SSE 经生产 Nginx 返回 HTTP 200、`text/event-stream`、`Cache-Control: no-cache, no-transform`、`X-Accel-Buffering: no`，并到达 `complete` 终态，已具备内网联调条件。
- 生产服务器仓库的 `origin` 使用 GitHub SSH 地址；`cm` 账号可在交互式输入 SSH 私钥口令后正常拉取。自动化执行使用非交互模式时无法提供该口令，因此后续服务器 `git pull` 由用户手动执行。
- 已新增 Assistant 遗留请求预览/恢复命令；当前数据库原有的 25 条遗留 `assistant_request` 及对应 Assistant 消息/Trace 已保留记录并恢复为失败终态。另有 2 条确认无业务价值的内部 Support 轮次，已在完整数据库备份后精确删除 2 条 Trace 与 4 条消息，未删除所属会话。
- Assistant 对接契约已统一：会话归属、知识库/Active Index、幂等及并发前置失败在 SSE 建立前返回 HTTP JSON；SSE `context` 使用公开字段白名单，已知短暂错误保留可重试语义；流发送失败或客户端断连会事务取消仍为 `RUNNING` 的预留请求及其关联消息/Trace；资源代理转发可用的 `Content-Length`，且不再暴露已删除文档的图片；业务后台新会话锁使用稳定 `client_chat_id`。
- 2026-09-11 生产服务器源码和后端已更新到 Assistant 欢迎语版本；欢迎语不再展示知识库名称、描述和文档数量，三条推荐问题固定使用已通过 Assistant Golden Set 的标准精确命中用例。更新时只重建后端导致其容器 IP 变化，旧前端 Nginx 仍缓存原地址，外部 `/api/` 和 `/health/` 暂时返回 502；重启前端可立即恢复。
- 生产 Nginx 已使用共享 upstream、Docker DNS 和运行时 `resolve`，避免后端容器重建换 IP 后继续使用旧地址；生产 smoke 包含“后端容器换 IP、前端不重启”的恢复验证。
- staging/production 已禁止查询 Embedding/Rerank/LLM 在缺少凭据时静默使用 fake，并记录实际查询模型；明显噪声在检索前短路，Rerank 降级不沿用焦点文档，非知识回答会清理来源和下载引用。2026-09-11 生产 Golden Trace 复核确认 58 个检索轮次使用真实 SiliconFlow 模型，5 个噪声轮次由输入门禁短路。
- 管理台已部署 Service Token 门禁：无凭证时仅允许进入凭证配置页，不挂载其余管理视图或启动轮询；收到 401 后停止后续自动请求，保存新凭证后恢复，避免认证错误持续刷屏。
- 管理台文档页现可修改文档显示名称，名称更新不改变原始文件、对象存储键或索引，也不触发索引重建；后续 RAG/Support/Assistant 回答使用新名称，历史消息、幂等重放和 Trace 不回写。回答正文中的文档标题统一以 Markdown 加粗展示；文档直达时只显示标题和后续内容（2026-09-15 起不再删除原文首条非空文本行，见 ADR-0011 废弃记录），不显示额外的“已找到/以下是文档内容”引导语。
- 对外 Assistant 原文下载接口已删除，业务后台只能展示文档来源元数据，不得使用 `document_id` 拼接下载地址；受控图片接口和管理端原文访问保持不变。
- 对外业务后台指南已按标准服务契约重构，包含可跳转目录、整体接入及恢复流程、详细接口关系、逐接口请求/响应字段说明、SSE 事件契约、错误处理和验收清单；文档注明测试阶段使用临时 Token、正式上线前必须轮换，但不记录真实凭据，也不包含平台核心逻辑或我方运维操作。
- 迁移 `0041` 已部署，将 Assistant 对外上线状态与知识库可维护状态分离：既有启用知识库在迁移时保持上线，新知识库默认下线；Assistant 列表、欢迎语、问答、会话与资源访问统一执行上线及归属门禁，列表 `ready` 使用当前执行策略的完整就绪条件。
- 管理台已展示 Assistant 上线状态，并修正 RAG 候选索引“自动生效”的错误文案：`READY` 候选复用现有激活接口设为当前索引，只有历史版本使用回滚。
- 2026-09-11 本地验证通过：本次 Python 文件 Ruff format/check、后端 Mypy（112 个源文件）、111 项相关后端测试通过且 1 项按条件跳过；前端 ESLint、类型检查、18 个测试文件共 109 项测试和生产构建通过。隔离迁移验证确认 `0040` 既有启用知识库升级后保持上线、`0041` 后新建知识库默认下线；Production Compose Smoke 确认默认私有、未上线欢迎语不可访问、服务健康及 Nginx 后端换 IP 自动恢复。
- 2026-09-11 已在生产服务器通过 Assistant 对外接口执行全部 53 个 Golden 用例/63 轮：51/53 通过（96.2%），5/5 噪声输入全部由门禁返回 `UNCONFIRMED`，错交付、范围外误命中、噪声误命中及 HTTP/SSE 错误均为 0；延迟 p50 4.868s、p95 14.630s、max 26.453s。两个失败分别是追问缺少公开文档引用，以及安全拒答措辞不在用例限定词中；报告保存在服务器 `rag-acceptance/reports/baseline-20260911-080747.json`。
- 已根据生产 Trace 修正上述两项 Golden 断言：文档未覆盖追问或回答为通用建议时不得为了通过测试而暴露引用；安全拒答统一接受“未涉及、未找到、无法确认、没有足够”。修正后离线重放 Run 11 原始响应为 53/53，真实模型全量复跑尚待执行。
- 2026-09-11 生产运营数据只读复核：近 7 天 Assistant 共 116 个完成轮次、0 个失败 Trace，p95 11.633s；数据库 `assistant_request` 为 116 条 `COMPLETED` 和 1 条无会话 `FAILED`，没有 `RUNNING`。当前工作树已新增 `0042`：Assistant 指标改为统计 `assistant_request` 并持久化终态重放次数，维护 Worker 每日清理超过配置期限的终态孤儿请求；生产仍为 `0041`，需部署后生效。管理台认证失败刷屏在前端门禁部署后已停止，最近 15 分钟新增为 0。
- 2026-09-11 已补齐提交门禁测试：新增构建模型加载、Assistant 遗留恢复/终态孤儿保留、维护任务和索引 GC 单元及 PostgreSQL 集成测试，并扩展管理 API、凭证门禁、路由/RAG store、Profile/Release/模型管理测试。最终工作树的 `scripts/check.sh` 完整通过：Ruff lint/format、Mypy（113 个源文件）、后端 635 项 Pytest、三项 fake smoke、后端累计覆盖率 80% 门槛、前端 ESLint/类型检查、19 个文件共 127 项测试及全部目录覆盖率门禁、生产构建均通过；构建仍有已记录的大包体积警告。Production Compose Smoke 也已在全新数据库验证 `0042`、两个 Worker、Assistant 私有默认边界和 Nginx 动态 DNS；真实模型 Golden 和服务器部署状态已按上条另行验证。
- 2026-09-11 文档显示名称修改、对外 Assistant 原文下载接口删除及回答标题加粗变更已完成完整门禁：`scripts/check.sh` 通过 Ruff lint/format、Mypy（113 个源文件）、后端 643 项 Pytest、三项 fake smoke、后端累计覆盖率 81%、前端 ESLint/类型检查、19 个文件共 128 项测试及覆盖率门禁和生产构建；构建仍仅有已记录的大包体积警告。
- 2026-09-11 文档直达展示已进一步收敛：RAG 与 Support 的新回答只以 Markdown 加粗标题开头，正文过滤重复的原文首行标题，不再输出“已找到/以下是文档内容/已提供文档”等引导语。`scripts/check.sh` 完整通过，后端 646 项 Pytest、三项 fake smoke、后端累计覆盖率 81%、前端 19 个文件共 128 项测试和生产构建均成功。

- RAG 首轮单强直达已完成本地离线实验，尚未接入在线链路：426 条可证明首轮的历史真实 Rerank Trace，按问题取最新后为 44 题；0.85 最强块门槛和 0.18 跨文档分差新增 1 题唯一目标相符直达，降到 0.60/0.10 开始自动选择歧义题。建议先做 RAG 独立影子记录与留出题真实模型验证，不混用 Support Profile/标签，不降低共享路由阈值；单块仅作选文档证据，仍须完整交付步骤和图片。实验脚本、结果口径与限制见 [`single-strong-experiment.md`](../../rag-acceptance/single-strong-experiment.md)。新增实验测试及现有路由/Chat 流程测试共 104 项通过；本次没有改生产、执行新的真实模型 Golden 或复跑全量门禁。

- 已实现轻量知识库 Assistant 管理：每库编辑并保存三条推荐问题、独立新会话试问、运行就绪检查和确认上下线。写入使用行锁与配置修订号，防止并发覆盖；线上问题保存立即生效，未上线库允许暂存不完整问题。欢迎语统一使用 Gateway 就绪条件，外部契约不变。本地已升级 `0043`，生产尚未部署本次功能。 本次曾执行后端全量 713 项测试及三项 smoke，均通过，累计覆盖率 81%；最终后端相关 API/数据库回归 73 项通过；最终前端 ESLint、类型检查、20 个文件 137 项测试及目录覆盖率门禁、生产构建通过，仍有已知大包警告。原始 `scripts/check.sh` 被已有实验测试文件的 Ruff 导入排序错误阻断；随后完整 Mypy 又受并行修改的 `rag/first_turn.py` 类型错误影响，不能宣称当前整个工作树完整门禁已通过。

- RAG 首轮第二轮本地实验完成：44 个旧问题的真实纯文本重排对比、17 个新问题（其中 2 个由噪声门禁短路）及 3 个原生多模态核验。二次文档重排在保守门槛下没有新增直达；“检查 PDC 接线盒”被聚合到“更换故障 PDC”在多模态下复现，建议先增加 RAG 独立的聚合/最强块排序冲突保护。DC24V 源 DOCX 有 110 个旧式 `w:pict`，当前解析仅得 17 段 TEXT、0 图片、0 中文且无告警，需修复摄取后再调召回。实验代码未接入在线链路，相关测试 130 项通过；详见 [`document-rerank-experiment.md`](../../rag-acceptance/document-rerank-experiment.md)。

- 2026-09-11 本地冒烟复核通过：Alembic `0043 (head)`，PostgreSQL/Redis/MinIO 健康；执行 `app.ingestion.smoke --provider fake`、`app.rag.smoke --embedding-provider fake --llm-provider fake`、`app.admin.smoke`，三项均为 passed。另以 FastAPI TestClient、真实本地数据库及 MinIO 完成 Assistant 管理与公开接口九组场景：新库默认私有、问题校验/修订冲突/管理鉴权、下线库管理试问、双库上线及欢迎问题隔离、线上修改即时生效、SSE complete/幂等重放/用户隔离/图片访问、下线后全部相关入口拒绝访问、历史保留与无 RUNNING 泄漏；临时知识库及图片已清理。前端配置组件、管理 API 和状态关联测试 3 个文件 49 项通过。本轮使用 fake 模型，不替代真实模型质量验收或生产部署验证；未修改业务代码。临时报告 `/tmp/shipdemo-assistant-publication-smoke.json`，复跑脚本 `/tmp/shipdemo-assistant-publication-smoke.py`（在 backend 目录用 `../.venv/bin/python` 执行，临时文件不作为长期工具依赖）。

- 知识库详情试问禁用原因已明确显示，并拆分“检查就绪”和“重新加载配置”：检查就绪不丢弃未保存问题、不更新原写修订号。排查确认用户选择的两个 m2/m6-smoke 库缺少 Active Index，VDR600 则已就绪；缺索引需先构建并激活后试问。前端 ESLint、类型检查、配置组件 9 项测试和构建通过，仍仅有已知大包警告。

## 当前阻塞

- 正式上线安全收口尚未完成：UFW 已启用，但当前检查账户不能读取具体规则；Docker 发布端口可能绕过普通 UFW 入站规则，必须核对 `DOCKER-USER` 或上游防火墙是否仅允许获准业务来源访问 `18080`。

## 近期下一步

1. V5 已完成；上线前保存当前生产镜像/提交作为唯一回滚路径，清理生产 `.env` 中已删除的旧 RAG 变量，并在测试环境使用新会话、新幂等键复跑 Assistant 冒烟和真实模型 Golden。
2. 完成 UFW/`DOCKER-USER` 来源限制和临时凭据轮换。
3. 生产部署 V5 后观察 Query Interpreter、文档级 Rerank、LLM 2 与总时限；不要用产品词、标题或编号硬编码换取单条全绿。跨库资产和真实实现信息泄露仍须零容忍。
4. 部署知识库 Assistant 管理与本次审核门禁：升级至 `0046`、更新前后端，逐库对活动索引生成并确认全部 RAG 文档理解，再维护三条推荐问题、试问并确认上线；外部接口结构不变，欢迎语继续使用通用文案。
5. 将修正后的 Golden 用例同步到服务器并在相同生产环境复跑全量 Assistant Golden Set；确认全绿后建立新基线，并继续关注相对 Run 10 的延迟长尾回升。
6. 下次生产更新执行 Alembic 到 `0047 (head)` 并重建后端、前端和维护 Worker，确认 Assistant 指标、终态孤儿清理、RAG 文档理解生成/审核及上线门禁均已生效；索引构建的人工重试/中止/前缀删除与任务记录修剪（ADR-0024）同批生效。ADR-0025 卡片跨版本复用与一键确认（提交 `badeaad`）无需迁移，同批部署即可：部署后首次构建存量卡片会因指纹算法变更（去除 index_id）重新生成一次并自愈，之后新增文档仅对新增部分调 LLM 并逐份/一键确认。

- 2026-09-18 RAG 理解卡片跨版本复用与一键确认已完成（ADR-0025，提交 `badeaad`，无迁移）：构建成功后按摄取层 REUSE 判定把卡片行（含 `routing_embedding` 与 `review_status`）从 `IndexBuildResult.prev_index_id` 复制到新索引，生成任务只为新增/变更文档调 LLM；`content_fingerprint` 去除 `index_id`（存量卡下次生成自愈）；新增 `POST .../rag-document-cards/confirm-batch` 与文档页"一键确认待确认"按钮。`scripts/check.sh` 完整通过（后端 823 项含两个新 PostgreSQL 集成测试、覆盖率 81%、Mypy；前端 174 项、ESLint、类型检查与构建，仅既有大包警告）。同日先期提交 `312500d` 修复 ADR-0024 自查发现的删除顺序缺陷（前缀扫删移到数据库行物理删除成功之后，指针恢复路径对象完整性优先）与取消后 worker 死亡的僵尸 BUILDING 索引回收缺口（reconcile 纳入 CANCELLED 任务并按取消语义结算）。

- 2026-09-11 文档交付标题去除规则已简化：RAG/Support 不再比较显示名称，统一只删除整份文档首条非空文本行，保留后续正文、图片与表格，不修改源文档或索引。相关后端 118 项测试、六个改动 Python 文件 Ruff lint/format 和三个改动源文件定向 Mypy 均通过；另只读加载本地 VDR600 的四份真实文档，核验交付时首行去除、剩余正文及共 14 张图片保留，输入对象未被修改。其中一份解析首行合并了标题和章节名，按当前规则会整行去除，详见 known-issues。本次未执行全量门禁或生产部署。新规则仅作用于新生成的交付响应，历史消息及幂等重放保持原快照。
