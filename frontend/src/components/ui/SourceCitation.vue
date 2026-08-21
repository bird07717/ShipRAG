<script setup lang="ts">
defineProps<{
  index?: number | string;
  document: string;
  section?: string;
  page?: number | string | null;
}>();
</script>

<template>
  <div class="source-citation">
    <span v-if="index !== undefined" class="citation-index">{{ index }}</span>
    <div class="citation-body">
      <b class="citation-doc">{{ document }}</b>
      <small v-if="section || page !== undefined" class="citation-loc">
        <template v-if="section">{{ section }}</template>
        <template v-if="page !== undefined && page !== null"> · Page {{ page }}</template>
      </small>
    </div>
  </div>
</template>

<style scoped>
.source-citation {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-surface);
  transition: background-color var(--transition-fast);
}

.source-citation:hover {
  background: var(--bg-hover);
}

.citation-index {
  display: inline-grid;
  min-width: 22px;
  height: 22px;
  place-items: center;
  padding: 0 4px;
  border-radius: 5px;
  color: var(--brand-primary);
  background: var(--brand-subtle);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
}

.citation-body {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.citation-doc {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.citation-loc {
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
