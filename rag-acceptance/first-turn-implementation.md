# RAG 首轮优化实施说明

## 范围与默认行为

本次只在本地改代码，不修改生产、不重建业务索引、不新增外部 Assistant 接口。

| 场景 | 新行为 |
| --- | --- |
| 聚合选中的文档不是原始最高分块所属文档 | 首轮改为两份文档澄清；用户确认后完整交付。 |
| 精确型号在现有召回中遗漏 | 按最新文档显示名称补入至多三份文档各一块，再统一重排；泛型号匹配超过三份则不补入。 |
| 单块强且领先另一文档 | 默认只记录影子结果；0.85/0.18 不是已验证可普遍上线的自动直达阈值。 |
| 追问、已有焦点、待确认回复、重排降级 | 不启用上述首轮判定，不抢占原有状态机。 |
| VML 旧式图片 | 提取实际图片并检查完整性；重复引用保留、兼容分支不重复提取。 |
| 稀疏正文含大量浮动文字图片 | 中止构建并提示转换源文件，不按错误顺序拼接。 |

单强指“选文档的证据只有一个强块”，不代表只返回一个 Chunk。所有直达路径仍返回原有完整步骤、图片和来源，使用同一会话版本校验。RAG 不读取 Support 标准问题、标签或 Profile；公共文档摄取修复同时影响两种模式。

## 配置与可观测性

配置见 [环境认知](../docs/ai/environment.md)：冲突保护和标题补召回默认开启；`M3_RAG_SINGLE_STRONG_MODE=shadow` 默认只观察，可设置 `off` 或 `enabled`。本次未启用二次文档重排、LLM 仲裁、宽泛动作词扩展或全量视觉 caption 加权。

- 冲突：Trace 路由 `resolved_from=RAG_AGGREGATION_CONFLICT`，阈值诊断包含原聚合目标、最强块目标及各自最高分。
- 单强：路由 `thresholds.rag_single_strong` 记录模式、分数和阈值；enabled 直达记录 `RAG_SINGLE_STRONG`。
- 型号召回：`rerank_result.title_recall` 记录规则、型号词和候选 Chunk ID；没有额外模型重排请求，原重排最多增加三块输入。

冲突保护和单强判断为候选列表内的线性计算。型号补召回增加数据库读取；泛型号仍有标题匹配查询成本，不能把最多三块理解为数据库只扫描三条。大库需用 Trace 和 SQL 执行计划评估，不在本次添加缓存或新索引。

## DC24V 验证结论与后续操作

真实文件：`VDR600/故障解决文本46—VDR600 如何屏蔽 DC24V解决绝缘低问题-中文.docx`。

旧解析为 17 TEXT、44 字符、0 IMAGE、0 中文且无告警；新解析提取 110 IMAGE，识别 105 个小型文字候选，并提示浮动版式和稀疏正文。早期逐碎片真实 OCR 出现 EMPTY_RESULT 和异常长输出；放大留白只能缓解部分小图，不能解决阅读顺序。最终验证在任何 OCR 调用前以 `LEGACY_FLOATING_TEXT_LAYOUT` 安全中止，报告 `BLOCKED`、退出码 2。这不是正文恢复成功，也不是质量验收通过。

后续需将该文件转换为有可编辑正文的 DOCX，人工对照原件核对文字、顺序与图片，再按现有文档管理流程构建候选索引。当前未引入 Office 渲染依赖，也未用猜测布局替代原步骤。

解析结果版本为 `ooxml-vml-1`。旧版本解析结果不再直接复用，包括升级前已排队的 REUSE 任务；下次构建可能重新处理全部旧版本文档，增加模型调用费用。保留当前活动索引，验收候选后再手动激活，不得跳过失败文件或伪造版本标记。

## 验证与部署边界

2026-09-11 最后一次本地验证：

- `scripts/check.sh` 全流程通过：Ruff lint/format、Mypy 118 个源文件、后端 759 项测试、三项 smoke、累计覆盖率 81%；前端 20 个文件共 139 项测试、覆盖率门禁、lint/type-check 与构建通过，仅保留已知大包警告。
- 本次三个新测试文件定向 36 项通过，覆盖跨文档分差、冲突后确认、影子/关闭/启用模式、后续轮次门禁、会话版本冲突、型号及知识库/索引隔离、改名、泛型号不随机补召回、VML 兼容分支/重复引用、失败保护与旧任务复用回退。
- 全量使用进程级 `REDIS_URL` 指向预先确认空闲的本地 Redis DB 14，未改 `.env`。首次共用开发队列的 smoke 失败，隔离后通过；中途工作区文档标题并行改动曾使五项断言失败，最后按最新工作区全量复跑已全部通过。测试数字包含工作区其他已有改动，不将其计作本次功能新增。
- Production Compose 使用临时验证凭据在内存中解析通过，核对后端、迁移及两个 Worker 均取得默认 shadow 配置；没有启动生产容器。开发 `.env` 和留空的生产示例自身缺少必需凭据，不能直接用作生产部署配置。
- `git diff --check` 通过。DC24V 只读报告为 `BLOCKED`，退出码 2，属于预期安全拦截，不算正文恢复成功；临时原始报告位于 `/tmp/shipdemo-legacy-docx-verification.json`。

本次主要改动文件：`backend/app/rag/first_turn.py`、`rag/repository.py`、`services/rag.py`；`backend/app/ingestion/{parser,legacy_text,pipeline,repository,models}.py`；`backend/app/core/config.py`、两个 `.env` 示例、`compose.production.yaml`；三个新测试文件与 `verify_legacy_docx.py`。此前实验测试的导入格式已修正；其他并行的 Assistant 管理、标题展示和迁移改动保留，未覆盖。

- 定向：`cd backend && ../.venv/bin/pytest -q -o addopts='' tests/test_rag_first_turn.py tests/test_legacy_docx.py tests/test_title_recall_postgres.py`。
- 全量：根目录 `bash scripts/check.sh`；本地运行 Worker 时注意 smoke 入队后自行取回任务的竞争，必要时使用确认空闲、无 Worker 消费的 Redis DB 隔离验收队列。
- 文件：根目录 `PYTHONPATH=backend .venv/bin/python rag-acceptance/verify_legacy_docx.py <DOCX路径>`；真实 OCR 验证须显式加 `--real-ocr`，仅使用本地模型配置，不写索引。
- 发布：部署新的后端及 Worker 镜像后先保持 shadow，在同环境复跑 Assistant Golden，特别检查 PDC 澄清确认、FFC600 电池、LRU 单强影子、歧义/噪声和后续追问。DC24V 必须先修复源文件及候选索引，不能仅重启服务。
- 生产尚未部署；修改后的真实模型全量 Golden、FFC600 补召回实际收益、DC24V 修复后的命中效果和延迟变化均待确认。
