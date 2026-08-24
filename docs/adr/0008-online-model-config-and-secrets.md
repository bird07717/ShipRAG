# ADR-0008：在线模型配置与密钥管理

- 状态：接受
- 日期：2026-08-21
- 关联：ADR-0005（密钥加密存储）、ADR-0007（冻结 Embedding 维度）

## 背景

V1.0.0 之后 `PATCH /api/v1/models/{id}` 仅开放 LLM/Rerank，API Key 完全依赖环境变量注入。管理台需要在不重启服务、不修改部署配置的情况下调整模型与凭据。

难点是安全边界：五类模型（LLM/EMBEDDING/RERANK/OCR/VISION）的消费时机不同，密钥又属于敏感凭据。无差别开放在线编辑会把“每轮对话读取的配置”与“索引构建绑定的配置”混为一谈，尤其 Embedding 与 `vector(N)` 列（ADR-0007）物理绑定，在线换模型可能产生无法写入或检索的索引。

## 决策

1. **按消费时机定义生效语义**：
   - LLM/RERANK：`begin_turn` 每轮快照，写入下一轮对话生效；
   - EMBEDDING/OCR/VISION：构建启动时一次性快照（`app/ingestion/model_config.py`），
     写入下一次索引构建生效；构建期间的写入不进入本次构建；
   - build-bound 类型变更后所有知识库标记 `rebuild_required=true`。
2. **密钥优先级链**：数据库密钥（ADR-0005 AES-GCM 加密，`MODEL_SECRET_KEY` 信封）
   > 环境变量。API 只写不读（`PUT/DELETE /api/v1/models/{id}/api-key`），
   响应仅含 `api_key_configured` 与 `api_key_source`。清除数据库密钥即回退环境变量。
3. **维度契约门禁**：EMBEDDING 行的 `model_name`/`base_url` 变更必须先通过在线探测
   （用候选值 + 生效密钥发起最小 Embedding 请求），实测输出维度 == 冻结维度 N
   才允许提交；维度不符返回 `422 EMBEDDING_DIMENSION_MISMATCH`，
   探测不可达返回 `409 MODEL_PROBE_FAILED`。
4. **构建期兜底不变式**：索引构建对每批返回向量校验长度 == N，
   不符则以 `EMBEDDING_DIMENSION_MISMATCH` 终止构建，防止绕过门禁的直改库写入。
5. **审计**：所有配置与密钥操作写入 `model_config_history`（action、变更字段名、
   actor=request id），不记录任何值。
6. **密钥轮换**：`MODEL_SECRET_KEY` 支持在线轮换（`MODEL_SECRET_KEY_PREVIOUS` +
   `app/tasks/key_rotation.py`），密钥只经环境变量传递，不经 Redis 序列化。

## 后果

### 正面

- 模型调整与密钥轮换不再需要重启或重新部署；生效语义与消费时机对齐，行为可预测。
- 直改库绕过门禁时，构建期不变式仍会拦截，`vector(N)` 不会收到脏数据。
- 密钥全生命周期（写入/轮换/清除）可审计且 API 不可读。

### 代价

- EMBEDDING 换模型必须产生一次真实上游调用（探测费用与上游可用性依赖）。
- `MODEL_SECRET_KEY` 成为需要与数据库备份同等保护的部署机密；
  丢失后数据库密钥不可恢复（可清除后回退环境变量）。
- build-bound 模型变更依赖管理员主动重建索引才真正生效（`rebuild_required` 仅提示）。
