# 双引擎架构拆分与实施计划

状态：V1 已实施并完成回归  
基线：`02b520e`  
目标版本：双引擎解耦 V1

管理页面中的运行策略入口已在后续的[问答引擎路由控制面收敛计划](./engine-routing-control-plane-plan.md)中统一；若本文 V1 页面描述与该计划冲突，以后者为准。

## 1. 背景与问题

当前系统同时承载两条问答链路：

1. Profile Support：问题 Profile 匹配、完整文档交付、会话锁定文档版本、基于完整文档和 LLM 的持续技术支持。
2. Generic RAG：Query Rewrite、向量与 BM25 混合检索、Rerank、上下文组装、LLM 生成和引用校验。

两条链路已经有 `support/` 和 `rag/` 领域目录，但仍存在以下混合：

- `/chat`、`/support/chat`、RAG Playground、模型配置和 Trace API 集中在 `api/rag.py`。
- 兼容入口直接通过配置分支选择 `TechnicalSupportService` 或 `RagService`，缺少独立路由策略和会话快照。
- 管理台使用一个 `adminStore` 同时持有共享数据、Support Profile、RAG 配置和 Trace。
- 管理台只有混合的模型/Prompt/RAG 配置页和混合错误页，无法按引擎独立运维。
- Trace 没有稳定的引擎、阶段、降级来源和父子 Trace 字段。

## 2. 目标

重构为“一个共享平台、两个独立引擎、一个统一管理后台、一个 Chat Gateway”。

```text
                              Admin Shell
                                  |
             +--------------------+--------------------+
             |                    |                    |
       Shared Admin         Support Admin          RAG Admin
   KB/Document/Index/Model  Profile/Policy/Error  Retrieval/Prompt/Error
             |                    |                    |
             +--------------------+--------------------+
                                  |
                           Chat Gateway
                    policy resolve + session freeze
                          /                 \
              Profile Support Engine    Generic RAG Engine
                          \                 /
                    controlled fallback/extension
                                  |
                         Shared Platform
               ingestion/index/storage/task/trace/error
```

### 2.1 必须满足的边界

- Support API 不定义在 RAG API 文件中。
- Generic RAG 提供不经过 Support 的显式问答入口。
- `/chat` 仅作为兼容 Gateway，不承载任一引擎的领域逻辑。
- Support 不直接依赖 RAG 内部 Pipeline；降级只能通过 Gateway/稳定接口触发。
- 文档摄取、不可变索引、MinIO、模型注册和原始错误日志保持共享。
- 管理台保持一个 Shell，但 Support 与 RAG 使用独立 Store、配置页和错误页。

### 2.2 模块职责清单

| 层级 | 模块 | V1 职责 | 依赖边界 |
|---|---|---|---|
| API | `app/api/chat.py` | 兼容 Chat Gateway、欢迎语、运行策略管理 | 只依赖 Gateway 和共享摘要查询 |
| API | `app/api/support.py` | 显式 Support Chat、Profile 管理 | 只依赖 Support 领域 |
| API | `app/api/rag.py` | 显式 RAG Chat、Playground、模型、Prompt、Trace | 不导入 Support 领域 |
| API | `app/api/engine_dependencies.py` | 组装两个引擎及 Gateway 的依赖 | 不包含业务判断 |
| Gateway | `app/gateway/models.py` | 引擎、策略和冻结策略值对象 | 无领域服务依赖 |
| Gateway | `app/gateway/repository.py` | 策略持久化、会话冻结、降级 Trace 关联 | 只依赖数据库与共享配置 |
| Gateway | `app/gateway/service.py` | 策略路由、白名单降级、SSE 转发 | 通过稳定服务入口调用 Support/RAG |
| Support | `app/support/*` | Profile 匹配、文档交付、文档版本锁定、完整文档问答、证据/风险门禁、工作流 | 不依赖 RAG Pipeline |
| RAG | `app/services/rag.py`、`app/rag/*` | Rewrite、混合召回、RRF、Rerank、上下文、生成、引用 | 不依赖 Support 领域 |
| Shared | `app/ingestion/*`、`app/tasks/*` | 文档解析、切块、Embedding、索引构建、异步任务 | 两个引擎共同消费发布后的索引 |
| Shared | `app/core/*`、`app/common/*` | 配置、资源、密钥、错误、中间件 | 不承载引擎路由逻辑 |
| Shared | `app/observability/*`、`rag_trace` | 通用 Trace、错误和指标存储 | 以 `engine`/`stage` 区分领域 |
| Admin Shell | `App.vue`、`AdminStudio.vue` | 统一导航、知识库上下文、页面装配 | 不保存引擎业务状态 |
| Shared Admin | `adminStore.ts`、共享 Views | 知识库、文档、索引、模型、共享日志 | V1 保留少量旧兼容字段 |
| Routing Admin | `engineRoutingStore.ts`、Engine Routing View | 唯一运行策略入口、双引擎就绪摘要 | 写入知识库 Runtime Policy |
| Support Admin | `supportAdminStore.ts`、Support Views | Profile、就绪状态、Support Errors | 查询固定 `engine=SUPPORT` |
| RAG Admin | `ragAdminStore.ts`、RAG Views | 全局 RAG Config、Playground、RAG Errors | 查询固定 `engine=RAG` |
| Chat Client | `api/chat.ts` | 只调用 Gateway，并正确处理任意 SSE 网络分片 | 不在浏览器内复制路由策略 |

## 3. 运行策略

每个知识库配置一种策略：

| 策略 | 行为 |
|---|---|
| `SUPPORT_ONLY` | 只运行 Profile Support；未匹配时返回澄清或 NO_MATCH。 |
| `RAG_ONLY` | 跳过 Profile，直接运行 Generic RAG。 |
| `SUPPORT_WITH_RAG_FALLBACK` | 优先 Support，仅在白名单结果下进入 RAG。 |

V1 自动降级白名单只有 `NO_MATCH`。以下情况不得自动降级：鉴权/配置错误、证据校验失败、高风险拦截、工作流冲突、文档外参数和非法图片引用。

### 3.1 会话冻结

首次成功响应后在 `conversation` 保存：

- `execution_policy`
- `execution_engine`
- `engine_config_version`

后续轮次优先读取会话快照，不受后台策略修改影响。对历史会话，字段为空时按知识库当前策略解析并在本轮结束后补齐。

在 `SUPPORT_WITH_RAG_FALLBACK` 中，如果首轮 Support 返回 `NO_MATCH` 并成功降级到 RAG，则会话冻结为 `RAG`，后续轮次直接走 RAG，避免每轮重复生成一个 Support NO_MATCH。

## 4. 后端设计

### 4.1 新增模块

```text
backend/app/gateway/
  models.py          # Engine、ExecutionPolicy、ResolvedPolicy
  repository.py      # 知识库策略、会话快照、降级 Trace 关联
  service.py         # 非流式和 SSE Gateway 编排

backend/app/api/chat.py       # /chat、/chat/stream、/chat/welcome
backend/app/api/support.py    # /support/chat、Profile 管理
```

现有 `api/rag.py` 保留 RAG 管理能力，并新增显式 `/rag/chat` 与 `/rag/chat/stream`。Support 路由和兼容 `/chat` 从该文件移除。

### 4.2 数据库迁移 0020

新增 `engine_runtime_policy`：

- `kb_id`：每个知识库唯一。
- `execution_policy`：三种策略之一。
- `config_version`：每次修改递增。
- `fallback_conditions`：V1 固定/校验为 `NO_MATCH` 白名单，为后续扩展保留结构。
- `updated_at`。

扩展 `conversation`：

- `execution_policy`
- `execution_engine`
- `engine_config_version`

扩展 `rag_trace`：

- `engine`：`SUPPORT` 或 `RAG`。
- `stage`。
- `parent_trace_id`。
- `fallback_from`。
- `fallback_reason`。

### 4.3 API

```text
POST /api/v1/chat                     # Gateway 兼容入口
POST /api/v1/chat/stream

POST /api/v1/support/chat             # 强制 Support
POST /api/v1/support/chat/stream
GET  /api/v1/support/problem-profiles
PUT  /api/v1/support/problem-profiles

POST /api/v1/rag/chat                 # 强制 RAG
POST /api/v1/rag/chat/stream
POST /api/v1/rag/playground

GET  /api/v1/runtime-policies/{kb_id}
PUT  /api/v1/runtime-policies/{kb_id}
GET  /api/v1/traces?engine=SUPPORT|RAG
```

显式引擎接口用于测试和运维，不读取切换策略。只有 `/chat` 读取策略。

### 4.4 SSE

Gateway 必须转发引擎原始事件。Support 降级场景先执行 Support；若结果为 `NO_MATCH`，发送 `fallback` 事件后进入 RAG SSE。

前端解析器必须保留最后一个不完整 SSE block，不能假设网络块和 SSE 帧对齐。

## 5. 管理后台设计

### 5.1 导航

共享区域：

- Dashboard
- Knowledge Base
- Documents
- Index Build
- Models & Credentials
- Shared Logs
- Engine Routing
- DemoChat

Support 区域：

- Problem Profiles
- Support Configuration
- Support Errors

RAG 区域：

- RAG Playground
- RAG Configuration
- RAG Errors

### 5.2 Store

```text
shared adminStore       知识库、文档、索引、模型、共享 Drawer
engineRoutingStore      唯一知识库运行策略、Support/RAG 就绪摘要
supportAdminStore       Profile、Support Trace/Error
ragAdminStore           全局 RAG Config、RAG Trace/Error
```

V1 允许旧 Store 暂时保留兼容字段，但新增页面不得再向其中加入新的 Support/RAG 状态。完成页面迁移后再删除重复字段。

### 5.3 独立错误页

Support Errors 只查询 `engine=SUPPORT`，重点展示 Profile 路由、完整文档、验证、工作流和人工升级错误。

RAG Errors 只查询 `engine=RAG`，重点展示 Rewrite、Embedding、检索、Rerank、Prompt、LLM 和引用错误。

两者继续复用 Trace 存储与通用表格组件，避免复制可观测性基础设施。

## 6. 兼容与迁移

- `CHAT_EXECUTION_MODE=technical_support` 映射为默认 `SUPPORT_ONLY`。
- `CHAT_EXECUTION_MODE=legacy_rag` 映射为默认 `RAG_ONLY`。
- 未创建 `engine_runtime_policy` 的知识库使用上述默认值。
- 现有 `/support/chat` 行为保持不变。
- 前端 Support Chat 改用 `/chat/stream`，从而验证后台切换；显式引擎测试页后续可直接调用对应接口。
- 历史 `rag_trace` 迁移时根据 Support Trace 的 `retrieval_result.support_version` 回填 `engine=SUPPORT`，其余为 `RAG`。

## 7. 实施批次

### 批次 A：路由和领域边界

- [x] 新建 Gateway 模块。
- [x] 新建 `api/chat.py` 和 `api/support.py`。
- [x] `api/rag.py` 移除 Support/Gateway 逻辑。
- [x] 增加显式 RAG Chat API。

### 批次 B：数据和可观测性

- [x] 新增迁移 0020。
- [x] Support/RAG Trace 写入引擎标识。
- [x] Trace 查询支持 `engine`。
- [x] 实现策略读取、更新、会话冻结和降级 Trace 关联。

### 批次 C：管理后台

- [x] 增加 Runtime Policy API 类型和客户端。
- [x] 新建 Support/RAG 独立 Store。
- [x] 新建两个配置页和两个错误页。
- [x] 调整导航和页面映射。
- [x] Support Chat 通过 Gateway 验证策略切换。

### 批次 D：稳定性

- [x] 修复 SSE 跨 chunk 解析。
- [x] 增加分片 SSE 测试。
- [x] 增加 Gateway 策略、降级白名单、会话冻结测试。
- [x] 运行 Ruff、Format、Mypy、Pytest、ESLint、Vue Type Check、Vitest、Build。

## 8. 验收标准

1. Support 与 RAG 具有物理独立的 API 文件和显式入口。
2. 管理员可以按知识库选择三种运行策略。
3. 同一会话在策略修改后继续使用已冻结引擎。
4. Support 只有 `NO_MATCH` 能自动降级，安全错误不会绕过门禁。
5. 降级产生两条可关联 Trace，并记录原因。
6. Support/RAG 各自具有配置页和错误页。
7. 浏览器在任意 SSE 网络分片下都能收到完整 `done` 事件。
8. 共享的文档摄取、索引发布、模型注册和原始错误日志不复制。
9. 现有显式 `/support/chat` 和管理 API 保持兼容。

## 9. 后续阶段（不阻塞 V1）

- 将 `RagRepository` 按 Retrieval、Conversation、Trace、Model Config 拆分。
- 将 `RagService` 拆为 Rewrite、Retrieval、Context、Generation、Citation 服务。
- 增加 Support 受控 RAG 扩展接口，不局限于 NO_MATCH 降级。
- 将 Support 配置从环境变量迁移为版本化 `support_config` 表。
- 将前端大型视图改为动态 import，降低首屏 bundle。

## 10. 实施与验证结果

完成日期：2026-08-25。

- Alembic 迁移链唯一 Head 为 `0020`；本地开发库已从 `0019` 迁移到 `0020`。
- 数据回填结果：历史 Trace 中 78 条识别为 `SUPPORT`，95 条保留为 `RAG`。
- 后端 Ruff、Format 和严格 Mypy 全部通过。
- 后端 Pytest：322 通过、2 跳过，覆盖率 72.29%，达到项目 70% 门槛。
- 前端 ESLint 和 Vue Type Check 全部通过。
- 前端 Vitest：14 个测试文件、69 个测试全部通过。
- 前端生产构建成功。现有主包超过 500 kB 的 Vite 警告不阻塞 V1，已列入后续动态加载优化。
- `git diff --check` 通过，无空白符错误。

部署时仍需在目标环境按正常发布流程执行 `alembic upgrade head`；运行策略只影响新会话，已有冻结会话不会随后台切换而漂移。
