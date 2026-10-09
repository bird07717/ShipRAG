# `next_suggestions` 字段实验：给字段下定义没有改善越界，不采用

日期：2026-10-09。仅本地固定输入实验；业务 Prompt 与代码未改，未部署。

## 结论

**不采用 `DEFINED`，不修改生产 Prompt。** 按实验前写定的规则：MU 加 PDC 含越界建议的回答从 BASELINE 的 14/16 到 DEFINED 的 14/16（要求至少减半，即 ≤8/16）；主回答没有改善；对照题还新增了把用户引向相邻流程的建议。

**原假设被否定。** 假设是“`next_suggestions` 在 Prompt 里没有定义，是 MU/PDC 越界内容的载体，定义后可收敛”。数据显示：
- 把字段强制清空（EMPTY 臂）后，MU/PDC 的主回答没有变化，说明建议字段不是主回答越界的原因，只是同一倾向的另一个出口。
- 定义字段只改变了形式（指令变成疑问句），没有改变内容边界。

## 设计

输入、模型、参数与此前相同：同一冻结索引，MU/PDC/FFC/PSU 四题的“实际多文档全文 A”，真实 DashScope `qwen3.8-flash`，温度 0.1、输出上限 4096、思考关闭、JSON Mode 开启。三个臂相对 BASELINE 只差 `output_contract` 的一条：

| 臂 | 变化 |
| --- | --- |
| BASELINE | 当前 Prompt，不变 |
| DEFINED | 新增一条：`next_suggestions` 只能是围绕当前问题的疑问句，不得是操作指令，不得引入当前问题没有涉及的操作或处理流程，不得与用户明示限制冲突，无合适问题返回空列表 |
| EMPTY | 新增一条：`next_suggestions` 必须返回空列表（诊断臂） |

4 题 × 3 臂 × 8 次 = 96 次真实调用，全部成功、无结构重试、无采集错误。中位延迟 BASELINE 8.4 秒、DEFINED 8.6 秒、EMPTY 7.0 秒（仅描述本轮，不推导生产延迟）。预检校验三个臂的 Prompt 只相差该条合同项，且冻结上下文与原 Prompt 哈希与历史一致。

## 判读规则（采集前写定，见 `run_suggestions_review.py` 文档字符串）

主指标：`next_suggestions` 中至少有一条提出超出用户请求的动作的回答数，逐条人工判读。MU 只要求观察记录、不打开/更换/升级；PDC 要求评估可靠性和是否需要专业检查，不要维修步骤。具体判定：涉及机箱内部检查、备件/更换/升级、或改变设备状态（重置参数、串口调试端口）的建议计为越界；经 OPU/日志/指示灯/外部电压的外部观察不计。采用标准：MU+PDC 越界建议减半以上、对照题不回退、主回答新增越界不超过 2 次。

机械筛查（`--summarize`）只是辅助，不是主指标。本次它给出了误导：MU 的“开盖类”词在建议里从 5 降到 1，看似改善，但逐条阅读发现 DEFINED 用“PSU 保险丝规格以便准备备件”“保险丝外观正常但怀疑接触不良如何检测”“重置 MU 通讯参数”等建议取代了它们，这些词不在筛查正则里。

## 结果

### 建议字段（主指标，人工判读，每格 8 次）

| 题 | BASELINE | DEFINED | EMPTY |
| --- | --- | --- | --- |
| MU | 6/8 越界 | 6/8 越界 | 无建议 |
| PDC | 8/8 越界 | 8/8 越界 | 无建议 |
| 合计 | **14/16** | **14/16** | — |

MU 的越界建议例子：BASELINE 为“检查 MU 机箱内 TB1/TB2 端子”“确认内部电源模块型号”“PSU 模块替代型号推荐”“串口连接调试端口”；DEFINED 为“PSU 保险丝规格是多少，以便准备备件”“如果确认为 3359 底板故障，更换前需要做哪些数据备份”“通过 OPU 菜单重置 MU 通讯参数”。PDC 两个臂都集中在“更换接线盒的步骤/备件/专用工具”“更换后的水密防水处理”。DEFINED 把指令改成了疑问句，内容仍指向同一批流程。

宽松敏感性检查：把我归为“临界”的两条（MU BASELINE #2 的 TB1/TB2 紧固，PDC DEFINED #5 的防氧化措施）都不计，则 MU 为 5/8 对 5/8、PDC 为 8/8 对 7/8，仍远未减半。

### 主回答（次指标）

MU 主回答中肯定性地要求打开机箱盖板/箱门或检查机箱内部指示灯、PSU 保险丝：BASELINE 6/8、DEFINED 7/8、EMPTY 7/8。PDC 主回答中，三个臂都在多数样本里展开临时对接、更换接线盒或“必须/建议更换”的建议。EMPTY 臂清空建议字段后主回答没有改善，因此建议字段不是主回答越界的原因。主回答的判读按句子匹配后人工阅读，边界句（如“虽然不拆机无法看到内部保险丝”）按否定处理。

### 对照题（核心内容保留）

| 项 | BASELINE | DEFINED | EMPTY |
| --- | --- | --- | --- |
| FFC：电脑地址 172.16.8.175 与“不能仅凭掉线确诊” | 8/8、8/8 | 8/8、8/8 | 8/8、8/8 |
| PSU：断电、3359、更换、拍照标记、五分钟检查 | 8/8 | 8/8 | 8/8 |
| PSU 图片标记数 | 5–8 | 5–7 | 5–6 |
| FFC 建议非空 | 1/8 | **8/8** | 0/8 |
| PSU 建议非空 | 0/8 | **8/8** | 0/8 |

**DEFINED 的副作用**：它让原本几乎不给建议的题也总是给建议，且包含把用户引向相邻流程的问题，例如 FFC“FFC 更换后需要执行哪些步骤？”，PSU“更换 3359 核心板后需要导入哪些数据？”“如何判断 3359 核心板是否需要一并更换？”（此前实验已把核心板更换扩展记录为 PSU 题的牵引内容）。核心内容没有回退，但对外多出了更多相邻流程指引。

## 限制

- 评阅人是同一个开发代理，非盲评，未经独立工程专家复核；越界判定规则在采集前固定，但个别条目属临界，已在敏感性检查里单列。
- 仅 4 个场景、每格 8 次，其中 MU/PDC 为刻意挑选的困难题；对照题也只覆盖 FFC、PSU 两个。不能给出总体正确率，也不能推出任意 Prompt 定义都无效，只说明“这版定义”在这批输入上没有达到预设标准。
- 固定输入绕过 LLM 1、检索和公开组装，不是端到端验证。
- 没有运行 Golden、模型评委或自动评分。

## 后续处理：字段在服务端丢弃（ADR-0040）

用户据此决定丢掉该字段：管线返回 `next_suggestions=[]`，当时不改 Prompt（见下节原先的“可选产品决策”分析，现已实施其服务端版本；Prompt 层移除在第二轮评估，用户最终决定采纳）。

## 可选的产品决策分析（实验时写下；服务端丢弃已按 ADR-0040 实施，Prompt 层要求返回空列表仍未做）

EMPTY 臂显示，强制清空建议字段不会降低主回答质量（对照题核心内容 8/8，MU/PDC 主回答与基线无差别），并能消除全部建议字段里的越界指引。是否对外保留该字段是产品决策：管理台 DemoChat/回答复核和对外 Assistant 响应都会展示它。若要尝试，应作为独立变更验证，并确认业务后台是否依赖该字段非空。

## 原始结果与复现

原始结果在本地 Git 忽略目录 `reports/suggestions-20261009/`：`prepared-inputs.json`（零付费预检快照）、`answers.json`（96 个完整回答、建议、usage、延迟与请求参数）。

```bash
# 零付费预检；输出路径必须不存在
PYTHONPATH=backend .venv/bin/python rag-acceptance/run_suggestions_review.py \
  --prepare-only --output rag-acceptance/reports/suggestions-recheck.json

# 真实付费采集，不覆盖原报告
PYTHONPATH=backend .venv/bin/python rag-acceptance/run_suggestions_review.py \
  --repeats 8 --concurrency 2 --output rag-acceptance/reports/suggestions-repeat.json

# 机械筛查汇总（仅辅助，不是主指标）
PYTHONPATH=backend .venv/bin/python rag-acceptance/run_suggestions_review.py \
  --summarize rag-acceptance/reports/suggestions-20261009/answers.json
```

复现需要同一数据库、冻结索引与真实模型配置；输入哈希变化时工具拒绝运行。

## 第二轮：从 Prompt 中移除该字段（2026-10-09，未过预设关卡，用户决定采纳）

服务端已丢弃该字段（ADR-0040），但 Schema 和示例仍要求模型生成它，白白消耗输出。第二轮实验检验“只把它从模型被要求写的内容里删掉”是否可以安全上线。

**设计**：BASELINE（当前 Prompt）对 `NO_FIELD`（只从 `output_schema.properties` 和 `output_example` 删除 `next_suggestions`，其余不变；校验类仍接受模型自行写入）。同样的四个场景、每格 8 次，共 64 次真实调用，`--arms BASELINE,NO_FIELD`。判读规则（三个关卡）在首次付费调用前写入 `run_suggestions_review.py` 的文档字符串，结果出来后未改动。

| 关卡 | 规则 | 结果 |
| --- | --- | --- |
| 1 有效性 | `NO_FIELD` 的结构化错误或重试不超过基线 +1（32 样本） | 通过：基线 0，`NO_FIELD` 重试 1 次（MU #5，46.1 秒，最终成功） |
| 2 对照题 | FFC 地址与“不能确诊” 8/8；PSU 核心步骤 8/8 且图片标记数在基线范围内 | 通过：FFC 8/8 和 8/8；PSU 8/8，图片标记 `NO_FIELD` 5–7，基线 5–11（`6,11,6,6,7,8,8,5`） |
| 3 目标题 | MU+PDC 主回答中“指示用户越界”的数量，`NO_FIELD` 比基线多不超过 2/16 | **未通过**：MU 基线 3/8、`NO_FIELD` 6/8；PDC 两边都是 8/8；合计 +3 |

第 3 关的判读：肯定性地指示用户打开机箱、柜门、盖板或检查机箱内部（含 PSU 保险丝）才计入，“若可视”“若有透明视窗”这类假设性说法和“此步骤超出不拆机限制”的声明不计入。逐条阅读了全部 16 个 MU 回答的全部相关句子。两条临界回答（基线 #4 有条件地提供打开路径；`NO_FIELD` #6 把用户的“不拆机”重新解释为只要不换部件）分别按两种口径验证：统一从严 +3、统一从宽 +3，只有对 `NO_FIELD` 单方面最有利的不对称配对才是 +2，该配对不采用。

**统计上不显著**：MU 的 3/8 对 6/8，Fisher 精确检验 p≈0.31（其它口径 0.28–0.61）。所以结论是“证据不足以判定有害，但按预先规则不能放行”，不是“证明有害”。

报告项（不作关卡）：
- 模型是否仍写该字段：`NO_FIELD` 0/32，基线 18/32。
- 输出 Token：基线 555、`NO_FIELD` 495，约少 10.8%，四个场景方向一致（MU 973→850，PDC 521→493，FFC 305→277，PSU 421→359）。
- 延迟中位数：MU 17.2→16.7、PDC 8.8→8.9、FFC 5.7→5.0、PSU 7.9→6.4 秒，方向不一致（PDC 反而多 0.1 秒），不据此声称提速。

**一个值得记住的假设（未证实）**：基线下模型把越界内容写进了会被服务端丢弃的 `next_suggestions`（第一轮 14/16 越界），主回答反而更守边界；去掉这个出口后，越界倾向可能转移到主回答里。与此一致的还有第一轮 EMPTY 臂（建议字段被要求为空）主回答 7/8 对基线 6/8（那时用的是较宽的口径）。如果成立，当前“让模型照常生成、服务端丢弃”的做法恰好是更安全的组合。但两轮的差异都在 n=8 的噪声范围内，需要更大样本才能确认。

**决定（更新）**：预先登记的规则下不放行，但用户随后明确选择采纳（有意推翻第 3 关，不是证据变化；见 ADR-0040）。落地后 `answer_generator.py` 的输出 Schema 与示例不再要求该字段，服务端丢弃保留；上线 Prompt 与实验的 `NO_FIELD` 臂逐字节一致。收益只占总 Token 约 0.7%（输出 Token 的 10.8%），成本约 1.4%–2.1%，平均延迟只快约 0.16 秒。部署后建议对 MU 做每臂至少 24 次的复测，判读口径在采集前写定。因为 BASELINE 校验对照的是上线前的 Prompt，改动后复现这轮实验需检出 `613ca69`。

局限同第一轮：评阅人是同一个开发代理、非盲评、只有四个场景，且 MU/PDC 是刻意挑选的难题；固定输入绕过 LLM 1、检索和公开组装。

复现：

```bash
PYTHONPATH=backend .venv/bin/python rag-acceptance/run_suggestions_review.py \
  --arms BASELINE,NO_FIELD --prepare-only --output rag-acceptance/reports/schema-field-recheck.json
PYTHONPATH=backend .venv/bin/python rag-acceptance/run_suggestions_review.py \
  --arms BASELINE,NO_FIELD --repeats 8 --concurrency 2 --output rag-acceptance/reports/schema-field-repeat.json
```

原始结果在 Git 忽略目录 `reports/schema-field-20261009/`（`prepared-inputs.json`、`answers.json`）。

