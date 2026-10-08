<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { adminApi } from "@/api/admin";
import { useAdminStore } from "@/composables/adminStore";
import {
  ACTIVE_LIST_LIMIT,
  isLongQueued,
  loadTaskCenter,
  taskCenter,
  taskTypeLabel,
} from "@/composables/taskCenterStore";
import type { RuntimeMetrics } from "@/types/admin";

/*
 * Capacity at a glance. /metrics/runtime is a snapshot of the single API
 * process that served this request (production runs several uvicorn
 * workers), so it is labelled as such; the task backlog comes from the
 * database and is service-wide. Loaded on mount and on demand — no polling.
 */
const store = useAdminStore();
const runtime = ref<RuntimeMetrics | null>(null);
const runtimeError = ref("");
const loading = ref(false);
const loadedAt = ref<Date | null>(null);
let generation = 0;

/*
 * SQLAlchemy QueuePool.overflow() starts at -pool_size and rises with every
 * connection opened, so connections currently open = size + overflow and only
 * a positive overflow means connections beyond the steady pool.
 */
const pool = computed(() => {
  const raw = runtime.value?.database_pool;
  if (!raw) return null;
  return {
    open: Math.max(raw.size + raw.overflow, 0),
    inUse: raw.checked_out,
    idle: raw.checked_in,
    size: raw.size,
    beyondPool: Math.max(raw.overflow, 0),
  };
});
const gate = computed(() => runtime.value?.turn_gate ?? null);
const gateUsage = computed(() =>
  gate.value && gate.value.limit > 0 ? Math.round((gate.value.in_flight / gate.value.limit) * 100) : null,
);
const backlogByType = computed(() => {
  const counts = new Map<string, { queued: number; running: number }>();
  for (const task of taskCenter.active) {
    const key = taskTypeLabel(task.task_type);
    const entry = counts.get(key) ?? { queued: 0, running: 0 };
    if (task.status === "QUEUED") entry.queued += 1;
    else entry.running += 1;
    counts.set(key, entry);
  }
  return [...counts.entries()].map(([label, value]) => ({ label, ...value }));
});
const queuedCount = computed(() => taskCenter.active.filter((task) => task.status === "QUEUED").length);
const longQueuedCount = computed(() =>
  taskCenter.active.filter((task) => isLongQueued(task, taskCenter.loadedAt ?? undefined)).length,
);
const runningCount = computed(() => taskCenter.active.filter((task) => task.status === "RUNNING").length);

async function refresh(): Promise<void> {
  if (!store.hasServiceCredential) return;
  const current = ++generation;
  loading.value = true;
  const [metrics] = await Promise.allSettled([adminApi.getRuntimeMetrics(), loadTaskCenter()]);
  if (current !== generation) return;
  loading.value = false;
  if (metrics.status === "fulfilled" && metrics.value?.database_pool && metrics.value?.turn_gate) {
    runtime.value = metrics.value;
    runtimeError.value = "";
  } else {
    runtime.value = null;
    runtimeError.value = "运行快照暂时无法加载。";
  }
  loadedAt.value = new Date();
}

onMounted(() => void refresh());
onBeforeUnmount(() => {
  generation++;
});
</script>

<template>
  <section class="panel runtime-capacity">
    <header class="panel-header">
      <div>
        <h3>运行容量</h3>
        <span class="sub">{{ loadedAt ? `读取于 ${loadedAt.toLocaleTimeString("zh-CN", { hour12: false })}` : "尚未读取" }} · 不自动刷新</span>
      </div>
      <el-button
        size="small"
        :icon="Refresh"
        :loading="loading"
        @click="refresh"
      >
        刷新
      </el-button>
    </header>
    <div class="panel-body capacity-body">
      <div class="capacity-group">
        <h4>当前响应进程快照</h4>
        <p class="muted capacity-note">
          只代表处理本次请求的那一个 API 进程；生产默认运行 2 个进程（BACKEND_WORKERS），两次刷新可能来自不同进程，不能当作全服务合计。
        </p>
        <p v-if="runtimeError" class="capacity-error">{{ runtimeError }}</p>
        <div v-else-if="gate && pool" class="capacity-grid">
          <div>
            <span>问答并发</span>
            <b>{{ gate.limit > 0 ? `${gate.in_flight} / ${gate.limit}` : `${gate.in_flight}` }}</b>
            <small>{{ gate.limit > 0 ? `占用 ${gateUsage}%，满额后新请求快速返回“服务繁忙”` : "未启用并发闸门（CHAT_TURN_CONCURRENCY_LIMIT=0）" }}</small>
          </div>
          <div>
            <span>数据库连接（使用中 / 已建立）</span>
            <b>{{ pool.inUse }} / {{ pool.open }}</b>
            <small>常驻连接池 {{ pool.size }} · 空闲 {{ pool.idle }}<template v-if="pool.beyondPool"> · 超出常驻池 {{ pool.beyondPool }}</template></small>
          </div>
        </div>
      </div>
      <div class="capacity-group">
        <h4>后台任务积压（全服务）</h4>
        <div class="capacity-grid">
          <div><span>排队中</span><b>{{ queuedCount }}</b><small>{{ longQueuedCount ? `其中 ${longQueuedCount} 个已排队超过 10 分钟` : "等待 Worker 领取" }}</small></div>
          <div><span>运行中</span><b>{{ runningCount }}</b><small>近期有进度更新</small></div>
          <div :class="{ warn: taskCenter.stale.length > 0 }">
            <span>疑似停滞</span><b>{{ taskCenter.stale.length }}</b>
            <small>运行中超过 30 分钟无进度更新（提示，非后端判定）</small>
          </div>
        </div>
        <ul v-if="backlogByType.length" class="backlog-list">
          <li v-for="item in backlogByType" :key="item.label">
            <span>{{ item.label }}</span><span class="muted">排队 {{ item.queued }} · 运行 {{ item.running }}</span>
          </li>
        </ul>
        <p v-if="taskCenter.error" class="capacity-error">{{ taskCenter.error }}</p>
        <p v-if="taskCenter.truncated" class="muted capacity-note">某一状态超过 {{ ACTIVE_LIST_LIMIT }} 条，以上为下限。</p>
        <el-button
          v-if="taskCenter.stale.length"
          link
          type="primary"
          @click="taskCenter.drawerOpen = true"
        >
          在后台任务中查看 →
        </el-button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.capacity-body {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 24px;
}

.capacity-group h4 {
  margin-bottom: 4px;
  font-size: 13px;
  font-weight: 600;
}

.capacity-note {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.6;
}

.capacity-error {
  color: var(--error);
  font-size: 12px;
}

.capacity-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}

.capacity-grid > div {
  display: grid;
  gap: 2px;
  padding: 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
}

.capacity-grid > div.warn {
  background: var(--warning-bg);
}

.capacity-grid span,
.capacity-grid small {
  color: var(--text-secondary);
  font-size: 12px;
}

.capacity-grid b {
  color: var(--text-primary);
  font-size: 18px;
  font-variant-numeric: tabular-nums;
}

.backlog-list {
  display: grid;
  gap: 4px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  font-size: 12px;
}

.backlog-list li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

@media (max-width: 900px) {
  .capacity-body {
    grid-template-columns: 1fr;
  }
}
</style>
