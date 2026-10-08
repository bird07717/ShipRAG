import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import LogsView from "@/views/LogsView.vue";
import type { TraceSummary } from "@/types/admin";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function record(question: string): TraceSummary {
  return { trace_id: question, mode: "CHAT", kb_id: "kb-1", index_id: "idx-1", question,
    status: "FAILED", latency: {}, error: {}, created_at: "2026-10-05T00:00:00Z" };
}
const wrappers: ReturnType<typeof mount>[] = [];
function editor(query = "tab=failed-turns&engine=RAG&source=ASSISTANT_API") {
  const wrapper = mount(LogsView, { props: { navigationQuery: query }, global: { plugins: [ElementPlus] } });
  wrappers.push(wrapper);
  return wrapper;
}
function refreshButton(wrapper: ReturnType<typeof editor>) {
  const pane = wrapper.findAllComponents({ name: "ElTabPane" }).find((item) => item.props("name") === "failed-turns")!;
  return pane.findAllComponents({ name: "ElButton" }).find((item) => item.text() === "刷新")!;
}

describe("failed trace request consistency", () => {
  beforeEach(() => {
    adminStore.selectedKnowledgeId = "kb-1";
    adminStore.serviceToken = "test-service-token";
    adminStore.credentialRejected = false;
    vi.spyOn(adminApi, "getFailureSummary").mockResolvedValue({
      from: "2026-10-01T00:00:00Z", to: "2026-10-05T00:00:00Z",
      scope: { trace_failures: "KNOWLEDGE_BASE", request_errors: "GLOBAL", knowledge_id: "kb-1" },
      trace_failures: [], request_errors: [], request_errors_by_day: [],
    });
    vi.spyOn(adminApi, "listRequestErrors").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  });
  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.restoreAllMocks();
    adminStore.selectedKnowledgeId = "";
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
  });

  it.each([
    ["deep-link", "success"], ["deep-link", "failure"],
    ["knowledge-base", "success"], ["knowledge-base", "failure"],
  ])("ignores an old %s request's late %s after the new scope loads", async (scope, outcome) => {
    const older = deferred<TraceSummary[]>();
    const list = vi.spyOn(adminApi, "listTraces").mockReturnValueOnce(older.promise).mockResolvedValue([record("新范围记录")]);
    const wrapper = editor();
    await flushPromises();
    if (scope === "deep-link") await wrapper.setProps({ navigationQuery: "tab=failed-turns&engine=SUPPORT&from=2026-10-04T00%3A00%3A00Z" });
    else adminStore.selectedKnowledgeId = "kb-2";
    await flushPromises();
    expect(list).toHaveBeenLastCalledWith(scope === "deep-link" ? "kb-1" : "kb-2", expect.objectContaining({ status: "FAILED" }));
    expect(wrapper.text()).toContain("新范围记录");
    if (outcome === "success") older.resolve([record("旧范围记录")]);
    else older.reject(new Error("旧请求失败"));
    await flushPromises();
    expect(wrapper.text()).toContain("新范围记录");
    expect(wrapper.text()).not.toContain("旧范围记录");
    expect(wrapper.text()).not.toContain("旧请求失败");
  });

  it.each(["success", "failure"])("does not finish the latest loading state when an older request ends with %s", async (outcome) => {
    const older = deferred<TraceSummary[]>();
    const newer = deferred<TraceSummary[]>();
    vi.spyOn(adminApi, "listTraces").mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    const wrapper = editor();
    await flushPromises();
    await wrapper.setProps({ navigationQuery: "tab=failed-turns&engine=SUPPORT" });
    if (outcome === "success") older.resolve([record("旧范围记录")]);
    else older.reject(new Error("旧请求失败"));
    await flushPromises();
    expect(refreshButton(wrapper).props("loading")).toBe(true);
    expect(wrapper.text()).not.toContain("旧范围记录");
    expect(wrapper.text()).not.toContain("旧请求失败");
    newer.resolve([record("最新记录")]);
    await flushPromises();
    expect(refreshButton(wrapper).props("loading")).toBe(false);
    expect(wrapper.text()).toContain("最新记录");
  });

  it.each(["success", "failure"])("ignores a late %s after leaving the failed-turn tab", async (outcome) => {
    const older = deferred<TraceSummary[]>();
    const list = vi.spyOn(adminApi, "listTraces").mockReturnValueOnce(older.promise).mockResolvedValue([record("返回后的记录")]);
    const wrapper = editor();
    await flushPromises();
    await wrapper.setProps({ navigationQuery: "tab=request-errors" });
    if (outcome === "success") older.resolve([record("旧范围记录")]);
    else older.reject(new Error("旧请求失败"));
    await flushPromises();
    expect(wrapper.text()).not.toContain("旧请求失败");
    expect(wrapper.text()).not.toContain("旧范围记录");
    await wrapper.setProps({ navigationQuery: "tab=failed-turns&engine=SUPPORT" });
    await flushPromises();
    expect(list).toHaveBeenLastCalledWith("kb-1", expect.objectContaining({ engine: "SUPPORT" }));
    expect(wrapper.text()).toContain("返回后的记录");
  });

  it("reports the latest failure and stops loading", async () => {
    vi.spyOn(adminApi, "listTraces").mockResolvedValueOnce([record("已有记录")]).mockRejectedValueOnce(new Error("最新请求失败"));
    const wrapper = editor();
    await flushPromises();
    expect(wrapper.text()).toContain("已有记录");
    await refreshButton(wrapper).trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("最新请求失败");
    expect(wrapper.text()).not.toContain("已有记录");
    expect(refreshButton(wrapper).props("loading")).toBe(false);
  });

  it("falls back from a removed escalation deep-link without loading escalation records", async () => {
    const list = vi.spyOn(adminApi, "listTraces").mockResolvedValue([]);
    const wrapper = editor("tab=escalations");
    await flushPromises();
    expect(wrapper.findComponent({ name: "ElTabs" }).props("modelValue")).toBe("request-errors");
    expect(wrapper.findAllComponents({ name: "ElTabPane" }).map((pane) => pane.props("name")))
      .not.toContain("escalations");
    expect(wrapper.text()).not.toContain("升级详情");
    expect(adminApi.listRequestErrors).toHaveBeenCalled();
    adminStore.selectedKnowledgeId = "kb-2";
    await flushPromises();
    expect(list).not.toHaveBeenCalled();
  });
});
