# Document Profile 属性化与按知识库上线执行计划

## 1. 文档状态

- 状态：`IMPLEMENTED`（旧 Release 读路径处于只读兼容期）
- 编写日期：2026-08-26
- 决策范围：Support Profile 的产品语义、数据模型、管理界面、路由和迁移；不改变 DOCX 摄取、RAG 索引构建及现有文档删除语义。
- 非目标：本计划不新建“高级操作”页面，不把现有 Profile 操作迁移到其他页面。

## 2. 背景与问题

## 实施记录（2026-08-26）

- 已新增 `document_profile`、`document_profile_match_term` 和
  `profile_publish_event`，并通过迁移将旧线上 Profile 回填为 `active_payload`；
  未发布旧草稿只回填为 `pending_payload`，已发布/已取消 Release 保持历史审计。
- 已实现按知识库生成、单文档生成、编辑待上线值、检查、原子上线和取消待上线值接口；
  新生成任务不再依赖可编辑 Release 或候选 Index。
- Support 运行时优先读取新线上属性；待上线、保守生成和失败属性不会参与快速命中，
  继续使用 RAG。旧 Profile 表仅在新属性尚未迁移时只读回退。
- 已将 Profile Review 收敛为同页文档属性工作台，并在 Documents 列表/详情展示
  Profile 状态、线上属性和待上线属性。
- 上线前仍需在目标环境执行 `alembic upgrade head`，再滚动重启 API 与 Worker。

当前实现将 Profile 建模为一套独立的候选 Release：生成草稿、逐条审核、发布前验证、发布、Rebase、历史 Release 和回滚。该模型适合“Support 是一个独立版本化产品”的场景，但不符合本项目对 Profile 的期望：它只是帮助高置信问题快速命中文档的属性，Generic RAG 仍是完整能力和默认兜底。

当前模型带来的问题包括：

- 上传一个文档后，管理员必须理解 Candidate、Draft、Approval、Validation、Release 等多个概念；
- LLM 的 JSON 包装、超时等技术问题会以 Release 错误形式长期占据审核页面；
- 已发布历史 Release 与当前线上 Release 容易在页面上被误解为同一种“已发布”；
- 修改一个文档属性需要经过与 RAG 索引版本绑定的复杂流程，实际收益不成比例；
- Documents 页面无法直观说明一份文档的 Profile 是否已生成、待上线、已上线或不可用于快速命中。

本计划将产品语义收敛为：**Profile 是文档属性，按知识库上线；RAG 不依赖 Profile，并在 Profile 不可靠时兜底。**

## 3. 目标体验

### 3.1 用户视角流程

```text
上传文档
  -> RAG 正常构建并自动可用
  -> 后台为每份文档生成待上线 Profile 属性
  -> 在同一 Profile Review 页面选择知识库
  -> 查看 / 编辑 / 单篇重新生成 / 全量重新生成属性
  -> 点击“上线 Profile 变更”
  -> 当前知识库所有待上线属性原子生效
```

Profile 生成失败、低置信或发生歧义时，文档仍然通过 RAG 使用；它们不阻断文档上传和 RAG 索引激活。

### 3.2 页面不拆分原则

所有已有的维护能力留在 **Profile Review** 页面：

- 选择知识库；
- 编辑属性；
- 单篇重新生成；
- 重建全部草稿（重命名为“重新生成全部待上线属性”）；
- 发布前校验（重命名为“检查待上线变更”）；
- 上线；
- 查看任务状态、失败原因和历史上线记录；
- 回滚（若保留）。

不同的是，页面按状态禁用不合法操作，并在原位置展示原因、任务进度和结果；不跳转到其他页面，也不把功能藏入“高级操作”。

## 4. 领域模型与状态

### 4.1 Profile 的职责边界

Profile 的职责是对高频、高置信问题提供快速、稳定的文档候选；它不是 RAG 的替代品。

```text
高置信且无歧义的 Profile 命中
  -> 直接选择关联文档，进入 Support 快速路径

无 Profile / 待上线 / 已停用 / 低置信 / 候选分数接近
  -> 按现有策略走 Generic RAG
```

必须避免“错误 Profile 命中后不再进入 RAG”的情况。Profile 缺失可接受，错误命中不可接受。

### 4.2 单文档属性状态

对管理端和 Documents 页面只暴露以下聚合状态：

| 状态 | 含义 | 是否参与快速命中 |
|---|---|---|
| `NOT_GENERATED` | 尚未生成 Profile | 否，走 RAG |
| `GENERATING` | 后台生成中 | 否，走 RAG |
| `PENDING_ONLINE` | 已生成或编辑，等待知识库上线 | 否，走 RAG |
| `ONLINE` | 当前线上属性可用 | 仅高置信、无歧义时参与 |
| `NEEDS_ATTENTION` | 保守生成、低置信或质量异常 | 否，走 RAG |
| `DISABLED` | 管理员明确不使用 Profile | 否，走 RAG |
| `FAILED` | 生成任务失败 | 否，走 RAG |

`ONLINE` 与“文档已被 RAG 索引”是两个独立事实。任何 `NOT_GENERATED`、`FAILED` 或 `NEEDS_ATTENTION` 文档都必须继续可被 RAG 检索。

### 4.3 上线语义

“上线”在本计划中指发布 Profile 属性变更，不能称为“上传”，以避免与源 DOCX 上传混淆。

- 首次生成 Profile：生成结果进入待上线态；
- 编辑或重新生成线上 Profile：新结果进入待上线态，原线上值保持不变；
- 点击“上线 Profile 变更”：同一知识库所有通过硬校验的待上线值原子替换为线上值；
- 文档删除：该文档的线上值和待上线值一并删除；
- 知识库删除：该知识库所有 Profile 属性一并删除；
- 取消待上线变更：仅删除待上线值，保持原线上值不变。

## 5. 数据模型设计

### 5.1 推荐的存储模型

产品上是“文档属性”；物理上建议保留独立表，避免把匹配字段塞入 `document_source` 并影响查询、审计和级联删除。

```text
document_profile
  document_id            PK / FK document_source(id) ON DELETE CASCADE
  kb_id                  FK knowledge_base(id) ON DELETE CASCADE
  active_payload         jsonb null
  active_version         integer not null default 0
  active_updated_at      timestamptz null
  active_source          AUTO | MANUAL
  active_confidence      numeric null
  enabled                boolean not null default true

  pending_payload        jsonb null
  pending_source         AUTO | MANUAL
  pending_confidence     numeric null
  pending_state          PENDING_ONLINE | NEEDS_ATTENTION | FAILED | null
  pending_error_code     text null
  pending_error_message  text null
  pending_updated_at     timestamptz null
```

`active_payload` 和 `pending_payload` 至少包含：

```json
{
  "intents": [
    {
      "problem_code": "PDC_UPGRADE",
      "canonical_question": "如何升级 PDC？",
      "aliases": ["PDC 怎么升级"],
      "keywords": ["PDC", "升级"],
      "product_name": "PDC",
      "model_names": ["VDR600"],
      "routing_summary": "..."
    }
  ]
}
```

一份文档可包含多个 `intents`，不强制“一文档一个问题”。这样安装、升级和故障排查共存于同一技术文档时，不会被迫合并为一个不准确的 Profile。

运行时为高效匹配，可由 `active_payload` 维护投影表 `document_profile_match_term`：

```text
id, document_id, kb_id, intent_key, term_type, normalized_term, weight
```

该投影仅保存线上、已启用的属性；待上线值不参与路由。上线事务内重建该文档的投影，文档删除通过外键级联删除。

### 5.2 知识库级待上线汇总

不再创建 `knowledge_release` 候选作为 Profile 的唯一操作单元。Profile Review 的知识库汇总由 `document_profile.pending_state` 实时计算：

- 待上线文档数；
- 需处理文档数；
- 生成中任务数；
- 当前线上属性数；
- 最近一次上线时间和变更数。

若保留上线审计，新增轻量表 `profile_publish_event`，仅记录一次上线动作的元数据、文档数、操作者、时间和校验摘要；它不承载草稿编辑和 RAG Index 绑定。

### 5.3 删除与完整性约束

1. `document_profile.document_id` 外键必须 `ON DELETE CASCADE`。
2. `document_profile.kb_id` 与 `document_source.knowledge_id` 必须一致，可通过写入服务校验和数据库约束/触发器保证。
3. 删除文档时先取消关联的 Profile 生成任务或使任务安全失败；任务执行时重新检查文档存在性。
4. 删除知识库时级联删除属性、匹配项、待上线值和发布审计；不保留孤立 Profile。

## 6. 后端执行设计

### 6.1 自动生成

上传或正文变更后的处理链路：

```text
文档上传 -> RAG Index 构建 -> RAG 自动生效
                         -> PROFILE_GENERATE(document_id)
                         -> pending_payload
                         -> PENDING_ONLINE / NEEDS_ATTENTION / FAILED
```

Profile 生成任务只写待上线属性，绝不修改 `active_payload`。任务必须：

- 提供 `QUEUED / GENERATING / SUCCEEDED / FAILED`、进度、文档数量、错误码；
- 兼容 Markdown JSON 包装和常见 JSON 前缀，再判定结构化输出无效；
- 将技术错误记录到任务详情；管理页面只展示业务结论，如“保守生成，建议确认”；
- 单文档失败不影响同批其他文档，也不影响 RAG。

### 6.2 编辑与重新生成

| 操作 | 写入结果 | 是否立即影响线上 |
|---|---|---|
| 编辑属性 | 更新 `pending_payload`，来源 `MANUAL` | 否 |
| 单篇重新生成 | 覆盖该文档 `pending_payload` | 否 |
| 全量重新生成 | 覆盖知识库内选中范围的待上线属性 | 否 |
| 停用属性 | 将待上线 `enabled=false` | 否 |
| 取消待上线变更 | 清空 `pending_*` | 否 |

“重新生成全部待上线属性”必须明确提示会覆盖自动待上线结果，但不得影响已生效的 `active_payload`。

### 6.3 上线与校验

新增知识库级接口：

```text
POST /knowledge-bases/{knowledge_id}/profiles/check
POST /knowledge-bases/{knowledge_id}/profiles/publish
POST /knowledge-bases/{knowledge_id}/profiles/discard-pending
```

`check` 在服务端执行硬校验并返回报告；页面每次属性变化后可自动调用，但保留按钮供用户主动刷新。

`publish` 在单一事务中：

1. 锁定知识库和全部待上线 Profile 行；
2. 重跑硬校验；
3. 将通过校验的 `pending_payload` 移入 `active_payload`；
4. 重建对应 `document_profile_match_term`；
5. 清空 `pending_*`；
6. 写入 `profile_publish_event`；
7. 返回最新知识库 Profile 汇总。

硬校验仅保留会导致错误路由或数据不一致的规则：

- 文档和知识库仍存在；
- 问题编码、Alias 和标准问法的冲突；
- UPDATE/停用对象存在；
- Payload 字段合法、长度受限；
- 匹配投影可写入。

措辞、关键词丰富度、模型未附完整证据等是质量信号，应进入 `NEEDS_ATTENTION` 或路由降级，不能被误用为整库无法上线的技术阻塞。

### 6.4 Support 路由与 RAG 兜底

运行时只查询线上、已启用 Profile 投影。快速命中必须同时满足：

- 最佳候选分数不低于阈值；
- 与第二候选有足够分差；
- 属性未标记为低置信或待处理；
- 所属文档仍可被当前 RAG 索引/文档库访问。

任一条件不满足即按当前执行策略进入 Generic RAG。记录 `PROFILE_LOW_CONFIDENCE`、`PROFILE_AMBIGUOUS`、`PROFILE_MISSING` 等原因，用于后续优化属性，不把它们视为用户请求失败。

## 7. 管理端与 Documents 展示

### 7.1 Profile Review 页面

保留当前单页面形态，顶部固定展示：

- 当前知识库；
- RAG 状态（只读）；
- Profile 上线状态：线上属性数、待上线数、需处理数、生成中数；
- 当前/最近任务进度条与失败信息；
- “检查待上线变更”和“上线 Profile 变更”按钮。

主表按文档显示：

| 列 | 内容 |
|---|---|
| 文档 | 名称、文档状态 |
| Profile 状态 | 未生成、生成中、待上线、已上线、需处理、失败、停用 |
| 当前线上属性 | 摘要、意图数、最近上线时间 |
| 待上线属性 | 摘要、来源、置信度、质量提示 |
| 操作 | 编辑、重新生成、停用/启用、取消待上线变更 |

页面底部保留全量重新生成、检查、上线、回滚/查看历史等操作；按钮永远可见，但按当前状态禁用并给出原因。禁止通过页面跳转来完成一个操作的后续步骤。

### 7.2 Documents 页面

Documents 列表增加 `Profile` 列，展示单文档聚合状态和简短提示：

- `已上线`：绿色，显示意图数；
- `待上线`：蓝色/警告色，显示“属性变更待上线”；
- `生成中`：显示进度入口；
- `需处理`：显示“低置信/保守生成，RAG 仍可用”；
- `未生成`、`失败`、`已停用`：明确说明“快速命中不可用，RAG 仍可用”。

文档详情抽屉增加 **Profile 属性** 标签，不显示 Release、Candidate Index 等内部概念。标签内分为：

```text
当前线上属性
待上线属性（若存在）
生成任务 / 最近错误（可展开）
```

该标签提供“前往同一知识库的 Profile Review”入口，并带上 `document_id` 作为筛选条件；编辑仍在 Profile Review 中完成，避免 Documents 与 Review 双写状态。

删除确认文案必须说明：

> 删除文档将同时删除其线上和待上线 Profile 属性；RAG 后续索引更新按原有流程执行。

### 7.3 术语统一

| 旧术语 | 新术语 |
|---|---|
| Profile Draft | 待上线 Profile 属性 |
| Release | 知识库 Profile 变更（仅内部审计使用） |
| 批准 | 不再逐条批准；改为编辑/确认待上线属性 |
| 发布前验证 | 检查待上线变更 |
| 发布 Support | 上线 Profile 变更 |
| 重建全部草稿 | 重新生成全部待上线属性 |

## 8. 分阶段实施计划

### 阶段 0：基线确认与契约冻结

1. 列出当前 `knowledge_release`、`problem_profile_draft`、`document_problem_profile` 的读写路径。
2. 明确 Support Gateway 当前是否在 Profile 未命中、歧义或低置信时执行 RAG fallback。
3. 为现有线上 Profile、发布历史、待处理草稿和活动任务导出可回放快照。
4. 冻结新 API、前端状态枚举和 Documents 展示文案契约。

验收：可用一个知识库清楚计算“当前线上属性、待上线属性、历史 Release、活动任务”的数量，且无重复归类。

### 阶段 1：数据迁移与双读

1. 新建 `document_profile` 和 `document_profile_match_term`。
2. 将 `document_problem_profile` 的线上数据回填到 `active_payload` 和匹配投影。
3. 将未发布的 `problem_profile_draft` 回填为对应文档的 `pending_payload`；PUBLISHED/CANCELLED Release 仅进入审计，不恢复为待上线。
4. 启用双读：运行时优先读取新投影；发现缺失时只读回退旧线上表并记录指标。
5. 删除操作先实现新表级联，并以集成测试确认不会留下孤儿项。

验收：双读结果在抽样知识库中等价；删除文档/知识库后新旧 Profile 记录均无残留。

### 阶段 2：生成、编辑和任务迁移

1. 将 `PROFILE_GENERATE/REFRESH/REGENERATE` 任务改为写 `pending_payload`。
2. 增加单文档、全量生成、编辑、停用、取消待上线变更 API。
3. 保留现有任务记录和轮询机制，任务元数据增加 `document_id`、`pending_state`、生成数量。
4. 结构化输出兼容逻辑先处理 Markdown/包装 JSON；仍不合法的结果写入 `NEEDS_ATTENTION` 或 `FAILED`，不影响 RAG。

验收：上传文档可得到待上线属性；单篇和全量重新生成均可显示实时进度；任何 Profile 任务失败都不会阻断 RAG。

### 阶段 3：上线、路由和 RAG fallback

1. 实现 `check`、`publish`、`discard-pending` 事务。
2. 运行时只使用 `active_payload` 投影；待上线值绝不参与匹配。
3. 增加阈值、候选分差、低置信、停用和文档可用性判断；不满足时显式 fallback RAG。
4. 为上线事件记录变更文档数、操作者、校验摘要和前后版本号。

验收：待上线属性不影响线上问答；上线后匹配投影原子切换；低置信/歧义问题进入 RAG 并可在 Trace 中看到原因。

### 阶段 4：管理端收敛与 Documents 可见性

1. 在现有 Profile Review 中替换 Release 队列为按文档的属性表和知识库汇总。
2. 保留全部已有操作，但改为新语义和状态约束。
3. 在 Documents 列表和详情抽屉展示 Profile 状态、线上/待上线属性及任务入口。
4. 清除页面上对 Candidate、Draft、Index Release 的用户可见依赖；技术细节留在任务详情/日志展开区。

验收：管理员可从 Documents 判断 Profile 是否影响快速命中；从 Profile Review 完成编辑、生成、检查、上线，无需跨页面补步骤。

### 阶段 5：旧模型退役

1. 停止创建新的 `knowledge_release`/`problem_profile_draft` Profile 候选数据。
2. 迁移或归档旧 API；兼容期内返回明确迁移提示，禁止双写冲突。
3. 在确认历史会话和审计需求均有替代方案后，删除旧表读取路径和无效前端状态。
4. 评估是否保留历史 Release 快照为只读审计数据；不得继续将其作为当前编辑对象。

验收：无运行时路径依赖旧 Release；数据库、API、前端枚举和运维文档一致。

## 9. 测试与验收矩阵

| 场景 | 期望结果 |
|---|---|
| 上传无 Profile 文档 | RAG 可用；Profile 进入生成中/待上线 |
| LLM 输出 Markdown JSON | 自动解析，生成待上线属性 |
| LLM 输出真正无效 | 文档保持 RAG 可用；属性为需处理/失败 |
| 编辑线上属性 | 只产生待上线值，线上匹配不变 |
| 上线一个知识库多文档变更 | 所有通过校验的待上线属性原子生效，匹配投影一致 |
| Alias 冲突 | 检查失败并指出文档/字段；不改变任何线上属性 |
| 高置信单一 Profile 命中 | 快速命中文档 |
| 低置信或两个候选接近 | 进入 RAG，Trace 记录 fallback 原因 |
| 删除文档 | 线上、待上线、匹配投影和未完成任务关联全部清理/失效 |
| 删除知识库 | 所有 Profile 数据级联删除 |
| Documents 页面 | 正确显示 Profile 状态，并说明 RAG 是否仍可用 |
| 历史数据迁移 | 新旧读路径结果等价，且不误把历史发布值变为待上线 |

自动化验证至少包括：数据库迁移集成测试、任务执行测试、API 契约测试、Gateway 路由/fallback 测试、Documents/Profile Review 交互测试，以及文档/知识库删除 E2E。

## 10. 发布、监控与回滚

### 10.1 灰度策略

1. 先在一个测试知识库启用新属性读写和 Documents 展示。
2. 进入双读观测期：比较新旧 Profile 命中、RAG fallback 和错误路由差异。
3. 按知识库开关逐步启用新路由；未启用知识库继续使用旧路径。
4. 确认指标稳定后停止旧模型写入并完成退役。

### 10.2 必备指标

- Profile 生成成功率、结构化输出修复率、保守生成率；
- 待上线属性数、上线失败率、校验失败原因分布；
- Profile 快速命中率、低置信/歧义 fallback 比例；
- Profile 命中后用户追问、转 RAG、人工纠正率；
- Documents 删除后 Profile 孤儿记录数（应为 0）。

### 10.3 回滚原则

- 新 Profile 属性上线失败时，原 `active_payload` 和匹配投影必须保持不变；
- 新路由异常时，按知识库开关切回旧 Profile 读路径或直接 RAG；
- 数据迁移在旧表只读保留期内可重新回填，不删除原始审计数据；
- 不通过“重新上传文档”回滚 Profile，避免损坏 RAG 和文档生命周期。

## 11. 需确认的产品决策

实施前需由产品和业务方确认：

1. 首次自动生成的属性是否一律待上线，还是允许满足严格阈值时自动上线；本计划默认一律待上线。
2. “上线”是否要求知识库内所有待上线项均通过校验，还是允许选择子集；本计划默认整库原子上线，后续可增加文档选择器。
3. 历史上线属性是否需要可视化回滚；若需要，保留 `profile_publish_event` 快照，不恢复完整 Release 工作流。
4. 一份文档多意图的最大数量，以及单意图 Alias/关键词数量上限。
5. 各知识库是否允许不同的 Profile 命中阈值和 RAG fallback 策略。
