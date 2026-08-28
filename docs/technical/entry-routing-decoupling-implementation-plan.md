# 新问题入口路由与 Support 职责收敛实施计划

状态：待评审，尚未实施  
关联：`docs/technical/support-routing-and-context-remediation-plan.md`  
范围：聊天入口路由、文档命中、RAG 文档选择、Generic RAG 与 Focused Support 的职责边界

## 1. 背景与问题定义

当前默认策略是 `SUPPORT_WITH_RAG_FALLBACK`。所有新问题先进入 Support；当 Profile 不能高置信命中时，Support 内部再调用 `SupportDocumentRouter`。因此一个最终为 `DOCUMENT_DELIVERED` 的响应可能实际经历：

```text
Support
  -> Profile 未命中
  -> Embedding + BM25 + Vector + Rerank
  -> 文档聚合
  -> DOCUMENT_DELIVERED
```

这导致以下问题：

1. `DOCUMENT_DELIVERED` 同时涵盖毫秒级 Profile 直达和依赖外部模型的 RAG 交付，响应类型无法体现成本；
2. Profile 的标准问题偏长或与用户短问法不一致时，原本明确的文档问题会错误地进入昂贵的 RAG 路径；
3. RAG 文档路由当前允许使用 Embedding 90 秒、Rerank 120 秒的上游超时，偶发慢请求会直接暴露给用户；
4. 已聚焦文档的普通追问仍可能进行全库文档切换检查；
5. 多候选的结果由 Support 返回，因此在界面和 Trace 中看似“一直是 Support 出问题”，难以区分 Profile、文档 RAG 和文档问答阶段。

本计划不删除 Support。Support 仍负责已确定文档后的交付、会话焦点、工作流与连续问答；需要收缩的是它作为所有新问题第一入口和 RAG 运行容器的职责。

## 2. 目标与非目标

### 2.1 目标

```text
明确的文档问题
  -> Profile 或已选文档
  -> 立即交付文档；不调用 Embedding、Rerank、LLM

不明确但可能属于文档的问题
  -> 有总时限的文档级 RAG
  -> 唯一候选：交付并建立焦点
  -> 多候选：返回选择
  -> 无可靠候选：Generic RAG

寒暄、范围外、无法确定文档的问题
  -> Generic RAG（可使用轻量开场分支，不强制检索）

已确定文档后的追问
  -> Focused Support
```

具体目标：

- Profile/用户选择的文档交付不依赖任何外部模型；
- 交互式文档选择有明确整体时限，不能受 90/120 秒上游默认超时支配；
- RAG 多候选仍由现有前端选择组件呈现；
- 保留现有对话、SSE、幂等、父子 Trace 和冻结索引语义；
- Trace 可精确区分入口决策、文档选择和回答生成的耗时及来源。

### 2.2 非目标

- 不删除 Profile、人工审核或 Support；
- 不引入新的向量库、路由服务、在线意图分类 LLM 或数据库表；
- 不改变 Embedding、BM25、RRF、Rerank 的排序算法；
- 不以降低 Profile 阈值作为解决全部命中问题的手段；
- 不修改现有聊天接口的核心请求和响应契约。

## 3. 目标架构

### 3.1 入口决策状态机

新建一个仅编排、无外部模型调用的入口决策层。它接收当前会话状态、用户问题和已发布 Profile，产出下一步动作：

```text
START
  |
  +-- 有有效 choice_id / 明确 document_id / problem_code --> DOCUMENT_DELIVERY
  |
  +-- 已有 focus_document_id -----------------------------> FOCUSED_SUPPORT
  |
  +-- 明确寒暄或范围外短句 -------------------------------> GENERIC_RAG_LIGHT
  |
  +-- Profile 高置信唯一匹配 -----------------------------> DOCUMENT_DELIVERY
  |
  +-- 其他新问题 -----------------------------------------> DOCUMENT_ROUTING
                                                        |
                                                        +-- 唯一文档 --> DOCUMENT_DELIVERY
                                                        +-- 多候选 ----> DOCUMENT_CHOICE
                                                        +-- 无文档 ----> GENERIC_RAG
```

其中 `GENERIC_RAG_LIGHT` 是 Generic RAG 的轻量入口，不必执行向量检索。它适用于纯寒暄、致谢、能力询问和明显范围外短问句；混合问题如“你好，如何升级 PDC”不能被归为轻量入口。

### 3.2 组件职责

| 组件 | 调整后的职责 | 禁止承担的职责 |
| --- | --- | --- |
| Gateway | 入口状态机、单轮所有权、引擎调度、Trace 关联 | 业务检索和文档内容生成 |
| ProfileResolver | 无网络的高置信文档快捷匹配 | 文档候选展示、LLM 仲裁 |
| Document Router | 在受限时间内只选择文档或候选 | 生成回答、写入会话焦点 |
| Support | 文档交付、选中文档后的状态维护、Focused Support 问答 | 新问题的默认 RAG 入口 |
| Generic RAG | 开场/范围外回复、无文档时的检索回答 | 擅自切换已聚焦 Support 文档 |

## 4. 详细实施设计

### 4.1 引入显式入口决策模型

新增 `EntryRouteDecision`（建议放在 `backend/app/gateway/models.py` 或独立 `entry_router.py`）：

```text
action:
  DOCUMENT_DELIVERY
  DOCUMENT_ROUTING
  DOCUMENT_CHOICE
  FOCUSED_SUPPORT
  GENERIC_RAG
  GENERIC_RAG_LIGHT

source:
  EXPLICIT
  PENDING_CHOICE
  PROFILE
  HEURISTIC
  DOCUMENT_ROUTER

profile_resolution:
  method / decision / confidence（仅诊断字段）
```

决策顺序必须固定：显式选择优先于焦点、焦点优先于寒暄、寒暄优先于 Profile、Profile 优先于文档 RAG。这样避免“你好”触发文档检索，也避免已选文档后的追问被重新路由。

### 4.2 文档快速交付

复用 `SupportService._deliver` 和现有 `finish_turn`，不重写文档交付或会话持久化。

触发条件：

- 用户传入有效 `choice_id`、`document_id` 或 `problem_code`；
- ProfileResolver 返回唯一高置信匹配；
- 文档 RAG 返回唯一高置信候选；
- 用户确认 RAG 文档选择。

约束：此分支不得创建 Embedding Provider、Rerank Provider 或 LLM Provider。Trace 中必须记录 `external_model_calls = 0`。

### 4.3 受限的文档级 RAG

保留 `SupportDocumentRouter` 的 Hybrid Retrieval 与文档聚合逻辑，但将其抽象为可由 Gateway 调用的 `DocumentRoutingService`。保留 Support 可复用的结果对象，避免复制算法。

为该服务增加整体 deadline 和分段预算，初始建议值：

| 阶段 | 初始预算 | 超时后的行为 |
| --- | ---: | --- |
| Embedding 与 BM25 并行 | 3 秒 | 无可靠候选，转 Generic RAG |
| Vector 检索 | 1 秒 | 记录降级；可用 BM25 候选则继续 |
| Rerank | 4 秒 | 禁止自动交付；有候选则返回选择 |
| 全流程 | 7 秒 | 取消剩余任务，按已有候选选择或转 Generic RAG |

这里的预算是交互式文档路由专用配置，不修改离线索引构建和 Profile 生成的上游超时。Rerank 超时后不可根据未重排分数自动交付文档，以避免误命中；最多返回候选选择。

### 4.4 Generic RAG 与轻量入口

Gateway 在以下情形调用现有 `RagService`：

- Document Router 明确返回 `NO_MATCH`；
- 交互式文档路由达到总 deadline 且无可展示候选；
- 明确的范围外/寒暄问题。

对纯寒暄、致谢、能力说明等问题，在 Generic RAG 内部增加轻量短路：不调用 Query Rewrite、Embedding、BM25、Rerank，而由受控模板或短 LLM 回复完成。该分支仍返回现有 `ANSWERED` 协议。

不可把“包含你好”的所有问题当作寒暄。例如“你好，VAU 图像如何配置”仍应进入 Profile/文档路由。

### 4.5 Focused Support 的切换检查

聚焦态默认直接进入 Focused Support。仅满足下列任一条件时才执行跨文档检查：

- 用户显式提出切换，如“换一个问题”“另一个设备”“不问这个了”；
- Profile 对另一文档形成唯一高置信匹配；
- 用户问题同时包含当前焦点之外的明确产品/型号与任务证据。

普通代词追问、步骤追问和故障延续不应触发全库 Embedding/Rerank。跨文档检查结果仍使用现有 `CONFIRM_DOCUMENT_SWITCH`，不会自动切换。

### 4.6 Query Rewrite 收紧

Query Rewrite 只属于 Generic RAG 完整检索路径。修改触发条件：

- 必须存在历史；
- 必须有指代、省略或承接信号，如“它”“这个”“还是”“下一步”“继续”；
- 或当前问题缺失产品/对象但上一轮包含可继承对象。

“有历史且问题短于 40 字”不得单独触发改写。改写设置单独使用 3 秒左右的硬 deadline；超时立即使用原问题，记录 `DEGRADED`，不影响后续检索。

## 5. 接口、前端与数据兼容性

### 5.1 保持不变

- 聊天请求字段及 SSE 事件名称；
- `DOCUMENT_DELIVERED`、`CLARIFICATION_REQUIRED`、`ANSWERED` 等公开响应类型；
- 文档选择的 `clarification.type = DOCUMENT_CHOICE`；
- 文档切换的 `clarification.type = CONFIRM_DOCUMENT_SWITCH`；
- 已有 `choice_id`、`document_id`、`problem_code` 语义；
- Conversation 的冻结索引和 focus 状态。

### 5.2 增量诊断字段

在 Trace 和可选响应 `routing` 中增加可忽略字段：

```json
{
  "entry_route": "DOCUMENT_ROUTING",
  "entry_source": "PROFILE_BELOW_DIRECT_THRESHOLD",
  "execution_engine": "DOCUMENT_ROUTER",
  "external_model_calls": 2,
  "timings": {
    "profile_ms": 1,
    "embedding_ms": 320,
    "bm25_ms": 18,
    "vector_ms": 8,
    "rerank_ms": 640,
    "document_delivery_ms": 12
  }
}
```

前端第一阶段不依赖这些字段；后续可在 Trace 详情页展示，帮助区分“Profile 未命中”“Rerank 慢”“LLM 慢”。

### 5.3 前端改动

第一阶段仅需确认现有聊天视图对上述既有响应类型均正常工作。无需重做选择组件。

可选第二阶段：在管理员 Trace 页面增加入口路由、实际执行引擎和分段耗时，以避免将所有问题泛称为 Support 故障。

## 6. 实施步骤

### 阶段 A：观测与安全边界

1. 为 `SupportDocumentRouter` 补充 Embedding、BM25、Vector、Rerank、聚合、文档读取和持久化耗时；
2. 在 Trace 区分 `PROFILE_DIRECT`、`DOCUMENT_ROUTER_DELIVERY`、`GENERIC_RAG`、`FOCUSED_SUPPORT`；
3. 实现 Document Router 的 deadline、任务取消和降级结果；
4. 为 Profile 快速交付添加“零外部模型调用”断言。

完成标准：能够定位一次慢文档交付究竟卡在何阶段；任一文档选择请求不会无限等待上游默认 90/120 秒。

### 阶段 B：Gateway 入口状态机

1. 在 `ChatGatewayService` 前置执行入口决策；
2. 将 Profile 唯一命中直接转到交付分支；
3. 将 Document Router 从 Support 内部兜底调整为 Gateway 编排的独立步骤；
4. 明确一条用户消息只能由一个引擎创建/完成 assistant message，fallback 复用既有父子 Trace 规则；
5. 保持选择确认回到 Support 的现有持久化路径。

完成标准：新问题的 Profile 直达不再进入文档 RAG；无 Profile 的文档问题能够通过 RAG 选择或 Generic RAG 处理。

### 阶段 C：Focused Support 与 Generic RAG 调整

1. 收紧 Focused Support 的跨文档检查触发条件；
2. 收紧 Generic RAG Query Rewrite 触发条件与时限；
3. 增加 Generic RAG 轻量开场/范围外分支；
4. 验证焦点会话、文档选择和 RAG fallback 的 SSE 顺序与幂等重放。

完成标准：普通聚焦追问不再调用全库文档检索；短寒暄不产生检索和 Rerank 调用。

### 阶段 D：Profile 高频表达修整

1. 从 Trace 汇总“Profile 未命中但 Document Router 成功交付”的高频问题；
2. 对每份 Profile 只补充真实短问法 Alias 或缩短标准问题，不整体降低阈值；
3. 在人工审核页展示命中/未命中样本，供审核人调整三个保留字段；
4. 以离线回放集评估直达率、错交付率和 RAG 降级率。

完成标准：高频明确问题优先走 Profile，且不以增加宽泛 Alias 的方式换取命中率。

## 7. 测试与验收

### 7.1 必须新增的后端测试

- Profile 精确/高置信匹配：断言无 Embedding、Rerank、LLM 调用；
- Profile 未命中 + 文档 RAG 唯一候选：断言交付、建立 focus、记录路由来源；
- 文档 RAG 多候选：断言 `DOCUMENT_CHOICE`，不自动交付；
- Embedding、Rerank 和全流程超时：断言在预算内结束，并按规则选择候选或 Generic RAG；
- 文档 RAG 超时不能自动交付；
- 纯“你好”“谢谢”“你能做什么”：断言无检索调用；
- “你好，如何升级 PDC”：断言不被寒暄短路；
- 已聚焦文档的“还是失败怎么办”：断言不调用文档 RAG；
- 明确新设备/新任务：断言仅给出切换确认，绝不自动切换；
- 同一 `request_id` 重放：断言不会重复创建消息或焦点状态。

### 7.2 回归验收指标

| 指标 | 验收目标 |
| --- | --- |
| Profile/确认选择的文档交付 | P95 不受外部模型延迟影响；Trace 显示 0 次外部模型调用 |
| 文档 RAG 选择 | 总耗时不超过配置的交互 deadline |
| Query Rewrite | 仅在真实承接式追问触发；超时不阻塞主检索 |
| 文档自动交付 | Rerank 降级或超时时为 0 次 |
| Focused Support 普通追问 | 不产生全库文档检索 |
| 前端协议 | 现有文档交付、选择、回答、切换确认均通过回归 |

## 8. 风险与决策点

| 风险 | 控制方式 |
| --- | --- |
| Gateway 和 Support 同时写入同一轮消息 | 明确 Gateway 只编排；文档交付与 Focused Support 保持单一持久化所有者 |
| 过短 RAG deadline 降低文档召回 | 超时仅禁止自动交付；保留候选选择与 Generic RAG 兜底，并通过 Trace 调整预算 |
| 寒暄规则误吞业务问题 | 仅匹配纯社交短句；包含产品、型号、操作或故障词时不短路 |
| 放宽 Profile 导致错文档 | 不调低全局阈值；以高频 Trace 样本补充具体 Alias |
| Focused Support 不再主动发现换题 | 仅收紧弱信号；保留显式换题、强 Profile 和强实体冲突三类检查 |

需要在实施前确认的产品决策：当 Document Router 超时但保留 2～3 个低置信候选时，默认是“展示候选选择”还是“直接进入 Generic RAG”。本计划建议前者，因为它更符合文档交付场景的可控性；没有可解释候选时才进入 Generic RAG。

## 9. 预期结果

实施完成后，用户不会再因为响应显示为 `DOCUMENT_DELIVERED` 就误以为它必然是快速路径：Trace 将明确显示它是 Profile 直达、用户确认交付，还是 RAG 选择后交付。

更重要的是，明确文档问题应稳定走本地、可预测的快速交付；不明确问题才承担有限时间的检索成本；Generic RAG 与 Support 连续问答各自回到合适的位置。
