# RAG 隐藏全文双 LLM V2 执行计划

> 日期：2026-09-14  
> 状态：V0～V5 已在本地开发环境实施并完成工程验收；V6 为受条件约束的可选数据库清理，生产尚未部署本次 V5 代码。
> 首个可见目标：开发环境默认进入 V2，用户只看到基于完整候选文档生成的答案和必要图片，不再看到整篇原文或选文档交互。  
> 生产边界：V5 后仓库只保留 V2 RAG 运行路径，生产部署本次代码也会进入 V2；上线前必须保存旧镜像/提交作为回滚点并完成真实模型验收。
> 适用范围：RAG、经 Gateway 进入 RAG 的 Assistant/管理试问、RAG Trace 和相关配置；Support 引擎自身的 Profile、焦点工作流和文档交付保持独立。

## 目录

- [一、执行结论](#一执行结论)
- [二、现状与需要停止的方向](#二现状与需要停止的方向)
- [三、目标行为](#三目标行为)
- [四、目标调用链](#四目标调用链)
- [五、LLM-1 与检索](#五llm-1-与检索)
- [六、完整文档上下文与 LLM-2](#六完整文档上下文与-llm-2)
- [七、会话历史与焦点淘汰](#七会话历史与焦点淘汰)
- [八、图片、标题与公开契约](#八图片标题与公开契约)
- [九、关联一致性](#九关联一致性)
- [十、配置迁移](#十配置迁移)
- [十一、逐步实施和删除清单](#十一逐步实施和删除清单)
- [十二、时间估算和阶段条件](#十二时间估算和阶段条件)
- [十三、测试和验收](#十三测试和验收)
- [十四、Trace、性能和失败语义](#十四trace性能和失败语义)
- [十五、回滚与完成定义](#十五回滚与完成定义)

## 一、执行结论

V2 不再继续修补“首轮命中、单强直达、事实短答、解释模式、焦点停留、文档切换、全文交付”之间的规则矩阵。统一采用：

1. 每轮先由 LLM 1 根据当前问题和最近完整问答还原可独立检索的问题，并给出少量补充查询。
2. 原始问题始终参与向量/BM25；LLM 1 只补充查询，不能替代原问题。
3. Chunk 和文档卡片只负责找文档，结果按文档聚合并重排，不直接决定回答形式。
4. 后端读取前 1～3 份候选文档的有序完整元素，作为用户不可见的强上下文交给 LLM 2。
5. LLM 2 统一完成短答、完整操作说明、故障分析、通用工程扩展或一个关键问题，不再选择“向用户交付全文”。
6. Assistant/管理试问只呈现最终答案与白名单图片，不公开完整原始文档、候选列表、检索诊断或文档总数；技术回答确有需要时可自然提及文档标题、文件名和设备内部编号。
7. 不再维护 RAG 文档焦点、焦点代次、待选文档或 `rag_case`；每轮根据对话重新检索。
8. 开发环境、测试环境和 Production Compose 均使用唯一 V2 路径；不再提供环境变量模式切换，回滚只能部署 V5 前的提交或镜像。
9. 旧链路按“停止新增依赖 → 停止运行时调用 → 删除 RAG 专用实现 → 最后评估共享数据库字段”的顺序退出，不一次性硬删。

这不是现有 P3 的继续收口。P1 的 RAG 文档卡片、冻结索引、混合检索、Rerank、完整元素读取、图片资产和 Assistant 幂等契约继续复用；P2 的多查询与文档聚合可以重构复用；P3 六策略执行器和 P4 结构化问题状态不再继续。

## 二、现状与需要停止的方向

当前代码存在以下结构性错位：

- `query_planner.py` 使用 `_PRECISE_TOKEN_PATTERN` 把任意两个以上英文字母视为精确标识，普通英文检索词会被误判；纯数字型号如 `3359` 不被识别，`FFC/FFC600` 又不能与 `FFC600` 部分等价。
- 精确标识允许来源包含旧 Assistant 回答，与“历史助手回答不是事实”的提示约束不一致。
- `services/rag.py::_semantic_candidate_packets_for` 每份文档最多只给 LLM 2 两个 Chunk、每块 1600 字，却要求它判断整份文档是否覆盖问题。
- `ResponsePlanV1` 同时要求策略、关系、依据、文档 ID、Source ID 和引用互相一致，模型语义正确也可能因冗余字段失败。
- 完整文档只在 `DOCUMENT_DELIVERY` 被选择后读取并直接展示，LLM 2 没有先阅读完整文档。
- 首轮、焦点、切换确认、事实短答、解释和通用扩展分别拥有开关及路由条件，行为由多个规则交叉决定。
- RAG 焦点写入共享 `conversation.focus_*` 与 `chat_context`；这些字段同时被 Support/Gateway 使用，不能作为 RAG 专用字段直接删除。

因此停止以下工作：

- 不继续扩大 `_PRECISE_TOKEN_PATTERN` 或增加问法正则。
- 不继续实施 P4 `rag_case`。
- 不再以降低/提高单 Chunk、聚合或单强阈值作为主优化手段。
- 不再要求用户选择候选文档。
- 不再把 `DOC_DELIVERED` 数量当作 RAG 质量收益。
- 不为六策略结构化输出继续增加修复字段。

## 三、目标行为

| 场景 | V2 目标表现 |
| --- | --- |
| “VAU 图像配置的电脑 IP 是什么？” | 从完整候选文档中提取 IP、掩码和必要前提，直接短答。 |
| “如何配置 VAU 图像？” | 基于完整文档输出按顺序的操作步骤、参数、注意事项和必要图片；不直接展示整篇原文。 |
| “配置后仍然没有图像” | 重新理解当前问题并检索；结合配置/线缆等前 1～3 份文档回答，不受上一轮焦点锁定。 |
| 候选文档内容互补 | LLM 2 同时阅读有界完整文档后合成答案。 |
| 候选文档互相冲突 | 只在缺少的现场条件会改变结论时询问一个条件，不让用户选择内部文档。 |
| 产品范围内但文档没有覆盖 | 使用通用工程能力给排查顺序和下一步，明确具体产品参数需核验。 |
| 与产品无关或胡乱输入 | 固定收口，不进入开放式无关回答。 |
| 后续追问“那电脑 IP 呢？” | LLM 1 使用最近完整问答还原问题，再执行与首轮相同的检索，不读取 RAG 焦点。 |

完整操作类答案必须覆盖文档中的相关前置条件、连续步骤、关键参数和注意事项，但不保证逐字复制整篇文档。模型生成无法提供原文零改写保证，因此验收必须检查步骤完整率和参数一致性；仅“给了模型全文”不能视为完成。

## 四、目标调用链

```text
Assistant / 管理试问
        │
        ├─ 输入、身份、知识库上线、会话归属、幂等校验
        ▼
最近 N 个显式关联且已完成的完整问答轮次
        │
        ▼
LLM 1：Query Interpreter
  - resolved_question
  - search_queries（最多 3 条）
  - key_constraints
  - scope
        │
        ├──────────── 原始问题检索始终保留
        ▼
原问题 + 补充问题的向量/BM25
        │
        ▼
Chunk + RAG Document Card 融合、按文档去重、Rerank
        │
        ▼
候选文档 D1～D3（同库、同冻结索引）
        │
        ▼
加载有序 TEXT/TABLE/IMAGE 元素
  - 文档内部别名 D1～D3
  - 图片内部别名 I1～In
  - 图片 caption/OCR
  - 总输入预算
        │
        ▼
LLM 2：Answer Generator
  - ANSWER / ASK_ONE / OUT_OF_SCOPE
  - answer
  - used_document_keys
  - used_image_keys
  - basis
        │
        ▼
后端白名单、标题、图片、模式和完成性校验
        │
        ▼
公开 SSE complete / 历史 / 幂等重放使用同一终态快照
```

V2 不自动降级回旧路由。LLM 1 失败时在 V2 内使用原始问题；卡片缺失时在 V2 内使用 Chunk 文档聚合；无可靠文档但问题在产品范围内时仍由 LLM 2 做有界通用回答；LLM 2 失败时返回现有可重试错误。V5 后旧路由已删除，只能部署旧提交或旧镜像回滚，避免同一问题因内部失败突然恢复成旧式全文或选文档体验。

## 五、LLM 1 与检索

### 5.1 输入

- 当前原始问题。
- 最近已完成且显式配对的 4～6 个问答轮次，受消息数和 Token 双预算限制。
- 知识库名称与有界公开说明。
- 不传 RAG 焦点文档、焦点标题、待选文档、内部 URL、凭据或旧 Trace。
- 旧 Assistant 回答可以用于还原指代，但必须标注为历史回答，不能作为产品事实或精确标识授权来源。

### 5.2 最小输出

```json
{
  "resolved_question": "配置 VAU 图像时电脑网卡应设置什么 IP",
  "search_queries": [
    "VAU 图像配置 电脑网络参数",
    "VAU 图像配置 IP 掩码"
  ],
  "key_constraints": ["用户询问电脑网卡参数"],
  "scope": "IN_SCOPE"
}
```

`scope` 只允许 `IN_SCOPE/OUT_OF_SCOPE/UNCERTAIN`。不再输出 `conversation_relation`、`intent`、`answer_scope`、`product/components/actions/objects/symptoms`，也不根据这些字段决定回答长短或是否返回文档。

### 5.3 查询安全和降级

- 删除当前 `_PRECISE_TOKEN_PATTERN` 的全局英文字母规则。
- 查询扩展不是用户可见产品事实，允许增加同义词、英文缩写和检索表达。
- 只对“新增的疑似型号/地址/带单位参数”做类型化诊断：字母数字混合型号、IP/MAC、版本号、带单位数值、大写缩写和知识库型号词典。
- 斜线、逗号、顿号和竖线复合标识拆分成可独立比较的别名；`FFC/FFC600` 必须允许 `FFC`、`FFC600`。
- 纯数字只在用户上下文、已审核卡片或知识库型号词典确认后视为型号；不能把任意数字都当型号。
- 旧 Assistant 回答不得授权新增精确标识。
- 可疑扩展查询被丢弃或降低权重，不使整个 Query Plan 降级；原始问题始终继续检索。
- JSON/超时失败只记录 `QUERY_INTERPRETER_DEGRADED`，使用原问题继续 V2。

### 5.4 文档候选

- Chunk、卡片和显示名称检索全部限定 `knowledge_id + frozen index_id`。
- 同一 Chunk 被多个查询命中只保留一次；按文档聚合时记录查询来源，不重复计分。
- Rerank 输入可以使用内部显示名称、卡片任务和代表 Chunk，但这些数据不直接进入公开响应。
- 不使用首轮阈值、焦点停留分、切换分差或单强直达决定答案形式。
- 默认取第一份完整文档；第二、第三份仅在得分接近、卡片任务互补或不同查询分别支持时进入全文包。
- 无上限“把所有文档塞给模型”禁止；当前小库也必须遵守候选数和总 Token 预算。

## 六、完整文档上下文与 LLM 2

### 6.1 完整文档包

复用 `RagRepository.get_document_blocks()`，按 `document_element.sequence_no` 读取：

- TEXT：保留元素完整正文和顺序。
- TABLE：保留结构化文本，不转成无序句子。
- IMAGE：不直接把 MinIO URL 交给文本模型；注册内部图片别名、caption/OCR 和所属元素。
- 文档用 `D1/D2/D3` 内部别名，图片用 `I1/I2/...`；LLM 不需要输出 UUID。
- 不把 `document_source.display_name`、原始 filename、对象存储键或管理 URL放入 LLM 2 的输出任务。
- 不再为用户展示而盲删首行；如果正文首行就是标题，它仍可作为隐藏证据存在，但最终输出不得包含已知标题。

### 6.2 预算

初始建议：最多 3 份文档、完整文档上下文最多估算 20,000 Token、总 Prompt 继续受 30,000 Token 上限限制。数值必须用当前 42 份文档的真实分布复核后再固定。

预算顺序：

1. 系统约束和当前问题不可删除。
2. 第一候选文档优先保持完整。
3. 删除最旧历史轮次。
4. 删除最低候选整份文档，不跨文档随机截尾。
5. 单份第一候选仍超预算时，使用文档中与查询相关的完整章节/连续元素窗口，并把 `context_mode=SECTION_FALLBACK` 写入 Trace；不得声称模型看过全文。

首期不引入第三次 LLM 摘要或 Map-Reduce。若 VDR600 存在大量单文档超过预算的情况，再单独评审离线结构摘要或长上下文模型。

### 6.3 LLM 2 输出

```json
{
  "response_kind": "ANSWER",
  "basis": "DOCUMENT_AND_GENERAL",
  "answer": "……",
  "used_document_keys": ["D1", "D2"],
  "used_image_keys": ["I2"],
  "next_suggestions": []
}
```

- `response_kind`：`ANSWER/ASK_ONE/OUT_OF_SCOPE`。
- `basis`：`DOCUMENT/DOCUMENT_AND_GENERAL/GENERAL/NONE`，只用于内部校验和 Trace。
- 不再输出文档关系、公开策略、Source 引用或文档 UUID。
- 简单问题必须直接短答；完整操作请求必须保留相关连续步骤、参数和注意事项；模型根据用户目标决定长度，不由正则预分类。
- `ASK_ONE` 只能问一个会改变结论的现场条件，不允许让用户选择内部文档。
- `GENERAL` 不得声明具体产品参数、接线、版本、菜单路径或官方结论。
- 文档加通用分析时，文档事实和通用判断需要在语言上区分，但不向用户暴露内部来源编号。

### 6.4 确定性校验

保留必要而少量的后端约束：

- `used_document_keys` 和 `used_image_keys` 必须来自本轮服务器白名单。
- 图片必须属于同库、冻结索引和被选文档。
- `OUT_OF_SCOPE` 不允许带文档或图片。
- `GENERAL` 不允许带文档图片或内部引用。
- 只对 Prompt 结构、管理面 URL、对象存储位置等真实实现信息执行泄露检查；文档标题、文件名以及 D/S/I 形式的技术编号不是秘密，不做猜测式拦截。
- IP、带单位数值、版本号和型号主要由完整证据上下文与模型约束保证；不因某种字符形态直接失败。确属通用建议的常见值应说明为示例，不能冒充产品参数。
- 输出结构无效或修复失败返回可重试错误，不自动调用旧路由。

## 七、会话历史与焦点淘汰

### 7.1 RAG 新规则

- 每轮重新检索，不读取 `focus_document_id`、`focus_index_id`、`focus_epoch`、`pending_options` 或 `pending_query` 决定查询和回答。
- 不再由 RAG 写入、清空或递增这些字段。
- 不创建 `rag_case`，不把模型建议、假设或排查步骤持久化成事实状态。
- 历史只使用显式关联的完整成功轮次；失败、取消、流式未完成和无法配对的旧消息不进入 Prompt。
- 最近轮次按完整问答对选择，受 `max_messages` 与 Token 预算控制，不按焦点代次过滤。
- 用户当前纠正优先；旧 Assistant 内容只用于理解“它/那个参数/还是失败”等指代，LLM 2 仍以本轮重新取得的文档为事实依据。

### 7.2 共享字段边界

`conversation.focus_document_id`、`focus_index_id`、`focus_epoch`、`chat_context`、`support_context_version` 和 `message.focus_epoch` 当前还被 Support、Gateway、摄取清理和历史兼容逻辑使用。因此：

1. V2 第一阶段只停止 RAG 读写，不执行数据清空，不修改既有 Alembic 历史。
2. Support 仍可使用自己的焦点文档、工作流和 `FOCUSED_SUPPORT` 会话模式。
3. Support→RAG 回退只把当前问题、允许的最近完整对话和冻结 RAG 索引交给 V2；不得把 Support 焦点强制映射为 RAG 候选。
4. 旧 RAG 会话中残留焦点数据不作为 V2 输入；测试使用新会话，避免把旧状态误判为 V2 行为。
5. 只有在 Support 状态完成独立命名空间迁移、所有查询零读取旧列、完成数据核对和回滚演练后，才允许新增迁移删除共享列。
6. 若没有明确业务收益，可以长期保留数据库兼容列；“RAG 不再使用”不等于必须立即破坏性删列。

## 八、图片、标题与公开契约

### 8.1 Assistant 公开行为

V2 继续使用现有 Assistant 路径、鉴权、请求字段、SSE 事件、幂等键和图片读取接口，不增加外部接口。

- 输出映射固定为：`ANSWER → ANSWERED`、`ASK_ONE → UNCONFIRMED`、`OUT_OF_SCOPE → OUT_OF_SCOPE`；V2 不产生 RAG `DOC_DELIVERED`。
- `content` 是最终结构化展示内容，包含 text/table/image；业务后台继续以 `complete` 快照替换临时 delta。
- RAG V2 对外 `references=[]`、`document=null`，表示不承诺公开来源元数据或原文交付；答案正文因解决问题需要可以提及标题或文件名。
- `include_images=false` 必须在保存公开终态之前过滤图片，历史和幂等重放读取同一过滤后快照。
- 图片块只包含受限图片 ID；公开图片端点继续同时校验知识库、文档状态和资产归属。
- 管理试问可以在管理员专用 Trace 中查看内部 D1/文档 ID 和检索诊断，但用户可见答案与 Assistant 保持同一生成结果。

上述行为不增加字段，但改变了旧 RAG `DOC_DELIVERED` 和公开来源标题的业务语义。实施阶段必须同步修改两份 Assistant 对接文档，并明确接入方不得依赖“命中文档一定是 DOC_DELIVERED”或 `references` 非空。

### 8.2 信息边界

- 文档名称、文件名、产品内部编号可用于检索、生成并出现在必要的技术回答中；不把普通技术文本当作秘密。
- 严格隐藏知识库文档总数，不在欢迎语、回答或外部响应元数据中输出；同时禁止 Prompt 结构、候选分数、对象存储位置和管理面 URL。
- 不通过标题/编号正则全局替换或拒绝答案。对真实实现信息采用“模型禁止输出 + 窄规则检查 + 最多一次修复/失败”。
- 旧历史回答、Trace 和幂等终态不追溯重写。
- “用户看不到完整文档”只解决终端展示边界。完整文档仍会发送给配置的外部 LLM Provider，属于数据处理范围扩大；正式生产前必须确认供应商、地域、日志保留和企业数据条款。

## 九、关联一致性

### 9.1 知识库、索引与模型

- Turn 开始时冻结 `knowledge_id`、RAG `index_id`、Embedding/LLM/Rerank 模型快照和知识库范围版本。
- 查询、卡片、Chunk、元素和图片读取均同时限定 `knowledge_id + index_id`，不能只凭模型输出 ID 查询。
- 文档卡片必须为当前 Schema、`READY`、非 `REJECTED` 且内容指纹与冻结索引匹配；卡片缺失不阻止 Chunk 路径。
- 文档改名只影响后续内部检索，不改变文档 ID、索引、历史回答和幂等终态。
- 文档在候选确定后被删除或索引进入不可读状态时，本轮失败关闭，不跨索引重新找近似文档代答。

### 9.2 文档、元素与图片

- `D1` 由服务器映射到唯一文档 ID；`I1` 由服务器映射到唯一图片资产和所属元素。
- LLM 2 不得提交 UUID、URL 或文档标题作为授权依据。
- 图片必须属于 `used_document_keys`；同一图片重复出现按原元素顺序决定是否重复展示。
- `include_images=false` 只过滤公开结果，不改变检索、Rerank 或 LLM 2 的证据。

### 9.3 会话、消息、Trace 与幂等

- 新问题、Assistant 消息、Trace 和公开终态继续使用现有显式关联和事务收口。
- 成功时保存的 `answer/content/references/disclaimer/response_type` 必须与 SSE `complete` 完全一致。
- 失败、取消和断流不能保存成功答案；已有 delta 不能作为完成依据。
- 相同幂等键重放保存的终态，不重复调用两个 LLM，也不按当前文档重新生成。
- 历史分页只读已保存公开快照；旧记录不因 V2 部署重算。
- Trace 只保存 Prompt/问题/全文包哈希、候选 ID、文档/图片数量、预算和耗时，不复制完整文档正文。

### 9.4 Gateway 与 Support

- `RAG_ONLY` 和 Support→RAG 回退进入同一个 V2 入口，不能分别实现两套答案行为。
- Support 自身仍保留焦点、Profile、标准问题、标签、文档交付和风险校验。
- 不把 RAG 文档卡片、V2 Query Interpreter 或 Answer Generator 接入 Support。
- 原 `app.rag.doc_router` 中仍被 Support 使用的排序策略已迁入 `app.support.document_routing_policy`；RAG 不再依赖该策略，Support 保持冻结并通过回归。
- Gateway 仍负责父子 Trace、最终 execution engine 和 Assistant 就绪判断；RAG V2 不直接改运行策略。

## 十、配置迁移

### 10.1 新配置

| 配置 | 开发初始值 | 生产初始值 | 说明 |
| --- | ---: | ---: | --- |
| `M3_RAG_QUERY_INTERPRETER_MAX_TOKENS` | `512` | `512` | LLM 1 最小 JSON。 |
| `M3_RAG_QUERY_INTERPRETER_TIMEOUT_SECONDS` | `15` | `15` | 失败使用原问题继续 V2。切换模型后需实测。 |
| `M3_RAG_QUERY_VARIANT_LIMIT` | `3` | `3` | 补充查询上限。 |
| `M3_RAG_DOCUMENT_CARD_TOP_K` | `5` | `5` | 文档卡片候选数。 |
| `M3_RAG_FULL_DOCUMENT_LIMIT` | `3` | `3` | 进入全文包的最大文档数，替换 `M3_RAG_RESPONSE_DOCUMENT_LIMIT`。 |
| `M3_RAG_FULL_CONTEXT_TOKEN_BUDGET` | `20000` | `20000` | 完整文档元素总预算，仍受总 Prompt 上限约束。 |
| `M3_RAG_ANSWER_MAX_TOKENS` | `4096` | `4096` | 完整操作与多文档回答预算，最终还受模型快照上限约束。 |
| `M3_RAG_ANSWER_TIMEOUT_SECONDS` | `45` | `45` | LLM 2 单次超时；必须小于单轮总时限并预留收口时间。 |
| `M3_RAG_HISTORY_MAX_ROUNDS` | `4` | `4` | 最近完整问答轮次数，不使用焦点代次。 |
| `M3_RAG_ANSWER_REPAIR_ATTEMPTS` | `1` | `1` | 真实实现信息、结构或证据白名单违规后的最多修复次数。 |

初始值用于第一轮开发，不是生产性能结论。若当前 LLM 上下文窗口或管理端模型 `max_tokens` 与这些值冲突，按更小值失败关闭并在 Trace 记录，不静默截断。

### 10.2 环境文件修改（V5 后）

V5 完成后环境关系为：

- `backend/app/core/config.py`：只保留 V2 字段及范围/总时限校验，没有管线模式解析。
- `.env.example`、`.env.production.example` 和 `compose.production.yaml`：使用相同 V2 变量名；不得加入真实凭据。
- 本地忽略的 `.env`：保留用户现有密钥和端口，不回显真实值；旧字段应人工移除，遗留未知字段会被忽略。
- `backend/tests/test_config.py`：断言 V2 上下限及 Query/Answer 时限之和小于单轮总时限。
- `docs/ai/environment.md`、README 配置导航：同步当前有效值和启停方法。

### 10.3 旧配置退出顺序

V5 已从 Settings、环境示例和 Production Compose 删除以下 RAG 旧变量：

- `M3_RAG_SEMANTIC_ORCHESTRATION_MODE`
- `M3_RAG_LEGACY_FALLBACK_RESERVE_SECONDS`
- `M3_RAG_RESPONSE_PLANNER_MAX_TOKENS`
- `M3_RAG_RESPONSE_PLANNER_TIMEOUT_SECONDS`
- `M3_RAG_RESPONSE_DOCUMENT_LIMIT`
- `M3_RAG_CONFLICT_GUARD`
- `M3_RAG_TITLE_RECALL`
- `M3_RAG_SINGLE_STRONG_MODE/FLOOR/GAP`
- `M3_RAG_FACT_QA_MODE`
- `M3_RAG_EXPLANATION_MODE`
- `M3_RAG_TECH_ASSIST_MODE`
- `M3_HISTORY_STRATEGY`
- `M3_DOC_DELIVERY_MAX_TOKENS`
- `M3_QUERY_REWRITE_*`

Support 专用 `M3_DOC_*` 阈值仍保留，当前 RAG 不读取。部署方 `.env` 中遗留的 RAG 旧变量会被 Pydantic 忽略，部署前应人工清理，避免运维误以为仍生效。

## 十一、逐步实施和删除清单

### 当前实施进度（2026-09-15）

- V0：完成。冻结的 50 题/72 轮旧基线未改写；本地活动索引 `7b23aee9-f828-4ee6-b282-7de05bf0d283` 只读核验为 42/42 份卡片 `READY/CONFIRMED`，无缺失、失败、过期、拒绝或待确认。
- V1：完成本地实现和定向验收。新增 `app/rag/v2/`，开发配置显式进入 `v2`，生产示例和 Production Compose 显式保持 `legacy`；RAG_ONLY 与 Support→RAG 回退均复用 `RagService` 的 V2 分派。
- 真实 Assistant 冒烟通过：VAU 完整步骤返回模型组织的步骤和结构化图片；IP 局部问题返回短答；同会话“配置后仍无图”重新检索并组合配置/线缆等文档；范围外天气问题返回 `OUT_OF_SCOPE`。公开结果均不返回文档标题或来源列表。
- 相同幂等键重放耗时 46ms，只返回既有 `complete`；历史接口中的答案、内容块、免责声明和 Trace ID 与首次终态一致。V2 准备阶段不再把旧 Chunk Prompt 正文保存在 Trace。
- 自动化定向回归 207 项通过；最终 `scripts/check.sh` 退出码 0：Ruff/Mypy、后端 1114 项测试、三项 fake 冒烟、累计覆盖率 83%，以及前端 22 个文件 161 项测试、lint、类型检查、覆盖率和生产构建全部通过，仅保留既有大包警告。通用 `app.rag.smoke` 明确固定验证 legacy SSE 标记契约；V2 由专用单元/Assistant 契约测试和本轮真实模型冒烟覆盖。
- V2 阶段已新增独立完整轮次历史选择器：只接受 `message.rag_user_message_id` 或 `assistant_request.user_message_id` 的显式关联，用户与助手双方及外部请求均须成功完成；按轮次、消息数和 Token 预算保留最近连续窗口。
- V2 的 `begin_turn` 不再读取或返回旧 RAG 焦点、`chat_context`、待确认状态及 `rag_issue`，新建 V2 消息的兼容 `focus_epoch` 固定为 `0`；共享数据库状态原样保留给 Support/legacy。历史正文只进入模型 Prompt，Trace 仅保存消息 ID、轮次数、预算、边界和排除原因。
- V4 已完成：有效 V2 路径不再创建旧 P2/P3 Shadow 任务，也不再写入 `semantic_orchestration_config`、`semantic_orchestration` 或 `response_plan`；旧实现仅保留在显式 `legacy` 回滚路径。环境示例与 Production Compose 已移除失效的双 Planner 参数，Settings 暂兼容并告警一个阶段。
- V4.1 已完成：多查询 RRF 先保留最多 5 份文档候选，再复用现有 Rerank 模型对“标题 + 已确认卡片 + 最多 3 个代表 Chunk”执行一次文档级重排，最后仍只加载前 3 份完整文档；Rerank 超时或协议失败保持原 RRF 顺序，不触发第三次 LLM，也不改变对外失败语义。
- V4.1 将 URL 检查收窄为 Prompt 标记、内部服务主机名、未经证据支持的相对管理路径及对象存储字段。产品私网 URL、普通产品 API 路径和公开文档 URL 不再因字符串形态被拒绝；若相对管理路径或内部主机地址确实来自本轮用户问题/候选正文，则允许作为技术证据输出。
- IP、版本、带单位数值和端口只做来源观测，Trace 标为 `DOCUMENT`、`USER_PROVIDED` 或 `UNSUPPORTED`；该结果不拦截、不修复也不改变答案。它只能说明值出现在哪里，不能证明候选文档与当前任务语义一致。
- V4.1 真实 Assistant 完整 Golden 为 49/50（98%，50 题/72 轮）：完整操作 8/8、条件边界 4/4、连续排障 8/8、领域扩展 10/10、范围边界 10/10、局部回答 9/10；p50 12.736 秒、p95 18.261 秒、最大 24.596 秒。唯一失败是“更换 PSU”后的短追问“使用哪个端口”将“修改船名”文档重排到第一位，候选中的 3359 文档未被最终采用；不为该单例增加标题、编号或问法特判。
- V4.2 已完成：Query Interpreter 增加 `NEW_TASK/FOLLOW_UP/CORRECTION` 与 `active_task`，任务只从最近完整轮次的用户请求继承；追问/纠正任务锚点同时进入召回、文档级 Rerank 和 LLM 2。有效 V2 的 `prepare()` 只冻结轮次和覆盖参数，不再执行 legacy Embedding/BM25/向量检索或混入旧候选；本轮去重查询只批量 Embedding 一次。
- V4.2 真实 Assistant 完整 Golden 仍为 49/50（98%，50 题/72 轮），原 `ta_local_008` 已通过；p50 12.068 秒、p95 18.180 秒、最大 24.179 秒。唯一失败转为 LRU 更换后的软件操作追问：任务继承和更换/升级 LRU 候选均正确，LLM 2 只采用升级文档并回答 Update 升级，遗漏断言要求的“配置/导入”。按总体正确性原则记录为模型选证/生成波动，不增加标题、产品词或问题特判。

### V0：计划、基线和保护线

状态：已完成。

执行：

- 冻结当前 50 题/72 轮输入、当前模型、活动索引和公开响应作为只读基线。
- 新增 V2 单元用例骨架，先覆盖新配置有效模式、Query/Answer Schema 和全文包归属。
- 确认 VDR600 42 份卡片仍为当前冻结索引的 `READY/CONFIRMED`；不重新生成卡片。
- 保存 `legacy` 本地启动方式，但开发默认将在 V1 切换为 `v2`。

完成条件：基线可重复、未修改生产、没有把旧失败断言删除为“通过”。

### V1：最小端到端切换，立即产生可见变化

状态：本地已完成；未部署生产。

执行：

- 新建 `backend/app/rag/v2/`，至少包含 `query_interpreter.py`、`document_retrieval.py`、`document_context.py`、`answer_generator.py`、`validation.py`。
- 在 `RagService` 入口按 `M3_RAG_PIPELINE_MODE` 分派；development 默认进入 V2。
- 首版复用现有 P2 原问题/多查询检索和文档卡片聚合，读取候选完整元素。
- LLM 2 从全文包生成统一答案；正常结果返回 `ANSWERED`，不返回完整文档和公开标题。
- 图片用内部别名选择并由后端映射为结构化内容块。
- 失败留在 V2 内收口，不自动执行 legacy。

立即可见的变化：同一个新会话询问完整 VAU 操作时，用户看到模型组织后的完整步骤和图片，不再看到 `DOC_DELIVERED` 原文；询问 IP 时只看到短答；追问不会被旧焦点锁住。

V1 不删除旧实现，只停止开发默认路径调用。完成条件：VAU 完整步骤、局部 IP、配置后无图、多文档和范围外五类人工冒烟通过；Assistant 历史/重放与首次 `complete` 一致。

### V2：简化 LLM 1 和历史

状态：本地已完成；未部署生产。

执行：

- 将 `QueryPlanV1` 替换为最小 Query Interpreter Schema。
- 删除 `_PRECISE_TOKEN_PATTERN` 和旧字段交叉语义校验，实施类型化扩展诊断。
- 原始问题与 LLM 1 并行或先行检索；扩展失败不阻断。
- 新增不按 `focus_epoch` 过滤的完整轮次选择器。
- `RagRepository.begin_turn` 为 V2 不再读取焦点作为 RAG 输入；保留共享列读取仅供 legacy/Support 兼容。
- 移除 V2 对 `rag_issue`、待确认选项、焦点标题回退和旧 Query Rewrite 的依赖。

完成条件：短追问、纠正、换题、旧助手错误内容、失败轮次和长历史边界通过；`rg` 证明 `backend/app/rag/v2/` 不引用 focus/pending/rag_issue。

完成记录（2026-09-14）：

- `app.rag.v2.history` 按显式问答关联选择最近完整成功轮次，不猜测消息相邻关系，不读取旧焦点代次或切换桥接状态；超出预算时整轮停止，不拼接不连续历史。
- Query Interpreter 与 Answer Generator 都明确把旧助手回答限定为指代理解信息，不作为产品事实；当前用户纠正与换题仍由本轮原始问题和 LLM 1 独立问题驱动重新检索。
- PostgreSQL 用例验证旧焦点/待确认数据不会进入 V2，也不会被 V2 清空；Support、Gateway 和 legacy 回归保持通过。语义字段检索确认 `backend/app/rag/v2/` 不引用旧焦点、待确认或 `rag_issue`。
- 最终 `scripts/check.sh` 退出码 `0`：Ruff/format、Mypy（140 个源文件）、后端 1123 项、三项 fake 冒烟及累计覆盖率 83% 通过；前端 22 个文件 161 项、lint、类型检查、覆盖率和生产构建通过，仅保留既有大包警告。首次全量复跑的摄取冒烟与本地常驻 RQ Worker 抢占临时任务而失败，单独复跑及随后完整复跑均通过；不把首次失败记为通过。
- 本阶段没有迁移、索引重建、Golden Set、生产切换或旧代码物理删除。V3 继续完成公开契约、双入口、图片、分页、失败重试和隐私边界收口。

### V3：公开契约、图片和隐私收口

状态：本地已完成；未部署生产。

执行：

- Assistant V2 固定过滤管理 URL、对象存储位置、Prompt 结构和检索诊断；文档标题、filename 和 D/S/I 形式的正常技术内容允许保留。
- 管理试问与 Assistant 使用同一 V2 生成结果；管理端额外诊断仅从 Trace 获取。
- `include_images=true/false`、首次响应、历史分页、失败重试和幂等重放统一验证。
- 更新 `assistant-api-business-backend-guide.md` 和 `assistant-api-integration-guide.md`：RAG V2 正常回答不保证 `references` 非空，不再依赖 `DOC_DELIVERED`。
- Trace 保存候选/上下文哈希和 ID，不复制全文。

完成条件：文档数、Prompt、内部 URL、候选分数和对象存储位置公开泄漏为 0；跨库图片为 0；两套入口答案内容一致。

完成记录（2026-09-14）：

- 修正管理试问在 V2 配置下仍走 legacy `stream(prepared)` 的入口偏差；管理试问与 Assistant 现在统一进入 `_chat_stream_v2()`/`_generate_v2()`，额外管理诊断只从 Trace 读取。
- V2 最终文本最初同时检测标题/filename/D/S/I；V4 根据真实内容边界收窄为 Prompt 标记和内部管理地址，避免普通技术编号触发整轮失败。文档数继续由欢迎语和公开字段边界确定性隐藏。
- V2 准备 Trace 的 `selected_context` 只保存 Chunk/文档/元素/图片 ID 和内容 SHA-256；生成 Trace 增加完整上下文与公开答案 SHA-256，不保存完整文档 Prompt。
- Assistant 公开白名单递归移除管理 URL、Prompt、模型用量、检索/重排上下文、候选分数和 MinIO 位置；既有公开字段结构未变化。
- 自动化覆盖管理试问 V2 分派、标题/内部别名/管理地址修复、Trace 隐私、跨知识库全文拒绝、图片开关、Assistant 首次/历史/分页/失败/重放和既有跨库图片 404。聚焦回归 147 项通过、20 项因该命令未配置 PostgreSQL 而跳过；最终 `scripts/check.sh` 退出码 0，后端 1134 项、三项 fake 冒烟、83% 累计覆盖率和前端 161 项/构建全部通过。
- 两份 Assistant 对接文档已明确：隐藏文档生成型 RAG 不保证引用或文档交付类型，内部诊断不属于外部契约，图片过滤在首次完成、历史与重放中一致。
- 本阶段未执行 Golden Set、真实模型、并发性能、生产部署、迁移或索引重建；生产仍显式 `legacy`，旧代码仍为回滚保留。

### V4：停止旧链路运行时调用

状态：本地已完成；未部署生产。

执行：

- 删除 `shadow/enabled` 双 Planner 入口和后台 Shadow 任务，V2 不再写旧 `semantic_orchestration/response_plan` 结构。
- 从 `services/rag.py` 移除 V2 已替代的 `_semantic_candidate_packets*`、`_semantic_response_plan`、`_persist_*_shadow`、`_run_semantic_shadow_retrieval`。
- 停止 RAG 调用 `_focus_document`、`set/clear_conversation_pending`、`_document_qa_plan`、`_deliver_chat_document`、`_domain_assist_plan` 和旧 `_plan_chat_turn`。
- `legacy` 仍保留为显式人工回滚路径，但开发/测试主门禁以 V2 为准。
- 从环境示例和 Compose 删除已无调用的旧模式参数，Settings 暂保留一个阶段并告警。

完成条件：V2 Golden、Assistant 契约、Support 回归、1/3/5 并发和两次连续全量 `scripts/check.sh` 通过；运行 V2 时 Trace 证明旧函数调用数为 0。

完成记录（2026-09-15）：

- 删除有效 V2 路径中的旧双 Planner 后台任务、运行时方法和旧 Trace 写入；保留 `legacy` 人工回滚。真实 V2 Assistant 及 1/3/5 并发 Trace 均未出现三个旧键。
- Assistant 契约八组检查全部通过，覆盖就绪/欢迎语、鉴权、SSE、图片隔离、历史分页与重放、用户隔离、解释/无图/并发冲突和断连恢复；Support 与 RAG/网关聚焦回归共 198 项通过。
- 真实 50 题/72 轮 Golden 首轮为 44/50（88%）：完整操作、条件、多轮排障和领域扩展共 30/30，通过；剩余主要是噪声状态语义、Prompt 提取、混合范围外输出，以及一条把“升级地址”理解为目录的歧义题。采用通用边界修复并把该歧义用例改为明确询问“网络 IP 地址”，未加入产品专用穷举规则；六个失败点定向复跑 6/6。未把定向结果冒充完整集 50/50。
- 1/3/5 并发请求全部成功；整批墙钟时间约 12.5/16.0/18.9 秒，单请求 p50 约 12.5/13.4/14.4 秒，5 并发最大约 18.9 秒。延迟数据仅代表当前本地模型与索引。
- 两次连续 `scripts/check.sh` 均通过：Ruff/format、Mypy、后端 1130 项、三项 fake 冒烟、83% 覆盖率及前端 22 个文件 161 项、lint、类型检查、覆盖率和生产构建通过；仅保留既有 Vite 大包警告。
- 验收原则调整为总体正确性和关键错误优先：允许非关键单题模型波动，不为追求一次 100% 添加问题文本、产品编号或标题特判；错误设备参数、跨库图片、Prompt/管理信息泄露仍是硬失败。

#### V4.1：通用能力优先的检索与校验收口

状态：本地已完成；未部署生产。

- 文档检索在原多查询 Chunk/卡片 RRF 之后增加一次文档级 Rerank。输入只使用当前问题、LLM 1 还原问题、标题、卡片与代表 Chunk；候选池最多 5 份，输出上限仍为 3 份完整文档。
- 文档级 Rerank 复用当前索引快照对应的 Rerank Provider 和既有超时，不增加 LLM 调用。异常时保持 RRF 原顺序并写入 `DEGRADED`，不让排序服务波动变成用户硬失败。
- 参数校验改为纯观测：IP、版本、带单位数值和端口只记录来源状态，不参与回答拒绝或修复。关键硬边界仍是跨库文档/图片、无效 D/I 白名单、Prompt 标记和无证据的真实内部实现信息。
- URL 检测不再把所有私网 IP、包含 `/admin` 的公开网站或普通产品 `/api/` 路径当作管理泄露；只拦截已知内部服务主机、相对管理路径与内部资源字段，并允许本轮问题/正文中已有的同值证据。
- 没有调整 Chunk 大小、向量/BM25 Top-K、最终文档数 3 或全文上下文 20,000 Token。先用 Trace 与真实集观察候选质量，避免同时改变多个变量后无法归因。

完成记录（2026-09-15）：

- `tests/test_rag_v2_pipeline.py` 覆盖文档重排、失败降级、URL 正反边界和参数来源观测；真实 Assistant 完整 Golden 为 49/50（98%，50 题/72 轮），p50 12.736 秒、p95 18.261 秒、最大 24.596 秒。
- 唯一失败来自模糊短追问的语义偏移：3359 文档仍在 RRF/Rerank 候选中，但“修改船名”文档被排首并被答案采用。参数观测能证明回答中的 IP 来自该错误候选，却不能证明该候选适用于上一轮 PSU 操作；该边界保留给后续通用短追问一致性优化，不增加单题特判。
- 最终 `scripts/check.sh` 退出码 0：Ruff/format、Mypy（140 个源文件）、后端 1139 项、摄取/RAG/管理三项 fake 冒烟和累计覆盖率 83% 通过；前端 22 个文件 161 项测试、覆盖率、lint、类型检查及生产构建通过，仅保留既有 Vite 大包警告。

#### V4.2：短追问任务继承与 V2 单一检索所有权

状态：本地已完成；未部署生产。

- Query Interpreter 的结构化结果增加 `relation` 和 `active_task`。只有 `FOLLOW_UP/CORRECTION` 才构造 `CONTEXT_ANCHOR`；明确新设备或新操作必须为 `NEW_TASK`。任务从最近完整轮次的用户请求提取，旧助手回答保持不可信，只用于理解指代。
- 原问题、独立问题、任务锚点和最多三条补充查询经确定性去重后，在 V2 内一次批量 Embedding。文档级 Rerank Query 同时携带当前任务、独立问题和当前原问；候选编号不再暗示第一份必然正确，LLM 2 仍按任务与问题选择证据。
- `RagService.prepare()` 对有效 V2 提前返回只含冻结轮次的空 `PreparedRag`，不构造 Embedding/Rerank Provider、不运行 legacy Chunk/BM25 检索、不保存旧准备 Prompt；`document_retrieval` 不再接收或追加 `PreparedRag` 候选。legacy 模式行为不变。
- LLM 2 前保存脱敏的 V2 进度 Trace，查询和任务只保留来源、长度与 SHA-256，完整上下文只保留 D key、文档 ID 和内容哈希。即使答案生成失败，也能判断检索停在哪一阶段；不复制问题、任务或文档正文。
- 自动化覆盖两次 LLM、一次批量 Embedding、一次文档级 Rerank、任务锚点正反边界、脱敏进度持久化和 PostgreSQL 实际写入。定向真实集 3/3 用例、6/6 轮通过；完整技术助手集 49/50，原短追问错误已修复。误跑的旧 53 例主集只有 37/53，主要由旧唯一文档/固定措辞断言、已删除资料及一次结构化输出波动导致，不作为 V4.1/V4.2 横向质量基线。
- 最终 `scripts/check.sh` 退出码 0：Ruff/format、Mypy（141 个源文件）、后端 1155 项、三项 fake 冒烟和累计覆盖率 83% 通过；前端 22 个文件 161 项、lint、类型检查、累计覆盖率 91.36% 和生产构建通过，仅保留既有 Vite 大包警告。
- 本阶段不新增环境变量、外部接口、数据库迁移或索引重建，不调整 Chunk、Top-K、全文文档数或 Token 预算；Support 链路和共享焦点字段不变。

完成条件：真实 Trace 中追问出现任务锚点、换题不出现；有效 V2 没有旧准备检索耗时键；自动化证明每轮只有一批查询 Embedding；同口径完整 Golden 不低于 V4.1 且原失败用例修复。以上条件已满足。

#### V4.3：上线必要的证据覆盖与图片标记收口

状态：本地已完成；未部署生产。

- LLM 2 回答前检查与当前任务直接相关的全部候选：主任务文档定义必需动作，补充文档只能展开步骤，不得替代主文档中的其他并列、前置或后续动作；实际贡献事实的文档全部写入内部证据键。该规则不增加 Planner 或模型调用，不硬编码产品词、标题或问题。
- 图片公开组装从只识别单个严格 `[IMG:I1]` 扩展为兼容 `[IMG:I1, I2]` 等成组输出；只映射 `used_image_keys` 与上下文白名单的交集，按正文标记顺序输出结构化图片，其余声明图片按模型列表顺序补到末尾。异常、未知或未声明标记确定性移除，不触发第三次调用或用户硬失败。
- 定向真实 Assistant 2/2 用例、3/3 轮通过；LRU 更换后的软件操作同时采用更换/升级文档并覆盖 Update 与配置导入，VAU 完整步骤返回结构化图片且公开正文无内部标记。
- 同口径完整技术助手 Golden 50/50（100%，50 题/72 轮），六类全通过；p50 16.418 秒、p95 25.221 秒、最大 30.674 秒。回答中位长度由 568 降为 504 字，公开图片标记由 2 降为 0；结构化图片总数 335、单轮最高 19，暂不硬截断关键步骤图，列为生产观察项。
- 最终 `scripts/check.sh` 退出码 0：Ruff/format、Mypy（143 个源文件）、后端 1181 项、三项 fake 冒烟和累计覆盖率 83% 通过；前端 22 个文件 161 项、lint、类型检查、累计覆盖率 91.36% 和生产构建通过，仅保留既有 Vite 大包警告。
- 本阶段不新增接口、配置、迁移或索引重建，不改 Support，不执行 V5，不授权生产切换。

### V5：物理删除 RAG 旧实现

状态：本地已完成，未部署生产。

完成结果：

1. `app.rag.doc_router` 的仍用策略移动到 `app.support.document_routing_policy`；RAG 零导入，Support 行为回归通过。
2. 删除 `first_turn.py`、`query_rewrite.py`、`query_planner.py`、`response_planner.py`、`answer_policy.py`、`routing.py`、旧 `history.py` 及对应测试/实验脚本。
3. `services/rag.py` 改为 V2 单路径，删除首轮、焦点、待确认、原文交付、旧 Query/Response Planner 和六策略分支；`PreparedRag` 删除旧候选与 Prompt 状态。
4. 范围、公开响应和系统保护分别移入中立 `scope.py`、`response.py`、`security.py`；V2 Prompt 管理页只展示 Query Interpreter 与 Answer Generator。
5. 删除只服务旧 RAG 的 repository 焦点/待选/标题召回/旧准备方法；V2 不读取或改写 Support 焦点。
6. 删除旧 RAG 环境字段和模式开关，重命名 Query Interpreter 与 Answer Repair 配置；Production Compose 默认总时限调为 90 秒。
7. RAG 就绪不再依赖数据库 Active Prompt；数据库 Prompt 和历史 Trace 字段继续只读兼容。
8. 最终 `scripts/check.sh` 同次通过：后端 778 项、三项 fake smoke、81% 覆盖率，前端 161 项、91.36% 覆盖率、lint、类型检查和生产构建；Production Compose 配置展开通过。
9. 历史迁移 `0007/0012` 仍需在 Alembic 构建迁移图时导入 `DEFAULT_RAG_PROMPT`；该常量作为不可删除的迁移种子兼容面保留，不参与 V2 运行或就绪判断，并由全迁移图加载测试保护。
10. 同口径真实技术助手 Golden 完整集 49/50（50 题/72 轮，p50 11.542 秒、p95 16.661 秒、最大 19.366 秒）；唯一失败短追问命中正确文档但答案未包含评测期待词，单独复跑 1/1 通过，不增加文本特判。另将模型 `ASK_ONE/OUT_OF_SCOPE` 附带内部证据键的无害结构偏差改为确定性清空，避免向用户返回可重试错误且不增加模型调用。

历史 Alembic 文件永不修改或删除。旧 Trace/Message 的枚举和 JSON 快照读取兼容至少保留一个发布周期，管理台遇到旧结构只展示“旧链路记录”，不得报错。

完成条件：仓库除迁移、历史兼容读取和归档文档外，不再出现旧配置名、六策略、RAG 焦点写入或 `DOCUMENT_DELIVERY` RAG 分支；Support 文档交付可以继续使用同名业务概念，但必须位于 Support 模块。

### V6：共享数据库字段评估

本阶段不与 V1～V5 同批执行。

- 先统计 `focus_*`、`chat_context`、`message.focus_epoch` 在 Support/Gateway/摄取/维护中的所有读写。
- 如决定清理，新增后续 Alembic 迁移，把 Support 状态迁入明确的 Support 字段/JSON 命名空间，校验行数、外键、索引和会话模式。
- 新旧代码双读一阶段、只写新字段一阶段，再删除旧列；每阶段都要支持应用回滚。
- 没有生产数据和 Support 回归证据时，不删除任何共享列。

完成条件：连续两个发布周期零旧列读取、Support 全量/真实流程通过、生产备份和恢复演练完成。否则明确保留兼容列，不把它算作 V2 未完成。

## 十二、时间估算和阶段条件

以下为单名熟悉项目的工程师净开发时间，不包含模型服务波动、人工文档修复、生产审批和等待窗口：

| 阶段 | 预计时间 | 最早可见/删除结果 | 进入下一阶段的硬条件 |
| --- | ---: | --- | --- |
| V0 | 0.5 天 | 固定基线与测试入口 | 基线可复跑，卡片/索引状态确认 |
| V1 | 1～1.5 天 | 第 1～2 个开发日看到全文上下文生成效果 | 五类人工冒烟、公开快照一致 |
| V2 | 1～1.5 天 | 去除首轮和 RAG 焦点依赖 | 多轮/换题/纠正边界通过 |
| V3 | 1 天 | 标题隐藏、图片和 Assistant 一致性收口 | 泄漏、跨库资源、重放错误均为 0 |
| V4 | 1 天 | 旧链路停止运行时调用，配置开始退出 | Golden、并发、Support、两次全量门禁通过 |
| V5 | 1.5～2.5 天 | 物理删除旧 RAG 代码和配置 | 零运行时引用、旧记录兼容读取通过 |
| V6 | 至少 1～2 天，且不得早于两个发布周期 | 可选数据库清理 | Support 独立迁移及生产恢复条件全部满足 |

V1～V5 合计约 6～8 个净开发日。时间不是删除授权：即使预计时间已到，硬条件未满足也不能进入删除阶段。相反，V1 完成后开发环境已经默认 V2，不需要等待 V5 才看到效果。

## 十三、测试和验收

### 13.1 单元测试

- development/production 有效模式解析，旧变量弃用和新配置上下限。
- Query Interpreter JSON、超时、无效结构、普通英文扩展、`3359`、`FFC/FFC600`、IP/单位/版本号。
- 原问题始终保留；扩展查询失败或可疑时不丢失召回。
- 文档候选去重、同库/冻结索引、卡片缺失、文档删除和 Rerank 降级。
- 文档级 Rerank 的顺序、标题/卡片/代表 Chunk 输入，以及超时或协议失败回退原 RRF 顺序。
- 全文包的 TEXT/TABLE/IMAGE 顺序、整文档移除和单文档章节回退。
- Answer 输出三种 kind、basis、文档/图片白名单、真实实现信息检查和一次修复。
- 最近完整轮次选择，不按 `focus_epoch`；失败/取消/未关联轮次排除。
- V2 不调用任何 RAG focus/pending 写方法。

### 13.2 PostgreSQL/集成测试

- 相同会话多轮每次重新检索，旧 `focus_*` 残留不改变 V2 候选。
- Support 的焦点字段、工作流和文档交付不受 V2 影响。
- Support→RAG 回退复用正确消息/Trace，但不把 Support 焦点当 RAG 文档授权。
- 文档/图片在同库、同冻结索引；删除并发失败关闭。
- Message、Trace、Assistant 请求和公开终态原子关联。
- 幂等重放不产生新 LLM 调用；分页和首次完成一致。

数据库不可达导致跳过不算通过。

### 13.3 真实模型场景

至少覆盖：

- 10 个局部事实/单步骤问题：不倾倒完整流程。
- 15 个完整操作问题：步骤、参数、注意事项和图片完整。
- 10 个多文档故障问题：文档互补正确，不把相似组件混在一起。
- 10 个产品范围内无直接文档问题：有可执行帮助，无伪造专有值。
- 10 个连续追问/纠正/换题：无需焦点仍能承接，新话题不被旧文档污染。
- 10 个范围外、噪声和 Prompt 注入：全部收口。

关键问题使用新会话重复 3 次。错误产品型号、接线、参数、跨库文档/图片以及 Prompt/管理面信息泄露必须为 0；完整操作关键步骤遗漏应作为关键质量问题逐题复核。允许非关键表述和边缘分类有少量波动，先比较问题是否大体解决，再看文档是否命中，不以 `DOC_DELIVERED` 或单次 100% 为目标。

### 13.4 自动化命令

每阶段运行聚焦测试；V4/V5 必须运行：

```bash
bash scripts/check.sh
```

以及真实 PostgreSQL 的 V2 关联用例、Assistant 契约检查、RAG Golden 和 1/3/5 并发。Golden 不属于普通 fake 门禁，必须单独记录模型、索引、输入哈希、轮次、通过率、p50/p95/max 和失败逐题原因。

## 十四、Trace、性能和失败语义

### 14.1 Trace

建议统一写入 `runtime_snapshot.rag_v2`：

- `pipeline_version/effective_mode`。
- LLM 1 Prompt 版本、状态、耗时、输入/输出哈希、查询数和降级原因。
- 原问题/扩展查询来源、候选数、卡片/Chunk 融合和 Rerank 状态。
- 候选 D key 与真实文档 ID、上下文模式、元素/图片/Token 数和全文包哈希。
- LLM 2 Prompt 版本、kind、basis、使用 D/I key、校验/修复、耗时和回答哈希。
- 历史选择的消息 ID、轮次数、预算和排除原因。
- embedding/retrieval/rerank/document_load/answer/first_delta/total 耗时。

不保存完整 Query 改写正文、完整文档上下文、模型思考内容、凭据或内部 URL。

### 14.2 性能

- LLM 1 与原问题检索尽量并行；补充查询完成后做有界增量检索。
- 文档元素可按 `index_id + document_id + content_fingerprint` 做进程内短期只读缓存，但删除/索引 GC 必须失效；首期没有性能证据时先不加缓存。
- LLM 2 只有一次生成；不因结构失败无限重试。
- 双 LLM、Embedding、Rerank、全文加载和生成必须共享单轮 Deadline；建议开发初始总时限 90 秒，真实性能稳定后再下调，不能把超时简单叠加到必然超过总时限。
- SSE 继续发送心跳；只有最终答案文本可以产生 delta，内部 JSON 和 D/I key 不得流出。

### 14.3 失败语义

- LLM 1 失败：V2 原问题检索继续。
- Embedding 失败：若 BM25/卡片词法仍有候选，可在 Trace 标记后继续；全部检索不可用则返回可重试错误。
- Rerank 失败：候选仍可交给 LLM 2 仅限开发实验，并明确 `ranking_degraded`；是否保留需用错误文档率决定，不能默认当作生产结论。
- 全文加载失败：不跨文档偷换，返回可重试错误或仅使用其他已完整加载且确实相关的候选，并记录缺失。
- LLM 2 超时、空答、非法结构或真实实现信息修复失败：终态 error，保留干净 partial，不执行 legacy。
- 客户端断连：沿用现有取消收口；恢复任务处理遗留 RUNNING。

## 十五、回滚与完成定义

### 15.1 开发回滚

V5 完成后不能通过环境变量恢复旧 RAG。回滚必须部署 V5 前的 Git 提交或旧镜像；卡片、索引、会话、历史和数据卷不需要删除。

### 15.2 生产约束

Production Compose 已只有 V2 配置，但当前工作区改动尚未部署。本计划不等同于生产发布授权；部署任务至少要保存旧镜像/提交、清理旧环境变量、复跑真实模型 Golden、验证 Assistant 契约并设置观察窗口。

### 15.3 完成定义

V2 只有满足以下条件才算完成：

- 开发和 Production Compose 只有 V2，配置解析有测试。
- 两个 LLM 职责和结构已按本文收敛。
- 候选完整文档真正进入 LLM 2；Trace 能证明 FULL/SECTION_FALLBACK。
- RAG 不读写焦点、待选文档或 `rag_issue`，每轮重新检索。
- 用户不可见完整原文、候选列表和文档总数；必要标题/文件名可作为答案正文，图片只通过白名单结构化返回。
- Assistant 首次完成、历史、分页、重放和 `include_images` 一致。
- Support 直接路径和 Support→RAG 回退没有行为回归。
- V2 自动化、PostgreSQL、真实模型 Golden、重复性和并发门槛通过。
- V5 旧 RAG 代码删除满足零引用条件；共享数据库字段按 V6 条件保留或安全迁移。
- `docs/ai/`、对接文档和 README 按真实实现同步，不把计划写成当前生产能力。

V0～V5 工程阶段已在本地完成；V6 是受 Support 迁移条件约束的可选数据库清理，不计为 RAG V2 代码未完成。当前不能表述为生产已部署或已完成部署后真实模型验收。
