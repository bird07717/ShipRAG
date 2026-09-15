# RAG 语义编排 P0 基线

记录日期：2026-09-13。本文冻结 P1 开始前的评测输入和已经执行过的真实小样本结果，
用于后续文档卡片与双 LLM 链路做同题对照。它不是生产放行报告，也不表示 50 个专项场景已完成真实模型回归。

## 冻结输入

| 文件 | 用例 / 轮次 | SHA-256 |
| --- | ---: | --- |
| `cases.jsonl` | 53 / 63 | `b63af76e0b7870eba61e4c213b635c0d6318bfa814fff5240c1b2fa1231d8317` |
| `fact-qa-cases.jsonl` | 6 / 6 | `50ebb54003eca4e8136dc735aa0d1b72054fd44b61494c1496126d7b1f00e472` |
| `quality-cases.jsonl` | 20 / 30 | `5a810e8117000201aca4b43e674391e762db49dc94386d29ecddd6a6862e7ff9` |
| `reasoning-cases.jsonl` | 11 / 18 | `faad2f19fce9ba939b07b7714d15bd8e112eb5a176495de9cdb4d4622ec09bfc` |
| `technical-assistant-cases.jsonl` | 50 / 72 | `bd2071315f21cf5e60a0e653d712b5168846a36d2518e4d61234684e79c8eaca` |
| `semantic-orchestration-expectations.jsonl` | 50 / 72 | `6d99208ca25075d5131eba9d19874de520e78c29f0533638f48ad0bed00ca682` |

专项集每轮都具有且只具有以下旁路期望字段：

- `expected_strategy`：`SHORT_ANSWER`、`DOCUMENT_DELIVERY`、`DOCUMENT_ASSISTED`、
  `GENERAL_TECH`、`CLARIFY` 或 `OUT_OF_SCOPE`。
- `allowed_document_keys`：允许作为依据或交付目标的文档标题关键字。
- `forbidden_document_keys`：不得引用或交付的相似文档关键字。
- `allow_general_knowledge`：该轮是否允许使用明确标注的通用工程知识。

72 轮的目标分布为：`SHORT_ANSWER` 11、`DOCUMENT_DELIVERY` 21、
`DOCUMENT_ASSISTED` 1、`GENERAL_TECH` 27、`CLARIFY` 6、`OUT_OF_SCOPE` 6。

## 已执行的真实小样本

本地诊断报告 `reports/baseline-20260913-212420.json` 的 SHA-256 为
`5b01b671c9dda60ecc24e79ff7de55c1af023ef73e3028495a4dfd18f96650d7`。
`reports/` 默认被 Git 忽略，因此以本文保存可审计摘要，原始报告留在执行环境。

| 指标 | 结果 |
| --- | --- |
| 接口 / 环境 | 本地 Assistant HTTP / VDR600 当时活动索引 / 真实模型 |
| 用例 / 轮次 | 5 / 6 |
| 自动通过 | 4 / 5（80%） |
| 延迟 | p50 10.294s；p95 与 max 35.681s |
| 通过类别 | 局部事实、领域扩展、范围外、两轮连续排查的既有宽松断言 |
| 失败类别 | “请给出配置 VAU 图像的完整步骤”返回 `UNCONFIRMED`，未交付目标文档 |

连续排查用例通过只说明当时响应类型满足断言；第二轮仍返回 `UNCONFIRMED`，不能据此宣称
结构化问题状态或上下文承接质量已经达标。领域扩展回答出现约 35 秒长尾，且工程内容仍需人工复核。

## 复现方式

静态结构与逐轮覆盖校验不调用模型：

```bash
cd backend
../.venv/bin/pytest -q --no-cov tests/test_rag_technical_assistant.py
```

真实专项回归会创建测试会话并调用真实模型，只能在已上线、就绪的隔离 RAG 知识库执行：

```bash
GOLDEN_ASSISTANT_TOKEN=<test-token-from-secret-manager> \
.venv/bin/python rag-acceptance/run_golden.py \
  --api-mode assistant \
  --base-url <test-service-base-url> \
  --kb-id <online-ready-rag-knowledge-base-id> \
  --external-user-id <stable-test-user-id> \
  --cases rag-acceptance/technical-assistant-cases.jsonl
```

后续对照必须记录当前用例文件哈希、活动索引、模型快照、开关状态、原始报告和工程师语义评审。
若评测输入有业务修订，应形成新基线并解释差异，不能覆盖本基线后继续声称同题对照。

## P0 本地验证结果

- 定向回归：欢迎语、回答路由、专项期望、解释和文档路由共 133 项通过。
- 完整门禁：Ruff lint/format（242 个 Python 文件）、Mypy（125 个源文件）、后端
  1024 项测试、三项 fake 冒烟和累计覆盖率 83% 通过。
- 前端：22 个测试文件共 155 项通过，覆盖率、ESLint、类型检查和生产构建通过；
  构建保留入口 JavaScript 超过 500 kB 的既有警告。
- 本轮未调用真实模型、未运行 Golden Set、未修改数据库或生产环境。
