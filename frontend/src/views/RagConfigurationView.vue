<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { useRagAdminStore } from "@/composables/ragAdminStore";
import type { ExecutionPolicy } from "@/types/admin";

const rag = useRagAdminStore();
const activePolicy = ref<ExecutionPolicy>("RAG_ONLY");
const policies: Array<{ value: ExecutionPolicy; label: string; description: string }> = [
  { value: "RAG_ONLY", label: "RAG only", description: "RAG V2 文档级检索：每查询一次批量 Embedding，走 Chunk/文档卡片混合召回与 RRF 融合，再按文档整体重排。" },
  { value: "SUPPORT_WITH_RAG_FALLBACK", label: "Support + RAG 兜底（已冻结）", description: "遗留策略配置：按 ADR-0021 已冻结，仅历史兼容保留。" },
];

const fields = [
  { key: "vector_top_k", label: "向量召回 Top K（每查询）" },
  { key: "bm25_top_k", label: "BM25 召回 Top K（每查询）" },
  { key: "fusion_top_k", label: "RRF 融合候选上限（Chunk）" },
] as const;
type FieldKey = (typeof fields)[number]["key"];

const form = reactive<Record<FieldKey, number>>({
  vector_top_k: 10,
  bm25_top_k: 10,
  fusion_top_k: 20,
});

const currentPolicy = computed(() => policies.find((item) => item.value === activePolicy.value)!);

function syncForm(): void {
  const config = rag.engineConfigs.find((item) => item.execution_policy === activePolicy.value);
  if (config) {
    for (const field of fields) form[field.key] = config[field.key];
  }
}

function selectPolicy(policy: ExecutionPolicy): void {
  activePolicy.value = policy;
  syncForm();
}

async function load(): Promise<void> {
  await rag.run(() => rag.loadEngineConfigurations());
  syncForm();
}

onMounted(() => void load());
</script>

<template>
  <div class="rag-configuration">
    <el-alert
      v-if="rag.error"
      :title="rag.error"
      type="error"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="rag.notice"
      :title="rag.notice"
      type="success"
      show-icon
    />
    <el-alert
      title="RAG V2 为文档级检索：Rerank 候选数量与隐藏全文上下文预算由环境变量控制，不在此调整；会话固定在创建时的索引版本。"
      type="warning"
      :closable="false"
      show-icon
    />
    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>{{ currentPolicy.label }} 检索配置</h3>
          <span class="sub">{{ currentPolicy.description }}</span>
        </div>
      </header>
      <div class="panel-body config-grid">
        <el-radio-group :model-value="activePolicy" @update:model-value="selectPolicy">
          <el-radio-button v-for="policy in policies" :key="policy.value" :label="policy.value">
            {{ policy.label }}
          </el-radio-button>
        </el-radio-group>
        <div v-for="field in fields" :key="field.key" class="param-row">
          <label>{{ field.label }}</label>
          <el-input-number v-model="form[field.key]" :min="1" :max="100" />
        </div>
        <div class="actions">
          <el-button type="primary" :loading="rag.loading" @click="rag.saveEngineConfig(activePolicy, { ...form })">
            保存当前策略配置
          </el-button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.config-grid { display: grid; gap: 16px; max-width: 680px; }
.param-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.param-row label { color: var(--text-secondary); font-size: 13px; font-weight: 600; }
.actions { display: flex; justify-content: flex-end; }
</style>
