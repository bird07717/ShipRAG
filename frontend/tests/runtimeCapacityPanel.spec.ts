import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import RuntimeCapacityPanel from "@/components/RuntimeCapacityPanel.vue";
import { adminStore } from "@/composables/adminStore";
import { resetTaskCenter } from "@/composables/taskCenterStore";
import type { TaskRecord } from "@/types/admin";

function task(id: string, status: TaskRecord["status"], minutesAgo: number, type = "INDEX_BUILD"): TaskRecord {
  const at = new Date(Date.now() - minutesAgo * 60_000).toISOString();
  return {
    id, task_type: type, status, stage: "PARSING", progress: 10, attempt: 1,
    kb_id: "kb-1", index_id: null, document_id: null, error_code: null, error_message: null,
    metadata: {}, created_at: at, started_at: at, finished_at: null, updated_at: at,
  };
}

describe("RuntimeCapacityPanel", () => {
  beforeEach(() => {
    adminStore.serviceToken = "test-token";
    adminStore.credentialRejected = false;
    resetTaskCenter();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    adminStore.serviceToken = "";
    resetTaskCenter();
  });

  it("translates the per-process pool snapshot and counts the service-wide backlog", async () => {
    // Real payload shape seen on the dev backend: overflow starts at -pool_size.
    vi.spyOn(adminApi, "getRuntimeMetrics").mockResolvedValue({
      turn_gate: { limit: 64, in_flight: 16 },
      database_pool: { size: 5, checked_in: 1, checked_out: 0, overflow: -4 },
    });
    vi.spyOn(adminApi, "listTaskRecords").mockImplementation(async (filters) =>
      filters?.status === "RUNNING"
        ? [task("a", "RUNNING", 1), task("b", "RUNNING", 60 * 24 * 20)]
        : [task("c", "QUEUED", 2, "RAG_DOCUMENT_CARD_GENERATE"), task("d", "QUEUED", 30)]);
    const wrapper = mount(RuntimeCapacityPanel, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("当前响应进程快照");
    expect(text).toContain("16 / 64");
    expect(text).toContain("占用 25%");
    expect(text).toContain("0 / 1"); // in use / open (5 + -4)
    expect(text).toContain("常驻连接池 5");
    expect(text).not.toContain("超出常驻池");
    expect(text).not.toContain("-4");
    const cells = wrapper.findAll(".capacity-group:last-child .capacity-grid > div").map((cell) => cell.find("b").text());
    // Long-queued tasks stay in the backlog; only the 20-day-silent RUNNING task is stalled.
    expect(cells).toEqual(["2", "1", "1"]); // queued, running, stalled
    expect(text).toContain("其中 1 个已排队超过 10 分钟");
    expect(text).toContain("索引构建");
    expect(text).toContain("RAG 文档理解生成");
  });

  it("shows pool overflow and a disabled gate, and reports load failures", async () => {
    const metrics = vi.spyOn(adminApi, "getRuntimeMetrics").mockResolvedValue({
      turn_gate: { limit: 0, in_flight: 3 },
      database_pool: { size: 5, checked_in: 0, checked_out: 7, overflow: 2 },
    });
    vi.spyOn(adminApi, "listTaskRecords").mockResolvedValue([]);
    const wrapper = mount(RuntimeCapacityPanel, { global: { plugins: [ElementPlus] } });
    await flushPromises();
    expect(wrapper.text()).toContain("未启用并发闸门");
    expect(wrapper.text()).toContain("7 / 7");
    expect(wrapper.text()).toContain("超出常驻池 2");

    metrics.mockRejectedValue(new Error("down"));
    await wrapper.findAll("button").find((button) => button.text().includes("刷新"))!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("运行快照暂时无法加载");
  });
});
