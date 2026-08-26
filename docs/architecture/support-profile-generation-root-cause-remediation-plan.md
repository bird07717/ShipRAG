# Support 自动 Profile 根因修复执行计划

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
4. 无可靠改写时允许返回少于 5 条 Alias；上限保留为 8 条。

若第二阶段模型不可用或复核失败：保留保守草稿，并设置 `generation_error` 与
草稿质量诊断；不得伪装为已验证的 LLM 结果。

#### 数据模型与界面

增加仅用于候选草稿的质量诊断字段，例如：

- `quality_status`: `PASS`、`REVIEW_REQUIRED`、`FALLBACK`；
- `quality_notes`: 来源缺失、主体不一致、复核降级等说明；
- 可选 `evidence`：供管理员核对，不进入线上 Profile 路由。

管理台草稿列表展示质量状态和诊断；`REVIEW_REQUIRED` 草稿不允许批量批准，
但管理员仍可修订后单独批准。

#### 涉及模块

- `backend/app/releases/models.py`
- `backend/app/releases/service.py`
- `backend/app/releases/repository.py`
- 新增 Alembic 迁移
- `frontend/src/types/admin.ts`
- `frontend/src/views/SupportProfilesView.vue`

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
3. 对设备实体做主体一致性检查：从标题和第一阶段 `core_subject` 获取预期主体；
   Alias/Keyword 引入其他设备实体时，标记为 `REVIEW_REQUIRED`，而不是自动删除。
4. 将预检结果写入 `quality_notes`；真实唯一性冲突保留为发布阻塞，但在草稿生成后
   立即可见。

预检不得将有冲突的草稿自动改成 `NONE`，也不得自动删除用户内容；应保持可编辑、
可追溯。

#### 涉及模块

- `backend/app/releases/service.py`
- `backend/app/releases/repository.py`
- `backend/app/releases/models.py`
- `frontend/src/views/SupportProfilesView.vue`

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

为避免破坏已有 API 客户端，保留原 `valid/errors/warnings` 字段，并新增字段后逐步
迁移前端展示。

#### 涉及模块

- `backend/app/releases/models.py`
- `backend/app/releases/service.py`
- `backend/app/releases/repository.py`
- `frontend/src/types/admin.ts`
- `frontend/src/views/SupportProfilesView.vue`
- 相关 API 与测试

#### 验收

- 仅因草稿尚未审核时，界面显示“待审核”，不显示为“验证失败”。
- 存在 Alias 或问题编码冲突时，仍明确显示“发布阻塞”。
- 发布接口仍只接受所有草稿已审核且没有真实阻塞的候选。

## 5. 执行顺序

1. 实施任务 A，并加入单元测试，先消除确定性的同文档重复问题。
2. 定义质量状态、诊断和证据数据结构，执行数据库迁移。
3. 实施任务 B 的两阶段离线生成与降级路径。
4. 实施任务 C 的预检，将结果写入质量诊断。
5. 实施任务 D 的验证状态拆分与管理台展示。
6. 扩充端到端测试：新增文档、同文档刷新、回填、全量刷新、LLM 降级、冲突与发布。
7. 在独立测试知识库执行一次全量 Profile 刷新，人工抽样审查结果后再用于生产知识库。

## 6. 测试与验收清单

### 后端

- 同一文档、低文本相似度时仍为 `UPDATE`。
- 不同文档的真实替代关系仍可判为 `UPDATE`。
- 设备仅出现在位置说明或图片描述时，不成为 Alias 问题主体。
- Alias 可少于 5 条，且不因数量不足生成填充词。
- LLM 生成或复核异常时保守降级，草稿仍可编辑。
- 预检与发布前验证均能识别编码、标准问题和 Alias 冲突。
- 未审核草稿不会被归类为真实验证失败。
- 发布事务和线上 Profile 路由行为不回归。

### 前端

- 草稿列表显示生成来源、质量状态和质量诊断。
- 待审核、质量提示、真实阻塞三类信息分开展示。
- 质量待复核草稿不能被批量批准。
- 编辑、重新生成、回填和全量刷新路径保持可用。

### 运行环境

- 数据库迁移成功应用到 head。
- `backend` 和 RQ `worker` 均运行；否则索引及离线 Profile 任务不会被消费。
- LLM 可用与不可用两种场景均完成验证。

## 7. 发布与回滚

### 发布策略

先部署代码和迁移，在测试知识库验证 Profile 草稿质量；确认后再对生产知识库执行
回填或全量刷新。生成结果仅位于候选发布版本，审核发布前不会影响线上问答。

### 回滚

- 未发布候选：废弃候选或退回草稿即可，线上 Profile 不变。
- 已部署代码：通过功能开关关闭第二阶段复核和预检，回退为保守草稿生成；不回退
  数据迁移字段。
- 不执行自动删除或覆盖线上 Profile 的回滚动作。

## 8. 完成定义

满足以下条件才视为本计划完成：

1. 同文档刷新不再产生重复 `CREATE` Profile。
2. 自动草稿不再以 Alias 数量为目标编造背景设备问法。
3. 冲突在草稿生成后即可定位，发布前验证仍保留最终保护。
4. 管理台能明确区分“待审核”和“真实发布阻塞”。
5. 后端静态检查、迁移检查、相关单元/集成测试及前端类型检查、Lint、测试均通过。
