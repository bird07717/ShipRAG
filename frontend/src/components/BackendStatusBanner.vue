<script setup lang="ts">
import { CircleCheckFilled, WarningFilled } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { backendConnectivity, backendTarget, probeBackend } from "@/api/connectivity";

/*
 * Global "backend unreachable" banner. It owns the only retry loop: while the
 * request layer reports the backend offline it probes /health/live with
 * backoff, and stops as soon as any request (probe or page) gets through.
 */
const emit = defineEmits<{ reconnected: [] }>();

const RETRY_DELAYS_MS = [3_000, 5_000, 10_000, 20_000, 30_000];
const checking = ref(false);
const recovered = ref(false);
const nextRetryAt = ref<number | null>(null);
const now = ref(Date.now());
let attempt = 0;
let retryTimer: ReturnType<typeof setTimeout> | undefined;
let clockTimer: ReturnType<typeof setInterval> | undefined;
let recoveredTimer: ReturnType<typeof setTimeout> | undefined;
let controller: AbortController | undefined;
let active = true;

const offline = computed(() => backendConnectivity.status === "offline");
const secondsToRetry = computed(() =>
  nextRetryAt.value === null ? 0 : Math.max(Math.ceil((nextRetryAt.value - now.value) / 1000), 0),
);
const offlineSinceLabel = computed(() =>
  backendConnectivity.offlineSince
    ? new Date(backendConnectivity.offlineSince).toLocaleTimeString("zh-CN", { hour12: false })
    : "",
);

function stopRetrying(): void {
  clearTimeout(retryTimer);
  clearInterval(clockTimer);
  retryTimer = undefined;
  clockTimer = undefined;
  nextRetryAt.value = null;
  controller?.abort();
  controller = undefined;
  checking.value = false;
}

function scheduleRetry(): void {
  clearTimeout(retryTimer);
  const delay = RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)]!;
  nextRetryAt.value = Date.now() + delay;
  now.value = Date.now();
  if (!clockTimer) clockTimer = setInterval(() => (now.value = Date.now()), 1_000);
  retryTimer = setTimeout(() => void retryNow(), delay);
}

async function retryNow(): Promise<void> {
  if (!active || checking.value) return;
  clearTimeout(retryTimer);
  checking.value = true;
  controller = new AbortController();
  const reachable = await probeBackend(controller.signal);
  if (!active) return;
  checking.value = false;
  // A successful probe flips the shared status to online; the watcher below
  // then stops the loop. Only keep retrying while still offline.
  if (!reachable && backendConnectivity.status === "offline") {
    attempt += 1;
    scheduleRetry();
  }
}

watch(
  () => backendConnectivity.status,
  (status, previous) => {
    if (status === "offline") {
      if (previous !== "offline") {
        attempt = 0;
        recovered.value = false;
        clearTimeout(recoveredTimer);
        scheduleRetry();
      }
      return;
    }
    stopRetrying();
    if (previous === "offline" && status === "online") {
      recovered.value = true;
      emit("reconnected");
      clearTimeout(recoveredTimer);
      recoveredTimer = setTimeout(() => (recovered.value = false), 4_000);
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  active = false;
  stopRetrying();
  clearTimeout(recoveredTimer);
});
</script>

<template>
  <div v-if="offline" class="system-banner tone-danger">
    <el-icon class="banner-icon"><WarningFilled /></el-icon>
    <!-- Only this static text is the live region; the per-second countdown below
         must not be re-announced every tick. -->
    <div class="banner-text" role="alert">
      <strong>无法连接后端服务</strong>
      <span>请求未能到达后端（{{ backendTarget }}），自 {{ offlineSinceLabel }} 起。页面上的数据可能已过期，提交的操作不会生效。</span>
    </div>
    <span class="banner-meta">{{ checking ? "正在重试…" : `${secondsToRetry} 秒后自动重试` }}</span>
    <el-button size="small" :loading="checking" @click="retryNow">立即重试</el-button>
  </div>
  <div v-else-if="recovered" class="system-banner tone-success" role="status">
    <el-icon class="banner-icon"><CircleCheckFilled /></el-icon>
    <div class="banner-text"><strong>已重新连接后端服务</strong><span>正在刷新页面数据。</span></div>
  </div>
</template>
