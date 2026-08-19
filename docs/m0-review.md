# M0 技术验证核查

核查日期：2026-08-17。

## 结论

M0 状态：**PASS**。

DOCX、MinIO、严格 BM25 POC 以及 10 项真实云模型契约探测全部成功，部署级 `EMBEDDING_DIMENSION` 已根据真实响应冻结为 `1024`。项目负责人同时接受在“未修改、仅服务端托管、不分发”边界内使用 `pg_search` 社区版的许可证风险，M0 已无未关闭项。

## 核查结果

|验证项|状态|证据与结论|
|---|---|---|
|系统范围、模块边界和全局不变量|PASS|架构文档和 ADR 已冻结|
|厂商公开 API 契约|PASS|已按官方文档核对 Embedding、Rerank、OCR、GLM thinking/stream 和 Vision 输入|
|Embedding：文本、批量、图片、混合列表、单对象图文融合|PASS|SiliconFlow 真实调用 5/5 通过，均返回 1024 维及请求 ID|
|VL Reranker：文本和多模态|PASS|真实调用 2/2 通过，预期文档均为 Top 1|
|DeepSeek-OCR|PASS|Data URL 输入成功并识别样例值 `3306`|
|GLM-5.2 流式 thinking|PASS|观察到 `reasoning_content`、正文和 `[DONE]`|
|GLM-5V-Turbo|PASS|Data URL 输入成功并识别样例值 `3306`|
|Embedding 维度|PASS|五种真实输入均返回 1024 维；配置与校验器已冻结为 `1024`|
|Mammoth/OOXML 标题、段落、表格、图片及相邻顺序|PASS|生成真实 DOCX 包并断言 Mammoth HTML、`word/media/*`、关系 ID 和块顺序|
|DOCX 来源定位规则|PASS|ADR-0003 已明确页码可空|
|MinIO 私有图片传输|PASS|匿名 GET 为 403，1 分钟签名 URL 可取回且字节一致；向厂商发送 Base64 Data URL，不披露签名 URL|
|严格中文 BM25 技术可行性|PASS|`pg_search 0.25.0` + PostgreSQL 17；中文、数字、英文型号、路径、排序及 dump/restore 通过|
|BM25 许可与交付方式|PASS（条件接受）|限定未修改、仅服务端托管、不分发；变化时重新评审|
|POST + SSE 协议|PASS|ADR-0004 已冻结，真实 GLM 流式事件验证通过|

脱敏在线报告见 [m0-online-report.json](evidence/m0-online-report.json)，本地证据见 [m0-offline-report.md](evidence/m0-offline-report.md)。

## 已落地的验证资产

- `backend/app/m0/online.py`：10 项真实厂商契约探测，只记录结构、维度、计数、延迟和错误分类，不保存响应正文或密钥。
- `backend/app/m0/docx_probe.py`：Mammoth 与 OOXML 双路径解析验证。
- `backend/app/m0/storage_probe.py`：私有对象、匿名访问和短期签名 URL 验证。
- `compose.m0-bm25.yaml`：独立、临时、固定摘要的 ParadeDB 验证环境，不修改 M1 数据库。
- `scripts/m0-bm25-smoke.sql`：严格 BM25、Lindera 中文分词和检索样例。
- `scripts/check-m0.sh`：完整 M0 门禁入口。

## 执行与验收

严格门禁：

```bash
docker compose up -d
./scripts/check-m0.sh
```

2026-08-17 的执行结果为在线探测 10/10 `passed`。报告必须持续满足：

- 五种 Embedding 请求均返回 `1024` 维；
- 图文融合对象返回一个联合向量；
- 文本与多模态 Rerank 的预期相关文档为 Top 1；
- OCR 和 Vision 均识别出样例关键值；
- GLM 流包含 reasoning、正文和终止标记；
- 输出不包含 Key、请求正文、模型响应正文或签名 URL。

没有云凭据的开发环境可以运行：

```bash
./scripts/check-m0.sh --allow-blocked
```

该选项只用于重放本地证据，不能把缺少凭据的报告标记为成功。

## pg_search 使用边界

许可证风险接受结论已记录在 ADR-0001。必须持续满足：

- 使用官方、未修改且锁定摘要的镜像；
- 只在本项目服务端运行，由后端通过 SQL 调用；
- 不向用户直接开放数据库，不分发镜像或安装包；
- 保留 AGPL-3.0 许可证、源码地址、版本和第三方组件记录。

修改扩展、客户私有化交付、分发镜像、开放数据库能力或合并源码时，必须重新评审。Alembic、HA、复制、升级、RRF 和生产安全基线属于后续实现验收项，不再阻塞 M0。

## 官方契约基线

- SiliconFlow Embeddings：<https://api-docs.siliconflow.cn/docs/api/embeddings-post>
- SiliconFlow Rerank：<https://api-docs.siliconflow.cn/docs/api/rerank-post>
- SiliconFlow Chat Completions：<https://api-docs.siliconflow.cn/docs/api/chat-completions-post>
- 智谱 GLM 新模型迁移：<https://docs.bigmodel.cn/cn/guide/start/migrate-to-glm-new>
- 智谱 GLM-5V-Turbo：<https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5v-turbo>
- ParadeDB：<https://github.com/paradedb/paradedb>
- ParadeDB Lindera 中文分词：<https://docs.paradedb.com/documentation/tokenizers/available-tokenizers/lindera>

在线探测执行时必须再次以厂商当日文档为准；接口或模型变更需要更新探测器和证据。
