<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { useRagAdminStore } from "@/composables/ragAdminStore";
import type { ExecutionPolicy } from "@/types/admin";

const rag = useRagAdminStore();
const activePolicy = ref<ExecutionPolicy>("RAG_ONLY");
const policies: Array<{ value: ExecutionPolicy; label: string; description: string }> = [
  { value: "SUPPORT_WITH_RAG_FALLBACK", label: "Support + RAG 兜底", description: "控制 Support 文档级检索及其单轮 Generic RAG 兜底。" },
  { value: "RAG_ONLY", label: "RAG only", description: "直接进入 Generic RAG 的检索配置。" },
];

const fields = [
  { key: "vector_top_k", label: "向量召回 Top K" },
  { key: "bm25_top_k", label: "BM25 召回 Top K" },
  { key: "fusion_top_k", label: "RRF 融合 Top K" },
  { key: "rerank_top_n", label: "Rerank Top N" },
  { key: "context_max_chunks", label: "上下文最大片段数" },
] as const;
type FieldKey = (typeof fields)[number]["key"];

const form = reactive<Record<FieldKey, number>>({
  vector_top_k: 10,
  bm25_top_k: 10,
  fusion_top_k: 20,
  rerank_top_n: 10,
  context_max_chunks: 8,
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
      title="配置按 Engine Routing 策略生效。知识库选择哪种策略，就在每次新请求中使用该策略的检索参数；会话仍固定在创建时的索引版本。"
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
