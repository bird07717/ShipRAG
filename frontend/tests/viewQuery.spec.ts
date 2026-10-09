import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import { replaceViewQuery, VIEW_QUERY_REPLACED_EVENT } from "@/utils/viewQuery";
import LogsView from "@/views/LogsView.vue";

describe("replaceViewQuery", () => {
  afterEach(() => window.history.replaceState(null, "", "#/dashboard"));

  it("rewrites the current view's query without a history entry and notifies the shell", () => {
    window.history.replaceState(null, "", "#/logs?tab=traces");
    const length = window.history.length;
    const notified = vi.fn();
    window.addEventListener(VIEW_QUERY_REPLACED_EVENT, notified);
    replaceViewQuery("logs", { tab: "task-failures", source: "", from: null });
    window.removeEventListener(VIEW_QUERY_REPLACED_EVENT, notified);

    expect(window.location.hash).toBe("#/logs?tab=task-failures");
    expect(window.history.length).toBe(length);
    expect(notified).toHaveBeenCalledTimes(1);
  });

  it("never rewrites another view's URL and skips no-op writes", () => {
    window.history.replaceState(null, "", "#/logs-archive?tab=x");
    const notified = vi.fn();
    window.addEventListener(VIEW_QUERY_REPLACED_EVENT, notified);
    replaceViewQuery("logs", { tab: "traces" });
    expect(window.location.hash).toBe("#/logs-archive?tab=x");
    window.history.replaceState(null, "", "#/logs?tab=traces");
    replaceViewQuery("logs", { tab: "traces" });
    window.removeEventListener(VIEW_QUERY_REPLACED_EVENT, notified);
    expect(notified).not.toHaveBeenCalled();
  });
});

describe("LogsView URL state", () => {
  beforeEach(() => {
    adminStore.selectedKnowledgeId = "kb-1";
    adminStore.serviceToken = "test-service-token";
    vi.spyOn(adminApi, "getFailureSummary").mockResolvedValue({
      from: "2026-10-01T00:00:00Z", to: "2026-10-05T00:00:00Z",
      scope: { trace_failures: "KNOWLEDGE_BASE", request_errors: "GLOBAL", knowledge_id: "kb-1" },
      trace_failures: [], request_errors: [], request_errors_by_day: [],
    });
    vi.spyOn(adminApi, "listRequestErrors").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    vi.spyOn(adminApi, "listTasksPage").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(adminApi, "listTracesPage").mockResolvedValue({ items: [], total: 0 });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    adminStore.selectedKnowledgeId = "";
    adminStore.serviceToken = "";
    window.history.replaceState(null, "", "#/dashboard");
  });

  it("writes the selected tab back and keeps the failed-turn scope only on that tab", async () => {
    window.history.replaceState(null, "", "#/logs?tab=failed-turns&engine=RAG&source=ASSISTANT_API");
    const wrapper = mount(LogsView, {
      props: { navigationQuery: "tab=failed-turns&engine=RAG&source=ASSISTANT_API" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();
    expect(window.location.hash).toBe("#/logs?tab=failed-turns&engine=RAG&source=ASSISTANT_API");
    const listTraces = vi.mocked(adminApi.listTracesPage);
    expect(listTraces).toHaveBeenCalledTimes(1);
    // Same state in another parameter order (as the shell re-reads the URL): no reload.
    await wrapper.setProps({ navigationQuery: "source=ASSISTANT_API&engine=RAG&tab=failed-turns" });
    await flushPromises();
    expect(listTraces).toHaveBeenCalledTimes(1);

    await wrapper.get("#tab-task-failures").trigger("click");
    await flushPromises();
    expect(window.location.hash).toBe("#/logs?tab=task-failures");

    // The write-back returning through navigationQuery is a no-op (no reload).
    const listTasks = vi.mocked(adminApi.listTasksPage);
    listTasks.mockClear();
    await wrapper.setProps({ navigationQuery: "tab=task-failures" });
    await flushPromises();
    expect(listTasks).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("#/logs?tab=task-failures");
    wrapper.unmount();
  });
});
