import { reactive } from "vue";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import type { TaskRecord } from "@/types/admin";

/*
 * Cross-knowledge-base view of background tasks, read from the global
 * GET /tasks list (task_record), so it includes tasks started from other
 * browsers or by the worker itself — unlike adminStore.trackedTasks, which
 * only knows what this browser session submitted.
 */

// Console-side hints, NOT the backend's verdict. backend/app/tasks/recovery.py
// only reconciles maintenance task types, and only together with the RQ job
// state (index builds are judged by RQ job state alone), so the console cannot
// tell "abandoned" from "slow" by time. It therefore:
// - moves a RUNNING task out of "in progress" after 30 minutes without any
//   progress update (the same heartbeat window recovery uses), as "stalled?";
// - never removes a QUEUED task from the backlog: one waiting behind a long
//   build is healthy. It is only flagged as waiting long after 10 minutes.
export const STALE_RUNNING_MS = 30 * 60 * 1000;
export const LONG_QUEUE_MS = 10 * 60 * 1000;
export const ACTIVE_LIST_LIMIT = 200;
const RECENT_WINDOW_MS = 24 * 60 * 60 * 1000;

export const TASK_TYPE_LABELS: Record<string, string> = {
  INDEX_BUILD: "索引构建",
  RAG_DOCUMENT_CARD_GENERATE: "RAG 文档理解生成",
  KNOWLEDGE_BASE_DELETE: "知识库删除",
  DOCUMENT_PROFILE_GENERATE: "文档属性生成（Support · 遗留）",
  PROFILE_GENERATE: "Profile 生成（Support · 遗留）",
  PROFILE_REGENERATE: "Profile 重新生成（Support · 遗留）",
  PROFILE_REFRESH: "Profile 重建（Support · 遗留）",
  PROFILE_BACKFILL: "Profile 补齐（Support · 遗留）",
};

export function taskTypeLabel(type: string | undefined): string {
  return (type && TASK_TYPE_LABELS[type]) || type || "未知任务";
}

function idleMs(task: TaskRecord, now: number): number | null {
  const updated = Date.parse(task.updated_at);
  return Number.isFinite(updated) ? now - updated : null;
}

/** RUNNING with no progress update for 30 minutes (a hint, not a verdict). */
export function isStaleTask(task: TaskRecord, now = Date.now()): boolean {
  const idle = idleMs(task, now);
  return task.status === "RUNNING" && idle !== null && idle > STALE_RUNNING_MS;
}

/** QUEUED for more than 10 minutes; still counted as backlog. */
export function isLongQueued(task: TaskRecord, now = Date.now()): boolean {
  const idle = idleMs(task, now);
  return task.status === "QUEUED" && idle !== null && idle > LONG_QUEUE_MS;
}

export const taskCenter = reactive({
  /** QUEUED + RUNNING rows from the last successful load. */
  activeRows: [] as TaskRecord[],
  /** Finished in the last 24 hours (only loaded when requested). */
  recentRows: [] as TaskRecord[],
  loading: false,
  error: "",
  loadedAt: null as number | null,
  /** True when a status list hit the page limit, so counts are lower bounds. */
  truncated: false,
  /** Drawer visibility, shared by every trigger button. */
  drawerOpen: false,

  get active(): TaskRecord[] {
    const now = this.loadedAt ?? Date.now();
    return this.activeRows.filter((task) => !isStaleTask(task, now));
  },
  get stale(): TaskRecord[] {
    const now = this.loadedAt ?? Date.now();
    return this.activeRows.filter((task) => isStaleTask(task, now));
  },
});

let loadGeneration = 0;

export function resetTaskCenter(): void {
  loadGeneration++;
  taskCenter.activeRows = [];
  taskCenter.recentRows = [];
  taskCenter.loading = false;
  taskCenter.error = "";
  taskCenter.loadedAt = null;
  taskCenter.truncated = false;
  taskCenter.drawerOpen = false;
}

export async function loadTaskCenter(options: { includeRecent?: boolean } = {}): Promise<void> {
  if (!adminStore.hasServiceCredential) {
    resetTaskCenter();
    return;
  }
  const generation = ++loadGeneration;
  const credential = adminStore.serviceToken;
  const isCurrent = () => generation === loadGeneration
    && adminStore.hasServiceCredential && adminStore.serviceToken === credential;
  taskCenter.loading = true;
  const [running, queued, recent] = await Promise.allSettled([
    adminApi.listTaskRecords({ status: "RUNNING", limit: ACTIVE_LIST_LIMIT }),
    adminApi.listTaskRecords({ status: "QUEUED", limit: ACTIVE_LIST_LIMIT }),
    options.includeRecent
      ? adminApi.listTaskRecords({ from: new Date(Date.now() - RECENT_WINDOW_MS).toISOString(), limit: 50 })
      : Promise.resolve(null),
  ]);
  if (!isCurrent()) return;
  taskCenter.loading = false;
  if (running.status !== "fulfilled" || queued.status !== "fulfilled"
    || !Array.isArray(running.value) || !Array.isArray(queued.value)) {
    // Keep the previous rows but say they may be stale.
    taskCenter.error = "后台任务列表暂时无法加载，显示的可能是上次结果。";
    return;
  }
  taskCenter.error = "";
  taskCenter.activeRows = [...running.value, ...queued.value];
  taskCenter.truncated = running.value.length >= ACTIVE_LIST_LIMIT || queued.value.length >= ACTIVE_LIST_LIMIT;
  taskCenter.loadedAt = Date.now();
  if (recent.status === "fulfilled" && Array.isArray(recent.value)) {
    taskCenter.recentRows = recent.value.filter((task) => !["QUEUED", "RUNNING"].includes(task.status));
  }
}
