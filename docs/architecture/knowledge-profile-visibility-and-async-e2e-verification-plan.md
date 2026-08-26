# Knowledge Profile 入口、文档 Profile 可见性与异步任务完整验证计划

> 状态：待实施。
>
> 本文以当前工作区为准：Profile 采用“候选 Index / Release / 草稿审核 / 原子发布”模型，
> 耗时操作复用 `task_record`、RQ `maintenance` 队列和前端任务通知。本文不重置或覆盖已有
> 异步删除、Profile 后台生成或 Release 审核改动。

## 1. 目标

新增两个管理员可见、可追踪的能力：

1. **Knowledge > 知识库详情**：在“删除知识库”“重建索引”旁增加“重建 Profile”。
2. **Documents > 文档详情**：展示该文档的已发布 Profile、Alias，以及当前未发布候选草稿的
   Profile/Alias（如有），明确两者的发布状态，不能将草稿误显示为线上生效数据。

同时在所有功能完成后进行一次完整的交付检查。该检查必须覆盖真实 API、数据库迁移、Redis/RQ
worker、MinIO、浏览器交互与任务失败/重试，不以“测试全部通过”作为唯一结论。

## 2. 已确认约束与产品决策

### 2.1 Profile 重建必须仍然走候选发布

Profile 不能直接覆盖线上 `document_problem_profile`。重建产物必须写入可审核的
`problem_profile_draft`，只有 Release 发布时才能与 Index 原子生效。

**Knowledge 详情的“重建 Profile”只是一条快捷入口，不是新的重建机制。**它必须复用
Index Build 中相同的候选解析、任务去重、RQ worker、草稿审核与 Release 发布链路；两个入口
提交到同一候选时，应返回同一个活跃任务，而不是创建两份 Profile 草稿或两个并行 worker。

因此，Knowledge 页面中的“重建 Profile”不能简单地对 Active Index 写数据。后端按下列规则
解析目标：

```text
存在 READY 且可编辑的当前候选 Release
  -> 提交 PROFILE_REFRESH，复用该候选 Index

不存在可编辑候选 Release
  -> 创建一个新的候选 Index 构建；构建成功后自动提交 PROFILE_REFRESH

候选 Index 正在 BUILDING
  -> 合并为该候选的“完成后 Profile 刷新”意图，不创建并行生成任务
```

这样按钮始终有明确结果：要么提交 Profile 任务，要么提交“构建后刷新 Profile”的编排任务；
不会把用户引导到一个因没有 Release 而必然失败的请求。

### 2.2 文档 Profile 信息的展示口径

文档详情必须至少区分下列状态：

| 数据 | 来源 | UI 标签 | 是否影响线上 Support |
| --- | --- | --- | --- |
| 已发布 Profile | `document_problem_profile` | `已发布` | 是 |
| 当前候选草稿 | 当前开放 `knowledge_release` 的 `problem_profile_draft` | `候选草稿 / 未发布` | 否 |
| 无 Profile | 无对应记录 | `尚未配置` | 否 |

Alias 应以标签或逐行列表展示，保留空 Alias 的事实；不可用“无 Alias”伪造默认 Alias。候选草稿
还应展示 `DRAFT / APPROVED / REJECTED`、`CREATE / UPDATE` 和质量状态，避免审核人员误以为它
已经生效。

## 3. 后端实施计划

### 3.1 统一的“知识库重建 Profile”编排接口

新增接口：

```http
POST /api/v1/knowledge-bases/{knowledge_id}/profiles/rebuild
Idempotency-Key: <uuid>
```

返回沿用现有任务提交契约：

```json
{
  "task_id": "...",
  "task_type": "PROFILE_REFRESH | PROFILE_REBUILD_ORCHESTRATOR | INDEX_BUILD",
  "status": "QUEUED",
  "replayed": false
}
```

实现要求：

1. 在同一数据库事务中锁定知识库和当前开放 Release，拒绝 `DELETING`、`DISABLED` 状态。
2. 复用 `PROFILE_REFRESH` 时，只允许一个同 Index 的活跃 Profile 任务；重复点击返回已有任务。
3. 需要新候选时，创建或合并 Index Build，并在 `task_record.metadata` 持久化
   `profile_refresh_after_build: true`。不得仅靠前端内存或 RQ job payload 保存依赖关系。
4. Index Pipeline 完成后读取该意图，提交 `PROFILE_REFRESH`；失败、取消或队列投递失败必须让
   编排任务进入 `FAILED` 并保留可读错误。
5. 所有任务继续走现有 `GET /tasks/{task_id}`、全局任务通知和失败重试策略。

建议涉及模块：

- `backend/app/api/ingestion.py` 或单独的 profile-maintenance router；
- `backend/app/ingestion/repository.py`、`backend/app/releases/repository.py`；
- `backend/app/tasks/maintenance.py`、`backend/app/tasks/ingestion.py`；
- `backend/app/services/ingestion.py`、`backend/app/releases/service.py`；
- 必要时新增 Alembic 迁移，用于持久化“构建后刷新 Profile”意图或任务依赖。

### 3.2 文档 Profile Context 读取接口

新增只读接口，避免前端拉取整个知识库的 Profile 后自行猜测候选归属：

```http
GET /api/v1/documents/{document_id}/profile-context
```

响应建议：

```json
{
  "document_id": "...",
  "published_profile": { "id": "...", "problem_code": "...", "aliases": [] },
  "candidate_draft": {
    "release_id": "...",
    "index_id": "...",
    "index_version": 7,
    "status": "DRAFT",
    "operation": "UPDATE",
    "quality_status": "REVIEW_REQUIRED",
    "problem_code": "...",
    "canonical_question": "...",
    "aliases": []
  }
}
```

规则：

- 文档不存在返回现有的 `DOCUMENT_NOT_FOUND`；调用方只能读取该文档所属知识库的数据。
- 只返回当前、未取消且未发布的候选草稿；多候选是数据异常，按最新候选返回并记录告警，而不是
  随机选择。
- 不向 Documents 页面暴露无关文档的 Profile，也不返回 LLM 的完整推理内容或内部存储路径。
- 已发布 Profile 与候选草稿均复用已有 Pydantic 校验字段；补全 TypeScript 类型和 API 单测。

建议把该查询实现为一个 repository 的确定性 SQL 查询，而不是调用
`GET /support/problem-profiles` 后在浏览器过滤。

## 4. 前端实施计划

### 4.1 Knowledge 详情的“重建 Profile”按钮

修改 `frontend/src/views/KnowledgeView.vue`：

1. 在详情抽屉 footer 中，紧邻“重建索引”增加“重建 Profile”。
2. 点击后显示二次确认，明确说明：
   - 不会直接修改线上 Profile；
   - 有候选时会覆盖该候选中未发布的自动草稿；
   - 无候选时会先创建候选构建，Profile 刷新会在构建完成后执行。
   确认文案应将其描述为“前往/提交当前候选的 Profile 重建”，不能暗示这是另一套独立流程。
3. 使用专属 action key，例如 `rebuild-profile:${knowledgeId}`，只让本按钮短暂 loading；
   禁止恢复整页 `v-loading`。
4. 收到 `202` 后关闭确认，不等待后台生成；调用现有 `trackTask()`，显示“任务已提交”的轻提示。
5. `DELETING`、已停用的知识库、或正在执行互斥删除任务时禁用该按钮并说明原因。

在 `adminStore` 中新增面向知识库的快捷提交方法，而不是让 View 直接拼接 API；该方法调用与
Index Build 相同的编排接口，完成通知仍由 `AdminStudio` 的全局任务轮询统一处理。

### 4.2 Documents 详情的 Profile & Alias 标签页

修改 `frontend/src/views/DocumentsView.vue` 和 `adminStore`：

1. 打开文档抽屉时并行加载 Elements、Chunks、`profile-context`；三者使用文档 ID 和抽屉当前
选择的版本进行过期响应保护。
2. 增加“Profile & Alias”标签页，顺序为：
   - 已发布 Profile 卡片：问题编码、标准问题、产品/型号、关键词、Alias、路由摘要、启用状态；
   - 当前候选草稿卡片：候选 Index 版本、草稿操作、审核状态、质量状态、标准问题、Alias；
   - 没有任何记录时使用明确 Empty State。
3. 不在 Documents 页面提供直接发布或绕过审核的编辑入口；如需修改，提供“前往候选审核”的导航，
   将用户带到对应 Index Build 抽屉。
4. `selectedDocument` 变化、抽屉关闭、知识库切换时清空旧 profile context，防止旧文档 Alias
   短暂显示在新文档上。
5. 为长 Alias、路由摘要使用可换行展示和最大高度，避免 640px 抽屉横向溢出。

## 5. 迁移、兼容与可观测性

1. 新迁移的 `down_revision` 必须以合并后的最新 Alembic head 为准，不能假定当前本地的未提交
   `0024` 已经在每个环境中存在。
2. `task_record` 是任务完成通知与审计的事实来源。知识库物理删除后必须仍可读取删除任务；保持
   `kb_id ON DELETE SET NULL` 与 metadata 中的知识库 ID/名称快照。
3. 所有新提交接口必须支持幂等键或同资源活跃任务去重，避免双击创建两次候选或两份 Profile
   刷新。
4. 日志需包含 `task_id`、`knowledge_id`、`index_id`、`release_id` 与请求 ID；任务 metadata
   记录目标、实际执行阶段、生成数和失败数。
5. 删除任务的 MinIO 对象清单必须可重试；“数据库记录删除了，但存储对象清理失败”不能只写日志。

## 6. 交付前完整检查（不是只跑测试）

所有条目均需记录命令输出、任务 ID、截图或 API 响应作为交付证据。

### 6.1 静态与迁移检查

- 后端 Ruff、格式检查、Mypy；前端 ESLint、`vue-tsc`、生产构建；全量单元测试。
- 在一次性 PostgreSQL 数据库从空库执行 `alembic upgrade head`，再运行 `alembic current`；验证
  新外键、清理清单、任务依赖字段和索引真实存在。
- 从当前版本数据库升级到 head，验证已有知识库、Index、Profile、任务记录仍可读取。
- 若迁移提供 downgrade，使用临时数据库执行 upgrade/downgrade/upgrade，验证 DDL 可逆或明确
  标记为不可逆并给出恢复方案。

### 6.2 真实异步任务链路

使用真实 PostgreSQL、Redis、MinIO、RQ worker 和 fake/测试模型完成如下流：

1. 创建知识库、上传至少两份 DOCX、构建候选 Index；确认 HTTP 很快返回 `202`，而耗时工作仅在
   worker 中运行。
2. 等待 Index 和 Profile 任务到达终态；检查 `task_record` 的阶段、进度、metadata、错误信息，
   并确认 Release 中出现可审核草稿。
3. 从 Knowledge 详情提交“重建 Profile”：分别覆盖“已有 READY 候选”“只有 Active Index”
   “正在 BUILDING”三个分支，确认去重/编排逻辑正确且不产生并行重复生成。
4. 让 LLM 或 MinIO 人为延迟/失败：确认提交接口不超时、任务转 `FAILED`、页面弹出失败提醒、重试
   后可以完成，且没有泄漏或重复数据。
5. 删除知识库：确认先可见 `DELETING`，后台清理所有对象后才删除数据库数据；完成后
   `GET /tasks/{task_id}` 仍能返回 `SUCCEEDED`。重复点击、刷新页面和存储删除失败重试均需验证。

### 6.3 浏览器验收

在真实浏览器而非只在组件测试中检查：

1. Knowledge 详情三个操作按钮的位置、禁用状态、确认文案、局部 loading 与 `202` 后可继续导航；
2. 提交 Profile/删除任务后切换到 Documents、Logs、Index Build，页面不被全局遮罩阻塞；
3. 完成时只收到一次 Element Plus 成功/失败通知；刷新页面后仍能追踪未完成任务，已完成任务不重复
   弹窗；
4. Documents 抽屉的已发布 Profile、候选草稿、Alias 空列表和无 Profile 四种状态；切换文档和
   切换知识库时不得出现陈旧数据；
5. 发布候选后重新打开文档详情，候选草稿应转为已发布数据，Alias 与实际 Support 路由结果一致。

### 6.4 数据完整性、权限与回归

- 一个文档最多有一个线上 Profile；同一候选最多有一份草稿；Alias 冲突、`UPDATE` 目标和审核状态
  均受现有发布验证保护。
- 未发布草稿不影响 Support/RAG 实际问答；发布后 Index 与已审核 Profile 原子生效。
- 无 token、错误 token、跨知识库 document ID 的 profile-context 请求均被正确拒绝。
- 回归上传、文档删除、索引重试、索引发布、旧快照激活、索引 GC、知识库删除、任务列表和日志页。

### 6.5 交付门槛

只有满足以下条件才可宣布完成：

- 所有自动检查通过；
- 6.2、6.3、6.4 每项均有实际环境证据；
- 没有未解释的 `QUEUED/RUNNING/DELETING` 残留记录；
- 失败任务可定位、可重试，且不会造成前端无限 loading；
- 迁移步骤、回滚/恢复步骤和已知限制写入发布说明。

## 7. 实施顺序

1. 先修复并验证候选 Release / Index / Profile 的控制面一致性；
2. 实现后端编排接口和文档 profile-context 查询，连同迁移与 API 测试；
3. 实现 Knowledge 详情按钮和 Documents Profile & Alias 标签页；
4. 补齐单元、API、worker 集成和浏览器端到端测试；
5. 按第 6 节在独立环境执行完整检查，汇总证据后再合并或发布。
