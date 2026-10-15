# RAG 链路黄金集（Golden Set）

对上线链路（`RAG_ONLY` → `RagService`）的黑盒回归评测：53 个用例 / 63 轮真实请求，
经 HTTP 调用真实后端与真实模型（SiliconFlow embedding/rerank/LLM），同时检查文档命中和实际回答正文。

2026-09-11 已在生产服务器通过 Assistant 对外接口完成全部 53 个用例；最新结果见
下方 Run 11，历史全绿基线仍保留下方 Run 10 供对比。

RAG 首轮单强直达的本地离线实验见
[`single-strong-experiment.md`](single-strong-experiment.md)：只读回放历史分数，
不改变在线 RAG/Support 策略，也不是新一轮真实模型 Golden 结果。

第二轮真实模型受控实验见 [`document-rerank-experiment.md`](document-rerank-experiment.md)：
比较文档级重排、候选多样性，并核验新题与多模态；发现 PDC 聚合错选及 DC24V
旧式图片摄取缺口，最终建议优先处理冲突保护与内容完整性；上述为实验时状态。

后续本地实现及未解决项见 [`first-turn-implementation.md`](first-turn-implementation.md)：
已接入冲突保护、精确型号补召回、默认 shadow 的单强判定和 VML 完整性保护；
当时尚未部署生产或复跑真实全量 Golden，DC24V 浮动文字正文仍需转换核验后重建。

2026-09-12 的回答质量实现、真实模型对照和剩余问题见
[`quality-implementation.md`](quality-implementation.md)。新增 20 个内容质量用例（30 轮）
及 6 个首轮事实问答实验用例；前者依据当前索引原文制作，不是 20 条客户差评。

解释、推断与 Assistant 兼容性优化见 [`reasoning-implementation.md`](reasoning-implementation.md)。
新增 11 个解释边界用例（18 轮），以及使用现有接口的真实 HTTP 契约检查。
主 Golden 的 `rag_fup_001` 允许有依据的混合回答引用当前 PDC 文档，保留未覆盖声明，
并校验引用范围、正文事实和依据范围说明；纯通用回答仍可无引用。NONE 检查实际公开引用，
不将“文档未涉及”的标题提及误计为引用；rag_xdoc_005 文档选择题接受既有 next_suggestions 中
符合预期的升级文档。前后报告用同一套新断言重评。

小体量专业技术助手的 72 个场景见
[`technical-assistant-cases.jsonl`](technical-assistant-cases.jsonl)：完整操作 9（部分含 `min_images` 配图下限）、局部回答 10、
无可靠正文的专业扩展 10、连续排查 8、领域边界 10、Conversation 路由 6、条件关联边界 6、
证据边界 8（`ta_evidence_*`：产品身份、厂商/认证、无文档排障、IP 防护等级、工作温度、技术问题夹带创作、
电气额定值、适用标准）、回答形式 2（`ta_format_*`：分点、英文，验证回答阶段只读 LLM 1 的标准化问题时形式要求不丢失）、设备名纠错 3（`ta_alias_*`：拼错的单元名按档案还原，另含不应被改写的对照题）。证据边界组自 2026-09-30 起按 ADR-0029 断言：规格类参数和适用标准允许给行业典型值，
但必须说明资料未提供、标明来源层级并引导核验；电气额定值等高后果参数不得给出具体范围；
`content_forbidden_patterns`（正则，作用于去空白、小写化后的正文）用于拦截“根据产品规格”等虚构出处。
自动断言只检查措辞形态，典型值是否合理仍需人工复核。
该文件已做 JSONL 和自动断言校验；2026-09-24 增加一般人工协助建议的非检索边界，
以及明确询问文档规定时的检索边界。本地真实 Assistant 完整运行通过 56/58（96.5%，82 轮），
报告为 `reports/baseline-20260924-092115.json`：联系人工建议正确跳过检索；PSU 模糊端口
追问与“未见联系规定 → 可自行更换”的错误推断各失败一例。人工复核还发现一个机器通过的
PSU 追问未采用 PSU 文档，因此自动分数不等同于内容放行。其中标明 `data_dependency`
的场景需相应源文档重制、上线后才能作为放行结果。

双 LLM 语义编排 P0 的逐轮内部目标位于
[`semantic-orchestration-expectations.jsonl`](semantic-orchestration-expectations.jsonl)，按同一用例 ID
记录 `expected_strategy`、`allowed_document_keys`、`forbidden_document_keys` 和
`allow_general_knowledge`。该文件是后续新旧链路对照的旁路期望，不是当前运行时配置；
冻结哈希、小样本真实结果和复现边界见
[`semantic-orchestration-p0-baseline.md`](semantic-orchestration-p0-baseline.md)。

## 运行

```bash
# 原管理接口模式（保留历史基线兼容）
.venv/bin/python rag-acceptance/run_golden.py

# Assistant 对外接口模式
.venv/bin/python rag-acceptance/run_golden.py --api-mode assistant

# 正文质量（关键步骤、参数、图片、追问及引用）
.venv/bin/python rag-acceptance/run_golden.py --api-mode assistant --cases rag-acceptance/quality-cases.jsonl

# 解释、计算、条件推断、通用知识与文档引用边界
.venv/bin/python rag-acceptance/run_golden.py --api-mode assistant --cases rag-acceptance/reasoning-cases.jsonl

# 小体量专业技术助手（必须显式指定测试服务和目标库，会调用真实模型）
.venv/bin/python rag-acceptance/run_golden.py \
  --api-mode assistant \
  --base-url <test-service-base-url> \
  --kb-id <online-ready-rag-knowledge-base-id> \
  --cases rag-acceptance/technical-assistant-cases.jsonl

# 只复跑一个或多个失败用例；--case-id 可重复
.venv/bin/python rag-acceptance/run_golden.py \
  --api-mode assistant \
  --cases rag-acceptance/technical-assistant-cases.jsonl \
  --case-id <case-id> --case-id <case-id>

# 模拟业务后台完整调用；请指定已上线、就绪且包含图片的测试知识库
PYTHONPATH=backend .venv/bin/python rag-acceptance/check_assistant_contract.py --kb-id <uuid> --base-url http://127.0.0.1:8000

# 用同一组断言重新评分前后两份完整响应报告
.venv/bin/python rag-acceptance/compare_golden.py before.json after.json --cases rag-acceptance/quality-cases.jsonl --output rag-acceptance/reports/comparison.json

# 通用可选参数
#   --base-url http://127.0.0.1:18000
#   --kb-id <uuid>
#   --external-user-id <稳定测试用户ID>  # 仅 Assistant 模式
```

契约检查默认使用 VDR600 的 PSU 更换/拍照标记问题，可通过 `--question`、`--follow` 替换为
当前库的文档交付题和解释追问。它创建独立测试用户的会话并真实调用模型，报告保留这些会话 ID；
不改文档、索引、上线配置，也不清理其他用户数据。验收完成后测试会话按正常保留策略清理。

凭据：

- 管理模式读取 `GOLDEN_TOKEN`，或回退读取根目录 `.env` 的 `SERVICE_TOKEN`。
- Assistant 模式读取 `GOLDEN_ASSISTANT_TOKEN`，或回退读取根目录 `.env` 的
  `ASSISTANT_SERVICE_TOKEN`。V2 评测还以只读观察用途读取 `GOLDEN_TOKEN`/`SERVICE_TOKEN`
  查询管理 Trace；该管理凭据不会发送到 Assistant 接口，也不会写入报告。

Assistant 模式调用 `/api/v1/assistant/chat/stream`，为每轮请求提供
`X-External-User-ID`、`X-Request-ID` 和 `Idempotency-Key`，解析 SSE 的
`complete/error` 终态。legacy 使用公开 `references` 与回答中的《文档标题》判定命中；
V2 不要求公开引用，评测器使用同一轮内部 Trace 的 `used_document_ids` 验证实际证据文档，
同时继续检查公开正文、图片和禁止内容；
正文校验读取完整 `content`（包括表格），无内容块时才回退到 `answer`。
目标知识库的管理策略必须为 `RAG_ONLY`；测试请求本身不能选择模型或回答模式。

约 10~15 分钟。报告写入 `reports/baseline-<时间戳>.json`，含每轮的 response_type、
命中文档、延迟、完整公开响应、答案摘录与失败原因。报告含知识库内容和会话标识，
按本地诊断数据保存；旧报告只有摘要时不能用于新正文断言或 `compare_golden.py` 对照。

## 用例设计

### 无断言的回答体验对照

六个体验采集工具（`run_context_review.py`、`run_fixed_context_review.py`、`run_evidence_input_review.py`、`run_thinking_card_review.py`、`run_condition_stage_review.py`、`run_suggestions_review.py`）启动时均拒绝已存在的 `--output` 路径；每次实验使用新文件名，保留历史原始答案用于对照。同一输出路径不要并发运行；该保护不改变评分方式，也不代表回答质量提高。

部分相关文档的体验实验使用 `context-applicability-questions.jsonl` 与
`run_context_review.py`：只采集真实 Assistant 完整回答与诊断，不评分、不调用模型评委。
同问题/同库/同模型前后分别采集后，逐条阅读正文并核对原文的设备、条件和阶段。
2026-10-08 两版提示词试验未证明稳定改善，业务修改已撤回，结果及运行方法见
[`context-applicability-review.md`](context-applicability-review.md)。Golden 的机械断言继续作为回归工具，不能替代回答体验或事实适用性评阅。

同日进一步进行任务/证据规则合并重组实验：有效固定输入 48 次 LLM2 对照（每版每题 3 次），另保留误选操作文档的 6 次，以及完整 Assistant 14 轮。未观察到足够的整体体验收益，未接入业务代码。报告、输入误选说明及命令见 [`context-reorganization-review.md`](context-reorganization-review.md)；新增 `run_fixed_context_review.py` 只采集原始回答，`context_prompt_candidate.py` 仅供隔离实验，不用于生产部署。

同日证据输入 A/B/C 实验见 [`evidence-input-review.md`](evidence-input-review.md)：五题每组重复三次，共 45 次真实 LLM2，只改变实际多份全文/人工核对单份全文/任务原文元素。PDC 片段有重复可见的局部收益，但 LRU 九次仍跨阶段套用五分钟，MU 空输入也出现未要求的断网干预，不能宣称整体改善。`run_evidence_input_review.py` 不评分、不运行 Golden，C 为人工诊断输入而非自动筛选器；PSU 完整操作 B=C，复现前须核对本地源采集与冻结索引。

2026-10-09 复用上述历史结果，独立测试思考模式与“全文＋已有卡片五字段”：六组全文输入每方向各三次，新增 36 个样本/40 次供应商请求。思考 16/18 返回有效结构、卡片 18/18，但均未证明稳定整体收益，未接入业务代码；正确 LRU 仍答错阶段、卡片仍泛化 PDC 条件。新工具 `run_thinking_card_review.py` 不评分、不改卡片，不重跑旧基线；逐题判断、历史指纹核验、结构失败及跨日比较限制见 [`thinking-card-review.md`](thinking-card-review.md)。

同日继续测试“全文＋人工条件—动作—阶段说明”：七题、原版/候选各三次，共 42 次同期真实 LLM2，未证明稳定整体收益，不接入业务。LRU 错误前提仍未纠正、PDC 仍泛化氧化处理，MU 仍有未请求的网络隔离建议；条件成立对照能作答、PSU 核心保留不等于新增收益或安全验收。`run_condition_stage_review.py` 只采集，`condition-stage-annotations.json` 是原文引用可核验的人工诊断输入，不是自动提取器或正式卡片变更。详见 [`condition-stage-review.md`](condition-stage-review.md)。

2026-10-09 另测 `next_suggestions` 字段：该字段在 Prompt 中从未定义，而 MU/PDC 回答 20/20 带越界建议。`run_suggestions_review.py` 对 4 题做 BASELINE/DEFINED/EMPTY 三臂单因素对比，共 96 次真实调用，判读规则采集前写定。MU+PDC 越界建议 14/16 → 14/16，未达到减半标准，且 DEFINED 让对照题也总是给出相邻流程建议；EMPTY 臂主回答不变，说明建议字段不是主回答越界的原因。不修改生产 Prompt；随后用户决定在服务端丢弃该字段（恒为空，ADR-0040）。第二轮（`--arms BASELINE,NO_FIELD`，64 次调用）检验把字段从 Schema/示例中移除：输出少约 11%，但 MU 主回答指示用户打开机箱的数量 3/8 → 6/8（p≈0.3，未过预先写定的第 3 关）；用户仍决定采纳，已作为有意推翻关卡记录（ADR-0040）。详见 [`suggestions-review.md`](suggestions-review.md)。

2026-10-09 另测文档卡片提示词 v1 → v2（`run_card_prompt_review.py`，13 份文档 × 2 种提示词 × 2 次共 52 次真实调用，只生成不写库，判读规则采集前写定）：条件丢失 4/29 → 15/30、工具类前提 11 → 2、凭空展开缩写 2 → 0，四个关卡全部通过；副作用是新卡片会自行加“注：……”（2/26）。卡片不进入 LLM 2，只影响召回与排序。详见 [`card-prompt-review.md`](card-prompt-review.md)。

2026-10-10 另测图片规则与提示词排布（均为真实模型、共享 `frozen_inputs.py` 的冻结输入，判读规则采集前写定）：[`image-rule-review.md`](image-rule-review.md)（`run_image_rule_review.py`，规则 12 两分支改写与图片按文档分组，60 个样本，预设规则会选 RULE 但证据太弱，未采纳）；[`prompt-layout-review.md`](prompt-layout-review.md)（`run_prompt_cache_probe.py` 缓存探针、`run_layout_review.py` 不退化筛查、`run_interpreter_constraint_check.py` 排除条件检查；LLM 2 采纳静态规则在前，LLM 1 因丢排除条件不改，ADR-0042）。2026-10-15 另测语言/格式类追问：[`format-request-review.md`](format-request-review.md)（`run_format_request_review.py`，约 300 次真实调用，LLM 1 新规则 + 服务端兜底 + 会话重试，预设关卡全过，ADR-0043；基线由去掉新增常量得到，与改动前提示词逐字节一致）；`run_regression_50.py` 为 50 例回归（新旧各 2 次，修复后 98/100 对 98/100）。排布比较用 `frozen_inputs.legacy_layout` / `static_first_layout` 从当前提示词重建两种顺序；`run_image_rule_review.py` 的 BASELINE 仍是旧排布，以便复现当时的付费运行。

| 类别 | 数量 | 考什么 |
| --- | --- | --- |
| standard | 10 | 标准问法直达（文档标题直译） |
| colloquial | 10 | 口语变体的词汇失配（含"如何检查VDR"→自检文档：同义词扩展回归钉） |
| identifier | 5 | 型号/错误码/英文标识（FFC600、tftp、time battery low） |
| cross_doc | 6 | 近邻文档歧义（接线盒 PDC/FFC、保险丝 AC/DC/电池） |
| out_of_scope | 5 | 范围外问题不得命中任何文档 |
| knowledge | 2 | 文档内事实问题的定位 |
| follow_up | 5 | 多轮：焦点保持 + 显式换文档 |
| focus_coverage | 3 | 聚焦会话下问未覆盖部件：必须拒答且不得套用最接近流程（2026-08-31 真实案例：问"压力释放器"答了 FFC 换壳流程） |
| meta | 1 | 元话语（"换一个问题"）：固定应答、不进 DOC_QA、秒级返回 |
| retrieval | 1 | 图片描述不得驱动检索（caption-only 命中不得浮出文档） |
| noise | 5 | 符号、单字、重复字符、键盘连打不得检索或暴露文档；已有焦点也不得误用 |

判定规则（`expected` 字段）：

- `tolerance: EXACT`——须命中 `document_keys` 指定文档，且不得命中 `forbidden_keys`；
  `document_keys` 为空表示"无必须命中的文档"（拒答/元话语轮），此时仅剩 forbidden 约束；
- `tolerance: ANY_OF`——命中列表中任一即可（歧义题的合理行为是命中或澄清）；
- `tolerance: NONE`——不得命中任何知识库文档；
- `answer_must_any`——归一化后的回答须包含其中至少一项（拒答标记、组件名等）；
- `answer_forbidden_any`——回答不得包含其中任何一项（如被错误套用的文档步骤原文）；
- `max_latency_ms`——该轮延迟上限（元话语轮防回归到重路径）。
- 所有用例拒绝空答案；文档交付拒绝只有标题、没有正文和图片，并要求公开文档引用。
- `response_types`、`content_must_all`、`content_must_groups`、`content_forbidden_any`——限定响应类型、全部必需内容、每组任选一项及禁止内容。
- `min_images`、`min_tables`、`require_reference`、`document_ids`、`forbidden_document_ids`——检查交付结构和引用关联；管理响应有 `sources` 时也检查其与引用的文档一致性。
- `answer_modes` 仅用于会公开该字段的管理/直入响应，Assistant 对外用例不得依赖内部模式；`require_no_reference` 确认通用工程回答没有冒充文档来源。
- `require_disclaimer`、`disclaimer_must_all`——检查回答已携带要求的统一短提醒及关键措辞。

规则断言不能替代完整语义评审。只命中标题、关键词或图片数量不代表整段回答全部正确；
真实失败应结合公开响应与 Trace 人工核对，再决定修改源文档、检索、Prompt 或用例。

命中提取自 `references[].title`、`sources[].document` 与答案文本中的《标题》
（对 LLM 转写幻觉做 ≥0.85 相似度容差，例如"-中文"被写成"-英文"）。

## 基线结果（2026-08-31，八次运行）

| 指标 | Run 1（基线） | Run 2（基线） | Run 3（P0-P2） | Run 5（P3+P4） | Run 6（P5 终版） | Run 9（P6-P10） | Run 10（P11-P12 终版） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 用例数 | 42 | 42 | 42 | 42 | 42 | 47 | **48** |
| 通过率 | 92.9% | 97.6% | 100% | 100% | **100%** | **100%** | **100%** |
| 错交付（forbidden 命中） | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| 范围外误命中 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 延迟 p50 | 5.9s | 7.5s | 2.5s | 1.9s | 2.4s | 2.6s | **2.8s** |
| 延迟 p95 | 38.0s | 29.9s | 36.5s | 23.2s | 8.3s | 9.0s | **9.1s** |
| 延迟 max | 64.0s | 96.0s | 61.6s | 28.0s | 14.4s | 13.2s | **14.7s** |

（Run 4 唯一失败是 NO_MATCH 回答用"文档11"编号而非《标题》引用候选——行为正确
但不可机检，已通过 prompt 规则消除。Run 7 中间态：P6-P8 逐项验证时 focus_coverage
从 0/3 修到 3/3；Run 8 重嵌后 identifier 一例被弱单候选澄清劫持，P10 修复。
Run 10 首跑 44/48——后 4 轮因 SiliconFlow 余额耗尽（402）失败，充值后复跑全绿。）

### Run 11：生产 Assistant 全量基线（2026-09-11）

| 指标 | 结果 |
| --- | --- |
| 用例 / 轮次 | 53 / 63 |
| 通过 | 51（96.2%） |
| 错交付 / 范围外误命中 / 噪声误命中 | 0 / 0 / 0 |
| HTTP 或 SSE 错误 | 0 |
| 延迟 p50 / p95 / max | 4.868s / 14.630s / 26.453s |

分类结果：`standard` 10/10、`colloquial` 10/10、`identifier` 5/5、
`cross_doc` 6/6、`out_of_scope` 5/5、`knowledge` 2/2、`noise` 5/5、
`meta` 1/1、`retrieval` 1/1、`follow_up` 4/5、`focus_coverage` 2/3。

两项失败均未错误交付文档：`rag_fup_001` 的追问正确谈及 PDC 升级失败，但公开
响应未携带预期文档引用；`rag_focus_001` 已安全拒答，但使用“没有足够的可靠信息，
无法确认”，没有命中用例限定的“未涉及/未找到”措辞。生产 Trace 显示 58 个检索
轮次使用真实 SiliconFlow Embedding、Rerank 和 LLM，5 个噪声轮次由输入门禁短路。
服务器报告为 `rag-acceptance/reports/baseline-20260911-080747.json`。

Run 11 后已修正这两项断言：聚焦文档不覆盖追问、或回答进入通用建议时，必须不
暴露文档引用，并通过安全拒答语义判断上下文是否正确；拒答词表统一接受“未涉及、
未找到、无法确认、没有足够”。用修正后的断言离线重放同一份 Run 11 响应为
53/53；该结果仅验证断言修正，不能替代下一次真实模型全量运行。

### 已实施的优化（累计）

1. **P0 — CLARIFY/OFFER_SWITCH 固定模板**：文档聚合已确定性算出候选时不再
   调 LLM 措辞，模板直出并枚举标题。澄清类问题 9~64s → 1~3s。
2. **P1 — DOC_QA/CHUNK_QA 精炼约束**：操作步骤/参数完整的前提下避免冗余
   （一般 ≤500 字）；追问已答过的问题时简要确认指向上一轮，不重复展开。
3. **P2 — Rerank 文本带文档标题**：`_build_rerank_documents` 前缀"文档标题：X"。
4. **P3 — Turn 级总时限**：`m3_turn_deadline_seconds`（默认 45s）。检索超限 →
   504 可重试；生成超限 → 部分答案 + 截断标记。
5. **P4 — ROUTING prompt 约束**：≤150 字、禁止转述内部分数、候选用《》标题。
6. **P5 — Query Rewrite 收紧**：旧规则对"有历史且 ≤40 字"一律改写（Trace 实测
   单次 1~8s，范围外问题也在付费）；新规则只对含指代/承接信号、或 ≤12 字且
   无产品/型号/代码 token 的问题改写，超时 8s → 3s。效果：p95 23.2s → 8.3s。
7. **P6 — 图片描述降权（2026-08-31）**：chunker 的 `search_text` 剥离视觉
   描述（BM25 不再命中 caption），图片/混合块的嵌入文本同步剥离（图片本身仍
   多模态嵌入），`content` 保留描述供生成。起因：真实会话中"压力释放器"仅凭
   图片描述里的"压力传感器"字样被路由到 FFC 换壳文档。已有索引经 SQL 等价
   修正（`regexp_replace` 与新代码推导一致），新构建自然生效。
8. **P7 — 覆盖判定规则 + 判例（2026-08-31）**：DOC_QA/CHUNK_QA 增加规则 2
   （先于作答执行的核心对象覆盖判定，仅图片描述提及=未覆盖，严禁套用最接近
   流程）与压力释放器正反判例；模式判定顺序同步前置覆盖判定。
9. **P8 — 元话语门禁（2026-08-31）**：`_META_ACK_PATTERN` 匹配"换一个问题/
   换个话题/算了/不问了"（锚定+≤12字防误伤"帮我换一个问题：LRU怎么换"），
   固定应答短路，跳过 DOC_QA。起因：真实会话中元话语走了 38.7s 全文 LLM 且
   回答语义错位（用户说换问题、机器人让它换问题）。
10. **P9 — lock_best_floor 0.75 → 0.70（2026-08-31）**：caption 降权后图片
    重文档的 best 分数系统性下移 ~0.03，明确意图问法（聚合分 2x t_high、
    5 命中）被 0.75 的单块 floor 卡成澄清。0.70 恢复直交，仍拦截 floor 存在
    意义上的"平庸关键词重叠"（best ~0.5）。
11. **P10 — 图片块手术式重嵌 + 单候选澄清门槛（2026-08-31）**：
    (a) 活跃索引 272 个 IMAGE/MIXED 块按新规则（caption-free 文本+图片）
    重算向量（路径 A 脚本，复用应用自身 provider/密钥/MinIO）；
    (b) `m3_doc_single_clarify_best_floor=0.65`——单候选澄清需 best ≥0.65，
    弱单候选（如仅凭产品型号+图片描述聚合到 t_low 的泛硬件块）落回目录
    LLM 回答做语义匹配。起因：重嵌后"FFC600电池组怎么更换"被 FFC 外壳
    文档的弱单候选劫持固定澄清，而真正对口的英文文档只有目录标题可匹配。
11. **P11 — 双信号澄清排序（2026-09-01）**：澄清候选按"最强单块序"与
    "聚合序"交错去重取前 3——第一名赌最强单块证据，第二名保住多块佐证
    信号；两信号同赢家时自然退化。机器决策（锁/STAY/ratio）仍用聚合序，
    仅展示层变化。起因："如何检查VDR"的澄清列表把"两个 0.37 平庸块求和"
    的文档排在了"单块 0.56"强证据之前。
12. **P12 — BM25 域内同义词扩展（2026-09-01）**：`app/rag/synonyms.py`
    六组高精度词表（检查/自检/检测/排查、更换/替换、报警/告警、核心板/
    主板、升级/update、麦克风/mic）。查询含组内词 → 组内其余词各跑一次
    BM25（每词限 3 块）；扩展候选不参与 RRF 融合（单通道排名必输双通道
    原始候选），去重后直通 rerank 输入（上限 3），由 reranker 对原问题
    打分恢复精度。起因："如何检查VDR"因"自检≠检查"词表失配，两检索通道
    均够不着《如何手动自检VDR》（向量排名 12 名开外）；扩展后该问题直接
    锁定交付自检文档。

## 关键发现

1. **命中质量良好**：五轮运行通过率 92.9% → 100%，全程零错交付、零范围外
   误命中，多轮焦点保持 100%。
2. **延迟的主因是"用 LLM 装饰确定性结果"**（已修复）：CLARIFY/OFFER_SWITCH
   分支原本把已算好的候选交给 LLM 自由发挥措辞，产生 9~96s 长尾；固定模板
   后该类问题 1~3s 返回且标题必然齐全。
3. **残余长尾**：DOC_QA 全文问答的 ~7.4k prompt tokens 预填充与 NO_MATCH
   路由 LLM 回答（现在受 45s 总时限保护 + 150 字精炼约束）。批量连续请求
   会触发上游限流放大长尾（隔离重测 12s vs 批量 60s）。后续可考虑 provider
   复用与 prompt 前缀缓存。
4. **同题非确定性**：阈值边缘 + LLM 温度导致的波动属预期；做改动对比时应
   关注分类通过率而非单例。

## 作为回归门禁

改动 RAG 链路（检索参数、prompt、路由阈值、模型切换）前后各跑一次：

- 通过率（整体与分类）不得下降超过 2pp；
- `forbidden_keys` 命中必须保持为 0；
- 延迟 p50/p95 需对比记录。

## 与 support-acceptance 的关系

`support-acceptance/` 面向已旁路的 Support 链路（Profile 路由契约），保留作历史
回归；本目录面向上线链路。真实流量中出现代表性失败时，经人工确认后按上述
`expected` 格式追加用例，保持总量小而有效。
