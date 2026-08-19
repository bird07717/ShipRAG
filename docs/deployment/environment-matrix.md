# M1 环境与版本矩阵

记录日期：2026-08-17。

## 实机环境

|工具|实机版本|结果|
|---|---|---|
|Python|3.12.3|后端安装、类型检查和测试通过|
|Node.js|20.20.2|满足 Vite 下限，前端检查和构建通过|
|npm|10.8.2|lockfile v3 安装通过|
|Git|2.43.0|仓库初始化为 `main`|
|Docker Engine|29.5.3|容器联调通过|
|Docker Compose|v5.1.4|Compose解析和服务启动通过|

## 固定容器版本

|组件|固定镜像|实机结果|
|---|---|---|
|PostgreSQL + pgvector + pg_search|`paradedb/paradedb:0.25.0-pg17`（Compose 锁定 Digest）|PostgreSQL 17.10、vector 0.8.6、pg_search 0.25.0|
|Redis|`redis:8.8.1-trixie`|Redis 8.8.1|
|MinIO Server|`minio/minio:RELEASE.2025-09-07T16-13-09Z`|健康检查通过|
|MinIO Client|`minio/mc:RELEASE.2025-08-13T08-35-41Z`|Bucket初始化成功|

M5 默认数据库固定为 `paradedb/paradedb:0.25.0-pg17@sha256:6a334b612cadfeb92c416ecf3816dd9a277c10976e2e931e2c33f7289867c7c9`，实测 PostgreSQL 17.10、`pg_search` 0.25.0 和 `vector` 0.8.6。M7 空卷部署发现 ParadeDB 初始化还需要预加载 `pg_cron`，当前已固定 `shared_preload_libraries=pg_search,pg_cron` 并通过重新验收。

镜像升级必须单独评审，并重新执行健康、迁移和数据兼容测试。

## 应用依赖

- Python直接依赖固定在`backend/pyproject.toml`，完整解析树固定在`backend/requirements.lock`。
- npm直接依赖固定在`frontend/package.json`，完整依赖树固定在`frontend/package-lock.json`。
- Bootstrap固定使用pip 25.2，因为pip-tools 7.5.0与pip 26的内部API不兼容。
- npm生产依赖和完整开发依赖使用官方 registry 审计，结果均为 0 个已知漏洞；Vite 7.3.6、Vitest/coverage 3.2.7 已纳入锁文件。

## V1.0.0 应用镜像

|镜像|基础镜像|实机结果|
|---|---|---|
|Backend / Worker|`python:3.12.11-slim-bookworm`|生产依赖锁安装、非 root 用户、只读运行文件系统验证通过|
|Frontend|`node:20.20.0-bookworm-slim` 构建 + `nginx:1.29.1-alpine` 运行|构建、静态页面、健康代理和 SSE 代理配置验证通过|

Backend 生产镜像只安装 `requirements.prod.lock`；开发、类型检查和测试依赖只存在于 `requirements.lock` 和开发环境。

当前构建提示 Element Plus 主 Bundle 超过 500 kB。按项目决策，该提示不阻塞 V1.0.0 发布。
