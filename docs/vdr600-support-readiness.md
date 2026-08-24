# VDR600 Support 试点清单

更新日期：2026-08-24。源目录 `/home/bird/Documents/VDR600` 当前包含 24 篇 DOCX。原始
技术文档经资料所有方授权用于本系统，不做默认脱敏，也不复制进 Git 仓库。

## 第一批 5 篇

优先覆盖不同任务和风险：

| 文档 | problem_code | 标准问题 |
|---|---|---|
| 故障解决文档11—如何U盘升级PDC | `PDC_UPDATE_UPGRADE` | 如何通过 U 盘升级 PDC？ |
| 故障解决文档10—如何更换故障PDC | `PDC_UNIT_REPLACEMENT` | 如何更换故障 PDC？ |
| 故障解决文档41-time battery low报警 | `TIME_BATTERY_LOW` | time battery low 报警如何处理？ |
| 故障解决文档34—如何手动自检VDR | `VDR_MANUAL_SELF_TEST` | 如何手动自检 VDR？ |
| VDR600如何修改船名 | `VDR_SHIP_NAME_CHANGE` | VDR600 如何修改船名？ |

Alias 初始可以全部留空。标准问法不能直接命中时，由闭集问题目录分类器选择；真实使用中
确认某种表达高频且稳定后，再把它加入 Alias。

## 每篇验收

- 文档已解析并进入 Active Index；
- Text/Table/Image Element 顺序与原文一致；
- 图片 Element 均能读取；
- Profile `support_ready=true`；
- 标准问题返回 `DOCUMENT_DELIVERED`；
- 返回全部 Element 和图片且不调用回答 LLM；
- 后续“这一步是什么意思”只基于当前文档解释；
- 文档没有的信息明确说明缺失，不编造；
- 高风险无依据操作返回人工升级；
- 转问另一篇文档时先要求确认。

## 本地执行结果

2026-08-24 已完成：

- 建立独立知识库 `VDR600 Support Pilot`；
- 导入源目录全部 24 篇 DOCX，并构建、激活同一个不可变 Index；
- 创建 24 个 Alias 为空的 Profile，全部 `support_ready=true`；
- 24 个标准问题全部返回 `DOCUMENT_DELIVERED`；
- 共核对 398 个有序 Element 和 121 张图片；
- 非标准问法“我想拿U盘给PDC刷一下新版本”通过 `LLM_CATALOG` 命中 `PDC_UPDATE_UPGRADE`；
- 聚焦文档后的步骤解释返回 `ANSWERED`，并通过结构化证据校验。

构建期间真实 Vision 服务多次返回 HTTP 429，现有重试最终全部恢复并完成任务；当前账户下的
默认图片并发偏高是已确认的性能风险，但没有造成文档、Element 或图片缺失。无需制作 200
条案例、Shadow 报告或百分比灰度；实际失败问题经过人工确认后加入小型开发验收集即可。
