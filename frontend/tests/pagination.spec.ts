import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import type { TraceSummary } from "@/types/admin";
import TracesView from "@/views/TracesView.vue";

function jsonResponse(body: unknown, total?: string) {
  return {
    ok: true,
    json: () => Promise.resolve(body),
    headers: new Headers(total === undefined ? {} : { "X-Total-Count": total }),
  };
}

describe("server-paged admin API", () => {
  beforeEach(() => sessionStorage.setItem("rag_service_token", "session-token"));
  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("reads the total from X-Total-Count and sends paging and search parameters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([{ trace_id: "t1" }], "137"));
    vi.stubGlobal("fetch", fetchMock);

    const page = await adminApi.listTracesPage("kb-1", { status: "FAILED", question: "端口", limit: 50, offset: 100 });

    expect(page).toEqual({ items: [{ trace_id: "t1" }], total: 137 });
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/v1/traces?knowledge_id=kb-1&status=FAILED&q=%E7%AB%AF%E5%8F%A3&limit=50&offset=100");
  });

  it("falls back to the page length when an older server sends no total", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([{ id: "a" }, { id: "b" }])));
    await expect(adminApi.listTasksPage({ status: "FAILED", limit: 20 })).resolves.toEqual({
      items: [{ id: "a" }, { id: "b" }],
      total: 2,
    });
  });

  it("builds the server-side document query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([], "0"));
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.listDocumentsPage("kb-1", {
      search: "PSU",
      buildStatus: "SKIPPED",
      cardState: "PENDING_REVIEW",
      cardIndexId: "idx-1",
      sort: "display_name",
      order: "asc",
      limit: 20,
      offset: 40,
    });

    expect(fetchMock.mock.calls[0]![0]).toBe(
      "/api/v1/knowledge-bases/kb-1/documents?q=PSU&build_status=SKIPPED&card_index_id=idx-1"
        + "&card_state=PENDING_REVIEW&sort=display_name&order=asc&limit=20&offset=40",
    );
  });
});

function trace(index: number): TraceSummary {
  return {
    trace_id: `trace-${index}`, request_id: `r-${index}`, mode: "CHAT", kb_id: "kb-1", index_id: "idx-1",
    question: `问题 ${index}`, status: "COMPLETED", latency: {}, error: {}, created_at: "2026-10-09T00:00:00Z",
  } as TraceSummary;
}

describe("TracesView server paging", () => {
  beforeEach(() => {
    adminStore.selectedKnowledgeId = "kb-1";
    adminStore.serviceToken = "test-service-token";
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    adminStore.selectedKnowledgeId = "";
    adminStore.serviceToken = "";
  });

  it("pages on the server and searches questions across all records", async () => {
    const list = vi.spyOn(adminApi, "listTracesPage").mockResolvedValue({
      items: Array.from({ length: 50 }, (_, i) => trace(i)),
      total: 130,
    });
    const wrapper = mount(TracesView, { global: { plugins: [ElementPlus] } });
    await flushPromises();
    expect(list).toHaveBeenLastCalledWith(
      "kb-1",
      expect.objectContaining({ limit: 50, offset: 0, question: undefined }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(wrapper.text()).toContain("共 130 条记录");

    const pagination = wrapper.findComponent({ name: "ElPagination" });
    pagination.vm.$emit("update:current-page", 2);
    pagination.vm.$emit("current-change", 2);
    await flushPromises();
    expect(list).toHaveBeenLastCalledWith("kb-1", expect.objectContaining({ offset: 50 }), expect.anything());

    // Typing searches on the server (debounced) and restarts from page 1.
    vi.useFakeTimers();
    await wrapper.get('input[aria-label="按问题内容搜索链路"]').setValue("端口");
    vi.advanceTimersByTime(400);
    vi.useRealTimers();
    await flushPromises();
    expect(list).toHaveBeenLastCalledWith(
      "kb-1",
      expect.objectContaining({ question: "端口", offset: 0 }),
      expect.anything(),
    );
    wrapper.unmount();
  });
});

describe("DocumentsView server-side list", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    adminStore.selectedKnowledgeId = "";
    adminStore.serviceToken = "";
    adminStore.documents = [];
    adminStore.documentTotal = 0;
    adminStore.documentFilters = { buildStatus: "", cardState: "", sort: "created_at", order: "desc" };
  });

  it("sorts and filters through the server and resets to the first page", async () => {
    const { default: DocumentsView } = await import("@/views/DocumentsView.vue");
    adminStore.serviceToken = "test-service-token";
    adminStore.selectedKnowledgeId = "kb-1";
    adminStore.knowledgeBases = [{ id: "kb-1", name: "产品库", rag_active_index_id: "idx-1" } as never];
    adminStore.documents = [{ id: "doc-1", display_name: "操作手册", filename: "manual.pdf", file_size: 10, created_at: "2026-10-09T00:00:00Z" } as never];
    adminStore.documentTotal = 75;
    adminStore.documentPage = 2;
    const list = vi.spyOn(adminApi, "listDocumentsPage").mockResolvedValue({ items: adminStore.documents, total: 75 });
    const wrapper = mount(DocumentsView, { global: { plugins: [ElementPlus] } });
    await flushPromises();
    expect(wrapper.text()).toContain("共 75 个文档");

    wrapper.findComponent({ name: "ElTable" }).vm.$emit("sort-change", { prop: "size", order: "ascending" });
    await flushPromises();
    expect(list).toHaveBeenLastCalledWith("kb-1", expect.objectContaining({ sort: "file_size", order: "asc", offset: 0 }));
    expect(adminStore.documentPage).toBe(1);

    adminStore.documentFilters.buildStatus = "SKIPPED";
    const buildSelect = wrapper.findAllComponents({ name: "ElSelect" })
      .find((select) => select.attributes("aria-label") === "按最近构建状态筛选" || select.props("placeholder") === "最近构建：全部")!;
    buildSelect.vm.$emit("change", "SKIPPED");
    await flushPromises();
    expect(list).toHaveBeenLastCalledWith("kb-1", expect.objectContaining({ buildStatus: "SKIPPED", sort: "file_size" }));
    expect(wrapper.text()).toContain("匹配 75 个文档");
    wrapper.unmount();
  });
});

describe("review fixes on paged views", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    adminStore.selectedKnowledgeId = "";
    adminStore.serviceToken = "";
  });

  it("resetting trace filters reloads once, not again after the search debounce", async () => {
    adminStore.selectedKnowledgeId = "kb-1";
    adminStore.serviceToken = "test-service-token";
    const list = vi.spyOn(adminApi, "listTracesPage").mockResolvedValue({ items: [trace(1)], total: 1 });
    const wrapper = mount(TracesView, { global: { plugins: [ElementPlus] } });
    await flushPromises();
    vi.useFakeTimers();
    await wrapper.get('input[aria-label="按问题内容搜索链路"]').setValue("端口");
    vi.advanceTimersByTime(400);
    await flushPromises();
    list.mockClear();

    await wrapper.findAll("button").find((button) => button.text() === "重置")!.trigger("click");
    vi.advanceTimersByTime(1000);
    vi.useRealTimers();
    await flushPromises();
    expect(list).toHaveBeenCalledTimes(1);
    expect(list).toHaveBeenLastCalledWith("kb-1", expect.objectContaining({ question: undefined, offset: 0 }), expect.anything());
    wrapper.unmount();
  });

  it("keeps the pager visible on a later page and labels card state from the row", async () => {
    const { default: ListPagination } = await import("@/components/ui/ListPagination.vue");
    const pager = mount(ListPagination, {
      props: { total: 10, page: 2, pageSize: 20 },
      global: { plugins: [ElementPlus] },
    });
    expect(pager.findComponent({ name: "ElPagination" }).exists()).toBe(true);
    await pager.setProps({ page: 1 });
    expect(pager.findComponent({ name: "ElPagination" }).exists()).toBe(false);

    const { default: DocumentsView } = await import("@/views/DocumentsView.vue");
    adminStore.serviceToken = "test-service-token";
    adminStore.selectedKnowledgeId = "kb-1";
    adminStore.knowledgeBases = [{ id: "kb-1", name: "产品库", rag_active_index_id: "idx-1" } as never];
    adminStore.documents = [
      { id: "d1", display_name: "甲", filename: "a.pdf", file_size: 1, created_at: "2026-10-09T00:00:00Z", card_state: "PENDING_REVIEW" },
      { id: "d2", display_name: "乙", filename: "b.pdf", file_size: 1, created_at: "2026-10-09T00:00:00Z", card_state: "REJECTED" },
    ] as never;
    adminStore.documentTotal = 2;
    const view = mount(DocumentsView, { global: { plugins: [ElementPlus] } });
    await flushPromises();
    expect(view.text()).toContain("待确认");
    expect(view.text()).toContain("已拒绝");
    view.unmount();
    adminStore.documents = [];
    adminStore.documentTotal = 0;
  });
});
