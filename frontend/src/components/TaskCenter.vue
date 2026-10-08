<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import StatusBadge from "@/components/ui/StatusBadge.vue";
import { useAdminStore } from "@/composables/adminStore";
import {
  ACTIVE_LIST_LIMIT,
  isLongQueued,
  loadTaskCenter,
  resetTaskCenter,
  taskCenter,
  taskTypeLabel,
} from "@/composables/taskCenterStore";
import type { TaskRecord } from "@/types/admin";

/*
 * Global background-task drawer across all knowledge bases. Mounted once at
 * the app root and owns the only poll (30 s, 5 s while open), which runs only
 * with a credential and while the page is visible; TaskCenterButton instances
 * just open it.
 */
const store = useAdminStore();
const open = computed({
  get: () => taskCenter.drawerOpen,
  set: (value: boolean) => (taskCenter.drawerOpen = value),
});
const showStale = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
let active = true;

const IDLE_POLL_MS = 30_000;
const OPEN_POLL_MS = 5_000;

const knowledgeNames = computed(() => new Map(store.knowledgeBases.map((kb) => [kb.id, kb.name])));

function knowledgeName(task: TaskRecord): string | null {
  return task.kb_id ? knowledgeNames.value.get(task.kb_id) ?? null : null;
}

function visible(): boolean {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

let refreshCall = 0;

/*
 * Triggers overlap (poll tick, drawer opening, a new tracked task, tab
 * becoming visible, credential change). Only the most recent call may
 * schedule the next tick, so overlapping calls never leave a second poll
 * chain behind; loadTaskCenter itself drops superseded responses.
 */
async function refresh(): Promise<void> {
  const call = ++refreshCall;
  clearTimeout(timer);
  timer = undefined;
  if (store.hasServiceCredential && visible()) await loadTaskCenter({ includeRecent: open.value });
  if (!active || call !== refreshCall) return;
  timer = setTimeout(() => void refresh(), open.value ? OPEN_POLL_MS : IDLE_POLL_MS);
}

function destination(task: TaskRecord): { view: string; label: string } | null {
  if (!knowledgeName(task)) return null;
  switch (task.task_type) {
    case "INDEX_BUILD":
      return { view: "indexes", label: "查看索引" };
    case "RAG_DOCUMENT_CARD_GENERATE":
    case "DOCUMENT_PROFILE_GENERATE":
      return { view: "documents", label: "查看文档" };
    case "KNOWLEDGE_BASE_DELETE":
      return { view: "knowledge", label: "查看知识库" };
    default:
      return task.task_type?.startsWith("PROFILE_") ? { view: "support-review", label: "查看审核" } : null;
  }
}

async function go(task: TaskRecord): Promise<void> {
  const target = destination(task);
  if (!target || !task.kb_id) return;
  open.value = false;
  if (store.selectedKnowledgeId !== task.kb_id) {
    store.selectedKnowledgeId = task.kb_id;
    await store.selectKnowledge();
  }
  store.navigate(target.view);
}

function formatTime(value: string | null): string {
  return value ? store.formatDate(value) : "—";
}

function idleLabel(task: TaskRecord): string {
  const minutes = Math.floor(((taskCenter.loadedAt ?? Date.now()) - Date.parse(task.updated_at)) / 60_000);
  if (minutes >= 60 * 24) return `${Math.floor(minutes / (60 * 24))} 天`;
  if (minutes >= 60) return `${Math.floor(minutes / 60)} 小时`;
  return `${Math.max(minutes, 0)} 分钟`;
}

watch(
  () => store.hasServiceCredential,
  (available) => {
    if (!available) {
      resetTaskCenter();
      open.value = false;
    }
    void refresh();
  },
);
// A task submitted from this browser shows up without waiting for the poll,
// and opening the drawer switches to the faster cadence with recent tasks.
watch(() => store.trackedTasks.length, () => void refresh());
watch(open, (value) => {
  if (value) void refresh();
});

function onVisibilityChange(): void {
  if (visible()) void refresh();
}

onMounted(() => {
  document.addEventListener("visibilitychange", onVisibilityChange);
  void refresh();
});
onBeforeUnmount(() => {
  active = false;
  clearTimeout(timer);
  document.removeEventListener("visibilitychange", onVisibilityChange);
});
</script>

<template>
  <el-drawer
    v-model="open"
    title="后台任务"
    size="520px"
    class="task-center-drawer"
  >
    <div class="task-center">
      <div class="task-center-toolbar">
        <span class="muted">
          全部知识库 · {{ taskCenter.loadedAt ? `更新于 ${new Date(taskCenter.loadedAt).toLocaleTimeString("zh-CN", { hour12: false })}` : "尚未加载" }}
        </span>
        <el-button
          size="small"
          :icon="Refresh"
          :loading="taskCenter.loading"
          @click="refresh"
        >
          刷新
        </el-button>
      </div>
      <el-alert
        v-if="taskCenter.error"
        :title="taskCenter.error"
        type="warning"
        :closable="false"
        show-icon
      />
      <p v-if="taskCenter.truncated" class="muted task-note">
        进行中的任务超过 {{ ACTIVE_LIST_LIMIT }} 条，以下只显示最近的部分。
      </p>

      <section>
        <h4>进行中（{{ taskCenter.active.length }}）</h4>
        <p v-if="!taskCenter.active.length" class="muted task-note">当前没有正在排队或运行的任务。</p>
        <article v-for="task in taskCenter.active" :key="task.id" class="task-row">
          <div class="task-head">
            <b>{{ taskTypeLabel(task.task_type) }}</b>
            <StatusBadge :status="task.status" />
          </div>
          <div class="task-meta">
            <span>{{ knowledgeName(task) ?? "知识库已删除或不在当前列表" }}</span>
            <span>阶段 {{ task.stage }}</span>
            <span>创建 {{ formatTime(task.created_at) }}</span>
            <span v-if="isLongQueued(task, taskCenter.loadedAt ?? undefined)" class="long-queue">已排队 {{ idleLabel(task) }}，可能在等待其他任务完成</span>
          </div>
          <el-progress :percentage="task.progress" :stroke-width="6" />
          <el-button
            v-if="destination(task)"
            link
            type="primary"
            @click="go(task)"
          >
            {{ destination(task)!.label }} →
          </el-button>
        </article>
      </section>

      <section v-if="taskCenter.stale.length" class="stale-section">
        <h4>
          疑似停滞（{{ taskCenter.stale.length }}）
          <el-button link @click="showStale = !showStale">{{ showStale ? "收起" : "展开" }}</el-button>
        </h4>
        <p class="muted task-note">
          运行中但超过 30 分钟没有任何进度更新。这是管理台的提示，不是后端判定：可能是 Worker 未运行、任务已中断或所属知识库已删除，也可能只是单个步骤耗时很长。这些任务不计入“进行中”；排队中的任务始终计入。
        </p>
        <template v-if="showStale">
          <article v-for="task in taskCenter.stale" :key="task.id" class="task-row is-stale">
            <div class="task-head">
              <b>{{ taskTypeLabel(task.task_type) }}</b>
              <StatusBadge :status="task.status" />
            </div>
            <div class="task-meta">
              <span>{{ knowledgeName(task) ?? "知识库已删除或不在当前列表" }}</span>
              <span>阶段 {{ task.stage }} · {{ task.progress }}%</span>
              <span>已 {{ idleLabel(task) }} 无更新</span>
            </div>
          </article>
        </template>
      </section>

      <section>
        <h4>最近 24 小时结束（{{ taskCenter.recentRows.length }}）</h4>
        <p v-if="!taskCenter.recentRows.length" class="muted task-note">没有最近结束的任务。</p>
        <article v-for="task in taskCenter.recentRows" :key="task.id" class="task-row">
          <div class="task-head">
            <b>{{ taskTypeLabel(task.task_type) }}</b>
            <StatusBadge :status="task.status" />
          </div>
          <div class="task-meta">
            <span>{{ knowledgeName(task) ?? "知识库已删除或不在当前列表" }}</span>
            <span>结束 {{ formatTime(task.finished_at ?? task.updated_at) }}</span>
          </div>
          <small v-if="task.error_message" class="task-error">{{ task.error_message }}</small>
        </article>
      </section>
    </div>
  </el-drawer>
</template>

<style scoped>
.task-center {
  display: grid;
  gap: 18px;
}

.task-center-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.task-center h4 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.task-note {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.6;
}

.task-row {
  display: grid;
  gap: 6px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border-subtle);
}

.task-row:last-child {
  border-bottom: 0;
}

.task-row.is-stale {
  opacity: 0.75;
}

.task-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.task-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.long-queue {
  color: var(--warning);
}

.task-error {
  color: var(--error);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.task-row .el-button {
  justify-self: start;
}
</style>
