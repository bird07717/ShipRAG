# 文档命中能力提升执行技术方案

状态：已实施并完成定向回归  
范围：现有 Profile Support 与 SupportDocumentRouter 内部增强  
原则：不新增服务、不增加新的路由层、不依赖人工操作

## 1. 目标

在保持低误交付率优先的前提下，提高以下问题的文档命中能力：

- 完整 Alias 出现在用户长问句中；
- 用户使用产品/型号、任务和症状组合描述问题；
- Profile 精确匹配未命中，但文档标题和 Profile 语义与检索片段共同指向同一文档；
- Discovery 阶段的短追问需要最近一轮用户上下文才能完成检索。

系统仍保持现有两层路由：

```text
ProfileResolver
    -> MATCHED / AMBIGUOUS / NO_MATCH
    -> SupportDocumentRouter
       Embedding + BM25 -> RRF -> Rerank -> 文档聚合
    -> DELIVER / CLARIFY / NO_MATCH
```

`NO_MATCH` 后继续由现有 Gateway 决定是否单轮进入 Generic RAG。

## 2. 非目标

本次不实施：

- 新的路由服务或 Profile 向量库；
- 在线 LLM Catalog 分类；
- 学习排序、连续元数据权重或动态阈值平台；
- 新的人工审核依赖；
- Profile Catalog 版本系统；
- Generic RAG 主链路重构。

人工编辑继续保留为可选能力。自动 Profile 质量不足时，文档仍可通过 Support 文档检索和 Generic RAG 使用，不阻塞知识库。

## 3. 当前缺口

### 3.1 Profile 生成

`DocumentProfileGenerator` 当前只读取文档正文前 20,000 个字符，长文档后部的型号、症状、注意事项和不适用范围可能无法进入生成输入。

### 3.2 Profile 快速路由

当前 Resolver 已支持 Canonical、Alias、产品/型号、任务和 Keyword，但存在以下缺口：

- Alias 只支持整句精确匹配；
- `symptoms` 和 `exclusions` 未参与决策；
- 多个结构化候选在没有 Catalog Classifier 时会退化为 `NO_MATCH`；
- 两个普通 Keyword 可能被视为足够证据。

### 3.3 Support 文档检索

当前 Rerank 输入只有 Chunk 正文，没有文档标题和 Profile 路由语义。Rerank 降级后仍可能自动交付完整文档。

### 3.4 Discovery 短追问

文档检索只使用本轮短问题，例如“还是失败怎么办”，会丢失上一轮用户已经提供的产品或任务信息。

## 4. 实施设计

### 4.1 Profile 代表内容选取

保持现有三阶段生成流程不变，只替换简单前缀截断：

1. 保留文档开头；
2. 保留文档结尾；
3. 从中间内容优先选取包含型号、任务、症状、错误码、注意和排除语义的行；
4. 总输入仍限制在 20,000 字符内；
5. 去重并保持稳定顺序。

该改动不增加模型调用次数。

### 4.2 ProfileResolver 分层规则

按以下顺序决策：

1. 有效 `choice_id`；
2. Canonical 规范化精确匹配；
3. Alias 规范化精确匹配；
4. 受控长 Alias 包含；
5. 产品/型号 + 任务；
6. 产品/型号 + 具体症状；
7. 任务 + 具体症状；
8. 多候选返回 `AMBIGUOUS`；
9. 无可靠候选返回 `NO_MATCH`。

受控长 Alias 必须满足：

- 规范化长度不少于 6；
- 用户问题长于 Alias；
- Alias 在当前知识库 Profile 集中唯一；
- Alias 包含产品/型号，或包含高区分度的字母数字标识；
- 通用短语不能触发。

`exclusions` 作为硬阻断：命中排除项的 Profile 不允许自动匹配。Keyword 只作为任务补充和候选辅助，不再允许“两个普通 Keyword”单独触发自动交付。

### 4.3 Support Rerank 语义增强

不修改召回和聚合架构。Rerank 文本改为：

```text
文档标题
标准问题
Alias
产品/型号
任务类型
症状
路由摘要
Chunk 正文
```

Profile 元数据限制长度，不包含 `exclusions`，避免负向语义反而提高候选相关度。

候选 Profile 的排除项在文档级决策前执行硬过滤，并写入 Trace。

### 4.4 Rerank 降级保护

当 Rerank 状态为 `DEGRADED` 时：

- 原文档决策为 `DELIVER` 的结果降为 `CLARIFY`；
- 没有候选时保持 `NO_MATCH`；
- Profile 精确快速路由不受影响。

### 4.5 Discovery 查询补全

仅在无焦点 Discovery 阶段，当本轮问题规范化后较短时，将最近一条已完成的用户问题与当前问题拼接后交给 SupportDocumentRouter。ProfileResolver 仍使用用户本轮原始问题，避免历史内容污染确定性匹配。

### 4.6 索引范围

在线 Profile 列表按当前 Support Turn 绑定的 `index_id` 检查文档是否已发布，避免旧会话使用不在冻结索引中的新文档 Profile。

## 5. 可观测性

现有 Trace 增加或保留以下信息：

- Profile 路由方法；
- 文档候选数量；
- Rerank 状态；
- 文档聚合分数、最高分、命中块数和阈值；
- 被排除项阻断的文档 ID；
- Rerank 降级保护原因；
- 实际检索 Query 是否包含 Discovery 上下文。

## 6. 测试范围

必须覆盖：

- Exact Alias 不回归；
- 唯一长 Alias 在长问句中命中；
- 通用 Alias 不触发包含匹配；
- 产品/型号 + 任务命中；
- 产品/型号 + 症状命中；
- 排除项阻断；
- 多个结构化候选返回 `AMBIGUOUS`；
- Rerank 输入包含标题和 Profile 元数据；
- Rerank 降级不自动交付；
- Rerank 正常且高置信时仍可交付；
- Discovery 短追问拼接最近一轮用户问题；
- Focused 文档切换仍必须用户确认。

## 7. 验收标准

1. 不新增运行服务、数据库表或路由层；
2. 明确 Alias 长问句可走 Profile 快速路径；
3. 症状可作为结构化证据，排除项可以阻断误交付；
4. 多候选不会静默选第一个；
5. Rerank 降级不会自动交付完整文档；
6. Profile 未就绪不会阻塞文档进入检索/RAG；
7. 现有 Gateway、SSE、Focused Support 和 Generic RAG 契约保持不变；
8. 后端相关测试、静态检查与前端构建通过。

## 8. 实施结果

已完成：

- Profile 生成从“正文前 20,000 字符”改为“开头 + 结尾 + 全文信号行”的稳定预算选取；
- Profile Resolver 增加受控 Alias 长问句、症状组合、排除项阻断和结构化多候选澄清；
- 普通 Keyword 不再以多个词命中作为独立自动交付依据；
- Support Profile 按当前 Turn 冻结的 Index 检查文档就绪状态；
- Support Rerank 输入加入标题和现有 Profile 路由语义；
- 文档检索应用 Profile 排除项硬过滤；
- Rerank 降级时将自动交付降为澄清；
- Discovery 短追问携带最近一条用户问题进入文档检索；
- Focused Support 仍只提出切换候选，不改变用户确认契约。

验证结果：

- 相关后端定向测试：55 通过；
- 排除已存在收集错误的 `tests/test_api.py` 后，后端测试：375 通过、33 跳过；
- Ruff（改动文件）：通过；
- Mypy：104 个源文件通过；
- 前端 ESLint、Vue Type Check：通过；
- 前端 Vitest：17 个文件、105 个测试通过；
- 前端生产构建：通过。

仓库原有的全量检查基线问题未在本次范围内修改：

- `tests/test_api.py` 仍导入已经从 `app.api.releases` 删除的 `get_release_repository`，导致全量 Pytest 收集失败；
- 全仓 Ruff Format 检查仍报告 13 个与本次功能无关的历史文件未格式化；
- 跳过 `test_api.py` 后，单独运行 Pytest 的默认覆盖率为 66.73%，低于项目 70% 门槛；项目完整 `scripts/check.sh` 还会通过三个 Smoke 任务累计覆盖率，本次未执行会写入开发环境状态的 Smoke 流程。
