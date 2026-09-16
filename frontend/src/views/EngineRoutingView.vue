<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { useAdminStore } from "@/composables/adminStore";
import { useEngineRoutingStore } from "@/composables/engineRoutingStore";
import type { ExecutionPolicy } from "@/types/admin";

const shared = useAdminStore();
const routing = useEngineRoutingStore();
const selectedPolicy = ref<ExecutionPolicy>("RAG_ONLY");

const policies: Array<{
  value: ExecutionPolicy;
  label: string;
  description: string;
}> = [
  {
    value: "RAG_ONLY",
    label: "仅 RAG V2（当前在线模式）",
    description: "新会话直接进入 RAG V2：查询理解、混合检索、文档级重排与双 LLM 生成。",
  },
  {
    value: "SUPPORT_WITH_RAG_FALLBACK",
    label: "Support 优先，RAG 自动降级（已冻结）",
    description: "遗留策略：按 ADR-0021 已冻结，实际运行均为 RAG_ONLY；仅为历史兼容保留，不建议新配置。",
  },
];

const selectedKnowledge = computed(() => shared.selectedKnowledge);
const supportReadiness = computed(() => routing.policy?.readiness.support);
const ragReadiness = computed(() => routing.policy?.readiness.rag);
const supportReady = computed(() => Boolean(supportReadiness.value?.ready));
const ragReady = computed(() => Boolean(ragReadiness.value?.ready));
const blockers = computed(() => {
  const result: string[] = [];
  const needsSupport = selectedPolicy.value !== "RAG_ONLY";
  const needsRag = true;
  if (needsSupport) result.push(...(supportReadiness.value?.blockers ?? []));
  if (needsRag) result.push(...(ragReadiness.value?.blockers ?? []));
  return [...new Set(result)];
});
const changed = computed(() => routing.policy?.execution_policy !== selectedPolicy.value);

async function load(): Promise<void> {
  await routing.load();
  if (routing.policy) selectedPolicy.value = routing.policy.execution_policy;
}

function selectKnowledge(): void {
  void shared.selectKnowledge();
}

function save(): void {
  if (blockers.value.length) return;
  void routing.savePolicy(selectedPolicy.value);
}

watch(() => shared.selectedKnowledgeId, () => void load());
onMounted(() => void load());
</script>

<template>
  <div class="engine-routing">
    <el-alert
      v-if="routing.error"
      :title="routing.error"
      type="error"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="routing.notice"
      :title="routing.notice"
      type="success"
      show-icon
    />

    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>问答引擎路由</h3>
          <span class="sub">统一控制网关（Gateway）对新会话暴露的问答能力</span>
        </div>
      </header>

      <div v-loading="routing.loading" class="panel-body routing-body">
        <el-alert
          title="Support 优先模式已冻结（ADR-0021）：实际运行均为 RAG_ONLY。"
          description="Support Profile 仍可维护用于历史兼容，但不再参与在线问答。新配置请选择「仅 RAG V2」。"
          type="warning"
          :closable="false"
          show-icon
        />
        <div class="knowledge-field">
          <label>知识库</label>
          <el-select
            v-model="shared.selectedKnowledgeId"
            placeholder="选择知识库"
            @change="selectKnowledge"
          >
            <el-option
              v-for="kb in shared.knowledgeBases"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            />
          </el-select>
        </div>

        <template v-if="shared.selectedKnowledgeId">
          <div class="readiness-grid">
            <article :class="['readiness-card', { ready: ragReady }]">
              <span>通用 RAG（Generic RAG · 当前在线）</span>
              <strong>{{ ragReady ? "READY" : "NOT READY" }}</strong>
              <small v-if="ragReadiness?.blockers.length">{{ ragReadiness.blockers.join("；") }}</small>
              <small>RAG Active Index：{{ selectedKnowledge?.rag_active_index_id || selectedKnowledge?.active_index_id || "无" }}</small>
            </article>
            <article class="readiness-card support-frozen">
              <span>
                Profile 支持（Profile Support）
                <el-tag size="small" type="info" effect="plain">已冻结</el-tag>
              </span>
              <strong>{{ supportReady ? "READY" : "NOT READY" }}</strong>
              <small>{{ supportReadiness?.ready_profile_count ?? 0 }} / {{ supportReadiness?.profile_count ?? 0 }} 个 Profile 已发布就绪（仅历史兼容）</small>
              <small v-if="supportReadiness?.blockers.length">{{ supportReadiness.blockers.join("；") }}</small>
              <small>Support Release：{{ selectedKnowledge?.support_active_release_id || "未发布" }}</small>
              <small>Support Index：{{ selectedKnowledge?.support_active_index_id || "无" }}</small>
            </article>
          </div>

          <el-alert
            title="策略修改只影响新会话；会话保存策略快照，但混合模式（Hybrid）会在每轮重新决定是否需要 RAG 兜底。"
            type="info"
            :closable="false"
            show-icon
          />
          <el-alert
            v-if="routing.policy?.source === 'DEFAULT'"
            title="当前显示的是系统默认策略，尚未为此知识库保存独立路由配置。"
            description="选择策略后点击“保存路由策略”，刷新页面仍会使用保存后的配置。"
            type="warning"
            :closable="false"
            show-icon
          />

          <el-radio-group v-model="selectedPolicy" class="policy-list">
            <el-radio
              v-for="policy in policies"
              :key="policy.value"
              :value="policy.value"
              border
              class="policy-option"
            >
              <span class="policy-copy">
                <strong>{{ policy.label }}</strong>
                <small>{{ policy.description }}</small>
              </span>
            </el-radio>
          </el-radio-group>

          <el-alert
            v-if="blockers.length"
            :title="`当前策略不可发布：${blockers.join('；')}`"
            type="warning"
            :closable="false"
            show-icon
          />

          <div class="policy-footer">
            <div class="policy-meta">
              <span>当前策略：{{ routing.policy?.execution_policy || "—" }}</span>
              <span>配置版本：{{ routing.policy?.config_version ?? 0 }}</span>
              <span>来源：{{ routing.policy?.source || "DEFAULT" }}</span>
              <span>更新时间：{{ shared.formatDate(routing.policy?.updated_at) }}</span>
              <span>自动降级白名单：{{ routing.policy?.fallback_conditions?.join(", ") || "无" }}</span>
            </div>
            <el-button
              type="primary"
              :loading="routing.loading"
              :disabled="!changed || blockers.length > 0"
              @click="save"
            >
              保存路由策略
            </el-button>
          </div>
        </template>
        <el-empty v-else description="请先选择知识库" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.routing-body { display: grid; gap: 20px; }
.knowledge-field { display: grid; grid-template-columns: 120px minmax(260px, 440px); align-items: center; gap: 14px; }
.knowledge-field label { color: var(--text-secondary); font-size: 13px; font-weight: 600; }
.readiness-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.readiness-card { display: grid; gap: 5px; padding: 16px; border: 1px solid var(--border-subtle); border-radius: 10px; background: var(--bg-subtle); }
.readiness-card.support-frozen { opacity: 0.75; }
.readiness-card span { display: flex; align-items: center; gap: 6px; }
.readiness-card span, .readiness-card small { color: var(--text-tertiary); }
.readiness-card strong { color: var(--danger); font-size: 14px; }
.readiness-card.ready strong { color: var(--success); }
.policy-list { display: grid; gap: 10px; }
.policy-option { width: 100%; height: auto; margin: 0; padding-block: 14px; }
.policy-option :deep(.el-radio__label) { width: 100%; white-space: normal; }
.policy-copy { display: grid; gap: 4px; }
.policy-copy strong { color: var(--text-primary); }
.policy-copy small { color: var(--text-tertiary); line-height: 1.5; }
.policy-footer { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; }
.policy-meta { display: flex; flex-wrap: wrap; gap: 8px 18px; color: var(--text-tertiary); font-size: 12px; }
@media (max-width: 760px) {
  .knowledge-field, .readiness-grid { grid-template-columns: 1fr; }
  .policy-footer { align-items: stretch; flex-direction: column; }
}
</style>
