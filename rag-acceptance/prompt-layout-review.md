# 提示词排布与供应商隐式缓存（2026-10-10，LLM 2 采纳静态规则在前，LLM 1 不改；ADR-0042）

问题：LLM 1 和 LLM 2 的用户消息都以逐请求变化的数据开头（用户提出的是 LLM 2 的排布，LLM 1 是随探针一并测试的），规则、Schema、示例（LLM 2 约 1.8k 估算 Token）在文档（最长约一万 Token）之后，两次不同请求几乎没有共同前缀，供应商的隐式前缀缓存几乎命中不了。线上 Trace 的命中占比只有 LLM 2 9%–11%、LLM 1 11%–24%，而且来自同一个请求被再次发送。

供应商事实（来自阿里云百炼“上下文缓存”文档）：隐式缓存自动生效、不能关闭，最少 256 Token 的共同前缀，命中部分按输入单价的 20% 计费，按前缀匹配，尽力而为不保证命中。

## 一、缓存探针（`run_prompt_cache_probe.py`，28 次真实调用）

四条互不相交的流：{LLM 2, LLM 1} × {CURRENT 现状排布, STATIC_FIRST 静态在前}，在 +0/30/60/90 秒和 +5/15/30 分钟各调用一次。每次换一个冻结案例并在问题后追加唯一编号，所以没有两次调用共享完整提示词，之前的实验不可能预热过缓存。STATIC_FIRST 只把同一个 JSON 提示词的键重排（静态键在前，其余保持原序）。判读规则采集前写在脚本里：STATIC_FIRST 在 +30/60/90 秒三次里至少两次命中（LLM 2 ≥1000、LLM 1 ≥800 Token）且 CURRENT 全部 <256，才算机制成立。

结果（`cached_tokens / prompt_tokens`）：

| 流 | +0 | +30 秒 | +60 秒 | +90 秒 | +5 分钟 | +15 分钟 | +30 分钟 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LLM2 CURRENT | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LLM2 STATIC_FIRST | 0 | 1024/20922 | 1024/5031 | 1024/19524 | 1024/20541 | 0/19545 | 0/20921 |
| LLM1 CURRENT | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LLM1 STATIC_FIRST | 0 | 1024/1389 | 1024/1366 | 1024/1360 | 1024/1357 | 0/1381 | 1024/1389 |

（“+5 分钟”距上一次调用约 3.5 分钟，“+15 分钟”距上一次 10 分钟，“+30 分钟”距上一次 15 分钟；每一步各用一个新问题。）

- **机制成立**：静态在前后，不同请求稳定命中 1024 Token，现状排布始终为 0。命中量固定为 1024（原因未验证，不猜）。
- **保留时间在分钟级且不稳定**：相邻两次调用间隔 30 秒到 3.5 分钟都命中；间隔 10 分钟两个阶段都未命中；间隔 15 分钟 LLM 1 又命中而 LLM 2 未命中（供应商文档说明缓存是尽力而为，这里的 LLM 1 命中无法解释，也不应依赖）。所以只有同一前缀在几分钟内被再次请求才可靠地有收益。
- **实际收益有限**：LLM 2 的提示词约 1.95 万 Token（比估算大），命中 1024 只占 5%，按 20% 计价约省输入费用的 4%；LLM 1 提示词只有约 1.4k Token，命中占 74% 但绝对金额小。延迟不能算收益：同一案例在没有缓存的第 0 步，两种排布也差约 2 秒，比后来的差距还大。

## 二、静态在前是否改变回答（`run_layout_review.py`，60 次真实调用）

决定“要不要改排布”由用户做出（理由：当前生产只是测试版，正式版上线后流量会上来）。所以这一步是不退化筛查，不是放行测试（两个阶段都按静态在前测，LLM 1 的后果见第三节）：LEGACY（旧排布，从现提示词把键移回原序，与改动前 `HEAD` 的提示词逐字节一致）对 STATIC_FIRST，五个案例（同上一份实验）× 每臂 3 次 × 回答阶段和理解阶段。判读规则采集前写定：回答有效性、FFC/PSU 对照、非操作类图片均值（≤ +1.0）、操作类完整率（不少于 LEGACY 减 1 次）和借用图片（≤ +1）、理解阶段（scope、relation、response_path 的众数一致且至少 2/3 一致）。

| 关卡 | LEGACY | STATIC_FIRST | 判定 |
| --- | --- | --- | --- |
| 1 有效性问题 | 0/15 | 0/15 | 通过 |
| 2 对照（FFC / PSU） | 3/3、3/3 | 3/3、3/3 | 通过 |
| 3 非操作类图片均值 | 2.33 | 2.83 | 通过（+0.5） |
| 4(a) 操作类送达全部主文档图 | 7/9 | 6/9 | 通过（≥ 7−1） |
| 4(c) 借用图片/次 | 3.00 | 4.33 | **未通过**（上限 4.00） |
| 5 理解阶段（5 个案例 × 3 次） | 一致 | 一致，各 3/3 | 通过 |

另有：回答阶段平均输出 565 → 546 Token，平均延迟 9.9 → 9.2 秒（单次差异在噪声之内，不作结论）。关卡 5 只比较了 scope/relation/response_path，这三个字段全部相同；但**逐条阅读 `resolved_question` 时发现 STATIC_FIRST 丢掉了用户的排除条件**（见下一节），关卡 5 没有测到这一点。

**未通过的关卡 4(c)**：STATIC_FIRST 每次回答借用其他文档的图片 4.33 张，LEGACY 3.00 张，差 1.33，略超 +1 的上限。差异主要来自 PDC 更换案例的一次（发了 14 张）和 FFC 更换案例（三次都是 15 张，LEGACY 为 18/12/8）。置换检验 p≈0.23，不可与噪声区分；LEGACY 自己两次测量（本实验 3.00，上一份实验的 BASELINE 2.00）相差 1.0。按预先写定的规则这是一次失败，已如实记录；用户已决定采纳排布，没有回退。风险（静态在前使完整操作类回答多发图）未被证实也未被排除。

## 三、LLM 1 静态在前会丢排除条件（`run_interpreter_constraint_check.py`，48 次调用）

发现：PDC 氧化案例的原话是“……不要给软件升级流程。”LEGACY 的 `resolved_question` 3/3 保留了这条（“（不要提供软件升级流程）”或“（排除软件升级流程）”），STATIC_FIRST 3/3 全部丢掉。LLM 2 只看 `resolved_question`、不看原话，所以丢了排除条件就会直接改变回答。

为分清是个案还是普遍，采集前写定 8 个各带一条显式排除的问题（`QUESTIONS`），每臂每题 3 次、温度 0，所以有效样本约等于 8 个问题。关卡：STATIC_FIRST 保留排除条件的样本数不少于 LEGACY 减 2（共 24 个）。正则初筛的数字是 LEGACY 18/24、STATIC_FIRST 15/24，但正则会把“在不更换部件的前提下”“在不拆机的情况下”这类改写误判为丢失（意思没变），所以以逐条阅读为准：

| 问题 | LEGACY | STATIC_FIRST |
| --- | --- | --- |
| PDC 检查（不要软件升级流程）、FFC 地址（只回答地址）、PDC 氧化（不要升级也不要更换）、PDC 升级准备（不要讲具体操作） | 各 3/3 | 各 3/3 |
| PSU 先别更换、FFC 端口别拆机（均被改写成“在不……的前提/情况下”） | 3/3、3/3 | 3/3、3/3 |
| LRU 软件操作（不要重复硬件拆装步骤） | 3/3（一次改写为“不包含硬件拆装步骤”） | **2/3**（一次丢失） |
| MU LED（不需要故障排查） | 3/3 | **1/3**（两次丢失） |
| 合计 | **24/24** | **21/24** |

加上关卡 5 里的 PDC 氧化案例（LEGACY 3/3、STATIC_FIRST 0/3），共 10 个问题 × 3 次：LEGACY 丢 0 个，STATIC_FIRST 丢 6 个，而且 3 个问题上 STATIC_FIRST 丢、LEGACY 不丢，没有一个问题反过来。按样本数做的精确检验 p≈0.01，但样本在问题内高度相关，按问题数算只有 3 对 0（p≈0.13）。证据是提示性的、方向一致，不是定论；预先写定的关卡无论按正则（15 < 16）还是按人工阅读（21 < 22）都没有通过。

**所以 LLM 1 不改排布**：它的提示词只有约 1.4k Token，命中后绝对节省可以忽略，却有丢失用户限制的风险，得不偿失。LLM 1 的提示词与改动前逐字节一致，`test_prompt_layout.py` 把“问题在前”固定下来并写明原因。

## 结论

- 缓存机制成立，但收益很小（LLM 2 约 4% 输入费用，只有几分钟内的连续请求才命中）。
- **LLM 2：已采纳静态规则在前**（用户决定）。回答有效性、对照、理解阶段没有退化；未通过的是借用图片一项，证据不显著，已如实记录。
- **LLM 1：不改**。静态在前会让 `resolved_question` 丢掉用户的排除条件，这是本次排布改动里唯一有语义危害的发现。
- 对话阶段提示词体量小（静态部分接近 256 Token 下限），没有测量，不改。
- 部署后看：Trace 里 `model_usage.answer.prompt_tokens_details.cached_tokens` 的命中占比；完整操作类回答的送达图片数。

## 复现

```bash
# 不花钱：打印每个流的首个键和静态部分估算 Token
PYTHONPATH=backend:rag-acceptance .venv/bin/python rag-acceptance/run_prompt_cache_probe.py \
  --prepare-only --output /tmp/probe-prep.json
# 付费：28 次调用，约 30 分钟（会等待到 +30 分钟）
PYTHONPATH=backend:rag-acceptance .venv/bin/python rag-acceptance/run_prompt_cache_probe.py \
  --output rag-acceptance/reports/prompt-cache-<date>/probe.json
PYTHONPATH=backend:rag-acceptance .venv/bin/python rag-acceptance/run_prompt_cache_probe.py \
  --summarize rag-acceptance/reports/prompt-cache-<date>/probe.json
# 排布不退化筛查：60 次调用（LEGACY 与 STATIC_FIRST 两种排布都由 frozen_inputs 里的
# legacy_layout / static_first_layout 从当前提示词重建）
PYTHONPATH=backend:rag-acceptance .venv/bin/python rag-acceptance/run_layout_review.py \
  --repeats 3 --output rag-acceptance/reports/layout-review-<date>/answers.json
PYTHONPATH=backend:rag-acceptance .venv/bin/python rag-acceptance/run_layout_review.py \
  --summarize rag-acceptance/reports/layout-review-<date>/answers.json
# 排除条件检查（LLM 1，48 次调用，很便宜）
PYTHONPATH=backend:rag-acceptance .venv/bin/python rag-acceptance/run_interpreter_constraint_check.py \
  --output rag-acceptance/reports/interpreter-constraints-<date>/answers.json
PYTHONPATH=backend:rag-acceptance .venv/bin/python rag-acceptance/run_interpreter_constraint_check.py \
  --summarize rag-acceptance/reports/interpreter-constraints-<date>/answers.json
```

原始结果在 `rag-acceptance/reports/prompt-cache-20261010/probe.json`、`layout-review-20261010/answers.json` 与 `interpreter-constraints-20261010/answers.json`（被 `.gitignore` 忽略，不入库）。
