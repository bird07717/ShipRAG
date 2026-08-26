# Support Profile 刷新、回填与单文档维护执行计划

## 1. 背景和问题

现有索引构建对未变更文档采用 `REUSE`。自动 Profile 生成只读取
`REBUILD` 文档，因此手动重建索引后，历史文档没有新的 Profile 草稿。
这造成两个错误的实际工作流：

- 用户以为重建仅影响 RAG；
- 用户以为必须删除并重传文档才能修改 Support Profile。

删除不是正确替代方案：已启用 Profile 引用的文档会被 `DOCUMENT_PROFILE_IN_USE`
保护而无法删除。

## 2. 目标与不变式

本次新增所有操作均在现有、未发布的知识库候选（`knowledge_release`）内完成：

```text
选择或新建候选 Index
  -> 回填 / 全量刷新 / 单文档草稿
  -> 人工审核草稿
  -> 发布候选（Profile 与 Index 原子生效）
```

不变式：

1. 不修改线上 `document_problem_profile`，直到审核发布成功。
2. 不删除、不重传原始 DOCX。
3. 单文档编辑先复制线上 Profile 为候选 `UPDATE` 草稿；保存后仍需要审核。
4. 全量刷新会覆盖同一候选中未发布的自动草稿，界面必须二次确认。
5. 回填只处理尚未达到当前富化版本的文档，且不覆盖已有候选草稿。

## 3. 操作定义

| 操作 | 范围 | 选择规则 | 结果 |
|---|---|---|---|
| 回填缺失 Profile | 当前候选全部文档 | 没有线上 Profile，或 Profile 富化版本低于当前版本 | 为筛中的文档创建草稿 |
| 全量刷新 Profile | 当前候选全部文档 | 不过滤 | 重新生成全部 Profile 草稿 |
| 编辑单文档 Profile | 一份已有线上 Profile 的文档 | 复制线上 Profile；已有候选草稿则直接返回该草稿 | `UPDATE` 草稿供人工编辑 |
| 重新生成单文档 Profile | 一份候选草稿的文档 | 对该文档正文重新调用 LLM | 覆盖该候选草稿，回到 `DRAFT` |

“富化版本”通过数据库字段 `profile_enrichment_version` 表示。旧数据默认为 `0`；当前增强 Prompt 生成并发布的 Profile 写入 `1`。这避免把“症状恰好为空”的有效 Profile 误判为待回填。

## 4. 后端设计

### 4.1 数据迁移

新增 `0023_profile_enrichment_version`：为 `problem_profile_draft` 和
`document_problem_profile` 增加非空整型版本，默认 `0`。

### 4.2 服务和 API

新增 API：

- `POST /indexes/{index_id}/profile-drafts/backfill`
- `POST /indexes/{index_id}/profile-drafts/refresh`
- `POST /releases/{release_id}/profiles/{profile_id}/draft`

`backfill` 与 `refresh` 都读取候选 Index 的全部 `index_document`，包含
`REUSE` 文档；自动构建后的既有 `/generate` 仍只处理 `REBUILD` 文档。

创建单文档草稿时，必须验证 Profile 所属知识库、候选可编辑、关联文档位于候选
Index；若该候选已有此文档的草稿，不覆盖而直接返回。

## 5. 管理端设计

发布候选区域增加“回填缺失 Profile”和“全量刷新 Profile”。
线上 Profile 表增加“创建编辑草稿”；有候选时复制后直接打开草稿编辑弹窗，
没有候选时提示先在索引构建页面创建候选。

## 6. 验收标准

- 候选包含 `REUSE` 文档时，回填/全量刷新仍能生成其草稿。
- 富化版本为当前版本的 Profile 不会被回填操作重复生成。
- 全量刷新覆盖草稿，回填不会覆盖已有草稿。
- 单文档编辑不会删除文档，且只能在候选中创建 `UPDATE` 草稿。
- 所有新草稿均为 `DRAFT`，发布前验证仍要求人工审核。
- 后端单元测试覆盖上述条件；前端类型检查、lint 和测试通过。

## 7. 回滚

这些操作仅创建或更新未发布草稿。若需撤销，可在候选中退回草稿、重新生成，
或创建新的候选；未发布前不会影响线上 Support。

## 8. 实施记录

已完成：

- 数据库迁移 `0023_profile_enrichment_version`，旧 Profile 默认版本为 `0`，
  新一代 LLM 草稿/发布 Profile 写入版本 `1`。
- `ReleaseService` 已区分构建期新增文档生成、旧文档安全回填、候选全量刷新和
  单文档重新生成；后面三种均会读取 `REUSE` 文档。
- 新增回填、全量刷新和从线上 Profile 创建编辑草稿的 API；编辑草稿保留线上
  Profile 的启用状态和路由字段。
- 管理台提供相应入口，并明确：不存在可编辑候选时不能直接改线上数据。

验证：

- 后端 Ruff、Mypy 通过；Profile/发布/Support 相关测试 40 项通过。
- 前端类型检查、Lint 和 Vitest 测试通过（92 项）。
