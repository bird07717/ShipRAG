# 管理后台导航与冗余页面收敛执行计划

## 1. 目标与范围

本次只收敛管理后台的信息架构，不改变知识库、文档、索引、Profile、问答路由或发布协议。

目标：

1. 让管理员围绕“知识内容 → 发布 → 线上验证 → 故障排查”找到唯一主入口。
2. 合并展示同一份数据、承担同一管理动作的重复页面。
3. 保留索引构建和 RAG Playground 等低频但必要的诊断能力，并明确其高级工具属性。
4. 兼容已有书签和旧 Hash 地址，避免页面收敛后出现空白或错误入口。

非目标：

- 不新增后端 API、数据库迁移或业务状态。
- 不重做视觉体系，不引入新的路由框架、权限模型或可配置菜单。
- 不删除仍可复用的页面组件，先通过入口和容器收敛降低变更风险。
- 不改变上传后自动构建、Profile 草稿审核和原子发布链路。

## 2. 当前代码结论

当前顶级导航共有 16 个入口，核心业务能力完整，但存在以下重复：

| 当前页面 | 实际职责 | 问题 | 处理方式 |
| --- | --- | --- | --- |
| 问题文档配置 | 候选草稿审核、发布、线上映射、就绪原因、历史 | 名称偏配置，无法突出发布主流程 | 更名为“发布管理”并保留为唯一发布入口 |
| Support Configuration | Profile 就绪数量与原因 | 数据和“问题文档配置”线上表重复，无独立动作 | 移除顶级入口，旧地址转到发布管理 |
| Support Errors | SUPPORT 引擎失败 Trace | 是 Logs 失败轮次的固定过滤子集 | 移除顶级入口，在 Logs 增加引擎过滤 |
| RAG Errors | RAG 引擎失败 Trace | 是 Logs 失败轮次的固定过滤子集 | 移除顶级入口，在 Logs 增加引擎过滤 |
| Trace | 全量 Trace 查询与详情 | 与 Logs 同属排障链路，入口分散 | 作为“Logs & Traces”的“全量 Trace”标签页 |
| RAG Configuration | 全局检索参数 | 属于系统级运行配置 | 作为“模型、凭证与系统配置”标签页 |
| Settings | 浏览器会话凭据 | 属于管理台系统访问配置 | 作为“模型、凭证与系统配置”的“服务凭证”标签页 |
| Index Build | 手工构建、进度、GC、旧快照管理 | 低频但无法由 Documents 完整替代 | 保留，移动到“高级工具”分组 |
| RAG Playground | 显式 RAG 检索调试 | 与 DemoChat 的真实 Gateway 验证目的不同 | 保留，移动到“高级工具”分组 |

## 3. 目标导航

目标顶级入口从 16 个收敛为 10 个：

1. 概览：Dashboard。
2. 知识管理：Knowledge Base、Documents、发布管理。
3. 运行与验证：Engine Routing、DemoChat 问答测试。
4. 运维：Logs & Traces、模型、凭证与系统配置。
5. 高级工具：Index Build、RAG Playground。

这里不把 Index Build 和 RAG Playground 强行塞入主流程页面，因为两者包含大量独立诊断内容；用分组降级比功能删除或不相关页面嵌套更可控。

## 4. 实施步骤

### 4.1 收敛导航和兼容旧地址

修改 `frontend/src/App.vue`：

- 按目标导航重排分组、名称和说明。
- 将 `support-configuration`、`support-errors`、`rag-errors`、`traces`、`rag-configuration`、`settings` 定义为旧地址别名。
- 旧地址分别重定向到 `support-profiles`、`logs` 或 `configuration`，并通过 Hash 查询参数定位到原能力对应的标签和引擎过滤条件。
- Hash 初始化和 `hashchange` 都执行规范化，保证地址栏与当前页面一致。

### 4.2 合并排障入口

修改 `frontend/src/views/LogsView.vue`：

- 在“失败轮次”增加 `SUPPORT / RAG` 引擎过滤，覆盖原两个 Errors 页面用途。
- 增加“全量 Trace”标签页并复用现有 `TracesView`，保留全量检索、耗时和详情能力。

修改 `frontend/src/components/AdminStudio.vue`：

- 移除冗余页面的顶级视图映射。
- 保留收敛后的规范页面映射。
- 未知视图回退到 Dashboard，而不是 Settings，避免错误地址进入敏感配置页。

### 4.3 合并系统配置入口

修改 `frontend/src/views/ConfigurationView.vue`：

- 保留 Models 和 Prompts 标签页。
- 加入“RAG 检索参数”标签页，复用 `RagConfigurationView`。
- 加入“服务凭证”标签页，复用 `SettingsView`，提供显式保存并重新加载按钮和当前会话配置状态。

### 4.4 测试与验证

调整或新增前端测试，覆盖：

1. 页面只显示收敛后的 10 个导航入口。
2. 被移除的重复页面名称不再显示。
3. 旧 Hash 地址会跳转到新的规范入口。
4. Logs 失败轮次可按引擎过滤，并正确传递 API 参数。
5. Logs 能进入全量 Trace 标签。
6. 模型、凭证与系统配置能展示 RAG 参数和服务凭证标签。
7. 保留的规范视图都能正常挂载。

执行：

```bash
cd frontend
npm test
npm run type-check
npm run lint
npm run build
```

最后执行 `git diff --check` 并人工复核差异，不处理工作区中与本计划无关的既有改动。

## 5. 完成标准

- 管理员不再面对就绪状态、引擎错误、Trace 和系统配置的重复顶级入口。
- 发布相关状态与动作只有“发布管理”一个入口。
- SUPPORT/RAG 的失败记录仍能在统一日志页被区分和筛选。
- 全量 Trace、RAG 参数、Service Token 能力没有丢失。
- 旧地址可用，规范地址会反映在 URL 中。
- 前端测试、类型检查、Lint、构建和差异检查通过。

## 6. 回滚边界

本次不删除后端能力或数据。若需要回滚，只需恢复导航、视图映射和容器标签；独立页面组件仍保留在代码库中，不涉及数据恢复。

## 7. 执行结果（2026-08-25）

计划已执行完成：

- 顶级导航由 16 个收敛为 10 个，并按概览、知识管理、运行与验证、运维、高级工具重新分组。
- “问题文档配置”已更名为“发布管理”，继续作为 Profile 审核、验证和原子发布的唯一入口。
- Logs 已增加引擎过滤和“全量 Trace”标签，替代 Support Errors、RAG Errors 与独立 Trace 入口。
- 模型配置已增加“RAG 检索参数”和“服务凭证”标签，替代独立 RAG Configuration 与 Settings 入口；凭证可明确保存、立即重新加载管理数据并显示当前会话状态。
- 六个旧 Hash 地址均保留兼容映射，并精准定位到目标标签：Support/RAG Errors 带引擎过滤，Trace、RAG Configuration 与 Settings 直接打开对应标签；未知视图安全回退到 Dashboard。
- Logs 和 Trace 在切换知识库后会重新加载当前知识库的数据。
- Index Build 与 RAG Playground 保留在高级工具中，功能没有被删除或塞入不相关主流程。
- Dashboard 已移除“后端尚未提供 `/metrics`、需迁移 0014”的过期推断，失败时只展示与实现版本无关的可重试提示。

验证结果：

- 前端完整测试：17 个测试文件、91 个测试全部通过。
- TypeScript / Vue 类型检查：通过。
- ESLint：通过（零 warning 门槛）。
- Vite 生产构建：通过。
- `git diff --check`：通过。

生产构建仍报告主 JavaScript chunk 超过 500 kB 的既有性能提示；它不影响本次构建成功，也不在本次信息架构收敛范围内。

## 8. 全局管理范围栏与知识库删除补充修复（2026-08-25）

### 8.1 问题结论

- `App.vue` 将“当前知识库、环境、线上版本、发布候选”作为全局区域渲染在所有非问答页面。系统配置、凭证等页面并不使用这些知识库上下文，因此这是错误的页面级作用域表达，也重复占用每页首屏空间。
- Knowledge Base 页面只有创建入口，前端 API 与后端均没有删除知识库能力。只增加前端按钮会形成不可用的假功能，因此本项需要最小范围补充后端删除接口。

### 8.2 实施内容

1. 删除 `App.vue` 的全局管理范围栏，不再在每个页面重复展示环境、线上版本和发布候选。
2. 只在实际依赖知识库范围且原本缺少选择器的页面补充局部选择器：Dashboard、发布管理、Logs & Traces；Documents、Index Build、Engine Routing、DemoChat、RAG Playground 继续使用各自已有的选择器，系统配置页不展示知识库选择器。
3. Knowledge Base 列表行菜单和详情抽屉增加“删除知识库”；要求管理员准确输入知识库名称后才允许永久删除。
4. 新增 `DELETE /api/v1/knowledge-bases/{knowledge_id}`。删除事务会清理该知识库的 Trace、会话、索引、文档及级联的 Profile/发布数据；删除成功后再回收 MinIO 文档与图片对象。
5. 构建中的知识库在前端禁用删除入口，后端同时以 `409 KNOWLEDGE_BASE_BUILD_IN_PROGRESS` 拒绝删除，避免与索引任务并发破坏数据。
6. MinIO 对象采用尽力回收：数据库删除成功但个别对象清理失败时，接口返回失败数量、后端记录日志，管理端明确提示管理员检查日志。

### 8.3 补充验证

- 前端类型检查、ESLint：通过。
- 前端 Vitest：17 个测试文件、91 个测试全部通过。
- 前端生产构建：通过；仍只有既有的主 chunk 超过 500 kB 提示。
- 后端 Ruff 检查、格式检查与 Mypy：通过。
- 后端定向 Pytest（API 与 ingestion）：25 个测试全部通过，包含删除顺序和构建中拒绝场景。
- 后端完整 Pytest：351 个通过、3 个按环境条件跳过。
- `git diff --check`：通过。
