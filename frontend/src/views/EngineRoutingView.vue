<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { useAdminStore } from "@/composables/adminStore";
import { useEngineRoutingStore } from "@/composables/engineRoutingStore";
import type { ExecutionPolicy } from "@/types/admin";

const shared = useAdminStore();
const routing = useEngineRoutingStore();
const selectedPolicy = ref<ExecutionPolicy>("SUPPORT_ONLY");

const policies: Array<{
  value: ExecutionPolicy;
  label: string;
  description: string;
}> = [
  {
    value: "SUPPORT_ONLY",
    label: "仅 Profile Support",
    description: "新会话只执行 Profile、完整文档交付和基于完整文档的持续问答。",
  },
  {
    value: "SUPPORT_WITH_RAG_FALLBACK",
    label: "Support 优先，RAG 自动降级",
    description: "先执行 Support；当前仅 NO_MATCH 可以把该会话降级并冻结到 RAG。",
  },
  {
    value: "RAG_ONLY",
    label: "仅 Generic RAG",
    description: "新会话跳过 Profile，直接执行查询改写、混合检索、重排和生成。",
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
  const needsRag = selectedPolicy.value !== "SUPPORT_ONLY";
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
          <span class="sub">统一控制 Gateway 对新会话暴露的问答能力</span>
        </div>
      </header>

      <div v-loading="routing.loading" class="panel-body routing-body">
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
            <article :class="['readiness-card', { ready: supportReady }]">
              <span>Profile Support</span>
              <strong>{{ supportReady ? "READY" : "NOT READY" }}</strong>
              <small>{{ supportReadiness?.ready_profile_count ?? 0 }} / {{ supportReadiness?.profile_count ?? 0 }} 个 Profile 已发布就绪</small>
              <small v-if="supportReadiness?.blockers.length">{{ supportReadiness.blockers.join("；") }}</small>
            </article>
            <article :class="['readiness-card', { ready: ragReady }]">
              <span>Generic RAG</span>
              <strong>{{ ragReady ? "READY" : "NOT READY" }}</strong>
              <small v-if="ragReadiness?.blockers.length">{{ ragReadiness.blockers.join("；") }}</small>
              <small>Active Index：{{ selectedKnowledge?.active_index_id || "无" }}</small>
            </article>
          </div>

          <el-alert
            title="策略修改只影响新会话；已有会话继续使用已冻结的执行引擎。"
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
