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
      title="本页参数用于调整文档检索的召回范围，保存后从下一轮使用对应策略的检索请求生效。"
      type="info"
      :closable="false"
      show-icon
    >
      <p>无需重启服务或重建索引。向量与 BM25 参数控制每个查询召回的片段数，融合参数控制合并后的候选片段上限；不代表最终回答使用的文档数量。</p>
      <p>最终候选文档数量和全文上下文预算由后端运行配置控制，不在本页调整。寒暄和非文档对话不执行检索，不受这些参数影响。</p>
      <p>已有会话也会使用新召回参数，但仍沿用创建时的索引版本。切换索引后，需开启新会话验收新索引。</p>
    </el-alert>
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
