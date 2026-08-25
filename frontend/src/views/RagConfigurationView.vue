<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";

import { useAdminStore } from "@/composables/adminStore";
import { useRagAdminStore } from "@/composables/ragAdminStore";
import type { ExecutionPolicy } from "@/types/admin";

const shared = useAdminStore();
const rag = useRagAdminStore();
const selectedPolicy = ref<ExecutionPolicy>("SUPPORT_ONLY");

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

async function load(): Promise<void> {
  await rag.run(() => rag.loadConfiguration());
  if (rag.policy) selectedPolicy.value = rag.policy.execution_policy;
  if (rag.config) {
    for (const field of fields) form[field.key] = rag.config[field.key];
  }
}

watch(() => shared.selectedKnowledgeId, () => void load());
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
    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>Generic RAG 配置</h3>
          <span class="sub">独立管理 RAG 启用策略、召回、融合、重排和上下文规模</span>
        </div>
      </header>
      <div class="panel-body config-grid">
        <div class="field">
          <label>知识库运行策略</label>
          <el-select v-model="selectedPolicy">
            <el-option label="仅 Profile Support" value="SUPPORT_ONLY" />
            <el-option label="Support 优先，RAG 降级" value="SUPPORT_WITH_RAG_FALLBACK" />
            <el-option label="仅通用 RAG" value="RAG_ONLY" />
          </el-select>
          <el-button
            :disabled="!shared.selectedKnowledgeId"
            :loading="rag.loading"
            @click="rag.savePolicy(selectedPolicy)"
          >
            保存引擎策略
          </el-button>
        </div>
        <el-divider />
        <div v-for="field in fields" :key="field.key" class="param-row">
          <label>{{ field.label }}</label>
          <el-input-number v-model="form[field.key]" :min="1" :max="100" />
        </div>
        <div class="actions">
          <el-button type="primary" :loading="rag.loading" @click="rag.saveConfig({ ...form })">
            保存 RAG 检索配置
          </el-button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.config-grid { display: grid; gap: 16px; max-width: 680px; }
.field { display: grid; grid-template-columns: 180px minmax(240px, 1fr) auto; align-items: center; gap: 12px; }
.param-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.field label, .param-row label { color: var(--text-secondary); font-size: 13px; font-weight: 600; }
.actions { display: flex; justify-content: flex-end; }
@media (max-width: 760px) { .field { grid-template-columns: 1fr; } }
</style>

