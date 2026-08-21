<script setup lang="ts">
import type { Component } from "vue";

defineProps<{
  label: string;
  value: string | number;
  context?: string;
  icon?: Component;
}>();
</script>

<template>
  <article class="metric-card">
    <div class="metric-head">
      <span class="label">{{ label }}</span>
      <span v-if="icon" class="metric-icon">
        <el-icon :size="15"><component :is="icon" /></el-icon>
      </span>
    </div>
    <strong class="value">{{ value }}</strong>
    <small v-if="context" class="context">{{ context }}</small>
  </article>
</template>

<style scoped>
.metric-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.label {
  color: var(--text-secondary);
  font-size: 13px;
}

.metric-icon {
  display: grid;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  place-items: center;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--brand-primary);
  background: var(--brand-subtle);
}

.value {
  display: block;
  margin: 8px 0 2px;
  color: var(--text-primary);
  font-size: 26px;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.context {
  display: block;
  color: var(--text-tertiary);
  font-size: 12px;
}
</style>
