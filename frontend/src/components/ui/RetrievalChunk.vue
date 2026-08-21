<script setup lang="ts">
defineProps<{
  score?: number | string;
  document: string;
  section?: string;
  content: string;
}>();

function formatScore(value: number | string | undefined): string {
  if (value === undefined) return "—";
  return typeof value === "number" ? value.toFixed(2) : String(value);
}
</script>

<template>
  <article class="retrieval-chunk">
    <header>
      <div class="chunk-meta">
        <b class="chunk-doc">{{ document }}</b>
        <small v-if="section" class="chunk-section">{{ section }}</small>
      </div>
      <span v-if="score !== undefined" class="score-badge">Score {{ formatScore(score) }}</span>
    </header>
    <p class="chunk-content">{{ content }}</p>
  </article>
</template>

<style scoped>
.retrieval-chunk {
  display: grid;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-surface);
}

.retrieval-chunk header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.chunk-meta {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.chunk-doc {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-section {
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.score-badge {
  flex-shrink: 0;
  padding: 0 7px;
  border: 1px solid var(--border-default);
  border-radius: 5px;
  color: var(--text-secondary);
  background: var(--bg-subtle);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 20px;
}

.chunk-content {
  display: -webkit-box;
  overflow: hidden;
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
}
</style>
