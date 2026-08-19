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
  <el-card class="health-card" shadow="never">
    <template #header>
      <div class="card-header">
        <div>
          <span class="eyebrow">SYSTEM STATUS</span>
          <h2>运行环境</h2>
        </div>
        <el-button :loading="loading" @click="refresh">重新检查</el-button>
      </div>
    </template>

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
            <el-tag :type="check.status === 'ok' ? 'success' : 'danger'" effect="plain">
              {{ check.status === "ok" ? "正常" : "异常" }}
            </el-tag>
          </div>
          <p>{{ check.version ? `v${check.version}` : (check.detail ?? "连接正常") }}</p>
          <small>{{ check.latency_ms }} ms</small>
        </article>
      </div>
    </template>
    <el-skeleton v-else :rows="3" animated />
  </el-card>
</template>

<style scoped>
.health-card {
  border: 1px solid var(--border);
  border-radius: 18px;
}

.card-header,
.dependency-title,
.overall {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-header h2 {
  margin: 3px 0 0;
  font-size: 20px;
}

.eyebrow {
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
}

.overall {
  justify-content: flex-start;
  gap: 10px;
  margin-bottom: 20px;
}

.pulse {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #e05260;
  box-shadow: 0 0 0 5px rgb(224 82 96 / 12%);
}

.pulse.ready {
  background: #28a879;
  box-shadow: 0 0 0 5px rgb(40 168 121 / 12%);
}

.dependency-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.dependency {
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #fbfcfa;
}

.dependency p {
  min-height: 36px;
  margin: 16px 0 4px;
  color: var(--muted);
  font-size: 13px;
}

.dependency small {
  color: #8a928c;
}

@media (max-width: 760px) {
  .dependency-grid {
    grid-template-columns: 1fr;
  }
}
</style>
