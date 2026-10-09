import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import type { ConversationListItem, KnowledgeBase } from "@/types/admin";
import ConversationsView from "@/views/ConversationsView.vue";

function conversation(overrides: Partial<ConversationListItem>): ConversationListItem {
  return {
    id: overrides.id ?? "conv-1",
    knowledge_id: "kb-1",
    user_id: "biz-user-1",
    title: "如何更换保险丝？",
    message_count: 4,
    last_execution_engine: "RAG",
    conversation_mode: null,
    created_at: "2026-10-07T01:00:00Z",
    updated_at: "2026-10-07T02:00:00Z",
    last_source: "ASSISTANT_API",
    ...overrides,
  };
}

const wrappers: ReturnType<typeof mount>[] = [];
function mountView(navigationQuery = "") {
  const wrapper = mount(ConversationsView, {
    props: { navigationQuery },
    global: { plugins: [ElementPlus] },
  });
  wrappers.push(wrapper);
  return wrapper;
}

describe("ConversationsView", () => {
  beforeEach(() => {
    adminStore.serviceToken = "test-token";
    adminStore.credentialRejected = false;
    adminStore.knowledgeBases = [
      { id: "kb-1", name: "产品库" } as KnowledgeBase,
      { id: "kb-2", name: "备件库" } as KnowledgeBase,
    ];
    adminStore.selectedKnowledgeId = "kb-1";
  });
  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.restoreAllMocks();
    adminStore.serviceToken = "";
    adminStore.selectedKnowledgeId = "";
    adminStore.knowledgeBases = [];
  });

  it("defaults to external Assistant conversations and opens the replay on row click", async () => {
    const list = vi.spyOn(adminApi, "listConversations").mockResolvedValue({ items: [conversation({})] });
    const open = vi.spyOn(adminStore, "openConversation").mockImplementation(() => {});
    const wrapper = mountView();
    await flushPromises();

    expect(list).toHaveBeenCalledWith(
      "kb-1",
      {
        source: "ASSISTANT_API",
        userId: undefined,
        limit: 50,
        offset: 0,
        activityFrom: undefined,
        activityTo: undefined,
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(wrapper.text()).toContain("如何更换保险丝？");
    expect(wrapper.text()).toContain("外部 Assistant");
    expect(wrapper.text()).toContain("biz-user-1");
    expect(wrapper.text()).toContain("1 个会话");
    await wrapper.get(".el-table__row").trigger("click");
    expect(open).toHaveBeenCalledWith("conv-1");
  });

  it("applies the exact user filter and labels conversations without traces", async () => {
    const list = vi.spyOn(adminApi, "listConversations").mockResolvedValue({
      items: [conversation({ id: "c2", user_id: null, last_source: null, title: null })],
    });
    const wrapper = mountView("source=ALL");
    await flushPromises();
    expect(list.mock.lastCall![1]).toMatchObject({ source: undefined });
    expect(wrapper.text()).toContain("（无标题）");
    expect(wrapper.text()).toContain("无链路记录");
    expect(wrapper.text()).toContain("无用户 ID");

    await wrapper.get('input[aria-label="用户 ID"]').setValue("  biz-user-9 ");
    await wrapper.findAll("button").find((button) => button.text() === "查询")!.trigger("click");
    await flushPromises();
    expect(list.mock.lastCall![1]).toMatchObject({ userId: "biz-user-9" });
  });

  it("reads source and user from the deep link and pages on the server", async () => {
    const firstPage = Array.from({ length: 50 }, (_, i) => conversation({ id: `c${i}` }));
    const list = vi.spyOn(adminApi, "listConversations").mockResolvedValue({ items: firstPage, total: 120 });
    const wrapper = mountView("source=INTERNAL_CHAT&user=tester");
    await flushPromises();
    expect(list.mock.lastCall![1]).toEqual({
      source: "INTERNAL_CHAT",
      userId: "tester",
      limit: 50,
      offset: 0,
      activityFrom: undefined,
      activityTo: undefined,
    });
    expect(wrapper.text()).toContain("共 120 个会话");

    // Next page keeps the filters and asks the server for the following rows.
    const pagination = wrapper.findComponent({ name: "ElPagination" });
    expect(pagination.exists()).toBe(true);
    pagination.vm.$emit("update:current-page", 3);
    pagination.vm.$emit("current-change", 3);
    await flushPromises();
    expect(list.mock.lastCall![1]).toMatchObject({ source: "INTERNAL_CHAT", userId: "tester", limit: 50, offset: 100 });

    // A filter change restarts from the first page.
    await wrapper.findAll("button").find((button) => button.text() === "查询")!.trigger("click");
    await flushPromises();
    expect(list.mock.lastCall![1]).toMatchObject({ offset: 0 });
  });

  it("drops a late response after switching knowledge base", async () => {
    let resolveOld!: (value: { items: ConversationListItem[] }) => void;
    vi.spyOn(adminApi, "listConversations")
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValue({ items: [conversation({ id: "kb2-conv", knowledge_id: "kb-2", title: "备件会话" })] });
    const wrapper = mountView();
    await flushPromises();
    adminStore.selectedKnowledgeId = "kb-2";
    await flushPromises();
    resolveOld({ items: [conversation({ title: "旧知识库会话" })] });
    await flushPromises();
    expect(wrapper.text()).toContain("备件会话");
    expect(wrapper.text()).not.toContain("旧知识库会话");
  });

  it("filters conversations by recent activity date and time on submit", async () => {
    const list = vi.spyOn(adminApi, "listConversations").mockResolvedValue({ items: [] });
    const wrapper = mountView();
    await flushPromises();

    const rangePicker = wrapper.findComponent({ name: "ElDatePicker" });
    expect(rangePicker.exists()).toBe(true);
    rangePicker.vm.$emit("update:modelValue", [
      new Date("2026-10-07T09:00:00.000Z"),
      new Date("2026-10-07T11:00:00.000Z"),
    ]);
    await wrapper.findAll("button").find((button) => button.text() === "查询")!.trigger("click");
    await flushPromises();

    expect(list.mock.lastCall![1]).toMatchObject({
      activityFrom: "2026-10-07T09:00:00.000Z",
      activityTo: "2026-10-07T11:00:00.000Z",
    });
  });

  it("shows errors and empty states without fabricating rows", async () => {
    vi.spyOn(adminApi, "listConversations").mockRejectedValueOnce(new Error("会话服务不可用")).mockResolvedValue({ items: [] });
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain("会话服务不可用");
    expect(wrapper.find(".el-table__row").exists()).toBe(false);

    await wrapper.findAll("button").find((button) => button.text() === "查询")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("该条件下没有会话");

    adminStore.selectedKnowledgeId = "";
    await flushPromises();
    expect(wrapper.text()).toContain("请选择知识库");
  });
});
