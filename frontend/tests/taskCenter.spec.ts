import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import TaskCenter from "@/components/TaskCenter.vue";
import TaskCenterButton from "@/components/TaskCenterButton.vue";
import { adminStore } from "@/composables/adminStore";
import {
  ACTIVE_LIST_LIMIT,
  isLongQueued,
  isStaleTask,
  loadTaskCenter,
  resetTaskCenter,
  taskCenter,
  taskTypeLabel,
} from "@/composables/taskCenterStore";
import type { KnowledgeBase, TaskRecord } from "@/types/admin";

const NOW = Date.parse("2026-10-08T12:00:00Z");
const minutesAgo = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

function task(overrides: Partial<TaskRecord>): TaskRecord {
  return {
    id: overrides.id ?? `task-${Math.random()}`,
    task_type: "INDEX_BUILD",
    status: "RUNNING",
    stage: "EMBEDDING",
    progress: 40,
    attempt: 1,
    kb_id: "kb-1",
    index_id: "idx-1",
    document_id: null,
    error_code: null,
    error_message: null,
    metadata: {},
    created_at: minutesAgo(5),
    started_at: minutesAgo(5),
    finished_at: null,
    updated_at: minutesAgo(1),
    ...overrides,
  };
}

const kb = { id: "kb-1", name: "产品库" } as KnowledgeBase;

describe("task center store", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    adminStore.serviceToken = "test-token";
    adminStore.credentialRejected = false;
    resetTaskCenter();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    adminStore.serviceToken = "";
    resetTaskCenter();
  });

  it("only moves long-silent RUNNING tasks out of progress; queued tasks always count", () => {
    expect(isStaleTask(task({ updated_at: minutesAgo(29) }), NOW)).toBe(false);
    expect(isStaleTask(task({ updated_at: minutesAgo(31) }), NOW)).toBe(true);
    // A task queued behind a long build is healthy backlog, never "stalled".
    expect(isStaleTask(task({ status: "QUEUED", updated_at: minutesAgo(600) }), NOW)).toBe(false);
    expect(isLongQueued(task({ status: "QUEUED", updated_at: minutesAgo(9) }), NOW)).toBe(false);
    expect(isLongQueued(task({ status: "QUEUED", updated_at: minutesAgo(11) }), NOW)).toBe(true);
    expect(isLongQueued(task({ status: "RUNNING", updated_at: minutesAgo(11) }), NOW)).toBe(false);
    expect(isStaleTask(task({ status: "FAILED", updated_at: minutesAgo(999) }), NOW)).toBe(false);
    expect(taskTypeLabel("INDEX_BUILD")).toBe("索引构建");
    expect(taskTypeLabel("NEW_TYPE")).toBe("NEW_TYPE");
  });

  it("loads running and queued tasks across knowledge bases", async () => {
    const list = vi.spyOn(adminApi, "listTaskRecords").mockImplementation(async (filters) => {
      if (filters?.status === "RUNNING") return [task({ id: "fresh" }), task({ id: "old", updated_at: minutesAgo(60 * 24 * 20) })];
      if (filters?.status === "QUEUED") {
        return [
          task({ id: "queued", status: "QUEUED" }),
          task({ id: "waiting", status: "QUEUED", updated_at: minutesAgo(45) }),
        ];
      }
      return [task({ id: "done", status: "SUCCEEDED" }), task({ id: "still", status: "RUNNING" })];
    });
    await loadTaskCenter({ includeRecent: true });
    expect(list).toHaveBeenCalledWith({ status: "RUNNING", limit: ACTIVE_LIST_LIMIT });
    expect(list).toHaveBeenCalledWith({ status: "QUEUED", limit: ACTIVE_LIST_LIMIT });
    expect(taskCenter.active.map((item) => item.id)).toEqual(["fresh", "queued", "waiting"]);
    expect(taskCenter.stale.map((item) => item.id)).toEqual(["old"]);
    expect(taskCenter.recentRows.map((item) => item.id)).toEqual(["done"]);
    expect(taskCenter.truncated).toBe(false);
  });

  it("flags truncated lists and keeps previous rows on failure", async () => {
    const full = Array.from({ length: ACTIVE_LIST_LIMIT }, (_, i) => task({ id: `t${i}` }));
    const list = vi.spyOn(adminApi, "listTaskRecords").mockImplementation(async (filters) => (filters?.status === "RUNNING" ? full : []));
    await loadTaskCenter();
    expect(taskCenter.truncated).toBe(true);
    list.mockRejectedValue(new Error("down"));
    await loadTaskCenter();
    expect(taskCenter.error).toContain("暂时无法加载");
    expect(taskCenter.activeRows).toHaveLength(ACTIVE_LIST_LIMIT);
  });

  it("ignores a late response after the credential was rejected", async () => {
    let resolve!: (value: TaskRecord[]) => void;
    vi.spyOn(adminApi, "listTaskRecords").mockImplementation((filters) =>
      filters?.status === "RUNNING" ? new Promise((done) => { resolve = done; }) : Promise.resolve([]));
    const pending = loadTaskCenter();
    adminStore.rejectCredential("invalid");
    resetTaskCenter();
    resolve([task({ id: "late" })]);
    await pending;
    expect(taskCenter.activeRows).toEqual([]);
    adminStore.credentialRejected = false;
  });

  it("does not request without a credential", async () => {
    adminStore.serviceToken = "";
    const list = vi.spyOn(adminApi, "listTaskRecords");
    await loadTaskCenter();
    expect(list).not.toHaveBeenCalled();
  });
});

describe("TaskCenter drawer and button", () => {
  beforeEach(() => {
    adminStore.serviceToken = "test-token";
    adminStore.credentialRejected = false;
    adminStore.knowledgeBases = [kb];
    resetTaskCenter();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    adminStore.serviceToken = "";
    adminStore.knowledgeBases = [];
    adminStore.selectedKnowledgeId = "";
    resetTaskCenter();
  });

  it("shows active, stalled and recent tasks and navigates to the owning page", async () => {
    vi.spyOn(adminApi, "listTaskRecords").mockImplementation(async (filters) => {
      if (filters?.status === "RUNNING") {
        return [
          task({ id: "active", updated_at: new Date().toISOString() }),
          task({ id: "orphan", kb_id: "gone", updated_at: new Date(Date.now() - 40 * 60_000).toISOString() }),
        ];
      }
      if (filters?.status === "QUEUED") return [];
      return [task({ id: "failed", status: "FAILED", error_message: "解析失败", finished_at: new Date().toISOString() })];
    });
    const selectKnowledge = vi.spyOn(adminStore, "selectKnowledge").mockResolvedValue();
    const navigate = vi.spyOn(adminStore, "navigate").mockImplementation(() => {});
    const button = mount(TaskCenterButton, { global: { plugins: [ElementPlus] } });
    const drawer = mount(TaskCenter, { global: { plugins: [ElementPlus] }, attachTo: document.body });
    await flushPromises();

    expect(button.get(".nav-badge").text()).toBe("1");
    expect(button.get("button").attributes("aria-label")).toBe("后台任务（1 个进行中）");
    await button.get("button").trigger("click");
    expect(taskCenter.drawerOpen).toBe(true);
    await flushPromises();

    const body = document.body.textContent ?? "";
    expect(body).toContain("进行中（1）");
    expect(body).toContain("产品库");
    expect(body).toContain("疑似停滞（1）");
    expect(body).toContain("最近 24 小时结束（1）");
    expect(body).toContain("解析失败");

    const goButton = Array.from(document.body.querySelectorAll<HTMLButtonElement>(".task-row button"))
      .find((item) => item.textContent?.includes("查看索引"));
    goButton!.click();
    await flushPromises();
    expect(adminStore.selectedKnowledgeId).toBe("kb-1");
    expect(selectKnowledge).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith("indexes");
    expect(taskCenter.drawerOpen).toBe(false);

    drawer.unmount();
    button.unmount();
  });

  it("marks long-queued tasks without dropping them from the badge", async () => {
    vi.spyOn(adminApi, "listTaskRecords").mockImplementation(async (filters) =>
      filters?.status === "QUEUED"
        ? [task({ id: "waiting", status: "QUEUED", updated_at: new Date(Date.now() - 45 * 60_000).toISOString() })]
        : []);
    const button = mount(TaskCenterButton, { global: { plugins: [ElementPlus] } });
    const drawer = mount(TaskCenter, { global: { plugins: [ElementPlus] }, attachTo: document.body });
    await flushPromises();
    expect(button.get(".nav-badge").text()).toBe("1");
    taskCenter.drawerOpen = true;
    await flushPromises();
    expect(document.body.textContent).toContain("已排队 45 分钟，可能在等待其他任务完成");
    expect(document.body.textContent).not.toContain("疑似停滞（");
    drawer.unmount();
    button.unmount();
  });

  it("keeps a single poll chain when refresh triggers overlap", async () => {
    vi.useFakeTimers();
    let release!: () => void;
    const list = vi.spyOn(adminApi, "listTaskRecords")
      .mockImplementationOnce(() => new Promise((resolve) => { release = () => resolve([]); }))
      .mockResolvedValue([]);
    const drawer = mount(TaskCenter, { global: { plugins: [ElementPlus] } });
    // Mount started a load (RUNNING request pending). Overlap it with two triggers.
    taskCenter.drawerOpen = true;
    adminStore.trackedTasks = [...adminStore.trackedTasks, { taskId: "x", taskType: "INDEX_BUILD", successMessage: "", knowledgeId: "kb-1" } as never];
    await flushPromises();
    release();
    await flushPromises();
    const afterOverlap = list.mock.calls.length;

    // Exactly one chain: one tick per 5 s while open (2 requests RUNNING+QUEUED, +1 recent).
    await vi.advanceTimersByTimeAsync(5_000);
    const perTick = list.mock.calls.length - afterOverlap;
    expect(perTick).toBe(3);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(list.mock.calls.length - afterOverlap).toBe(6);
    drawer.unmount();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(list.mock.calls.length - afterOverlap).toBe(6);
    adminStore.trackedTasks = [];
  });

  it("disables the trigger without a credential", () => {
    adminStore.serviceToken = "";
    const button = mount(TaskCenterButton, { global: { plugins: [ElementPlus] } });
    expect(button.get("button").attributes("disabled")).toBeDefined();
  });
});
