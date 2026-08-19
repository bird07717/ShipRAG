# Third-Party Software Notices

本文件记录项目直接采用、且需要保留许可证与来源信息的第三方运行组件。它不替代各组件随附的完整许可证文本。

## ParadeDB `pg_search`

- 组件：ParadeDB Community / `pg_search`
- 版本：`0.25.0`
- 容器：`paradedb/paradedb:0.25.0-pg17`
- 镜像摘要：`sha256:6a334b612cadfeb92c416ecf3816dd9a277c10976e2e931e2c33f7289867c7c9`
- 许可证：GNU Affero General Public License v3.0（AGPL-3.0）
- 源码：<https://github.com/paradedb/paradedb>
- 许可证原文：<https://www.gnu.org/licenses/agpl-3.0.html>
- 项目是否修改该组件：否
- 部署方式：仅服务端托管，由后端通过 SQL 调用
- 是否向客户分发：否

风险接受边界和重新评审条件见 [ADR-0001](docs/adr/0001-bm25-in-postgresql.md)。若修改组件、客户私有化部署、分发镜像或安装包、直接开放数据库能力，必须在实施前更新本记录并重新评审许可方案。
