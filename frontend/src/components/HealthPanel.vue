<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { fetchReadiness } from "@/api/health";
import type { ReadinessResponse } from "@/types/health";

const health = ref<ReadinessResponse | null>(null);
const loading = ref(false);
const error = ref("");
let controller: AbortController | null = null;

const labels: Record<keyof ReadinessResponse["checks"], string> = {
  postgres: "PostgreSQL",
  redis: "Redis / RQ",
  minio: "MinIO",
};

const overallLabel = computed(() => (health.value?.status === "ready" ? "服务就绪" : "依赖未就绪"));

async function refresh(): Promise<void> {
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    health.value = await fetchReadiness(controller.signal);
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") return;
    error.value = cause instanceof Error ? cause.message : "无法连接后端服务";
    health.value = null;
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);
onBeforeUnmount(() => controller?.abort());
</script>

<template>
  <section class="panel health-panel">
    <header class="panel-header">
      <div>
        <h3>System Status</h3>
        <span class="sub">运行环境依赖检查</span>
      </div>
      <el-button size="small" :loading="loading" @click="refresh">重新检查</el-button>
    </header>
    <div class="panel-body">
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
      />
      <template v-else-if="health">
        <div class="overall">
          <span :class="['pulse', health.status]" />
          <strong>{{ overallLabel }}</strong>
        </div>
        <div class="dependency-grid">
          <article v-for="(check, name) in health.checks" :key="name" class="dependency">
            <div class="dependency-title">
              <span>{{ labels[name] }}</span>
              <span class="status-badge" :class="check.status === 'ok' ? 'success' : 'danger'">
                {{ check.status === "ok" ? "正常" : "异常" }}
              </span>
            </div>
            <p>{{ check.version ? `v${check.version}` : (check.detail ?? "连接正常") }}</p>
            <small>{{ check.latency_ms }} ms</small>
          </article>
        </div>
      </template>
      <el-skeleton v-else :rows="3" animated />
    </div>
  </section>
</template>

<style scoped>
.overall {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.overall strong {
  color: var(--text-primary);
  font-size: 14px;
}

.pulse {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--error);
  box-shadow: 0 0 0 4px var(--error-bg);
}

.pulse.ready {
  background: var(--success);
  box-shadow: 0 0 0 4px var(--success-bg);
}

.dependency-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.dependency {
  display: grid;
  gap: 4px;
  padding: 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
}

.dependency-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.dependency-title > span:first-child {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
}

.dependency p {
  min-height: 34px;
  margin: 6px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.dependency small {
  color: var(--text-tertiary);
  font-size: 11px;
}

@media (max-width: 760px) {
  .dependency-grid {
    grid-template-columns: 1fr;
  }
}
</style>
