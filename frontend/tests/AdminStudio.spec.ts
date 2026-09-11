import ElementPlus, { ElMessageBox } from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import AdminStudio from "@/components/AdminStudio.vue";
import { adminStore } from "@/composables/adminStore";

describe("AdminStudio settings", () => {
  beforeEach(() => {
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    adminStore.error = "";
    adminStore.notice = "";
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    sessionStorage.clear();
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
  });

  it("accepts and saves a service access credential", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) }),
    );

    const wrapper = mount(AdminStudio, {
      props: { view: "configuration", navigationQuery: "tab=credentials" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    expect(wrapper.find(".el-tabs__item.is-active").text()).toContain("服务凭证");
    const input = wrapper.get("input");
    await input.setValue("session-token");
    expect(input.element.value).toBe("session-token");

    const saveButton = wrapper.findAll("button").find((button) => button.text() === "保存并重新加载");
    await saveButton!.trigger("click");
    await flushPromises();
    expect(sessionStorage.getItem("rag_service_token")).toBe("session-token");
    expect(wrapper.text()).toContain("当前会话已配置");
    wrapper.unmount();
  });

  it("confirms and deletes a parsed document", async () => {
    adminStore.saveToken("session-token");
    vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([
      {
        id: "kb-1",
        name: "产品库",
        description: null,
        status: "ENABLED",
        assistant_online: true,
        runtime_state: "READY",
        active_index_id: "index-1",
        building_index_id: null,
        rebuild_required: false,
        document_count: 2,
        active_chunk_count: 10,
        created_at: "2026-08-17T00:00:00Z",
        updated_at: "2026-08-17T00:00:00Z",
      },
    ]);
    vi.spyOn(adminApi, "listDocuments").mockResolvedValue([
      {
        id: "doc-1",
        knowledge_id: "kb-1",
        filename: "manual.docx",
        display_name: "操作手册",
        file_size: 1024,
        status: "STORED",
        created_at: "2026-08-17T00:00:00Z",
        updated_at: "2026-08-17T00:00:00Z",
      },
    ]);
    vi.spyOn(adminApi, "listIndexes").mockResolvedValue([]);
    vi.spyOn(adminApi, "listTraces").mockResolvedValue([]);
    vi.spyOn(adminApi, "listModels").mockResolvedValue([]);
    vi.spyOn(adminApi, "listPrompts").mockResolvedValue([]);
    const deleteSpy = vi.spyOn(adminApi, "deleteDocument").mockResolvedValue({
      document_id: "doc-1",
      deleted: true,
      build_request: {
        requested: true,
        coalesced: false,
        index_id: "index-2",
        task_id: "task-2",
        rebuild_required: false,
      },
    });
    vi.spyOn(ElMessageBox, "confirm").mockImplementation(async () => "confirm" as never);

    const wrapper = mount(AdminStudio, {
      props: { view: "documents" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    const deleteButton = wrapper.findAll("button").find((button) => button.text() === "删除");
    expect(deleteButton).toBeDefined();
    await deleteButton?.trigger("click");
    await flushPromises();

    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining("新索引发布前"),
      "删除文档",
      expect.objectContaining({ type: "warning" }),
    );
    expect(deleteSpy).toHaveBeenCalledWith("doc-1");
    expect(wrapper.text()).toContain("文档已删除，索引更新已提交");
    wrapper.unmount();
  });

  it("does not mount management views or poll without a service credential", async () => {
    const listKnowledgeBases = vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([]);

    const wrapper = mount(AdminStudio, {
      props: { view: "dashboard" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("需要配置服务凭证");
    expect(wrapper.text()).toContain("管理台已暂停自动加载和轮询");
    expect(listKnowledgeBases).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("stops automatic requests after the backend rejects the credential", async () => {
    vi.useFakeTimers();
    adminStore.saveToken("invalid-session-token");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: () => Promise.resolve({ error: { message: "服务凭证无效" } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(AdminStudio, {
      props: { view: "dashboard" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    expect(adminStore.credentialRejected).toBe(true);
    expect(wrapper.text()).toContain("需要配置服务凭证");
    const callsAfterRejection = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10_000);
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(callsAfterRejection);
    wrapper.unmount();
  });
});
