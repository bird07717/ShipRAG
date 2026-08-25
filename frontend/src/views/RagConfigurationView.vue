<script setup lang="ts">
import { onMounted, reactive } from "vue";

import { useRagAdminStore } from "@/composables/ragAdminStore";

const rag = useRagAdminStore();

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
  if (rag.config) {
    for (const field of fields) form[field.key] = rag.config[field.key];
  }
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
      title="这是系统级全局 RAG 配置，影响所有进入 RAG 的知识库和下一轮 RAG 请求。总引擎策略请在 Engine Routing 页面管理。"
      type="warning"
      :closable="false"
      show-icon
    />
    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>Generic RAG 全局配置</h3>
          <span class="sub">独立管理召回、融合、重排和上下文规模</span>
        </div>
      </header>
      <div class="panel-body config-grid">
        <div v-for="field in fields" :key="field.key" class="param-row">
          <label>{{ field.label }}</label>
          <el-input-number v-model="form[field.key]" :min="1" :max="100" />
        </div>
        <div class="actions">
          <el-button type="primary" :loading="rag.loading" @click="rag.saveConfig({ ...form })">
            保存全局 RAG 检索配置
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
