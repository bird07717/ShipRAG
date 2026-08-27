# RAG 自动发布与 Support 人工审核链路解耦执行计划

## 1. 文档状态

- 状态：`IN_PROGRESS`
- 基线分支：`feat/HitDoc`
- 编写日期：2026-08-26
- 当前执行阶段：阶段四代码与自动化验证已完成（Rebase、Draft 复用与 REMOVE、双链路显式回滚、GC 保护扩展、NO_MATCH 反馈面板）；真实外部模型 E2E 由使用者在真实环境执行
- 目标：把“共享文档处理、RAG 自动生效、Support 人工发布”建模为三组独立状态和两条独立运行版本链路。

## 2. 背景与已确认问题

当前上传文档后会创建不可变候选索引，并依次执行 DOCX 解析、图片处理、Chunk、Embedding 和持久化。索引完成后又生成 Profile Draft，最终由同一个 Knowledge Release 发布事务同时：

1. 将旧 `ACTIVE` Index 设为 `DEPRECATED`；
2. 将候选 Index 设为 `ACTIVE`；
3. 更新 `knowledge_base.active_index_id`；
4. 发布已审核的 Problem Profile。

这使三类完成条件被错误地合并：

| 状态维度 | 正确完成条件 | 当前问题 |
|---|---|---|
| 文档处理 | Parser、Image、Chunk、Embedding、完整性检查成功 | 与是否允许业务发布混在 Index 状态中 |
| Generic RAG | 技术索引通过硬校验即可自动生效 | 被迫等待 Profile 人工审核 |
| Profile Support | Profile 生成后经过人工审核与发布校验 | 审核能力隐藏在 Index Build Drawer，并会同时切换 RAG Index |

直接表现为：

- Documents 页面把“候选 RAG 索引”和“人工审核发布”描述为同一步；
- Index Build 页面同时展示技术流水线、Profile 编辑和联合发布按钮；
- 管理员没有独立、可检索的 Support Profile 审核队列；
- `knowledge_base.active_index_id` 同时被两个引擎当作运行版本；
- RAG 更新频率和 Support 审核频率无法独立；
- 删除或替换文档后，如果 RAG 先切换而 Profile 没有同步版本化，Support 可能引用新索引中不存在的文档；
- 旧索引 GC 尚不能表达“RAG 已不用，但 Support 或历史会话仍在使用”。

## 3. 目标架构

### 3.1 总体流程

```text
上传文档
  -> 共享文档处理
       Parser -> Image/OCR/Vision -> Parent/Child Chunk -> Embedding -> Index READY
       |
       +-> RAG 发布链
       |    技术预检 -> 自动激活 -> rag_active_index_id
       |
       +-> Support 发布链
            Profile Draft 生成 -> 人工审核 -> 验证 -> 发布
            -> support_active_release_id -> frozen index_id
```

### 3.2 必须保持的边界

- Parser、图片理解、Chunk、Embedding、MinIO 和不可变 Index 继续共享，不复制两套摄取管线。
- RAG 不依赖 Profile 是否完成或是否通过人工审核。
- Support Profile 不得自动发布，也不得通过公开接口直接写入线上 Profile。
- RAG Index 激活失败时保留旧 RAG Active Index。
- Support Release 发布失败时保留旧 Support Active Release。
- RAG 和 Support 会话分别冻结自己的运行版本。
- Support 到 RAG 的 fallback 保留父子 Trace，但允许两条 Trace 使用不同 Index 版本。
- GC 不得删除任何仍被 RAG、Support、候选 Release、任务或保留期内会话引用的 Index。

## 4. 目标状态模型

### 4.1 文档处理状态

沿用文档和任务的处理信息，在 API 聚合层统一映射为：

```text
UPLOADED -> PROCESSING -> READY
                      \-> FAILED
```

`PROCESSING` 的细分阶段继续来自 `task_record.stage`：

```text
PARSING
PROCESSING_IMAGES
CHUNKING
EMBEDDING
VERIFYING
COMPLETED
```

### 4.2 RAG 状态

```text
PENDING -> INDEXING -> READY -> ACTIVATING -> ACTIVE
                  \-> FAILED       \-> ACTIVATION_FAILED

ACTIVE -> SUPERSEDED
```

RAG 激活只执行机器可验证的硬检查，不要求人工操作。

### 4.3 Support 状态

单篇文档面向 Support 的聚合状态：

```text
NOT_ENABLED
GENERATING
REVIEW_REQUIRED
APPROVED
PUBLISHED
REJECTED
FAILED
SUPERSEDED
```

其中：

- `NOT_ENABLED`：明确不进入 Profile Support；
- `REJECTED`：本批候选被人工拒绝；
- `PUBLISHED`：只表示当前 Support Active Release 中存在生效 Profile；
- `REVIEW_REQUIRED` 不能和 `NOT_ENABLED` 混用。

### 4.4 合法的版本差

系统必须允许：

```text
RAG Active Index: v20
Support Active Release: r15 (frozen on Index v18)
Support Candidate Release: r16 (based on Index v20)
```

页面要把版本差作为正常状态展示，而不是把它当成必须立即消除的错误。

## 5. 目标数据模型

### 5.1 Knowledge Base 运行指针

新增：

```text
knowledge_base.rag_active_index_id uuid null
knowledge_base.support_active_release_id uuid null
```

兼容迁移：

1. `rag_active_index_id` 从现有 `active_index_id` 回填；
2. `support_active_release_id` 从最近的 `PUBLISHED` Knowledge Release 回填；
3. 迁移窗口内保留 `active_index_id`，后端双写并增加一致性检查；
4. 所有读路径迁移完成后再移除或停止使用旧字段。

### 5.2 Support Release

第一轮可保留表名 `knowledge_release`，但代码、API 和页面语义统一解释为 Support Release。后续是否重命名表由迁移成本决定，不作为解耦前置条件。

Release 必须稳定保存：

```text
id
kb_id
index_id
base_release_id
status
validation_result
generation_error
created_at
updated_at
published_at
```

建议状态：

```text
GENERATING
REVIEW_REQUIRED
VALIDATING
READY_TO_PUBLISH
PUBLISHED
REJECTED
SUPERSEDED
VALIDATION_FAILED
```

### 5.3 Profile 版本

目标是让每个已发布 Profile 明确归属 `release_id` 和 `index_id`。实施时有两个选项：

1. 推荐：将 `document_problem_profile` 攽造成版本表，运行时按 `support_active_release_id` 查询；
2. 兼容：保留 `document_problem_profile` 作为当前线上投影，新增不可变 Profile Snapshot 表供历史会话和回滚使用。

阶段二设计评审时根据现有 Repository SQL 影响面选择，验收标准不变：旧 Support 会话不得读取后来覆盖的新 Profile 版本。

### 5.4 Conversation 与 Trace

Conversation 增加或明确冻结：

```text
execution_engine
execution_policy
engine_config_version
rag_index_id
support_release_id
focus_index_id
focus_document_id
```

Trace 至少保存：

```text
engine
index_id
support_release_id
parent_trace_id
fallback_from
fallback_reason
```

## 6. 目标后端行为

### 6.1 上传与构建

上传继续创建不可变候选 Index。Pipeline 成功后：

1. 对 Index 执行 RAG 技术预检；
2. 在独立事务中自动更新 `rag_active_index_id`；
3. 将旧 RAG Index 标记为 `SUPERSEDED/DEPRECATED`，但不立即删除；
4. 异步提交 Profile 生成任务；
5. Profile 生成失败不回滚已经成功的 RAG 激活；
6. Profile 任务状态和 RAG 激活状态分别记录。

### 6.2 Support 发布

Support Release 发布事务只做：

1. 锁定 Knowledge Base、Release、Draft 和目标 Index；
2. 重跑 Profile 硬规则、冲突、证据和文档完整性检查；
3. 发布所有明确处置的 Draft；
4. 更新 `support_active_release_id`；
5. 将旧 Support Release 标记为 `SUPERSEDED`；
6. 不修改 `rag_active_index_id` 和 RAG Index 状态。

### 6.3 Readiness

RAG readiness：

- `rag_active_index_id` 存在且 Index 可查询；
- Query Embedding 与 Index 快照兼容；
- Prompt、LLM、Rerank 满足当前策略要求。

Support readiness：

- `support_active_release_id` 存在且已发布；
- Release 对应 Index 仍存在；
- 至少一个 Profile 可用；
- Profile 文档存在于冻结 Index；
- 全文 Element/图片资源完整；
- LLM 满足持续问答要求。

### 6.4 GC

GC 删除前必须排除：

- RAG Active Index；
- Support Active Release 的 Index；
- 未终结 Support Release 的 Index；
- 活跃任务引用的 Index；
- 保留期内 Conversation 的冻结 Index；
- 运维显式保留的 Index。

## 7. 目标管理台

### 7.1 Documents

每篇文档同时展示：

- Processing 状态；
- RAG 状态与版本；
- Support 状态与版本；
- Profile Review 快捷入口。

### 7.2 RAG Indexes

现有 Index Build 页面收敛为技术运维页，只展示：

- Pipeline；
- Index 快照；
- 模型和构建原因；
- 自动激活状态；
- 失败重试；
- 旧索引 GC。

不得继续承载 Profile Draft 编辑和 Support 发布主操作。

### 7.3 Support Profile Review

新增独立一级页面，提供：

- 待审核/已批准/已拒绝/质量待复核统计；
- Support Release 列表；
- Profile Draft 列表；
- CREATE/UPDATE/NONE 操作选择；
- 标准问题、Alias、关键词、产品、型号、任务类型、路由摘要、症状、排除项编辑；
- 生成依据、质量提示和冲突展示；
- 批准、拒绝、重新生成；
- Release 验证与发布；
- 基于 Index 版本、当前 RAG 版本、当前 Support 版本的版本差提示。

### 7.4 Support Published Profiles

后续独立只读页面展示当前 Support Active Release 及已发布 Profile，并提供创建下一批 UPDATE Draft 的入口。

## 8. API 演进

阶段一复用现有 API：

```text
GET   /knowledge-bases/{kb_id}/releases
GET   /releases/{release_id}
PATCH /profile-drafts/{draft_id}
POST  /profile-drafts/{draft_id}/regenerate
POST  /releases/{release_id}/validate
POST  /releases/{release_id}/publish
```

阶段二新增聚合/运行状态 API：

```text
GET /knowledge-bases/{kb_id}/engine-publication-state
GET /knowledge-bases/{kb_id}/support-review-queue
GET /documents/{document_id}/engine-state
```

现有 Release API 可以保留路径以避免客户端破坏性迁移，但响应应增加：

```text
rag_active_index_id
support_active_release_id
support_active_index_id
version_lag
```

## 9. 分阶段执行

### 阶段一：独立 Support 审核工作台

目标：不改动后端联合发布语义，先消除“没有具体审核页面”的操作阻塞，并把当前真实影响写清楚。

任务：

- [x] 新增 `SupportReviewView.vue`；
- [x] 导航新增 `Profile Review`；
- [x] 将旧 `support-profiles` Hash 迁移到新页面；
- [x] 展示候选 Release、Index 版本和 Profile 任务状态；
- [x] 支持 Draft 编辑、批准、拒绝、标记 NONE；
- [x] 支持 Release 验证和现有联合发布；
- [x] 在发布确认中明确阶段一仍会联合切换 RAG Index 和 Profile；
- [x] Documents 的候选审核快捷入口改为新页面；
- [x] Index Build 保留兼容入口，但弱化为跳转到 Profile Review；
- [x] 增加前端测试；
- [x] 通过 type-check、lint、Vitest 和 build。

阶段一验收：

- 管理员无需进入 Index Build Drawer 即可完成 Profile 审核闭环；
- 未审核、明确拒绝、NONE 和 APPROVED 可区分；
- 页面不会声称 RAG 已经自动发布；
- 发布前有清晰的阶段性联合发布风险提示。

### 阶段二：运行指针和发布事务解耦

目标：从数据层解决 RAG 自动发布与 Support 人工发布的冲突。

任务：

- [x] Alembic 增加 `rag_active_index_id`、`support_active_index_id`、`support_active_release_id`；
- [x] 回填并增加外键、引用索引和兼容字段双写；
- [x] Repository 读写迁移为双指针；
- [x] Index Pipeline 成功后自动激活 RAG；
- [x] Support Publish 不再切换 RAG Index；
- [x] Support begin_turn 从 Active Support Release/Index 获取并冻结版本；
- [x] RAG begin_turn 从 RAG Active Index 获取并冻结快照；
- [x] Gateway readiness 分别查询两套状态；
- [x] Trace 保存对应 Release/Index；
- [x] Profile 生成任务与 RAG 构建/激活结果保持独立；
- [x] 增加事务、回滚、兼容和全量回归测试；GC 增加发布与会话引用保护。

阶段二验收：

- 上传成功后无需人工操作即可在新 RAG 会话中查询到新文档；
- Support 继续使用旧发布版本，直到人工发布；
- Support 发布不改变 RAG Active Index；
- 任一发布事务失败都不会破坏另一个引擎的线上版本；
- RAG 与 Support 可以稳定运行在不同 Index 版本。

### 阶段三：管理台双状态与 Index Build 收敛

任务：

- [x] Documents 增加 Processing/RAG/Support 三状态；
- [x] Dashboard 增加 Processing、RAG Online、Support Release 三条健康摘要；
- [x] Index Build 改为 RAG Indexes/技术处理工作台；
- [x] 从 Index Build 移除 Profile 主编辑、审核、验证和发布；
- [x] Profile Review 展示当前 RAG/Support 版本和落后 RAG 的候选；
- [x] Engine Routing readiness 展示 RAG Index、Support Index 与 Support Release 来源；
- [x] 增加 UI 回归测试和本地浏览器页面 E2E（未认证空状态）。

### 阶段四：版本追赶、回滚与 GC

任务：

- [x] Support Release Rebase/Refresh：`POST /releases/{release_id}/rebase` 显式把落后候选迁移到当前 RAG Index；未变化文档原样复用人工草稿（含审核状态），已变化文档重置为待复核并标注需重新生成，离开索引的文档生成 REMOVE 建议，缺失草稿的新文档提交 PROFILE_BACKFILL 补齐；原候选被取消，绝不静默重绑审核结果；
- [x] 未变化文档复用人工编辑 Draft（Rebase 按文件哈希判定复用；Backfill 跳过已有草稿的文档，不覆盖人工编辑）；
- [x] 新增/修改/删除文档生成 CREATE/UPDATE/REMOVE/NONE 建议（`0028` 扩展 `operation` CHECK；REMOVE 发布时软停用线上 Profile，保留审核痕迹）；
- [x] RAG Index 回滚：`POST /knowledge-bases/{kb_id}/rag-index/rollback`，目标版本必须显式给出，双写 `rag_active_index_id` 与兼容 `active_index_id`，幂等，不触碰 Support 指针与会话冻结版本；
- [x] Support Release 回滚：`POST /knowledge-bases/{kb_id}/support-release/rollback`，仅允许回滚到已发布且存在发布时快照（`support_release_profile`）的 Release；恢复线上 Profile 投影、软停用快照之后新增的 Profile；不改 RAG；幂等；
- [x] GC 引用保护扩展：开放（非 CANCELLED）Release 的 Index、`support_active_release_id` 直接引用的 Index、QUEUED/RUNNING 任务引用的 Index 均不可回收；物理删除前在同一事务内复查引用，命中即恢复状态并跳过；
- [x] 冻结会话保留清理：`conversation_retention_days`（默认 90，0 表示永久保留）；GC 显式清理超过保留期且无活动的会话，并在响应中报告清理明细（可审计）；
- [x] NO_MATCH 反馈进入 Profile Review 工作台（高频未命中问题面板，指向下一批 Alias/Profile 草稿）；
- [ ] 真实环境 E2E（连续上传、构建期间再上传、审核期间再上传、Rebase 后重审发布、双链路分别回滚、PostgreSQL/Redis/MinIO/Task/Trace 副作用核对）：由使用者在真实环境执行。

阶段四验收要点（代码层已由测试覆盖）：

- Rebase 分类清晰区分可复用、需重新生成、需人工复核与 REMOVE 建议；
- 人工编辑过的草稿在 Rebase 与 Backfill 中不会被覆盖；
- 两条回滚互相独立，均为显式目标版本，旧会话继续使用冻结版本；
- GC 不删除任何 Active、已发布 Release、开放候选、活跃任务或冻结会话引用的 Index。

## 10. 测试矩阵

### 10.1 后端

- 单元：状态聚合、readiness、Profile 验证、Index 引用保护；
- Repository：双指针回填、锁、并发更新、重复发布；
- Pipeline：构建成功自动激活、激活失败保留旧版本；
- Support：使用 Support Active Release，而非 RAG Active Index；
- RAG：使用 RAG Active Index；
- Gateway：不同 Index 的 Support->RAG fallback Trace；
- GC：所有引用场景均不得误删；
- 迁移：upgrade/downgrade 与历史数据回填。

### 10.2 前端

- 新导航和 Legacy Hash；
- 空审核队列；
- Draft 编辑/批准/拒绝/NONE；
- 验证错误和质量待复核；
- 发布确认与刷新；
- RAG/Support 版本差；
- API 失败不得显示成空数据；
- type-check、ESLint、Vitest、production build。

### 10.3 实际环境

- 上传一篇全新文档；
- 等待 RAG 自动激活并在新会话查询；
- 验证旧 Support 会话不受影响；
- 审核并发布 Support Profile；
- 验证新 Support 会话命中；
- 删除/替换文档后重复完整流程；
- 核对 Trace、Task、MinIO 和数据库副作用。

## 11. 兼容、迁移与回滚

### 阶段一回滚

- 删除新页面和导航映射即可；
- 不涉及数据库和后端契约；
- 阶段三已移除 Index Build Drawer 中的 Profile 编辑和发布入口；Support 操作统一收敛至 Profile Review。

### 阶段二迁移窗口

- 旧 `active_index_id` 与 `rag_active_index_id` 暂时双写；
- readiness 检测不一致并阻止危险发布；
- 数据回填脚本只写新增字段，不删除旧值；
- 切换前后记录 Index/Release 指针审计。

### 阶段二回滚

- 运行逻辑可切回旧 `active_index_id`；
- 新字段保留，不做破坏性删除；
- 已经独立发布的 Support Release 不反向覆盖 RAG 指针；
- 如需完全回退，必须显式选择要恢复的 RAG Index 和 Support Release，禁止按时间猜测。

## 12. 风险清单

| 风险 | 控制措施 |
|---|---|
| Support 审核落后多个 RAG 版本 | 明确展示 version lag，允许独立发布旧快照或显式 Rebase |
| Profile 指向已删除文档 | Support Release 冻结 Index；发布和 readiness 重查文档存在性 |
| GC 删除 Support 使用的旧 Index | 扩展引用图和删除前事务校验 |
| 连续上传造成候选任务重复 | 复用现有幂等和 follow-up build，Profile Task 继续保持每 Index 唯一活动任务 |
| RAG 激活和 Profile 生成部分成功 | 两条结果独立落库，禁止用一个 Task 状态概括 |
| 历史会话版本漂移 | Conversation 冻结 engine-specific Index/Release |
| 管理台把 RAG 与 Support 显示为同一发布状态 | Documents、Dashboard、Profile Review 和 Engine Routing 均显示独立版本/状态；Index Build 不再提供 Support 发布入口 |

## 13. 完成记录

| 日期 | 阶段 | 状态 | 结果 |
|---|---|---|---|
| 2026-08-26 | 计划 | DONE | 完成现状调查、目标模型、迁移、测试和回滚设计。 |
| 2026-08-26 | 阶段一 | DONE | 新增独立 Profile Review 页面；支持 Release 队列、草稿编辑、批准、拒绝、NONE、重建、验证与阶段性联合发布；Documents 和 Index Build 均跳转到新工作台。 |
| 2026-08-26 | 阶段二 | DONE | 新增双运行指针与会话版本冻结；RAG 构建成功自动激活；Support 发布只切换 Support Release；readiness、Trace、GC 和管理台文案按双链路拆分。 |
| 2026-08-26 | 阶段三 | DONE | Documents、Dashboard、Profile Review 和 Engine Routing 展示独立 Processing/RAG/Support 状态；Index Build 收敛为纯 RAG 技术工作台，移除 Profile 主编辑和发布入口。 |
| 2026-08-26 | 阶段四 | DONE（代码与自动化验证） | Rebase/Refresh 与 Draft 复用、REMOVE 建议、RAG 与 Support 显式回滚、GC 引用保护扩展与会话保留清理、NO_MATCH 反馈面板；迁移 `0028`；真实外部模型 E2E 待使用者执行。 |

## 14. 阶段一验证记录

- `npm --prefix frontend run type-check`：通过；
- `npm --prefix frontend run lint`：通过，0 warning；
- `npm --prefix frontend test -- --reporter=dot`：17 个测试文件、99 项测试全部通过；
- `npm --prefix frontend run build`：通过，Vite production bundle 构建成功；
- `git diff --check`：通过；
- 本地 Vite 页面 `#/support-review`：完成浏览器 DOM 与全页布局检查；
- 未在浏览器中写入 Service Token，因此视觉检查覆盖未认证/空队列状态；带真实 Release 数据的渲染与批准动作由组件测试覆盖；
- 构建保留既有单包超过 500 kB 的 Vite warning，本阶段未扩大范围处理代码分包。

## 15. 阶段二验证记录

- 新增 Alembic `0027`，在实际 PostgreSQL 上完成 `0026 -> 0027`，并验证 `0027 -> 0026 -> 0027` 可逆迁移；
- 回填核对：3 个现有知识库的 `active_index_id` 与 `rag_active_index_id` 无差异，已有 Active Index 的 Support 回填无缺失；
- `ruff check`：修改范围通过；
- `mypy app`：100 个后端模块严格类型检查通过；
- 后端全量测试：364 项通过、23 项按外部环境条件跳过；
- 前端 `type-check` 与 ESLint：通过；
- 前端 Vitest：17 个测试文件、99 项测试全部通过；
- 前端 production build：通过；保留既有单包超过 500 kB 的 Vite warning；
- `git diff --check`：交付前再次执行；
- 未执行真实外部模型的上传问答 E2E：当前本机只运行 PostgreSQL、Redis、MinIO，API/Worker 未常驻，且外部模型调用属于环境相关验证；由 Pipeline、Repository、Gateway 和 Support/RAG 全量测试覆盖代码路径。阶段四完整 E2E 将补齐真实连续上传、审核中再上传及回滚场景。

## 16. 阶段三验证记录

- Documents 页面：验证 Processing、RAG、Support 三个独立状态卡与对应跳转入口；
- Dashboard：验证 Processing、RAG Online、Support Release 三个运营摘要，不再将 Support 审核描述为 RAG 发布前置条件；
- RAG Indexes：验证只保留技术构建、任务日志、诊断和 GC；不再呈现 Profile 编辑、审核、验证或 Support 发布操作；
- Profile Review：新增当前 RAG/Support 版本显示，并将落后当前 RAG 的开放候选标记为“落后 RAG”；
- Engine Routing：分别显示 Support Release、Support Index 与 RAG Active Index；
- 前端 `type-check`、ESLint、Vitest：全部通过（17 个测试文件、99 项测试）；
- production build：通过；保留既有单包超过 500 kB 的 Vite warning；
- 本地浏览器：完成 Dashboard、Documents、RAG Indexes 的 DOM/页面布局回归，确认无控制台 error；未写入 Service Token，因此真实数据态继续由组件测试覆盖。

## 17. 阶段四验证记录

- 新增 Alembic `0028`（`problem_profile_draft.operation` 增加 `REMOVE`；新表 `support_release_profile` 发布快照），在真实 PostgreSQL 测试库上完成 `0027 -> 0028`，并验证 `0028 -> 0027 -> 0028` 可逆迁移；本地开发库 `rag_platform` 已升级到 `0028 (head)`；
- 后端 `ruff check`：通过（顺带修复 `test_profile_rebuild_integration.py` 中一个既有未使用导入）；
- 后端 `mypy app`：100 个模块严格类型检查通过；
- 后端全量测试：372 项通过、32 项按环境条件跳过（基线 364+23，新增 8 项单元测试与 9 项 PostgreSQL 集成测试；集成测试默认跳过，配置 `PHASE4_TEST_DATABASE_URL` 后全部通过）；
- 阶段四集成测试（`test_phase4_postgres_integration.py`，真实 PostgreSQL）覆盖：Rebase 分类与人工草稿复用、Rebase 目标校验、REMOVE 发布软停用与快照、Support 回滚恢复快照并停用新 Profile、快照缺失拒绝回滚、RAG 回滚仅切换 RAG 指针且会话冻结不变、GC 保护开放 Release/活跃任务并在删除前事务内复查、过期会话保留清理；
- 前端 `type-check`、ESLint：通过；
- 前端 Vitest：17 个测试文件、104 项测试全部通过（新增 Rebase 确认、Support 回滚、RAG 回滚、回滚按钮可见性、NO_MATCH 反馈面板）；
- 前端 production build：通过；保留既有单包超过 500 kB 的 Vite warning；
- `git diff --check`：交付前执行；
- 真实外部模型 E2E 未在本地执行：本机未常驻 API/Worker 与 Service Token，外部模型调用与真实副作用核对由使用者按第 10.3 节矩阵执行。
