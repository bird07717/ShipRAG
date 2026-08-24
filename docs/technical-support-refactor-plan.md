# 对话式技术支持系统重构完整计划

状态：提案，待评审  
适用仓库：Enterprise RAG Platform / Shipdemo  
编制日期：2026-08-24  
目标版本：V2 Technical Support  
事实来源优先级：当前源代码与 Alembic 迁移 > 当前 OpenAPI > 本计划

## 1. 执行摘要

本项目不建议另起仓库重写。建议保留当前项目中已经具备的文档入库、图片处理、不可变索引、任务、会话、模型配置、密钥管理、Trace、管理后台和生产部署能力，在当前仓库内新增独立的 `support` 技术支持领域，并由新的 `TechnicalSupportService` 逐步接管正式 `/api/v1/chat` 流量。

当前产品的真实业务模型不是通用知识问答，而是：

```text
一个知识库约 50～60 篇短技术文档
每篇文档对应一个完整问题和完整解决方案
文档中的文字、表格和图片共同构成受信事实源
```

目标对话模型为：

```text
首轮：识别问题 → 完整投递对应文档 → 设置聚焦文档
后续：围绕聚焦文档 → LLM 解释、拆解、陪伴执行和受限诊断
转题：识别可能的其他文档 → 用户确认 → 切换并完整投递新文档
越界：文档不足 → 收集最小诊断信息 → 低风险检查 → 必要时升级人工
```

现有 Generic RAG 管线不会立即删除，而是从正式 Chat 主路径移出，继续服务于 Playground、诊断、A/B 对比和未来长文档或跨文档场景。待新链路通过真实评测和生产观察后，再删除实际无人使用的 Query Rewrite、Chunk Retrieval、RRF、Rerank、Context Expansion 和通用 RAG Chat 逻辑。

本次重构的本质是：

> 保留平台底座，在当前项目中重写问答核心；让产品从“通用多模态 RAG”收敛为“以完整技术文档为事实锚点的对话式技术支持系统”。

## 2. 背景与问题定义

### 2.1 当前产品需求

企业软件后台把用户问题转发给本项目，本项目返回：

- 问题对应的完整技术解决方案；
- 文档中的原始文字、表格和图片；
- 后续基于同一文档的解释和步骤指导；
- 用户遇到文档外现象时的受限诊断；
- 必要的澄清、文档切换或人工升级建议；
- 可供企业后台审计和关联的 Conversation、Message、Trace 和 Index 信息。

企业软件负责最终用户界面、业务身份和可能的图片代理；本项目负责知识文档、对话式技术支持、事实边界和结构化响应。

### 2.2 当前架构与需求的偏差

当前正式 Chat 在决定文档交付或聚焦问答之前，通常先执行：

```text
Query Rewrite
→ Query Embedding
→ BM25 Chunk Search
→ Vector Chunk Search
→ RRF Fusion
→ VL Rerank
→ Neighbor Expansion
→ Context Selection
→ Prompt Build
→ Chat Plan
```

对当前短文档、一文档一问题的语料而言，这会产生不必要的复杂度：

1. 正确文档已经完整，却先被切成多个 Chunk；
2. 多篇主题相近的文档容易同时进入上下文；
3. LLM 需要从零散片段重建原文流程；
4. 图片可能脱离原始步骤位置；
5. 首轮完整投递仍支付 Embedding、Rerank 和上下文构建成本；
6. 已聚焦文档的后续追问仍可能重复执行全库检索；
7. `RagService` 同时承担 Generic RAG、文档路由、会话状态、文档投递、后续支持、SSE 和 Trace，职责过度集中；
8. 当前回答模式偏通用问答，无法充分表达文档事实、通用解释、待验证假设和风险边界。

### 2.3 已确认的质量基线问题

当前 `golden-set/golden-set.jsonl`：

- 不是合法 JSONL；
- 只有一个历史案例；
- 保存的是一次回答和完整 Trace，不是可自动执行的规范评测集；
- 缺少正确文档、关键 Element、关键图片、必答点、禁答点、风险等级和期望对话状态。

因此，现有工程测试能够验证系统按设计运行，但不能证明真实回答质量。重构前必须先建立新的质量基线。

## 3. 重构决策

### 3.1 采用当前仓库内重构

不新建项目，原因如下：

- 文档上传、校验、MinIO、Element 和 Image Asset 可以直接复用；
- Knowledge Index 的构建隔离和原子发布仍然需要；
- Conversation、Message、Trace 和请求关联能力仍然需要；
- LLM、OCR、Vision Provider 及密钥管理仍然需要；
- FastAPI、PostgreSQL、Redis/RQ、Alembic、Nginx、管理后台和测试门禁仍然需要；
- 全量重写会重新引入已解决的安全、幂等、任务、发布和数据一致性问题。

### 3.2 采用绞杀式重构

不继续在现有 `RagService` 中叠加更多分支，也不一次删除旧链路。采用以下迁移方式：

```text
阶段 A：新增 TechnicalSupportService，旧 Chat 仍可用
阶段 B：新旧链路 Shadow/A-B 对比
阶段 C：TechnicalSupportService 接管 /chat
阶段 D：RagService 只服务 /rag/playground
阶段 E：根据真实使用删除无人使用的旧 Chat 能力
```

### 3.3 Generic RAG 降为可选能力

Generic RAG 保留用于：

- RAG Playground；
- 管理员诊断和参数实验；
- 未来长文档；
- 未来跨文档综合；
- 旧链路回放；
- Shadow 对比。

Generic RAG 不再定义正式产品 Chat 的行为。

## 4. 目标与非目标

### 4.1 本次重构目标

1. 首轮问题可以可靠映射到一篇解决方案文档；
2. 明确匹配后零回答 LLM、零 Chunk RAG 完整投递文档；
3. 后续对话始终以聚焦文档为产品事实边界；
4. LLM 可以解释术语、拆解步骤并陪伴用户执行；
5. 文档外现象可以进入受限诊断，而不是立即拒答或自由猜测；
6. 文档事实、通用解释、一般指导和待验证假设必须可区分；
7. 无文档依据的高风险操作必须阻止或升级人工；
8. 问题转移时必须确认后切换文档，不能静默混合；
9. 文字、表格和图片以原始顺序结构化返回；
10. Conversation Workflow 必须幂等、可审计、并发安全；
11. 企业后台获得稳定、可版本化的 API 契约；
12. 新旧链路可以安全切换和回退。

### 4.2 本次重构非目标

- 不建设通用 Agent 或工具调用平台；
- 不实现开放网络搜索；
- 不允许 LLM 自动操作设备；
- 不自动提交工单或联系外部人员，除非后续单独授权；
- 不建设多租户完整 IAM，但为 API Client 与 Knowledge Base 授权预留边界；
- 不在第一阶段删除所有 Chunk、Embedding、BM25 和 Rerank 数据结构；
- 不在本次重构中迁移 Embedding 维度；
- 不把 LLM 编译结果当成原始事实源；
- 不支持未经确认的跨文档混合回答；
- 不把“模型认为可能”持久化成“用户已确认事实”。

## 5. 核心设计原则

### 5.1 完整文档是首轮交付单元

问题明确对应文档后，按 `document_element.sequence_no` 返回完整内容，不通过 LLM 改写，不通过 Chunk 重建。

### 5.2 聚焦文档是后续事实锚点

Conversation 一旦设置 `focus_document_id`，后续普通追问直接加载完整文档，不重新执行全库 Chunk RAG。

### 5.3 事实边界不等于只能复述

LLM 可以：

- 用通俗语言解释；
- 拆解复杂步骤；
- 解释命令和术语的一般含义；
- 根据用户当前进度指出下一步；
- 补充操作系统、网络工具等通用操作方法；
- 提出低风险、可逆或只读的诊断检查；
- 生成最小必要澄清问题；
- 组织人工升级信息。

LLM 不可以：

- 编造产品参数、密码、目录、命令或兼容性；
- 把通用知识描述为当前产品确定事实；
- 把可能原因描述为已确认根因；
- 无文档依据提供固件写入、格式化、拆机、接线、断电或硬件更换操作；
- 静默切换或混合其他文档；
- 把未确认步骤标记为已完成。

### 5.4 首轮完整、后续相关

- 首轮返回目标文档的全部图片；
- 后续回答只返回模型引用且经过服务端验证的相关图片；
- 企业后台需要时仍可通过文档引用访问全部内容。

### 5.5 不确定时收集信息，不伪造答案

优先询问能改变下一步判断的最小信息，例如：

- 当前步骤；
- 完整错误文本；
- 命令输出；
- 产品型号和版本；
- IP 和子网掩码；
- 指示灯状态；
- 现场截图。

### 5.6 高风险行为必须有文档依据

高风险建议必须关联当前文档 Element。没有依据时，结果必须转为澄清、低风险信息收集或人工升级。

## 6. 目标运行架构

```text
Enterprise Software Backend
            │
            ▼
      POST /api/v1/chat
            │
            ▼
 TechnicalSupportService
   │
   ├─ BeginSupportTurn
   │    ├─ 冻结 Active Index
   │    ├─ 创建/读取 Conversation
   │    └─ 读取 Focus 与 Workflow
   │
   ├─ 无 Focus
   │    └─ ProblemResolver
   │         ├─ document_id/problem_code
   │         ├─ pending choice
   │         ├─ canonical question/alias
   │         ├─ deterministic rules
   │         └─ LLM catalog classification
   │              ├─ MATCHED → DocumentDelivery
   │              ├─ AMBIGUOUS → Clarification
   │              └─ NO_MATCH → NoMatch
   │
   └─ 有 Focus
        └─ FocusedSupportAssistant
             ├─ Full Document Context
             ├─ Workflow State
             ├─ Recent History
             ├─ Structured LLM Output
             ├─ SupportPolicy
             ├─ SupportValidator
             ├─ Document Switch
             └─ Escalation
```

Generic RAG 独立保留：

```text
POST /api/v1/rag/playground
            │
            ▼
        RagService
            │
 Query Rewrite / Embedding / BM25 / Vector / RRF / Rerank / Context / LLM
```

## 7. 对话状态机

### 7.1 顶层状态

```text
ALIGNING
  ├─ MATCHED ───────────────→ DOCUMENT_DELIVERED → DOC_FOCUS
  ├─ AMBIGUOUS ─────────────→ CLARIFICATION_REQUIRED
  └─ NO_MATCH ──────────────→ NO_MATCH

DOC_FOCUS
  ├─ 解释文档 ──────────────→ DOCUMENT_EXPLANATION
  ├─ 询问步骤 ──────────────→ STEP_GUIDANCE
  ├─ 通用操作细节 ──────────→ GENERAL_OPERATION_GUIDANCE
  ├─ 文档外现象 ────────────→ DIAGNOSTIC_ASSISTANCE
  ├─ 信息不足 ──────────────→ NEED_MORE_INFORMATION
  ├─ 可能转题 ──────────────→ CONFIRM_DOCUMENT_SWITCH
  └─ 无依据高风险/无法继续 ─→ ESCALATION_REQUIRED
```

### 7.2 首轮文档解析优先级

```text
1. 请求 document_id
2. 请求 problem_code
3. Conversation pending choice
4. canonical_question 精确匹配
5. alias 归一化精确匹配
6. 产品/型号/故障码/动作确定性规则
7. LLM 对完整问题目录做闭集分类
8. AMBIGUOUS 或 NO_MATCH
```

第一版不新增文档级 Embedding。只有评测证明目录分类和 Alias 无法满足质量、成本或延迟目标时，才评估 BM25/Embedding 候选召回。

### 7.3 文档切换

聚焦状态下发现新问题时：

1. LLM 只能输出 `document_switch.query`，不能直接修改 Focus；
2. `ProblemResolver` 根据新问题查找候选文档；
3. 返回 `CONFIRM_DOCUMENT_SWITCH`；
4. 用户确认后才能更新 `focus_document_id`；
5. 切换成功后完整投递新文档；
6. 旧 Workflow 不得污染新文档流程。

## 8. 代码模块重构

### 8.1 新增目录

```text
backend/app/support/
├── __init__.py
├── models.py
├── repository.py
├── service.py
├── problem_resolver.py
├── document_delivery.py
├── focused_assistant.py
├── workflow_state.py
├── prompt.py
├── policy.py
├── validator.py
├── escalation.py
└── metrics.py
```

### 8.2 模块职责

#### `models.py`

定义：

- `ProblemProfile`
- `ProblemResolution`
- `ProblemDecision`
- `FocusedDocument`
- `DocumentContentBlock`
- `SupportMode`
- `EvidenceType`
- `RiskLevel`
- `SupportSection`
- `SupportDraft`
- `SupportResult`
- `WorkflowState`
- `WorkflowUpdate`
- `Clarification`
- `EscalationPackage`

#### `repository.py`

封装：

- Problem Profile 查询；
- Active Index 下的完整文档加载；
- Element 和 Image Asset 加载；
- Conversation Focus 和 `chat_context`；
- Message、Trace 和幂等记录；
- 文档切换事务。

#### `service.py`

正式 Chat 的唯一领域编排入口：

```python
class TechnicalSupportService:
    async def handle(...)
    async def stream(...)
```

#### `problem_resolver.py`

只负责问题到文档的闭集解析，不生成答案。

#### `document_delivery.py`

按原始 Element 顺序完整投递文档，零回答 LLM。

#### `focused_assistant.py`

使用完整聚焦文档、Workflow 和有限历史调用 LLM，生成结构化 Support Draft。

#### `workflow_state.py`

处理步骤状态、用户观察、待补充信息和并发更新。

#### `policy.py`

定义事实层级、风险分级、允许和禁止行为。

#### `validator.py`

校验 Evidence、Image、Risk、Workflow、Document Switch 和结构化输出。

#### `escalation.py`

生成可供企业后台提交人工支持的结构化升级包。

### 8.3 现有模块调整

#### `backend/app/services/rag.py`

- 保留 Generic RAG 和 Playground 编排；
- 移出 Chat 文档投递、Focus、文档切换和后续支持职责；
- 最终不再作为 `/chat` 的默认 Service。

#### `backend/app/rag/repository.py`

- 保留 Generic RAG 检索和 Trace 查询；
- Support 专用查询迁移至 `support/repository.py`；
- 迁移期间避免复制不一致 SQL。

#### `backend/app/rag/prompt.py`

- 保留 Generic RAG Prompt；
- 新 Support Prompt 独立放在 `support/prompt.py`。

#### `backend/app/rag/doc_router.py`

- 旧 Chunk 聚合路由仅供 Legacy Chat；
- 新 `ProblemResolver` 不依赖 Chunk 分数聚合。

#### `backend/app/rag/llm.py`

增加结构化 Completion 接口，并继续保留现有 Stream 接口。

#### `backend/app/api/rag.py`

- `/chat` 和 `/chat/stream` 切换为 `TechnicalSupportService`；
- `/rag/playground` 继续使用 `RagService`；
- 迁移期支持运行模式开关。

## 9. 数据模型计划

### 9.1 迁移 `0015_support_problem_profile`

新增表：

```sql
CREATE TABLE document_problem_profile (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES document_source(id),
    kb_id uuid NOT NULL REFERENCES knowledge_base(id),
    problem_code varchar(100) NOT NULL,
    canonical_question varchar(500) NOT NULL,
    aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
    keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
    product_name varchar(200),
    model_names jsonb NOT NULL DEFAULT '[]'::jsonb,
    task_type varchar(50),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
```

约束和索引：

- 唯一 `(kb_id, problem_code)`；
- 一个启用 Profile 必须指向同知识库未删除文档；
- Alias 必须是字符串数组；
- Model Names 必须是字符串数组；
- 同一知识库内启用 Alias 不允许规范化后完全冲突；
- 索引 `(kb_id, enabled)`；
- 索引 `document_id`。

### 9.2 Conversation State

第一版继续使用现有 `conversation.chat_context jsonb`，不立即新增多个列。

目标结构：

```json
{
  "support_version": 1,
  "focus": {
    "document_id": "uuid",
    "problem_code": "PDC_USB_UPGRADE",
    "index_id": "uuid"
  },
  "workflow": {
    "current_step": 4,
    "completed_steps": [1, 2, 3],
    "skipped_steps": [],
    "blocked_step": 4
  },
  "observations": [
    {
      "source": "USER",
      "value": "telnet连接超时",
      "created_at": "2026-08-24T00:00:00Z"
    }
  ],
  "collected_information": {
    "computer_os": "Windows 11",
    "computer_ip": "172.16.8.175",
    "subnet_mask": "255.255.255.0"
  },
  "missing_information": ["ping结果", "Telnet完整错误"],
  "pending_options": [],
  "escalation": {
    "required": false,
    "reason": null
  }
}
```

状态规则：

- `completed_steps` 只能保存用户明确确认的步骤；
- 用户陈述保存为 `source=USER`；
- 模型假设不得写入 User Observation；
- 切换文档时清理旧 Workflow 和待确认诊断信息；
- State Update 与 Assistant Message 在同一事务提交；
- 使用行锁或乐观版本防止并发覆盖。

### 9.3 Message 结构化内容

评审时决定采用以下之一：

方案 A，推荐：新增 `message.content_blocks jsonb`、`message.response_type`、`message.support_mode`，便于历史回放和企业后台重新拉取完整消息。

方案 B：结构化内容只保存在 Trace，Message 继续只存 Markdown 摘要。该方案迁移少，但历史消息 API 无法完整恢复图片和 Section。

默认决策采用方案 A，并在 `0015` 或独立 `0016_support_message_content` 中实现，避免将过多职责塞入单个迁移。

### 9.4 不修改历史迁移

- 不重写 `0001`–`0014`；
- 所有新 Schema 通过增量迁移实现；
- 不删除已有 Chunk、Embedding 或 Trace 数据；
- Downgrade 必须不破坏原始文档和历史 Conversation。

## 10. API 契约计划

### 10.1 请求

```http
POST /api/v1/chat
Authorization: Bearer <service-token>
Idempotency-Key: <enterprise-request-id>
X-Request-ID: <infrastructure-request-id>
Content-Type: application/json
```

```json
{
  "knowledge_id": "uuid",
  "question": "PDC怎么用U盘刷版本？",
  "conversation_id": null,
  "problem_code": null,
  "document_id": null,
  "choice_id": null,
  "user_id": "enterprise-user-123",
  "request_id": "enterprise-request-456",
  "include_images": true
}
```

字段规则：

- `document_id` 存在时优先，但必须校验 Knowledge Base 和 Active Index；
- `problem_code` 存在时优先于自然语言解析；
- `choice_id` 只能用于当前 Conversation 的 Pending Option；
- `request_id` 是企业业务关联 ID；
- `X-Request-ID` 是基础设施链路 ID；
- `Idempotency-Key` 防止重试产生重复消息和重复 LLM 费用。

### 10.2 首轮文档响应

```json
{
  "response_type": "DOCUMENT_DELIVERED",
  "support_mode": null,
  "answer": "【已提供文档】《如何使用U盘升级PDC》",
  "sections": [],
  "content": [
    {
      "type": "text",
      "sequence_no": 1,
      "text": "故障解决文档11—如何使用U盘升级PDC"
    },
    {
      "type": "image",
      "sequence_no": 2,
      "image_asset_id": "uuid",
      "caption": "Telnet终端版本检查截图",
      "url": "/api/v1/image-assets/uuid/content"
    }
  ],
  "images": [
    {
      "image_asset_id": "uuid",
      "sequence_no": 2,
      "mime_type": "image/png",
      "caption": "Telnet终端版本检查截图",
      "url": "/api/v1/image-assets/uuid/content"
    }
  ],
  "document": {
    "id": "uuid",
    "problem_code": "PDC_USB_UPGRADE",
    "title": "如何使用U盘升级PDC",
    "download_url": "/api/v1/documents/uuid/content"
  },
  "routing": {
    "method": "ALIAS",
    "confidence": 1.0
  },
  "conversation_id": "uuid",
  "message_id": "uuid",
  "trace_id": "uuid",
  "index_id": "uuid",
  "latency_ms": 45
}
```

### 10.3 后续技术支持响应

```json
{
  "response_type": "ANSWERED",
  "support_mode": "DIAGNOSTIC_ASSISTANCE",
  "answer": "当前文档只说明应通过Telnet连接172.16.8.150，没有提供连接失败的专用处理流程……",
  "sections": [
    {
      "type": "DOCUMENT_FACT",
      "title": "文档中已确认的信息",
      "content": "电脑IP应设置为172.16.8.175，子网掩码为255.255.255.0。",
      "evidence_element_ids": ["uuid"]
    },
    {
      "type": "GENERAL_GUIDANCE",
      "title": "可以进行的基础检查",
      "content": "可以先执行ping 172.16.8.150并记录结果。",
      "risk_level": "LOW",
      "disclaimer": "这是通用网络诊断方法，不是当前文档中的产品专用处理流程。"
    },
    {
      "type": "NEED_INFORMATION",
      "title": "请补充",
      "content": "请提供Telnet完整错误和ping结果。"
    }
  ],
  "content": [],
  "images": [],
  "document": {
    "id": "uuid",
    "problem_code": "PDC_USB_UPGRADE",
    "title": "如何使用U盘升级PDC"
  },
  "workflow": {
    "current_step": 4,
    "completed_steps": [1, 2, 3],
    "missing_information": ["ping结果"]
  },
  "clarification": null,
  "escalation": {
    "required": false,
    "reason": null
  },
  "conversation_id": "uuid",
  "message_id": "uuid",
  "trace_id": "uuid",
  "index_id": "uuid",
  "latency_ms": 1200
}
```

### 10.4 澄清和文档切换

```json
{
  "response_type": "CLARIFICATION_REQUIRED",
  "clarification": {
    "type": "CONFIRM_DOCUMENT_SWITCH",
    "message": "你现在的问题更接近《如何更换故障PDC》，是否切换？",
    "choices": [
      {
        "choice_id": "switch_1",
        "document_id": "uuid",
        "problem_code": "PDC_REPLACE",
        "title": "如何更换故障PDC"
      }
    ]
  },
  "conversation_id": "uuid",
  "trace_id": "uuid"
}
```

### 10.5 人工升级响应

```json
{
  "response_type": "ESCALATION_REQUIRED",
  "answer": "当前文档没有提供该异常的进一步处理流程。为避免设备损坏，建议暂停未在文档中说明的写入或拆机操作，并联系技术支持。",
  "escalation": {
    "required": true,
    "reason": "文档未覆盖Telnet服务不可用后的产品专用处理方法",
    "summary": {
      "problem_code": "PDC_USB_UPGRADE",
      "current_step": 4,
      "completed_steps": [1, 2, 3],
      "observations": ["Telnet连接超时", "Ping无响应"],
      "checks_performed": ["确认电脑IP", "确认网线连接"],
      "missing_information": ["设备指示灯状态"]
    },
    "trace_id": "uuid"
  }
}
```

### 10.6 SSE

正式后台集成以非流式 `/chat` 为主。保留 `/chat/stream` 时：

- `trace`：返回 Conversation/Trace；
- `status`：正在定位文档、分析步骤或执行安全校验；
- `source`：文档和图片引用；
- `message`：仅在能够保持结构一致时流式输出；
- `done`：必须包含经过完整校验的最终结构；
- `error`：结构化错误。

结构化 LLM 输出需要完整校验，因此不以逐 Token 流式为首要目标。

## 11. LLM 结构化输出

### 11.1 Provider 扩展

在现有 LLM Provider 上增加：

```python
async def complete_structured(
    prompt: str,
    schema: type[BaseModel],
) -> BaseModel:
    ...
```

执行规则：

1. 要求模型只输出 JSON；
2. 收集完整输出；
3. 使用 Pydantic 校验；
4. 格式错误时允许一次带校验错误摘要的重试；
5. 第二次仍失败则返回 `STRUCTURED_OUTPUT_INVALID`；
6. 不得把半截 JSON 或未验证文本直接发送给企业后台；
7. 不记录上游密钥和敏感 Header；
8. Trace 可保留脱敏后的原始输出摘要。

### 11.2 Support Draft

```json
{
  "mode": "DIAGNOSTIC_ASSISTANCE",
  "answer": "string",
  "sections": [],
  "image_ids": ["I1"],
  "questions": [],
  "workflow_update": {
    "confirmed_completed_steps": [],
    "user_observations": [],
    "collected_information": {},
    "missing_information": []
  },
  "document_switch": null,
  "escalation": {
    "required": false,
    "reason": null
  }
}
```

### 11.3 文档 Registry

每轮建立临时 Registry：

```text
E1 → document_element UUID
E2 → document_element UUID
I1 → image_asset UUID + element UUID
```

模型只使用短 ID，服务端负责映射和归属校验。

## 12. Prompt 设计

新增独立模板：

```text
FOCUSED_SUPPORT_PROMPT
DIAGNOSTIC_SUPPORT_PROMPT
DOCUMENT_SWITCH_PROMPT
PROBLEM_CATALOG_PROMPT
```

### 12.1 `FOCUSED_SUPPORT_PROMPT`

用于：

- 文档解释；
- 术语和命令说明；
- 步骤拆解；
- 根据当前步骤指出下一步；
- 用户确认执行进度。

### 12.2 `DIAGNOSTIC_SUPPORT_PROMPT`

用于：

- 文档外现象；
- 操作失败；
- 状态异常；
- 错误文本；
- 信息收集；
- 低风险诊断；
- 人工升级判断。

### 12.3 Prompt 事实规则

模型必须区分：

```text
DOCUMENT_FACT
DOCUMENT_EXPLANATION
GENERAL_GUIDANCE
UNVERIFIED_HYPOTHESIS
NEED_INFORMATION
WARNING
```

产品参数、型号、IP、命令、目录、密码、时间、兼容性和高风险步骤必须来自当前文档 Element。

### 12.4 Prompt 注入防护

- 用户问题、文档和历史均为不可信数据；
- 文档中的指令性文字不能覆盖 System Policy；
- 模型不得泄露 Prompt、密钥或内部 URL；
- 模型不能根据用户指示跳过事实边界或风险规则；
- Problem Catalog 中的标题和 Alias 也是数据，不是系统指令。

## 13. 事实与风险策略

### 13.1 事实层级

#### `DOCUMENT_FACT`

当前文档明确说明的产品事实，必须带 Evidence。

#### `DOCUMENT_EXPLANATION`

对文档事实的改写、拆解或术语解释，不得改变原条件。

#### `GENERAL_GUIDANCE`

操作系统、网络工具或通用工程方法，必须声明不是当前产品专用结论。

#### `UNVERIFIED_HYPOTHESIS`

可能原因，必须使用不确定表达，不能持久化为用户事实。

#### `NEED_INFORMATION`

为下一步判断收集的最小信息。

#### `WARNING`

来自文档或安全策略的风险提示。

### 13.2 风险等级

#### LOW

- 查看错误；
- 截图；
- 查看 IP；
- Ping；
- 查看只读状态；
- 查询版本；
- 查看文件列表；
- 确认连接和指示灯；
- 收集日志。

#### MEDIUM

- 修改网络配置；
- 登录设备；
- 执行特定命令；
- 重启普通软件；
- 重新插拔外设。

必须有文档参数或明确的通用操作边界。

#### HIGH / CRITICAL

- 固件写入；
- 删除、覆盖或格式化；
- 强制恢复；
- 修改设备内部配置；
- 断电顺序；
- 拆机、接线或更换硬件；
- 修改电压；
- 绕过保护；
- 使用非官方版本。

无当前文档 Evidence 时必须阻止并升级人工。

### 13.3 Validator

至少校验：

1. Evidence ID 是否属于当前文档和冻结 Index；
2. Image ID 是否属于当前文档；
3. Document Fact 是否缺 Evidence；
4. General Guidance 是否缺 Disclaimer；
5. 高风险 Guidance 是否有 Document Evidence；
6. 输出中的 IP、命令、目录和文件名是否出现文档外新增；
7. 模型是否试图直接切换文档；
8. 模型是否把未确认步骤标记完成；
9. 模型是否把 Hypothesis 保存成 User Observation；
10. JSON 是否完整、输出是否疑似截断；
11. Escalation 是否包含最小必要上下文；
12. 所有公开 ID 是否可解析且未跨知识库。

第一版不声称能够自动证明每一句话都完全有依据。采用结构化事实层级、Evidence Registry、确定性规则和真实评测逐步降低风险。高风险场景可按需增加第二模型审核，但不默认对全部请求增加成本。

## 14. 图片处理与交付

### 14.1 首轮

- 返回选中文档全部图片；
- 保持 Element 顺序；
- 返回 `image_asset_id`、Caption、MIME、尺寸和访问 URL；
- 不依赖 LLM 选择；
- 不在 Chat JSON 中返回 Base64。

### 14.2 后续

- Prompt 中图片使用 `I1`、`I2`；
- 模型只返回相关图片短 ID；
- 服务端验证后映射真实 Image Asset；
- 无合法图片 ID 时不返回图片；
- 用户请求“重新看完整文档”时可以再次返回全部内容。

### 14.3 企业交付

推荐企业软件后台代理图片：

```text
User Client
  → Enterprise Backend
  → 本项目 image_asset content API（携带服务凭据）
```

备选方案是短期签名 URL，不允许向用户客户端暴露长期 Service Token。

## 15. Trace 与指标

### 15.1 Support Trace

Trace 增加：

```json
{
  "focus_document_id": "uuid",
  "problem_resolution": {},
  "support_mode": "DIAGNOSTIC_ASSISTANCE",
  "document_claims": [],
  "general_guidance": [],
  "hypotheses": [],
  "requested_information": [],
  "workflow_before": {},
  "workflow_after": {},
  "risk_flags": [],
  "validation_result": {},
  "document_switch": null,
  "escalation": null
}
```

可以先存入现有 JSONB 字段，待查询模式稳定后再决定是否新增专用列。

### 15.2 质量指标

新增：

- 首轮 Document Hit@1；
- 标准问题和 Alias 命中率；
- LLM Catalog 命中率；
- 澄清率和澄清后成功率；
- 文档切换准确率；
- 首轮 Element 完整率；
- 首轮 Image 完整率；
- Document Fact Evidence 缺失率；
- General Guidance 占比；
- 未验证假设占比；
- 高风险拦截率；
- 人工升级率；
- Structured Output 重试率；
- Workflow 冲突率；
- 平均每轮模型调用次数；
- 首轮与后续 P50/P95/P99 延迟；
- 每轮 Token 和费用。

## 16. 分阶段实施计划

### 阶段 0：质量基线和契约冻结

交付物：

- 修复并拆分 Golden Set；
- 至少 200 个问题路由案例；
- 初始后续对话和安全案例；
- 当前 Legacy 基线报告；
- Support API 契约评审稿；
- 事实层级和风险政策评审稿。

验收：

- JSONL 全部可解析；
- 数据集能够在 CI 中执行；
- 当前系统基线可重复；
- API 和枚举完成评审。

回退：不涉及生产行为。

### 阶段 1：Support 领域骨架

交付物：

- `app/support` 模块；
- Domain Models；
- `TechnicalSupportService` 空编排；
- Support Repository 接口；
- API Dependency；
- Feature Flag。

验收：

- 不改变 Legacy Chat；
- Ruff、Mypy、Pytest 通过；
- 模块边界无循环依赖。

### 阶段 2：Problem Profile 和问题解析

交付物：

- `0015_support_problem_profile`；
- Profile CRUD API；
- 管理台编辑能力；
- 确定性 Resolver；
- LLM Catalog Resolver；
- Resolver Trace。

验收：

- `problem_code` 和 Alias 确定性命中 100%；
- 非法文档 ID 和跨知识库 ID 被拒绝；
- LLM不能返回目录外文档；
- 无匹配和多匹配正确返回。

### 阶段 3：首轮文档完整投递

交付物：

- `DocumentDelivery`；
- 完整 Element Blocks；
- 全部 Image Assets；
- Focus 更新；
- Message 和 Trace；
- 幂等支持。

验收：

- 首轮零回答 LLM；
- 首轮零 Embedding、RRF 和 Rerank；
- Text/Table/Image 完整率 100%；
- Element 顺序 100%；
- Focus 与 Message 同事务；
- 重复请求不重复创建消息。

### 阶段 4：Focused Support 基础能力

交付物：

- 完整文档 Context Builder；
- `FOCUSED_SUPPORT_PROMPT`；
- 结构化 Completion；
- `DOCUMENT_EXPLANATION`；
- `STEP_GUIDANCE`；
- Evidence Registry；
- 基础 Validator。

验收：

- 后续普通追问不执行全库 RAG；
- 产品事实能关联 Element；
- 图片 ID 可验证；
- 文档解释不改变参数和警告；
- 不完整 JSON 不作为成功响应。

### 阶段 5：Workflow State

交付物：

- Workflow State Model；
- State Reducer；
- 用户观察和已完成步骤更新；
- State 持久化；
- Conversation 并发控制。

验收：

- 用户明确完成的步骤正确记录；
- 模型推断不能记录为完成；
- 用户观察和模型假设严格区分；
- 并发请求不能丢失更新；
- 切换文档清理旧状态。

### 阶段 6：诊断和风险策略

交付物：

- `DIAGNOSTIC_SUPPORT_PROMPT`；
- `GENERAL_OPERATION_GUIDANCE`；
- `DIAGNOSTIC_ASSISTANCE`；
- `NEED_MORE_INFORMATION`；
- Risk Policy；
- 高风险拦截；
- Validator 扩展。

验收：

- 文档外现象不会直接生成确定根因；
- 只询问最小必要信息；
- 通用指导带明确边界；
- 高风险无依据操作被拦截；
- 低风险诊断可正常输出。

### 阶段 7：文档切换

交付物：

- Document Switch Detection；
- Resolver 二次调用；
- `CONFIRM_DOCUMENT_SWITCH`；
- Pending Choice；
- 用户确认后原子切换；
- 新文档完整投递。

验收：

- 不静默切换；
- 不混合两篇文档；
- 用户拒绝后保持原 Focus；
- 用户确认后清理旧 Workflow；
- 另一个 Knowledge Base 的文档不能成为候选。

### 阶段 8：人工升级

交付物：

- Escalation Policy；
- Escalation Package；
- 企业 API 字段；
- Demo Chat 展示；
- Trace 支持。

验收：

- 文档不足和高风险场景正确升级；
- 升级包不包含密钥和内部敏感配置；
- 升级包包含当前步骤、观察、已执行检查和 Trace；
- 不自动向外部系统提交工单。

### 阶段 9：管理台和 Trace

交付物：

- Problem Profile 编辑；
- Problem Resolver 测试；
- Support Mode 展示；
- Workflow 展示；
- Evidence、Risk、Switch、Escalation 展示；
- 新质量指标。

验收：

- 管理员可以不改数据库直接维护 Alias；
- Trace 可以解释每轮回答来源和决策；
- UI 不暴露模型密钥或内部敏感数据。

### 阶段 10：企业集成

交付物：

- Idempotency-Key；
- 业务 Request ID；
- 图片代理或签名 URL 方案；
- 稳定错误码；
- 超时和重试说明；
- API Client Knowledge Base 授权设计；
- 联调脚本和契约测试。

验收：

- 企业后台重试不会重复生成；
- 图片不需要暴露 Service Token；
- 错误码和 HTTP 状态一致；
- 调用方不能访问未授权知识库。

### 阶段 11：Shadow 和切换

新增：

```text
CHAT_EXECUTION_MODE=legacy_rag|technical_support|shadow
CHAT_SHADOW_SAMPLE_RATE=0.0..1.0
```

Shadow 原则：

- 只返回主链路结果；
- 影子链路结果仅存脱敏对比 Trace；
- 控制真实模型成本；
- 不重复更新 Conversation；
- 不产生外部副作用。

验收门禁：

- 路由和投递达到目标；
- 后续事实错误率下降；
- 高风险越界满足要求；
- 文档切换和升级正确；
- 延迟、成本和错误率可接受；
- 回退开关完成演练。

### 阶段 12：Legacy 清理

在新链路稳定后：

- `/chat` 完全切换 `TechnicalSupportService`；
- `RagService` 仅服务 Playground；
- 删除旧 Chat 专用 Query Rewrite；
- 删除旧 Chat Chunk Retrieval 编排；
- 删除旧 Chat RRF/Rerank/Context Expansion；
- 删除旧 Chat Prompt 和模式后验；
- 保留历史 Trace 读取兼容；
- 评估是否继续保留 Chunk、Embedding、BM25 和 Rerank 基础能力。

删除任何数据表、扩展或生产依赖前必须单独评审和迁移，不在本阶段直接物理清理历史数据。

## 17. 测试计划

### 17.1 Problem Resolver 单元测试

- document_id；
- problem_code；
- canonical question；
- Alias；
- 大小写、空格、标点和全角半角归一化；
- 产品、型号、故障码和动作；
- Pending Choice；
- 多项匹配；
- 无匹配；
- 模型返回非法 ID；
- 跨知识库隔离；
- 已删除或禁用 Profile。

### 17.2 Document Delivery 单元测试

- TEXT；
- TABLE；
- IMAGE；
- Element 顺序；
- 全部图片；
- 文档引用；
- Active Index 冻结；
- 文档已删除；
- Index 不一致；
- include_images=false；
- 幂等请求。

### 17.3 Focused Assistant 单元测试

- 文档解释；
- 命令解释；
- 步骤拆解；
- 询问下一步；
- 用户跳过步骤；
- 用户否认完成步骤；
- 通用操作系统指导；
- 文档外现象；
- 最小信息收集；
- 图片引用；
- 输出截断；
- JSON格式错误。

### 17.4 Workflow 单元测试

- 用户明确完成步骤；
- 模型推断不得完成；
- User Observation；
- Assistant Hypothesis；
- Collected Information；
- Missing Information；
- 并发更新；
- 重复请求；
- 文档切换；
- Conversation 恢复。

### 17.5 Policy 和 Validator 单元测试

- 合法 Evidence；
- 不存在的 Element；
- 另一个文档的 Element；
- 非法 Image；
- General Guidance 缺 Disclaimer；
- Document Fact 缺 Evidence；
- 文档外 IP、命令、目录和文件名；
- 高风险无依据；
- 未经确认直接切换；
- Hypothesis 写成事实；
- 模型输出 Prompt 注入内容；
- Escalation 内容脱敏。

### 17.6 集成测试

核心场景：

```text
首轮提问
→ 找到文档
→ 完整投递
→ 设置Focus
→ 用户询问某一步
→ LLM解释
→ 用户报告异常
→ 收集信息
→ 低风险检查
→ 发现另一个问题
→ 请求切换
→ 用户确认
→ 投递新文档
→ 高风险未覆盖
→ 生成人工升级包
```

### 17.7 API 契约测试

- Bearer Token；
- Knowledge Base 授权；
- Idempotency-Key；
- X-Request-ID；
- 企业 Request ID；
- Conversation ID；
- Choice ID；
- 错误码；
- 超时；
- 重试；
- SSE 断连；
- 图片获取；
- Active Index 切换。

### 17.8 质量评测

至少覆盖：

- 200–300 个问题路由案例；
- 每篇文档至少一个解释问题；
- 每篇流程文档至少一个步骤细化问题；
- 高频文档至少一个文档外异常；
- 相似文档对至少一个切换案例；
- 所有高风险类别至少一个阻断案例；
- 无答案、无文档和空知识库案例。

## 18. 上线门禁

### 18.1 首轮门禁

- 标准问题和 Alias 确定性命中率 100%；
- Document Hit@1 达到业务确认目标，建议不低于 95%；
- Text/Table/Image 完整率 100%；
- Element 顺序准确率 100%；
- 跨文档内容污染为 0；
- 首轮回答 LLM 调用次数为 0；
- 首轮 Generic RAG 调用次数为 0。

### 18.2 后续门禁

- 文档参数、IP、命令和目录错误率接近 0；
- 高风险无依据建议为 0；
- Evidence ID 合法率 100%；
- 非法图片引用为 0；
- 文档切换必须经过确认；
- Structured Output 最终失败有明确错误，不能返回半成品；
- Workflow 不将模型假设保存成用户事实；
- 文档不足时能够澄清或升级人工。

### 18.3 工程门禁

- Ruff 通过；
- Mypy strict 通过；
- Backend Pytest 通过并满足覆盖率门禁；
- Frontend lint/type-check/test/build 通过；
- Alembic upgrade/downgrade 演练通过；
- 生产 Compose Smoke 通过；
- 幂等和并发测试通过；
- 回退开关演练通过；
- 无密钥、Authorization Header 或 MinIO Secret 泄露。

## 19. 部署、迁移与回退

### 19.1 部署顺序

1. 部署向后兼容的数据库迁移；
2. 部署包含 Support 模块但默认 `legacy_rag` 的 Backend/Worker/Frontend；
3. 配置 Problem Profile；
4. 执行离线评测；
5. 开启小比例 Shadow；
6. 审查质量、成本和安全 Trace；
7. 小比例切换 `technical_support`；
8. 扩大流量；
9. 完成正式切换；
10. 稳定期后再清理 Legacy。

### 19.2 回退

- 运行时可切回 `CHAT_EXECUTION_MODE=legacy_rag`；
- 新表和新字段保持向后兼容；
- 不回滚或删除已创建的 Conversation 和 Message；
- Active Index 不受 Chat 切换影响；
- Shadow 不写 Workflow；
- 回退后新 Support Trace 仍可读取；
- 不使用 `git reset`、数据库物理回滚或破坏性数据操作作为日常回退方案。

### 19.3 数据迁移

Problem Profile 初始数据来源：

1. `document_source.display_name`；
2. 文档首个标题 Element；
3. 人工确认的 `problem_code`；
4. 人工维护 Alias；
5. 可使用 LLM生成 Alias 草稿，但未经审核不得直接启用。

## 20. 安全与权限

### 20.1 服务调用

- 生产环境必须配置 Service Token；
- 企业前端不得持有长期服务密钥；
- 图片由企业后台代理或使用短期签名 URL；
- 日志和 Trace 不保存 Authorization Header。

### 20.2 多企业预留

未来若多个企业共享部署，新增：

```text
api_client
api_client_knowledge_base
```

所有请求在解析 `knowledge_id` 后校验调用方授权。仅依赖用户传入 Knowledge ID 不构成租户隔离。

### 20.3 Prompt 和隐私

- 文档、用户输入和历史均视为不可信数据；
- Trace 保留策略需允许脱敏和关闭 Prompt 保留；
- 用户上传的错误截图不得未经授权发送到其他 Provider；
- 模型配置和上游密钥继续使用现有加密与审计机制。

## 21. 需要保留、降级和最终评估删除的能力

### 21.1 保留

- FastAPI、统一错误、Request ID、日志、鉴权；
- PostgreSQL、Alembic；
- Redis/RQ；
- MinIO；
- DOCX 校验和解析；
- Element 和 Image Asset；
- OCR 和 Vision Caption；
- Knowledge Snapshot/Index 原子发布；
- Conversation、Message、Trace；
- LLM Provider 和密钥管理；
- 管理后台和生产部署。

### 21.2 降级为可选

- `document_chunk`；
- Parent/Child Chunk；
- Embedding；
- BM25/`pg_search`；
- Rerank；
- Query Rewrite；
- Generic RAG Prompt；
- RAG Playground。

### 21.3 新链路稳定后评估删除

- 正式 Chat 中的 Query Rewrite；
- 正式 Chat 中的 Chunk BM25/Vector Search；
- 正式 Chat 中的 RRF/Rerank；
- 正式 Chat 中的 Neighbor Expansion；
- 正式 Chat 中的 Context Selection；
- 旧五模式后验；
- 旧 `CHUNK_RAG` Chat 分支；
- 仅服务旧 Chat 的配置项。

数据库表、PostgreSQL 扩展和历史数据的物理删除必须另立 ADR 和迁移计划。

## 22. 风险与缓解

### 22.1 Problem Profile 维护不完整

风险：Alias 不足导致首轮路由失败。  
缓解：管理台可编辑、真实问题反馈闭环、LLM目录分类兜底、路由评测持续扩充。

### 22.2 LLM 结构化输出不稳定

风险：JSON格式错误或字段缺失。  
缓解：Provider 结构化接口、Pydantic 校验、一次格式重试、最终失败结构化错误。

### 22.3 通用解释越过产品边界

风险：模型把一般知识写成产品事实。  
缓解：Section 类型、Evidence、Disclaimer、Validator、高风险规则和真实安全评测。

### 22.4 Workflow 状态错误

风险：模型把步骤误标完成。  
缓解：只接受用户明确陈述、State Reducer、Workflow Update 校验、Conversation 并发控制。

### 22.5 文档切换错误

风险：静默混合或切换到错误文档。  
缓解：模型不直接选 ID、Resolver 二次解析、用户确认、原子切换。

### 22.6 高风险建议

风险：未依据文档给出固件、硬件或电源操作。  
缓解：Risk Policy、Evidence 强制、确定性关键字/命令检测、必要时二次 Judge、人工升级。

### 22.7 双链路长期共存

风险：维护成本增加。  
缓解：明确切换日期和 Legacy 删除门禁；Generic RAG 最终只保留 Playground，不保留两套正式 Chat。

### 22.8 API 兼容

风险：企业后台依赖旧枚举或字段。  
缓解：保留 `answer/content/references`，新增字段先保持可选，提供契约测试和迁移说明。

## 23. 完成定义

本次重构只有同时满足以下条件才算完成：

1. `/api/v1/chat` 默认由 `TechnicalSupportService` 执行；
2. 首轮明确问题完整返回目标文档及全部图片；
3. 首轮不调用回答 LLM，不执行 Generic RAG；
4. 后续使用完整聚焦文档、Workflow 和有限历史；
5. 后续能够解释、拆解、陪伴执行和进行低风险诊断；
6. 文档事实、通用指导和待验证假设在结构上可区分；
7. Evidence 和 Image 全部经过服务端归属校验；
8. 高风险无依据操作被阻止；
9. 文档切换必须经用户确认；
10. 文档不足时能够收集信息或生成人工升级包；
11. Conversation State 幂等、并发安全且可回放；
12. Support Trace 能解释本轮的文档、模式、Evidence、风险和状态变更；
13. 企业后台接口、图片交付、错误码和重试完成联调；
14. 质量、静态检查、测试、迁移、构建和生产 Smoke 全部通过；
15. 回退开关完成演练；
16. Legacy Chat 已退出正式主路径；
17. 无密钥、敏感 Header 或跨知识库数据泄露；
18. 无已知高风险未处理项。

## 24. 推荐任务拆分顺序

```text
P0  修复评测集与基线
P0  冻结 API、事实层级和风险政策
P0  建立 support 模块骨架
P0  Problem Profile 与 Resolver
P0  首轮完整文档投递

P1  Focused Support 结构化输出
P1  Evidence Registry 和 Validator
P1  Workflow State 和并发控制
P1  文档外现象与低风险诊断
P1  文档切换
P1  人工升级包

P2  管理台 Problem Profile
P2  Support Trace 和指标
P2  企业图片代理/签名 URL
P2  API Client Knowledge Base 授权
P2  Shadow 与切换

P3  Legacy Chat 清理
P3  Embedding/Rerank/pg_search 使用审计
P3  物理基础设施简化 ADR
```

## 25. 评审时需要确认的决策

以下决策建议在阶段 0 评审中冻结：

1. 企业后台是否能优先传 `problem_code` 或 `document_id`；
2. API 是否已经存在必须兼容的外部消费者；
3. 首轮是否始终返回全部图片；默认建议“是”；
4. 后续是否允许一般技术知识；默认建议“允许，但必须标记并限制风险”；
5. 高风险操作类别和企业安全红线；
6. 是否要求 Message 历史完整恢复结构化 Blocks；默认建议“是”；
7. 图片采用企业后台代理还是短期签名 URL；默认建议“后台代理”；
8. 人工升级包由谁消费；本次只返回，不自动发送；
9. Support Prompt 和 Trace 的保留周期；
10. Shadow 流量比例和上线质量门禁。

## 26. 最终架构结论

本项目不需要整体重写，也不应继续把所有技术支持行为塞入现有 RAG 编排。

最终应形成清晰边界：

```text
平台底座
  ├─ 文档与图片
  ├─ Knowledge Snapshot
  ├─ Worker 和对象存储
  ├─ Conversation 和 Trace
  ├─ 模型和密钥
  └─ 企业 API 与管理后台

产品核心
  ├─ Problem Resolver
  ├─ Document Delivery
  ├─ Focused Support Assistant
  ├─ Workflow State
  ├─ Evidence Boundary
  ├─ Risk Policy
  ├─ Document Switch
  └─ Human Escalation

可选技术能力
  └─ Generic RAG / Embedding / BM25 / Rerank / Playground
```

产品价值不再由 RAG 链路复杂度衡量，而由以下结果衡量：

- 是否找对解决方案；
- 是否完整呈现受信文档；
- 是否能帮助用户理解和执行；
- 是否能安全处理文档外现象；
- 是否能明确区分事实、解释和假设；
- 是否在无法可靠处理时及时澄清或升级人工。
