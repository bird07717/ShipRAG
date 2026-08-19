# ADR-0001：PostgreSQL 内的严格 BM25

- 状态：接受
- 日期：2026-08-17
- 决策者：项目负责人（本次确认）

## 背景

V1 已冻结支持 BM25、Vector Search 和 Hybrid Retrieval，同时技术栈限定 PostgreSQL + pgvector，不引入 Elasticsearch/OpenSearch 服务。

`pgvector` 不提供 BM25。PostgreSQL 原生全文检索的 `ts_rank` 和 `ts_rank_cd` 提供相关性排序，但不是严格 BM25，不能在测试、后台或产品文案中冒充 BM25。

当前方案 `pg_search` 是 PostgreSQL 扩展，在数据库内提供基于 Tantivy 的 BM25 能力，符合“不增加第二套搜索服务”的架构目标。社区版本使用 AGPL-3.0；本项目已明确采用未修改、仅服务端托管、不分发的使用方式。

## 决策

1. V1 的 `BM25` 验收含义是严格 BM25，不接受 `ts_rank`/`ts_rank_cd` 等价替代。
2. 首选实现候选为 PostgreSQL `pg_search` 扩展；向量继续由 `pgvector` 提供。
3. 接受在以下边界内使用 `pg_search` 社区版的许可证风险：
   - 使用官方、未修改的二进制或容器镜像；
   - 仅部署在本项目服务端，由 FastAPI 后端通过 SQL 调用；
   - 不向最终用户开放数据库或 `pg_search` 接口；
   - 不向客户分发 ParadeDB 镜像、安装包或修改版本；
   - 保留许可证、源码地址、版本、镜像摘要和第三方组件记录。
4. 用户数量不是本决策的限制条件；部署和分发方式发生变化时才触发重新评审。
5. 以下任一情况发生前必须重新评审许可证并决定是否购买商业许可：修改 `pg_search`、客户私有化/on-prem 交付、分发镜像或安装包、直接开放数据库能力、将其源码链接或合并进闭源程序。
6. 生产集成仍须完成目标 CPU/容器、安全基线、Alembic、HA/复制、升级和 RRF 验证；这些是后续实现门禁，不阻塞 M0 技术验证结论。
7. 镜像和扩展版本必须锁定，不跟随浮动 `latest`。
8. PostgreSQL 原生 FTS 可以作为本地诊断或显式降级适配器，但响应、Trace 和后台必须标记 `lexical_engine=postgres_fts`，不得标记为 BM25，也不得通过 BM25 验收。

## 接口边界

业务层依赖抽象：

```text
LexicalRetriever.search(
  kb_id,
  index_id,
  query,
  top_k
) -> ranked chunk candidates
```

返回必须包含：

```text
engine
engine_version
rank
raw_score
chunk_id
```

Hybrid Fusion 只使用排名执行 RRF，不把 BM25 原始分数与向量相似度直接相加。

## 后果

### 正面

- 满足严格 BM25 产品要求。
- 文本、向量和业务元数据仍在 PostgreSQL 中，避免外部搜索集群同步。
- RRF 不依赖跨引擎分数量纲。

### 代价与风险

- PostgreSQL 运行镜像不再是只有 `pgvector` 的标准镜像。
- 增加第三方扩展的许可证、升级、备份和运维风险。
- 中文搜索质量依赖 tokenizer 配置，需要真实语料测试。
- 扩展版本可能影响 SQL 与索引迁移，必须版本固定。

## 接受记录

本决策以以下证据和约束为基础：

- `docs/deployment/bm25-compatibility.md`；
- 固定版本的 PostgreSQL/扩展镜像定义；
- 建库、建索引、中文查询和备份恢复的自动化冒烟测试；
- 本 ADR 中记录的“未修改、仅服务端托管、不分发”风险接受结论；
- 根目录 `THIRD_PARTY_NOTICES.md` 中的第三方组件记录。

## 参考

- ParadeDB `pg_search` 项目说明：<https://github.com/paradedb/paradedb>
- PostgreSQL 全文检索文档：<https://www.postgresql.org/docs/current/textsearch.html>

链接用于记录决策依据；真正实施时必须再次核对锁定版本的文档和许可证。
