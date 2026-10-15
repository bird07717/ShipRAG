# 语言/格式类追问（"Answer in English"）复核

日期：2026-10-15。真实 DashScope 模型（与生产同配置），工具 `run_format_request_review.py`，原始结果 `rag-acceptance/reports/format-request-review-20261015.json`（该目录被 Git 忽略，只保留在本机）。判读规则和关卡在第一次付费调用前写进了工具的 docstring。

## 起因

生产库 2026-10-13 17:41（北京时间）同一会话：上一轮 `vdr ffc error` 已正常完成，下一句 `answer in english` 被 LLM 1 判成 `NEW_TASK` + `OUT_OF_SCOPE`，服务端不再调用 LLM 2，直接返回固定话术“我只能回答与本产品…请换一个与产品相关的问题”。同一会话 14:43 同一句话被判成 `NEW_TASK` + `UNCERTAIN`，没有继承任务，只是会话路径碰巧翻译对了。历史传递没有问题，是 LLM 1 判错。

## 改动

1. LLM 1 提示词：新增两条规则（`FORMAT_REQUEST_RULES`）和三个路由示例（`FORMAT_ROUTE_EXAMPLES`）。纯语言/格式/篇幅请求必须继承最近一轮任务，不得判 `OUT_OF_SCOPE`；要求重新作答或补充细节走 `KNOWLEDGE`，只翻译/压缩/改写上一条回答走 `CONVERSATION`。键顺序不变。
2. 服务端兜底 `guard_format_only_out_of_scope`：LLM 1 仍判 `OUT_OF_SCOPE`，但问题不超过 30 个字、含语言/格式词、并且存在完整的上一轮时，改为继承上一轮任务（`FOLLOW_UP` / `UNCERTAIN` / `KNOWLEDGE`），Trace 的 `query_interpreter.scope_guard` 记录 `FORMAT_ONLY_OUT_OF_SCOPE_INHERITED`。
3. 会话路径：规则要求直接给出完整内容；检测以冒号结尾的空引导语，重试一次，仍失败才回退到固定话术。
4. 回归检查（见下文“其他问题有没有受影响”）后追加两处：兜底遇到带引号的文本（如 `把 'good morning' 翻译成法语`）不再触发；新增 `guard_english_requirement`，原问题里有明确的英文作答指令而 `resolved_question` 里没有任何英文要求时，服务端补一句（Trace `language_guard`）。

## 实验设计（采集前写定）

BASELINE 是去掉新增条目后的提示词，已核对与改动前的 `HEAD` 逐字节一致（LLM 1 与会话各 4 组输入）。NEW 是现提示词。LLM 1 原始输出（未经兜底）：

- FORMAT：9 种请求 × 3 个长历史 × 3 次 = 81 个样本/臂。
- 对照 C1 新任务、C2 真实无关请求、C3 省略式追问、C4 无历史的 "Answer in English"、C5 显式排除条件（沿用 `run_interpreter_constraint_check.py` 的 8 个问题）。
- 会话路径：3 种改写输入 × 8 次/臂。LLM 2：英文要求 × 3（只报告，不设关卡）。

关卡：G1 NEW 的 FORMAT 正确率 ≥ 90% 且无 `OUT_OF_SCOPE`；G2 路径符合预期 ≥ 80%；G3 各对照 NEW ≥ BASELINE − 1（C5 为 −2）；G4 会话路径 NEW 的未完成回答数 ≤ BASELINE 且英文请求的回答以英文为主。

## 结果（共约 300 次调用）

| 项目 | BASELINE | NEW |
| --- | --- | --- |
| FORMAT 正确（继承任务、不判无关、保留格式要求） | 45/81 | **81/81** |
| FORMAT 被判 `OUT_OF_SCOPE` | 1 | 0 |
| FORMAT 路径符合预期 | 53/81 | 75/81 |
| C1 新任务不继承旧任务 | 9/9 | 9/9 |
| C2 真实无关请求仍判无关 | 9/9 | 9/9 |
| C3 省略式追问继承 | 6/6 | 6/6 |
| C4 无历史的 "Answer in English" 不判无关 | 3/3 | 3/3 |
| C5 排除条件（正则筛查） | 10/16 | 12/16 |
| 会话回答以冒号结尾（未完成） | 0/24 | 0/24 |

G1–G4 全部通过。读样本后的说明：

- BASELINE 的 36 个失败样本几乎都是英文请求（`answer in english`、`Answer in English.`、`Please answer in English`）丢掉任务，在 FFC 和 PDC 的长历史下整组 3/3 失败；中文 `用英文回答` 在基线就正确。比此前短历史探针更差。
- NEW 路径不符的 6 个全是 `简短一点`：NEW 把它判成“继承任务后重新作答”（`KNOWLEDGE`），基线判成“改写上一条回答”（`CONVERSATION`）。两种行为都合理，但前者要重新检索，回答里可能带图；见已知问题。
- C5 的 4 个 BAD 是正则筛查不认“不提供”“在不更换…的前提下”，逐条读 `resolved_question` 后，排除条件都保留了，BASELINE 与 NEW 一致。
- **会话路径的空引导语在本次没有复现**：此前短历史探针 3/15，这次长历史两臂都是 0/24（基线的回答是“Here is the translation:”后接全文）。所以第 3 项只是安全网，由单元测试覆盖，本次实验没有证明它降低了发生率。新规则让回答不再带“Here is the translation”前言。
- 观察（既有问题，未处理）：翻译时模型把 `FFC` 展开成 “Floating Flash Card” 或 “Floating Storage Unit”，历史里写的是“浮式存储单元”。
- LLM 2（PSU，英文要求，3 次）：3/3 英文，送达图片 4/3/4 张，其中 2 次借用 1 张其他文档的图片，与已记录的图片问题一致。

## 端到端核对

- 把兜底套到 BASELINE 记录下来的那条 `OUT_OF_SCOPE` 样本（`answer in english` + 历史 `vdr ffc error`）：改为 `FOLLOW_UP` / `UNCERTAIN` / `KNOWLEDGE`，`resolved_question` 为“vdr ffc error；当前要求：answer in english”。
- 真实 `interpret_query`（现提示词 + 兜底）4 种英文请求 × 3 个历史 × 3 次 = 36 次：36/36 状态 `PASSED`、`FOLLOW_UP`、`KNOWLEDGE`、继承了任务，0 次判无关；兜底没有触发（模型自己判对了）。
- 后端全量：967 passed / 125 skipped（DB 集成变量置空），Ruff、格式检查、Mypy 通过。

## 局限

- 每格 3 次，温度 0 时 LLM 1 仍会翻转，只能说明大幅改善，不能给出稳定率。
- 历史是为本次写的三段长回答，不是生产原文；生产只有用户自己的几条测试记录可对照。
- 兜底用关键词和长度判断，对“把这首诗翻译成英文”这类短的无关创作请求，在存在上一轮时也会被继承为产品追问，之后只靠 LLM 2 的规则 1（不执行无关写作）约束，这一点没有实测。这是为了避免固定话术误伤而接受的代价。
- 会话路径的回合不进入历史（见已知问题），所以“用英文回答”的偏好不会延续到下一个问题。

## 其他问题有没有受影响：50 个用例回归（2026-10-15）

工具 `run_regression_50.py`，用例与期望值在付费调用前写定，每例 2 次、新旧各一份。BASELINE 是旧提示词的原始输出，NEW 是现提示词 + 服务端保护（`interpret_query`）。类别：A 单轮产品问题 10、B 闲聊/确认 6、C 真正无关 6、D 省略式追问 10、E 历史之后的新任务 6、F 带格式词但有自己对象的真实问题 8、G 英文产品问题 4。关卡：R1 总数不低于基线 −3；R2 任何类别不低于基线 −2；R3 兜底在 C 以外的类别不触发；R4 新旧判定不同的用例逐个读。

第一次运行（总分 98 对 95，R1–R3 都过），读样本后发现两个真实问题：

1. **兜底误伤（C4，2/2）**：`把 'good morning' 翻译成法语` 在 FFC 历史之后原来被正确拒绝，兜底把它改成了“vdr ffc error；当前要求：把 'good morning' 翻译成法语”。原因：这句话带着要翻译的文本，不是纯格式请求。修复：带引号文本的消息不触发兜底。
2. **英文指令被丢（F8）**：`Please answer in English: how to check DAU voltage?`（无历史）。加到 8 次后，旧提示词丢 1/8，新提示词丢 5/8。实测后果：LLM 2 对不带英文要求的英文问题 5/5 用中文回答（文档是中文），所以丢了指令用户就会拿到中文答案。修复：`guard_english_requirement`。

修复后重测（同一 50 例）：

| 类别 | BASELINE | NEW |
| --- | --- | --- |
| A 单轮产品问题 | 20/20 | 20/20 |
| B 闲聊/确认 | 12/12 | 12/12 |
| C 真正无关（含两个短翻译请求） | 12/12 | 12/12 |
| D 省略式追问 | 18/20 | 18/20 |
| E 历史之后的新任务 | 12/12 | 12/12 |
| F 带格式词的真实问题 | 16/16 | 16/16 |
| G 英文产品问题 | 8/8 | 8/8 |
| 合计 | 98/100 | 98/100 |

- R3：兜底在所有类别里 0 次触发；F1/F5/F8 各 8 次，NEW 24/24。
- D6 `那 PDC 呢`（FFC 历史之后换对象）新旧都答不好：基线 NEW_TASK 丢上下文，新版同样。这是既有弱点，不在本次改动里，没有处理。
- B2/B3/B5 的差异只是 `IN_SCOPE` 与 `UNCERTAIN` 的互换，路径和关系都符合预期。

注意：英文问题**没有**英文指令时，LLM 2 仍用中文回答（上面测得 5/5）。这是既有行为，没有改，因为不确定用户是否希望英文问题自动得到英文回答。
