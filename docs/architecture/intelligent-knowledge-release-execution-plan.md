# 知识库智能发布流程执行计划

## 1. 目标

将当前彼此独立的“上传文档、构建索引、维护 Problem Profile、激活索引”改造成一个可审核、可验证、原子发布且可持续优化的知识库发布流程。

最终操作路径：

```text
上传文档
  -> 构建候选索引（READY，线上 Active Index 保持不变）
  -> 自动生成 Profile 草稿并判断 CREATE / UPDATE / NONE
  -> 管理员审核、直接修改草稿
  -> 硬规则校验、冲突检查和匹配回归
  -> 原子发布候选索引与审核通过的 Profile
  -> 从 NO_MATCH 等线上数据生成下一轮改进建议
```

## 2. 设计原则

1. 草稿与线上配置隔离：管理员编辑草稿时不得改变当前线上 Profile。
2. 候选版本不影响线上：新索引在发布前保持 `READY`，旧 `ACTIVE` 索引继续服务。
3. 索引与 Profile 同时发布：不能出现索引已切换但 Profile 尚未生效，或 Profile 已指向尚未发布文档的状态。
4. 硬错误阻止发布，AI 质量告警允许人工判断：数据库一致性、别名冲突、文档缺失等必须阻断；模型生成的软质量分仅提示。
5. AI 只生成建议：自动生成、替换判断和反馈分析均不直接修改线上配置。
6. 保持运行时封闭目录：Profile Support 只从当前知识库已发布、已就绪的 Profile 中选择文档。

## 3. 范围

### 本次实现

- 增加知识库 Release 和 Profile Draft 数据模型。
- 文档变更触发的索引默认进入候选状态，不自动替换线上索引。
- 候选索引完成后自动为本次新增/重建文档创建 Profile 草稿。
- 自动判断草稿操作：
  - `CREATE`：新问题；
  - `UPDATE`：疑似已有问题的新文档版本；
  - `NONE`：管理员确认该文档不参与 Profile Support。
- 提供草稿查询、修改、重新生成、验证、审批和原子发布 API。
- 改进 Profile 确定性匹配，避免单个宽泛型号词直接命中文档。
- 提供基于 `NO_MATCH` Trace 的发布后反馈摘要。
- 管理页面提供候选 Release、草稿审核、验证和发布操作。
- 增加数据库迁移、单元测试和前端交互测试。

### 本次不做

- 不允许 AI 无人审核直接发布。
- 不自动把线上问题写成 Alias。
- 不删除旧索引；继续使用现有 GC 策略。
- 不改变已经锁定文档版本的历史 Support 会话。
- 不把 Generic RAG Chunk 命中直接当作 Profile 命中。

## 4. 状态模型

### Knowledge Release

```text
DRAFT -> VALIDATING -> READY_TO_PUBLISH -> PUBLISHED
                    \-> VALIDATION_FAILED
       \-> CANCELLED
```

- 一个候选索引对应一个 Release。
- `PUBLISHED` Release 指向当前已发布索引和 Profile 版本集合。
- Release 保存验证报告和生成错误，不让生成失败破坏索引构建任务。

### Profile Draft

```text
DRAFT -> APPROVED -> PUBLISHED
      \-> REJECTED
```

- `CREATE` 和 `UPDATE` 草稿发布时写入 `document_problem_profile`。
- `NONE` 草稿用于显式确认“该文档不进入 Profile Support”，不会写入线上 Profile。
- `UPDATE` 通过稳定 `problem_code` 更新线上 Profile 的 `document_id` 和元数据。

## 5. 数据库变更

新增 `knowledge_release`：

- `id`, `kb_id`, `index_id`
- `status`
- `validation_result`
- `generation_error`
- `created_at`, `updated_at`, `published_at`

新增 `problem_profile_draft`：

- `id`, `release_id`, `kb_id`, `document_id`
- `operation`, `target_profile_id`
- Profile 全部可编辑字段
- `status`, `generation_source`, `confidence`, `reasoning`
- `validation_result`
- `created_at`, `updated_at`

约束：

- Release 与 Index 一一对应。
- Draft 文档、候选索引和知识库必须一致。
- 发布事务再次执行全部关键一致性检查，不能只信任早先验证结果。

## 6. 后端执行步骤

1. 创建候选构建时同步创建 Release。
2. 文档变更构建使用 `activate_on_success=false`。
3. Pipeline 完成后：
   - 保持索引为 `READY`；
   - 读取本次 `REBUILD` 文档的标题和完整 Elements；
   - 尝试使用 LLM 生成结构化 Profile 建议；
   - LLM 不可用时使用文档标题生成可编辑的保守草稿；
   - 根据标题、标准问题与现有 Profile 的相似度建议 `CREATE` 或 `UPDATE`。
4. 管理员通过 API 修改草稿字段和操作类型。
5. 验证服务检查：
   - 候选索引状态和归属；
   - 文档 Elements 与图片资源完整性；
   - 必填字段、问题编码、标准问题和 Alias 冲突；
   - UPDATE 目标存在；
   - 草稿之间的冲突；
   - 标准问题与 Alias 的确定性路由回归；
   - 宽泛关键词、公共型号等软告警。
6. 原子发布事务：
   - 锁定知识库、候选索引、Release 和草稿；
   - 重新执行硬校验；
   - 发布所有 APPROVED 的 CREATE/UPDATE 草稿；
   - 将旧 Active Index 设为 DEPRECATED；
   - 将候选索引设为 ACTIVE 并更新 `knowledge_base.active_index_id`；
   - 将 Release 和 Draft 设为 PUBLISHED。
7. 反馈接口聚合指定知识库近期 `NO_MATCH` 问题，输出频次、示例和处理建议，不自动修改 Profile。

## 7. 匹配规则调整

保持优先级：

1. 显式 `document_id` / `problem_code` / 待确认选择；
2. 标准问题规范化精确匹配；
3. Alias 规范化精确匹配；
4. 产品或型号与任务意图的组合匹配；
5. Profile 元数据候选集上的 LLM 封闭分类；
6. 置信不足返回澄清或 `NO_MATCH`。

确定性规则不得因仅出现一个公共型号词（例如 `VDR600`）直接选中唯一文档。关键词只有在具备足够区分度时才允许直接命中。

## 8. 管理页面

在 Problem Profiles 页面增加“发布候选”区域：

- 展示候选索引、Release 状态和生成错误；
- 展示每篇变更文档的草稿、建议操作和置信度；
- 支持编辑全部 Profile 字段；
- 支持选择 CREATE / UPDATE / NONE；
- 支持批准、拒绝、重新生成；
- 展示硬错误与软告警；
- 只有验证通过时启用“发布”按钮；
- 发布后刷新知识库、索引、Profile 和 Release 状态。

增加“反馈建议”区域，只读展示近期重复 `NO_MATCH` 问题。

## 9. 验收标准

1. 上传新文档后旧 Active Index 不变化，新索引最终为 `READY`。
2. 构建完成后自动出现可编辑 Profile 草稿；生成失败时也有保守草稿或明确错误。
3. 编辑草稿不会改变现有 `document_problem_profile`。
4. Alias/标准问题冲突、文档不在候选索引、图片缺失会阻止发布。
5. 单独的公共型号词不再直接命中文档。
6. 发布事务完成后，索引与 Profile 同时可见；事务失败时两者都不切换。
7. 旧的已聚焦会话继续使用原 `focus_index_id`。
8. 不同知识库的 Release、Draft、Profile 和反馈严格隔离。
9. 管理页面可以完成审核、验证和发布闭环。
10. 后端与前端测试通过，迁移可升级和降级。

## 10. 回滚策略

- 发布前：取消 Release 即可，线上状态不变。
- 发布失败：事务回滚，旧 Active Index 和 Profile 保持不变。
- 发布后业务回滚：用旧 DEPRECATED Index 创建新的回滚 Release，并通过同一验证/发布路径恢复对应 Profile 版本；本次先保留数据与接口扩展点，不提供无审核的一键回滚。

