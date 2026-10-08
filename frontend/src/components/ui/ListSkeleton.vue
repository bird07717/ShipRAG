<script setup lang="ts">
withDefaults(
  defineProps<{
    columns?: number;
    rows?: number;
    /** Number of metric placeholders; match the real card count to avoid layout jumps. */
    count?: number;
    variant?: "table" | "metrics" | "chart";
  }>(),
  { columns: 5, rows: 5, count: 4, variant: "table" },
);
</script>

<template>
  <div
    class="list-skeleton"
    :class="`is-${variant}`"
    role="status"
    aria-label="正在加载内容"
  >
    <template v-if="variant === 'metrics'">
      <div v-for="index in count" :key="index" class="skeleton-metric">
        <span class="skeleton-line short" />
        <span class="skeleton-line value" />
        <span class="skeleton-line medium" />
      </div>
    </template>
    <div v-else-if="variant === 'chart'" class="skeleton-chart">
      <span
        v-for="index in 14"
        :key="index"
        class="skeleton-bar"
        :style="{ height: `${30 + ((index * 37) % 60)}%` }"
      />
    </div>
    <template v-else>
      <div class="skeleton-table-head" :style="{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }">
        <span v-for="index in columns" :key="index" class="skeleton-line short" />
      </div>
      <div
        v-for="row in rows"
        :key="row"
        class="skeleton-table-row"
        :style="{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }"
      >
        <span
          v-for="column in columns"
          :key="column"
          class="skeleton-line"
          :class="{ short: column > 2 }"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.list-skeleton {
  overflow: hidden;
  border-radius: var(--radius-md);
}

.skeleton-table-head,
.skeleton-table-row {
  display: grid;
  align-items: center;
  gap: 20px;
  padding: 14px 18px;
}

.skeleton-table-head {
  min-height: 44px;
  background: #f7f9f7;
}

.skeleton-table-row {
  min-height: 56px;
  border-top: 1px solid var(--border-subtle);
}

.skeleton-line {
  display: block;
  width: 82%;
  height: 12px;
  border-radius: 999px;
  background: linear-gradient(90deg, #edf1ee 20%, #f7f9f7 40%, #edf1ee 60%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s ease-in-out infinite;
}

.skeleton-line.short {
  width: 54%;
}

.list-skeleton.is-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-content-gap);
  overflow: visible;
}

.skeleton-metric {
  min-height: 112px;
  padding: 16px 18px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  box-shadow: var(--shadow-surface);
}

.skeleton-metric .value {
  width: 46%;
  height: 25px;
  margin: 14px 0 10px;
}

.skeleton-metric .medium {
  width: 68%;
}

.skeleton-chart {
  display: flex;
  align-items: flex-end;
  gap: 4%;
  height: 220px;
  padding: 12px 44px 30px;
  border-bottom: 1px solid var(--border-subtle);
}

.skeleton-bar {
  flex: 1;
  border-radius: 4px 4px 0 0;
  background: linear-gradient(90deg, #edf1ee 20%, #f7f9f7 40%, #edf1ee 60%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s ease-in-out infinite;
}

@keyframes skeleton-shimmer {
  0% { background-position: 100% 0; }
  100% { background-position: -100% 0; }
}

@media (max-width: 1200px) {
  .list-skeleton.is-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .list-skeleton.is-metrics {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-line,
  .skeleton-bar {
    animation: none;
  }
}
</style>
