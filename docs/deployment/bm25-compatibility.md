# BM25 扩展兼容性门禁

对应 ADR：[ADR-0001](../adr/0001-bm25-in-postgresql.md)。

状态：**技术 POC 通过；许可证风险按限定部署方式接受**。

## 已验证基线

|项目|实测值|结果|
|---|---|---|
|镜像|`paradedb/paradedb:0.25.0-pg17`|PASS|
|镜像摘要|`sha256:6a334b612cadfeb92c416ecf3816dd9a277c10976e2e931e2c33f7289867c7c9`|PASS，Compose 已锁定|
|运行平台|Linux `amd64`|PASS，仅代表当前开发机|
|PostgreSQL|17.10|PASS|
|`pg_search`|0.25.0|PASS|
|`vector`|0.8.4|PASS|
|中文 tokenizer|`pdb.lindera(chinese)`|PASS|
|检索样例|中文、端口数字、英文型号、文件路径|PASS|
|严格 BM25 排序|数据库端口文档为 Top 1|PASS|
|备份恢复|自定义格式 `pg_dump` 恢复到新数据库并重查|PASS|

验证环境使用 tmpfs 和独立 Compose project，退出后删除临时数据库，不接触 M1 的 PostgreSQL 数据卷。可重复执行：

```bash
./scripts/check-m0-bm25.sh
```

## 风险接受与后续实现验证

- [x] 部署方式明确为未修改、仅服务端托管、不分发。
- [x] 项目负责人接受该边界内使用 `pg_search` 社区版的许可证风险。
- [x] 候选版本和镜像摘要固定。
- [x] PostgreSQL 17、当前 Linux amd64 容器环境兼容。
- [x] SQL 建索引、查询和删除临时环境成功。
- [x] 中文 tokenizer、数字、英文型号和路径检索样例通过。
- [x] 表、BM25 索引及数据的备份恢复通过。
- [x] 当前生产参考环境 Linux amd64、内部端口隔离、非 root Backend 和只读应用文件系统验证。
- [x] Alembic 创建、升级和删除 BM25 索引成功。
- [ ] 高可用、复制、故障转移和扩展升级路径通过。
- [x] RRF 查询同时使用 pgvector 和 BM25 结果。

跨节点高可用、复制和自动故障转移仍属于后续基础设施增强；V1.0.0 已完成单节点服务中断恢复与逻辑备份恢复。

## 后续生产集成动作

1. 将默认 PostgreSQL 镜像替换为经过测试且同时提供 pgvector/pg_search 的生产镜像。
2. 在 Alembic 中显式创建扩展和 BM25 索引。
3. 将 `REQUIRED_POSTGRES_EXTENSIONS` 增加 `pg_search`。
4. 增加 HA、升级、RRF、生产安全基线和真实语料回归测试。
5. 持续维护 `THIRD_PARTY_NOTICES.md` 和镜像摘要。

默认 Compose 已在 M5 切换为锁定摘要的 ParadeDB PostgreSQL 17 镜像，同时启用 `vector` 与 `pg_search`。PostgreSQL `ts_rank`/`ts_rank_cd` 不得命名或验收为 BM25。

## 许可证来源

ParadeDB 仓库声明社区版为 AGPL-3.0，并提供单独的商业许可：<https://github.com/paradedb/paradedb>。本项目按 ADR-0001 记录的限定部署方式接受风险；部署或分发边界变化时必须重新评审。
