# 问答引擎路由控制面收敛计划

状态：已实施并完成回归  
前置版本：双引擎解耦 V1、智能知识发布 0021 迁移  
目标版本：管理控制面 V1

## 1. 决策摘要

后台只保留一个知识库级“问答引擎路由”配置入口。Support 和 RAG 领域页只管理各自能力，不再重复提供总引擎开关。

客户端始终调用稳定的 Gateway 接口：

```text
POST /api/v1/chat
POST /api/v1/chat/stream
```

后台配置改变的是 Gateway 后面的运行能力，不动态增删 HTTP 路由。显式 `/support/chat` 与 `/rag/chat` 继续作为受服务令牌保护的运维、诊断和领域集成接口，不作为 DemoChat 的调用目标。

## 2. 两种切换语义

### 2.1 管理者手动切换

管理员按知识库保存以下策略之一：

| 策略 | 新会话行为 |
|---|---|
| `SUPPORT_ONLY` | 只执行 Profile Support。 |
| `RAG_ONLY` | 跳过 Profile，直接执行 Generic RAG。 |
| `SUPPORT_WITH_RAG_FALLBACK` | Support 优先，仅对白名单结果自动降级。 |

手动切换更新知识库运行策略并递增 `config_version`，只影响尚未冻结的新会话。已有会话继续使用其 `execution_engine` 快照。

### 2.2 自动降级切换

自动降级不修改知识库配置。V1 仅当策略为 `SUPPORT_WITH_RAG_FALLBACK` 且 Support 返回 `NO_MATCH` 时：

1. Gateway 保留已完成的 Support Trace。
2. Gateway 发送 `fallback` SSE 事件并以同一会话执行 RAG。
3. RAG 成功后关联父子 Trace，并把该会话冻结为 `RAG`。
4. 其他会话、知识库策略及配置版本不发生变化。

安全、证据、鉴权、模型、工作流错误和人工升级结果均不得触发自动降级。

## 3. 管理页面职责

### 3.1 新增共享页面：Engine Routing

这是唯一可写入 `engine_runtime_policy` 的页面，包含：

- 知识库选择器。
- 当前策略、配置版本、来源和更新时间。
- 三种策略及其准确影响说明。
- `NO_MATCH` 自动降级白名单说明。
- Support 就绪摘要：已就绪 Profile 数量。
- RAG 就绪摘要：知识库 Active Index 状态。
- “只影响新会话；已冻结会话不迁移”的明确提示。
- 保存前的前端预检：目标引擎未就绪时禁用保存并说明原因。

V1 的预检是管理防误操作，不改变后端既有路由语义；后续可将同一门禁下沉到策略更新 API。

### 3.2 Support Configuration

只展示 Support 领域状态：

- Profile 总数和发布就绪数量。
- 不就绪 Profile 及原因。
- 指向 Problem Profile 管理的说明。

该页面不读取、不保存 Runtime Policy。

### 3.3 RAG Configuration

只管理系统级 RAG 检索参数：

- Vector Top K。
- BM25 Top K。
- RRF Fusion Top K。
- Rerank Top N。
- Context Max Chunks。

当前 `rag_config` 是全局单例，因此页面必须明确标注“影响所有进入 RAG 的知识库和下一轮 RAG 请求”，不再让知识库选择上下文造成按库配置的误解。

### 3.4 DemoChat

DemoChat 只允许选择知识库，不提供引擎选择或切换能力，并始终调用 `/api/v1/chat/stream`。页面负责渲染 Gateway 返回的答案、文档、图片、引用、澄清、错误，以及实际执行引擎和自动降级事件；这些信息只读，不构成第二个引擎切换入口。

RAG Playground 继续用于显式 RAG 诊断，但不代表真实对外路由结果。

## 4. 前端状态边界

```text
adminStore                 共享知识库和管理 Shell 状态
engineRoutingStore         唯一 Runtime Policy 状态与保存动作
supportAdminStore          Profile readiness + Support Errors
ragAdminStore              全局 RAG Config + RAG Errors
DemoChat local state       知识库、会话和答案渲染
```

约束：`supportAdminStore` 与 `ragAdminStore` 不得再持有 `RuntimePolicy` 或调用 `updateRuntimePolicy`。

## 5. 实施范围

### 批次 A：控制面单一入口

- [x] 新建 `engineRoutingStore.ts`。
- [x] 新建 `EngineRoutingView.vue`。
- [x] 在共享 Runtime 导航增加唯一引擎路由入口。
- [x] 页面映射和烟雾测试覆盖新入口。

### 批次 B：领域页面去重

- [x] Support Store/View 移除 Runtime Policy 状态和保存动作。
- [x] RAG Store/View 移除 Runtime Policy 状态和保存动作。
- [x] RAG 页面明确全局配置作用域。
- [x] 两个领域页不再出现引擎选择器。

### 批次 C：对外测试边界

- [x] 确认 DemoChat 只有知识库选择器。
- [x] 测试锁定 DemoChat 使用 `/api/v1/chat/stream`。
- [x] 不在 DemoChat 增加显式 Support/RAG 调试入口。

### 批次 D：回归与交付

- [x] 增加路由 Store 和页面交互测试。
- [x] 运行 ESLint、Vue Type Check、Vitest 和生产 Build。
- [x] 运行后端 Ruff、Format、Mypy 和 Pytest，确认控制面调整未破坏 Gateway。
- [x] 更新本文档执行状态和验证结果。

## 6. 验收标准

1. 管理后台只能在 Engine Routing 页面修改知识库运行策略。
2. Support/RAG 配置页不存在重复引擎开关。
3. 自动降级仍只接受 `NO_MATCH`，且不修改知识库策略。
4. 已冻结会话不受管理员切换影响。
5. DemoChat 只选择知识库，并始终调用 Gateway。
6. RAG 全局参数的作用范围在页面中没有歧义。
7. Support/RAG 错误页面继续按引擎隔离。
8. 前后端现有自动化检查全部通过。

## 7. 非本次范围

- 动态注册或删除 HTTP 路由。
- 强制迁移、终止或重置已有会话。
- 将全局 `rag_config` 改为知识库级配置。
- 新增管理员身份、审批流或完整策略变更审计表。
- 修改 Support/RAG 核心问答算法。

## 8. 实施结果

完成日期：2026-08-25。

- 新增唯一的 Engine Routing 导航、页面和 Store；代码检索确认只有 `engineRoutingStore` 调用 `updateRuntimePolicy`。
- Engine Routing 按知识库展示策略版本、来源、自动降级白名单、Support Profile 和 RAG Active Index 就绪状态。
- 前端预检会阻止将新会话切换到当前未就绪的目标引擎。
- Support Configuration 已收敛为 Profile 和完整文档就绪检查页。
- RAG Configuration 已收敛为系统级全局检索参数页，并明确其影响所有 RAG 请求。
- DemoChat 只保留一个知识库选择器，测试锁定其调用 `/api/v1/chat/stream`，不出现引擎选择项。
- DemoChat 展示实际 `SUPPORT`/`RAG` 执行引擎与 `SUPPORT → RAG` 自动降级原因；切换知识库会取消旧流并创建无会话 ID 的新请求。
- Support 在结构化输出完成证据和安全校验后，通过 `trace`、多个 `message` 和 `done` 事件增量交付，避免把未经校验的模型片段提前暴露给客户端。
- Support 后续问答严格应用 Prompt token 预算；正常文档保持完整上下文，超限时先移除旧历史，再选择与问题相关的完整文档元素，并记录预算使用与页面提示。
- Profile 查询同时约束 Profile 和文档归属知识库；跨知识库同名 Profile、前端切库重置和统一 Gateway 请求均有回归测试。
- DemoChat 的 SSE 客户端改为按协议行解析，兼容 LF、CRLF、CR、任意网络分片、跨字节 UTF-8 和无尾部分隔符；服务端未发送 `done`/`error` 就断流时会明确报错。
- Support 直接交付完整文档时，图片下方只允许展示原 DOCX 作者提供的替代文本；AI 图片描述和 OCR 仍可供内部聚焦问答使用，但不会被当作文档正文展示。
- 前端 ESLint、Vue Type Check、生产 Build 通过；Vitest 16 个测试文件、79 个测试通过。
- 本次后端文件 Ruff、Format、严格 Mypy 通过；Pytest 337 个测试通过、2 个跳过，覆盖率 71.61%。
- `git diff --check` 通过。

生产构建仍有现存的单包超过 500 kB 警告，不影响本次控制面功能，动态加载优化仍属于后续工作。
