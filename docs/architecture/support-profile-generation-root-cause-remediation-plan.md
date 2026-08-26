# Support 自动 Profile 根因修复执行计划（已实施）

> 状态：代码、迁移与目标回归测试已完成；未修改已有候选草稿，也未对生产知识库执行回填。
> 本版以 2026-08-26 当前工作区为准，替代本文中与当前实现冲突的前端路径和执行顺序。

## 0. 复核结论与前置架构修复

### 0.1 当前代码状态

当前工作区不是干净状态，包含一组尚未提交的“后台维护任务”改动：

- 新增 `maintenance` 队列、`PROFILE_GENERATE / BACKFILL / REFRESH / REGENERATE`
  任务提交与轮询；
- 为知识库删除增加后台任务和 `0024_background_maintenance_tasks` 迁移；
- Index Build 页面增加“重建 Profile 草稿”按钮。

这些改动必须保留，不在本计划中覆盖、重置或删除；实施时应先分别通过其已有测试，
并将 Profile 质量改造接入同一维护队列，避免出现两套异步执行器。

但已提交的 `f57350f（管理页面改善）` 同时产生了一个控制面断点：

1. 新索引构建不再创建 `knowledge_release`，而是取消所有未发布 Release；
2. 索引构建完成后不再调用 Profile 自动生成；
3. Index Build 页面直接调用索引激活接口；
4. Profile 回填、刷新、重新生成 API 和当前未提交的后台任务仍要求存在可编辑
   `knowledge_release`。

因此，在当前状态下，Profile 维护任务即使成功入队，也会因找不到候选 Release 而
无法完成；直接发布索引还会绕开 Profile 审核与原子发布。这与
`support-profile-rag-fallback-execution-plan.md`、
`support-profile-maintenance-execution-plan.md` 和
`intelligent-knowledge-release-execution-plan.md` 共同定义的架构冲突。

### 0.2 本计划确认的架构决策

本计划继续采用已确认的发布模型，而不是把 Profile 降级为独立、可绕过的附属功能：

```text
Index Build 创建候选 Index + 一个 Release
  -> 后台生成/复核 Profile 草稿
  -> 管理员审核草稿与质量诊断
  -> 发布前验证
  -> Release 原子发布 Index + Profile
```

管理页面不恢复已删除的独立 `SupportProfilesView.vue`。发布审核能力将整合进当前
`IndexBuildView.vue` 的候选详情抽屉/面板中；Index Build 仍是唯一入口。

当前候选中的草稿、冲突和审核状态明确不在本计划处理范围内。

### 0.3 新增任务 0：恢复候选发布控制面

#### 后端

1. 在 `IngestionRepository` 创建 `activate_on_success=false` 的候选 Index 时：
   - 取消同一知识库的旧开放 Release；
   - 为新 Index 创建一个 `knowledge_release(status='DRAFT')`；
   - 保持一个 Index 只对应一个 Release。
2. 恢复索引构建完成后的自动 Profile 任务提交：
   - Index Pipeline 成功并将 Index 置为 `READY` 后，提交 `PROFILE_GENERATE`；
   - 复用当前工作区尚未提交的 `maintenance` 队列和 `TaskRecordRepository`；
   - 生成失败只记录 Release 的 `generation_error` 和任务失败，不把完成的候选
     Index 错误标记为构建失败。
3. 恢复索引激活保护：存在未发布 Release 的 Index 不允许直接调用旧激活接口；
   只能通过 `ReleaseRepository.publish()` 完成 Index 与 Profile 的原子切换。
4. 保留原子发布事务中的最终复查，不能只依赖后台任务或前端状态。

#### 前端

1. `IndexBuildView.vue` 的“审核并发布”改为打开候选审核，而不是直接激活 Index。
2. 在当前 Index 详情抽屉中加载 Release、草稿、后台任务进度与验证结果。
3. 将当前“重建 Profile 草稿”按钮绑定到存在候选 Release 的 Index；没有候选时
   显示创建候选的引导，不能提交一个必然失败的任务。
4. 恢复 `adminStore` 中最小必要的 Release 工作区状态和 API 刷新逻辑，但不恢复
   已移除的独立导航入口。

#### 验收

- 上传或手工构建后，只有一个可操作的候选 Release；
- Index 完成后自动提交 Profile 生成任务，前端可查看任务状态；
- 未发布 Release 存在时，直接激活接口返回 `RELEASE_REQUIRED`；
- 发布成功后，索引和已审核 Profile 在一个事务中同时生效。

## 1. 目的与范围

本计划修复自动生成 Support Profile 的系统性问题，不处理当前候选中的具体草稿、
Alias 冲突或审核操作。

目标是让新增文档、旧文档回填和全量刷新都能稳定产生**可审核的高质量草稿**：

```text
文档解析完成
  -> LLM 提取文档核心问题与证据
  -> LLM 生成并复核 Profile
  -> 确定 CREATE / UPDATE
  -> 预检冲突与来源一致性
  -> 写入待审核草稿
  -> 人工审核后发布
```

本计划不包含：

- 自动批准或自动发布 Profile；
- 删除、重传、修改当前已上传文档；
- 修改当前发布候选中的 25 份草稿；
- 在线问答路径增加新的 LLM 调用。

## 2. 已确认根因

### 2.1 同一文档被误判为 CREATE

当前 `CREATE / UPDATE` 判断只比较新 Profile 的标准问题、文档标题与线上标准问题的
文本相似度，阈值为 `0.72`。它没有优先判断 `document_id` 是否已经拥有线上
Profile。

同一文档因 LLM 改写标准问题后，文本相似度可以低于阈值，从而被错误创建为新的
Profile，并在发布验证时产生问题编码或 Alias 冲突。

### 2.2 Alias 与关键词没有严格限定为核心问题

当前 Prompt 要求每篇文档生成 5～10 条 Alias。对于信息量有限的文档，模型会把
正文中的设备位置、图片描述、相关设备等背景信息扩展为 Alias 或关键词。

例如 DAU 通讯线缆文档包含 VAU 机箱的安装位置和图片文字，模型却将 VAU 作为
DAU 问题的 Alias。该内容虽然出现在正文中，但不是文档所解决问题的主体。

### 2.3 冲突仅在发布前发现

问题编码、标准问题和 Alias 的冲突目前在发布前验证阶段集中暴露。生成阶段没有
足够的来源一致性检查和冲突预检，导致低质量草稿积累到发布阶段。

### 2.4 待审核状态被表现为验证失败

草稿初始状态为 `DRAFT` 是正常流程；但发布前验证将每一份未批准草稿记录为错误，
并将候选置为 `VALIDATION_FAILED`。这混淆了“尚待审核”和“存在真实发布阻塞”。

## 3. 设计原则

1. 文档身份优先于语义相似度：同一文档已有线上 Profile 时必须更新该 Profile。
2. 精确性优先于 Alias 数量：没有可靠改写时允许 Alias 为空或较少。
3. LLM 负责语义理解，确定性规则负责身份、冲突与状态边界。
4. 所有自动结果仍写入候选草稿，不直接影响线上 Support 路由。
5. 离线生成可增加一次质量复核；运行时 Profile 匹配不增加 LLM 延迟。

## 4. 实施任务

### 任务 A：按文档身份确定 UPDATE

#### 改动

调整 `ReleaseService._suggest_operation` 的输入，传入当前 `document_id`。

决策顺序改为：

```text
线上存在相同 document_id 的 Profile
  -> UPDATE，target_profile_id 固定为该 Profile

否则，存在通过语义相似度判定的替代目标
  -> UPDATE，target_profile_id 为相似目标

否则
  -> CREATE
```

同一文档存在多个线上 Profile 属于数据异常：不自动选择，生成 `NONE` 或标记为
`REVIEW_REQUIRED`，并返回明确诊断，不得创建新的重复 Profile。

#### 涉及模块

- `backend/app/releases/service.py`
- `backend/tests/test_knowledge_releases.py`

#### 验收

- 同一 `document_id` 且文本相似度低于阈值时，仍生成 `UPDATE` 草稿。
- 草稿继承原 Profile 的稳定 `problem_code`、启用状态和目标 ID。
- 不同文档仍可使用现有相似度逻辑建议替代或新增。

### 任务 B：两阶段离线 Profile 生成与来源复核

#### 改动

将当前单次 LLM 生成改为“提取范围 -> 生成并复核”两阶段离线流程。

新增仅用于离线生成的 Pydantic 输出模型，不让未验证的 LLM 字段直接进入草稿：

- `ProfileScope`：`core_subject`、`task`、`symptoms`、`exclusions`、`evidence`；
- `ProfileQualityReview`：修订后的 `GeneratedProfile`、`quality_status`、
  `quality_notes` 和按字段归属的 `evidence`。

每份文档按以下顺序执行：

```text
正文 + 标题
  -> ProfileScope（识别操作对象与证据）
  -> GeneratedProfile（按范围生成 Profile）
  -> ProfileQualityReview（核对主体、Alias、关键词和证据）
  -> 确定性预检
  -> 写入候选草稿
```

生成和复核都在当前 `maintenance` 后台任务中运行，不增加 Support 在线问答的
LLM 调用与延迟。单篇文档任一 LLM 阶段不可用时，任务继续处理其余文档；该文档写入
保守草稿和明确的 `quality_status/quality_notes`，最终由管理员审核。

第一阶段输出结构化问题范围：

- `core_subject`：文档实际操作或故障对象；
- `task`：检查、升级、更换、配置、排障等任务；
- `symptoms`：文档明确支持的症状或错误码；
- `exclusions`：明确不适用或容易混淆的范围；
- `evidence`：支持上述判断的简短正文片段或段落序号。

第二阶段接收正文、问题范围和候选 Profile，执行以下复核：

1. Alias 只能是核心标准问题的用户改写，不能把背景设备转变为问题主体。
2. Keyword 必须服务于核心对象或任务，删除位置说明、图片中的旁路设备和无关型号。
3. 每一项症状、排除范围、错误码必须能在正文证据中找到依据。
4. 无可靠改写时允许返回 `0` 条 Alias；上限为 `8` 条，移除“至少 5 条”的要求。

若第二阶段模型不可用或复核失败：保留保守草稿，并设置 `generation_error` 与
草稿质量诊断；不得伪装为已验证的 LLM 结果。

#### 数据模型与界面

新增一份迁移（执行时以当前 Alembic head 为 `down_revision`，预期在当前未提交
`0024_background_maintenance_tasks` 之后）并只扩展 `problem_profile_draft`：

- `quality_status`: `PASS`、`REVIEW_REQUIRED`、`FALLBACK`、`MANUAL_REVIEWED`；
- `quality_notes jsonb`: 来源缺失、主体不一致、预检冲突、复核降级等说明；
- `quality_evidence jsonb`: 字段、正文摘录与段落/元素标识；仅供审核，不进入线上
  Profile 路由。

人工编辑任一 Profile 字段后，草稿继续回到 `DRAFT`，并标为 `MANUAL_REVIEWED`；
保留生成阶段诊断供追溯，但人工批准仍是发布的唯一授权动作。

管理台草稿列表展示质量状态和诊断；`REVIEW_REQUIRED` 草稿不允许批量批准，
但管理员仍可修订后单独批准。

#### 涉及模块

- `backend/app/releases/models.py`
- `backend/app/releases/service.py`
- `backend/app/releases/repository.py`
- 新增 Alembic 迁移
- `frontend/src/types/admin.ts`
- `frontend/src/views/IndexBuildView.vue`
- `frontend/src/composables/adminStore.ts`

#### 验收

- DAU 文档中仅作为位置或图片描述出现的 VAU，不会成为 DAU Profile 的 Alias 或
  Keyword。
- 对证据不足的文档，Alias 数量可以小于 5，且草稿标记为待复核而非编造补全。
- LLM 复核故障时，系统仍生成可编辑的保守草稿并明确降级原因。

### 任务 C：生成阶段的确定性预检与冲突反馈

#### 改动

在写入草稿前增加预检，不等待发布前验证才发现显而易见的问题：

1. 以 `document_id`、目标 Profile 和操作类型检查更新关系。
2. 比较候选草稿与线上 Profile、同候选其他草稿的：
   - `problem_code`；
   - 规范化标准问题；
   - 规范化 Alias。
3. 对设备实体做主体一致性检查：从标题、`core_subject` 和正文中可识别的设备/
   型号标识获取预期主体；Alias/Keyword 引入其他设备实体时，标记为
   `REVIEW_REQUIRED`，而不是自动删除。
4. 将预检结果写入 `quality_notes`；真实唯一性冲突保留为发布阻塞，但在草稿生成后
   立即可见。

预检不得将有冲突的草稿自动改成 `NONE`，也不得自动删除用户内容；应保持可编辑、
可追溯。

预检的冲突比较必须排除当前 `UPDATE.target_profile_id` 对应的线上 Profile；否则
合法更新会被误报为与自身冲突。相同 `document_id` 的线上 Profile 则由任务 A 的
硬规则优先转为 `UPDATE`。

#### 涉及模块

- `backend/app/releases/service.py`
- `backend/app/releases/repository.py`
- `backend/app/releases/models.py`
- `frontend/src/views/IndexBuildView.vue`

#### 验收

- 生成重复 `problem_code` 或 Alias 时，草稿生成完成即可展示冲突原因。
- 发布前验证仍保留相同的最终兜底检查。
- 预检不会修改线上 Profile，也不会自动批准草稿。

### 任务 D：拆分“待审核”与“真实验证失败”状态

#### 改动

调整发布候选验证结果的状态语义：

```text
存在未审核草稿，且没有真实质量/完整性阻塞
  -> REVIEW_REQUIRED（或保持 DRAFT 并提供待审核摘要）

存在编码、标准问题、Alias、文档完整性或索引状态阻塞
  -> VALIDATION_FAILED

全部草稿已审核通过且无阻塞
  -> READY_TO_PUBLISH
```

验证报告拆分字段：

- `review_required`: 待批准草稿及数量；
- `errors`: 真实发布阻塞；
- `warnings`: 非阻塞质量提示。

为避免破坏已有 API 客户端，保留原 `valid/errors/warnings` 字段，并新增：

- `publishable`: `errors` 为空且没有待审核草稿时为 `true`；
- `review_required_count`: 未批准草稿数量；
- `review_required`: 草稿 ID、文档名、状态与质量状态的摘要。

语义定义为：`valid` 仅表示不存在真实硬错误；`publishable` 才表示可立即发布。
`save_validation()` 在仅待审核时将 Release 保持为 `DRAFT`，只在真实错误时写入
`VALIDATION_FAILED`，全部满足时写入 `READY_TO_PUBLISH`。`publish()` 对存在待审核
草稿返回明确的 `RELEASE_REVIEW_REQUIRED`，而不是依赖下游的泛化状态错误。

#### 涉及模块

- `backend/app/releases/models.py`
- `backend/app/releases/service.py`
- `backend/app/releases/repository.py`
- `frontend/src/types/admin.ts`
- `frontend/src/views/IndexBuildView.vue`
- 相关 API 与测试

#### 验收

- 仅因草稿尚未审核时，界面显示“待审核”，不显示为“验证失败”。
- 存在 Alias 或问题编码冲突时，仍明确显示“发布阻塞”。
- 发布接口仍只接受所有草稿已审核且没有真实阻塞的候选。

## 5. 执行顺序

1. 先冻结并单独验证当前未提交的后台维护任务改动；不使用 `git reset`、
   `git checkout` 等操作覆盖它们。
2. 实施任务 0：恢复候选 Release 创建、构建完成后的自动 Profile 任务提交、直接
   激活保护，以及 Index Build 内候选审核入口。先用一个新建文档验证控制面闭环。
3. 实施任务 A，并加入同文档低相似度回归测试，消除确定性的重复 `CREATE` 问题。
4. 定义质量状态、诊断和证据数据结构；在当前迁移链末尾新增质量迁移。
5. 实施任务 B 的两阶段离线生成、按文档降级与后台任务进度上报。
6. 实施任务 C 的预检，将同文档、实体一致性和碰撞结果写入质量诊断。
7. 实施任务 D 的验证报告/状态拆分，并在 Index Build 候选审核界面展示待审核、
   硬阻塞和质量提示。
8. 扩充端到端测试：新增文档、同文档刷新、回填、全量刷新、LLM 降级、冲突、
   后台任务和原子发布。
9. 在独立测试知识库执行一次全量 Profile 刷新，人工抽样审查结果后再用于生产知识库。

## 6. 测试与验收清单

### 后端

- 同一文档、低文本相似度时仍为 `UPDATE`。
- 不同文档的真实替代关系仍可判为 `UPDATE`。
- 候选 Index 创建后一定拥有一个可编辑 Release；构建完成后自动存在
  `PROFILE_GENERATE` 维护任务。
- 有开放 Release 的候选 Index 不能通过直接激活接口绕过审核。
- 设备仅出现在位置说明或图片描述时，不成为 Alias 问题主体。
- Alias 可少于 5 条，且不因数量不足生成填充词。
- LLM 生成或复核异常时保守降级，草稿仍可编辑。
- 后台 Profile 任务能上报 `QUEUED / RUNNING / SUCCEEDED / FAILED`、文档数量和进度；
  失败不会把已完成的 Index 构建误判为失败。
- 预检与发布前验证均能识别编码、标准问题和 Alias 冲突。
- 未审核草稿不会被归类为真实验证失败，且 `valid` 与 `publishable` 语义有测试覆盖。
- 发布事务和线上 Profile 路由行为不回归。

### 前端

- Index Build 候选审核界面显示生成来源、质量状态、质量诊断、后台任务状态。
- 待审核、质量提示、真实阻塞三类信息分开展示。
- 质量待复核草稿不能被批量批准。
- 编辑、重新生成、回填和全量刷新路径保持可用，且不会调用不存在 Release 的 API。

### 运行环境

- 数据库迁移成功应用到 head。
- `backend` 和 RQ `worker` 均运行，且 worker 订阅 `index_build` 与 `maintenance`
  队列；否则索引及离线 Profile 任务不会被消费。
- LLM 可用与不可用两种场景均完成验证。

## 7. 发布与回滚

### 发布策略

先部署代码和迁移，在测试知识库验证“候选创建 -> 后台 Profile 生成 -> 审核 ->
原子发布”闭环；确认后再对生产知识库执行回填或全量刷新。生成结果仅位于候选发布
版本，审核发布前不会影响线上问答。

### 回滚

- 未发布候选：废弃候选或退回草稿即可，线上 Profile 不变。
- 已部署代码：通过 Profile 生成开关关闭第二阶段复核和预检，回退为保守草稿生成；
  不回退数据迁移字段。
- 控制面回滚：仅在确认 Profile 候选功能需要停用时，禁用候选创建/生成入口；不得
  恢复“候选存在时可直接激活索引”的绕过路径。
- 不执行自动删除或覆盖线上 Profile 的回滚动作。

## 8. 完成定义

满足以下条件才视为本计划完成：

1. 当前 Index Build、后台任务和 Release 重新形成单一候选控制面，不存在直接激活
   绕过 Profile 审核的路径。
2. 同文档刷新不再产生重复 `CREATE` Profile。
3. 自动草稿不再以 Alias 数量为目标编造背景设备问法。
4. 冲突在草稿生成后即可定位，发布前验证仍保留最终保护。
5. 管理台能明确区分“待审核”和“真实发布阻塞”，并展示质量与后台任务状态。
6. 后端静态检查、迁移检查、相关单元/集成测试及前端类型检查、Lint、测试均通过。

## 9. 复核记录（2026-08-26）

实施前已完成的工作：

- 读取当前 Git 状态、最近提交及所有与 Profile 发布相关的未提交改动；
- 对照三份既有技术文档，确认它们一致要求“候选 Release 审核后原子发布”；
- 发现当前提交的 Index Build 直接发布策略与上述架构冲突，且会让已存在的
  Profile API / 后台任务缺少候选上下文；
- 将原计划的独立 `SupportProfilesView.vue` 路径调整为现有
  `IndexBuildView.vue` 内的候选审核体验；
- 将当前未提交的后台维护任务作为需要保留、验证并接入的前置基线。

## 10. 实施记录（2026-08-26）

已完成：

- 任务 0：候选 Index 创建时恢复 `knowledge_release`，构建完成后通过 `maintenance`
  队列提交 `PROFILE_GENERATE`；未发布候选不能通过旧索引激活接口绕过发布。
- 任务 A：同一 `document_id` 已有线上 Profile 时强制生成 `UPDATE`，不再依赖文本相似度。
- 任务 B：实现“范围提取 -> Profile 生成 -> Profile 质量复核”三阶段离线 LLM 流程；
  Alias 改为允许 `0--8` 条，模型不可用时写入可编辑的 `FALLBACK` 草稿。
- 任务 C：生成后立即对问题编码、标准问题与 Alias 执行线上集合冲突预检；更新草稿
  排除自身目标 Profile，冲突写入质量诊断并保留最终发布校验。
- 任务 D：`valid` 只表示无硬错误，`publishable` 表示可立即发布；待审核或质量待复核
  草稿不会显示为验证失败，发布返回 `RELEASE_REVIEW_REQUIRED`。
- 管理台：在 `IndexBuildView.vue` 实现候选详情、后台 Profile 任务状态、草稿编辑/
  批准、质量状态、发布前验证和原子发布入口。
- 数据库：开发数据库已从 Alembic `0023` 升级到 `0025`，新增后台任务表和
  `problem_profile_draft` 质量字段；未重写任何既有草稿数据。

验证结果：

- 前端类型检查、Lint 和 92 项前端测试通过。
- 后端相关静态检查通过，Profile/Release 目标回归 14 项通过。
- 全量后端测试为 357 通过、4 跳过、3 项既有失败：缺少
  `support-acceptance/cases.jsonl` 的两项评测，以及 Support 模块仍导入通用 RAG
  router 的隔离约束一项；均不由本次改动引入。

尚未执行：在独立测试知识库实际上传文档、由运行中的 API/RQ worker 消费任务、调用
真实 LLM 并人工抽样审核的运行时验收。当前工作区没有运行中的 `backend`/RQ worker，
因此不将该项伪报为已完成。
